from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.runner.reasoning import PolicyCompiler, RunProfileResolver, compile_subagent_policy
from app.schemas.agent import AnswerMode, PlanExecution, RequestedReasoningPolicy
from app.schemas.subagents import (
    DelegationContract,
    DelegationInput,
    DelegationRejectionCode,
    DelegationRequest,
    SubagentArtifactReference,
    SubagentBudgetEnvelope,
    SubagentContextItem,
    SubagentContextManifest,
    SubagentExecutionStatus,
    SubagentQuestion,
    SubagentResult,
)


def delegation_request(**updates) -> DelegationRequest:
    values = {
        "request_id": "research-runtime",
        "objective": "Research durable subagent runtimes",
        "success_criteria": ["Return a sourced comparison"],
        "scope": {"included": ["runtime architecture"], "excluded": ["UI design"]},
        "inputs": [
            DelegationInput(
                kind="evidence",
                ref="evidence://run/source-1",
                data_labels=["public"],
                allowed_purposes=["research"],
            )
        ],
        "output_schema": {
            "type": "object",
            "properties": {"comparison": {"type": "array"}},
            "required": ["comparison"],
        },
        "required_capabilities": ["information.search"],
        "budget": SubagentBudgetEnvelope(),
        "deadline_at": datetime.now(UTC) + timedelta(minutes=5),
        "dedupe_key": "runtime-comparison-v1",
    }
    values.update(updates)
    return DelegationRequest.model_validate(values)


def test_delegation_request_and_contract_are_strict_and_serializable():
    request = delegation_request()
    contract = DelegationContract(
        contract_id="contract-1",
        contract_hash="sha256:contract",
        task_id="task-1",
        run_id="run-1",
        parent_execution_id="agent-root",
        depth=1,
        request=request,
        created_at=datetime.now(UTC),
    )

    restored = DelegationContract.model_validate(contract.model_dump(mode="json"))

    assert restored == contract
    with pytest.raises(ValidationError):
        DelegationRequest.model_validate({**request.model_dump(), "unknown": True})
    with pytest.raises(ValidationError):
        contract.contract_id = "changed"


def test_delegation_schema_exposes_stable_validation_codes():
    with pytest.raises(ValidationError) as criteria_error:
        delegation_request(success_criteria=[" "])
    assert DelegationRejectionCode.missing_success_criteria.value in str(criteria_error.value)

    with pytest.raises(ValidationError) as schema_error:
        delegation_request(output_schema={"type": "array"})
    assert DelegationRejectionCode.invalid_output_schema.value in str(schema_error.value)

    with pytest.raises(ValidationError) as scope_error:
        delegation_request(scope={"included": ["same", "same"]})
    assert DelegationRejectionCode.incomplete_scope.value in str(scope_error.value)


def test_context_manifest_requires_bounded_explicit_items():
    item = SubagentContextItem(
        id="contract",
        kind="delegation_contract",
        content="bounded contract",
        summary="Delegated objective and constraints",
        content_hash="sha256:context",
        provenance={"run_id": "run-1"},
        estimated_tokens=12,
        size_bytes=16,
    )
    manifest = SubagentContextManifest(
        agent_execution_id="agent-child",
        purpose="research",
        items=(item,),
        total_estimated_tokens=12,
        created_at=datetime.now(UTC),
    )

    assert SubagentContextManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    with pytest.raises(ValidationError):
        SubagentContextManifest(
            agent_execution_id="agent-child",
            purpose="research",
            items=(item,),
            total_estimated_tokens=11,
            created_at=datetime.now(UTC),
        )


def test_subagent_result_status_contract_is_total():
    completed = SubagentResult(
        status="completed",
        summary="Done",
        outputs={"comparison": []},
        artifacts=[
            SubagentArtifactReference(
                id="artifact-1", uri="artifact://run-1/report", name="report.md"
            )
        ],
        usage={"tokens": 20},
    )
    assert completed.status == SubagentExecutionStatus.completed

    waiting = SubagentResult(
        status="waiting_parent",
        question=SubagentQuestion(
            prompt="Which region?", required_fields=["region"], continuation_token="token-1"
        ),
    )
    assert waiting.question is not None
    with pytest.raises(ValidationError):
        SubagentResult(status="waiting_parent")
    with pytest.raises(ValidationError):
        SubagentResult(
            status="completed",
            question=SubagentQuestion(prompt="Unexpected", continuation_token="token-2"),
        )


def test_subagent_settings_are_disabled_and_conservative_by_default():
    settings = Settings()

    assert settings.agent_subagent_execution_enabled is False
    assert settings.agent_subagent_max_depth == 1
    assert settings.agent_subagent_read_only is True
    assert settings.agent_subagent_max_parallel_children <= 2

    with pytest.raises(ValidationError):
        Settings(
            agent_subagent_max_children_total=1,
            agent_subagent_max_children_per_parent=2,
        )


def test_policy_compiler_freezes_effective_subagent_limits():
    settings = Settings(
        model_provider="mock",
        model_name="mock-agent",
        agent_subagent_execution_enabled=True,
        agent_subagent_rollout_cohort="admin-canary",
        agent_subagent_max_children_total=3,
        agent_subagent_max_children_per_parent=2,
        agent_subagent_max_parallel_children=2,
    )
    child_policy = compile_subagent_policy(settings)
    snapshot = PolicyCompiler().compile(
        RequestedReasoningPolicy(), subagent_policy=child_policy
    )

    serialized = snapshot.model_dump(mode="json")
    assert serialized["effective"]["subagents"]["enabled"] is True
    assert serialized["effective"]["subagents"]["budgets"]["max_children_total"] == 3
    assert serialized["effective"]["subagents"]["model_routing"]["allowed_models"] == [
        "mock-agent"
    ]


def test_standard_profile_cannot_enable_subagents_but_trusted_profile_can():
    policy = compile_subagent_policy(Settings(agent_subagent_execution_enabled=True))

    standard = RunProfileResolver().resolve(
        AnswerMode.standard,
        RequestedReasoningPolicy(),
        subagent_policy=policy,
    )
    trusted = RunProfileResolver().resolve(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto,
        subagent_policy=policy,
    )

    assert standard.reasoning_policy.effective.subagents.enabled is False
    assert trusted.reasoning_policy.effective.subagents.enabled is True


def test_historical_reasoning_snapshot_defaults_to_disabled_subagents():
    snapshot = PolicyCompiler().compile(RequestedReasoningPolicy()).model_dump(mode="json")
    snapshot["effective"].pop("subagents")

    restored = PolicyCompiler().compile(RequestedReasoningPolicy()).model_validate(snapshot)

    assert restored.effective.subagents.enabled is False
