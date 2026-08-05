from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.application.agent_runtime.policies.reasoning import (
    AgentReasoningPolicyCompiler,
    compile_subagent_policy,
    resolve_run_profile,
)
from app.application.subagents.eligibility import subagent_execution_eligibility
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.types import AnswerMode, PlanExecution
from app.common.schemas.subagents import (
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


def test_subagent_settings_are_governed_and_conservative_by_default():
    settings = AstraRuntimeSettings()

    assert settings.tool_swarm_enabled is True
    assert settings.agent_subagent_rollout_cohort == "trusted_read_only"
    assert settings.agent_subagent_max_depth == 1
    assert settings.agent_subagent_read_only is True
    assert settings.agent_subagent_max_parallel_children <= 2

    with pytest.raises(ValidationError):
        AstraRuntimeSettings(
            agent_subagent_max_children_total=1,
            agent_subagent_max_children_per_parent=2,
        )


def test_policy_compiler_freezes_effective_subagent_limits():
    settings = AstraRuntimeSettings(
        model_provider="mock",
        model_name="mock-agent",
        agent_subagent_rollout_cohort="admin-canary",
        agent_subagent_max_children_total=3,
        agent_subagent_max_children_per_parent=2,
        agent_subagent_max_parallel_children=2,
    )
    child_policy = compile_subagent_policy(settings)
    snapshot = AgentReasoningPolicyCompiler().compile(
        RequestedReasoningPolicy(), subagent_policy=child_policy
    )

    serialized = snapshot.model_dump(mode="json")
    assert serialized["effective"]["subagents"]["enabled"] is True
    assert serialized["effective"]["subagents"]["budgets"]["max_children_total"] == 3
    assert serialized["effective"]["subagents"]["model_routing"]["allowed_models"] == [
        "mock-agent"
    ]


def test_swarm_tool_switch_is_the_product_enablement_gate():
    user_enabled = compile_subagent_policy(AstraRuntimeSettings(tool_swarm_enabled=True))
    user_disabled = compile_subagent_policy(AstraRuntimeSettings(tool_swarm_enabled=False))

    assert user_enabled.enabled is True
    assert user_disabled.enabled is False
    assert user_disabled.budgets.max_parallel_children == 0


def test_standard_profile_uses_a_clamped_shared_subagent_policy():
    policy = compile_subagent_policy(AstraRuntimeSettings(tool_swarm_enabled=True))

    standard = resolve_run_profile(
        AnswerMode.standard,
        RequestedReasoningPolicy(),
        subagent_policy=policy,
    )
    trusted = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto,
        subagent_policy=policy,
    )

    quick_policy = standard.reasoning_policy.effective.subagents
    trusted_policy = trusted.reasoning_policy.effective.subagents

    assert quick_policy.enabled is True
    assert quick_policy.read_only is True
    assert quick_policy.budgets.max_depth == 1
    assert quick_policy.budgets.max_children_total == 2
    assert quick_policy.budgets.max_tokens == 8_000
    assert quick_policy.budgets.max_wall_time_seconds == 120
    assert quick_policy.model_routing.max_reasoning_effort.value == "fast"
    assert trusted.reasoning_policy.effective.subagents.enabled is True
    assert trusted_policy.budgets.max_children_total == 4
    assert trusted_policy.budgets.max_tokens == 16_000


def test_subagent_execution_eligibility_is_shared_across_answer_modes():
    policy = compile_subagent_policy(AstraRuntimeSettings(tool_swarm_enabled=True))

    assert subagent_execution_eligibility(policy, live_swarm_enabled=True).executable
    disabled = subagent_execution_eligibility(policy, live_swarm_enabled=False)
    assert disabled.executable is False
    assert disabled.reason == "swarm_disabled"


def test_historical_reasoning_snapshot_defaults_to_disabled_subagents():
    snapshot = AgentReasoningPolicyCompiler().compile(RequestedReasoningPolicy()).model_dump(mode="json")
    snapshot["effective"].pop("subagents")

    restored = AgentReasoningPolicyCompiler().compile(RequestedReasoningPolicy()).model_validate(snapshot)

    assert restored.effective.subagents.enabled is False
