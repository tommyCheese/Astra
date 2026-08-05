from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.application.subagents.context import (
    SubagentContextComposer,
    SubagentContinuationService,
    SubagentExchangeService,
    create_context_checkpoint,
)
from app.application.subagents.governance import FrozenChildCatalog
from app.common.schemas.subagents import (
    DelegationContract,
    DelegationInput,
    DelegationRequest,
    DelegationScope,
    EffectiveDelegationScope,
    SubagentBudgetEnvelope,
)
from app.domain.grounding.ledger import GroundingEvidenceConflictError
from app.domain.grounding.schemas import GroundingEvidenceFragment, GroundingEvidenceKind
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _contract(*, inputs: list[DelegationInput] | None = None) -> DelegationContract:
    request = DelegationRequest(
        request_id="context-1",
        objective="Research the delegated subject",
        success_criteria=["Return one verified finding"],
        scope=DelegationScope(included=["subject:one"]),
        inputs=inputs or [],
        output_schema={"type": "object", "properties": {}},
        resource_scope={"purpose": "research"},
        budget=SubagentBudgetEnvelope(),
        dedupe_key="context:one",
    )
    return DelegationContract(
        contract_id="dc-context",
        contract_hash="sha256:contract",
        task_id="task-1",
        run_id="run-1",
        parent_execution_id="root-1",
        depth=1,
        request=request,
        created_at=NOW,
    )


def _scope() -> EffectiveDelegationScope:
    return EffectiveDelegationScope(
        actions=("network_read",),
        resources=("https://example.com/**",),
        effect_kinds=("network_read",),
        tools=("web_search",),
        skills=("managed:research",),
        data_labels=("public",),
        allowed_purposes=("research",),
        network_destinations=("https://example.com/**",),
        workspace_read_roots=("inputs/**",),
        private_staging_root=".astra/subagents/child-1/staging",
    )


def _catalog() -> FrozenChildCatalog:
    return FrozenChildCatalog(
        tools=({"name": "web_search", "version": "1"},),
        tool_digest="sha256:tools",
        skills=(
            {
                "qualified_identity": "managed:research",
                "revision_id": "rev-1",
            },
        ),
        skill_digest="sha256:skills",
    )


def test_context_composer_is_minimal_reference_first_and_records_gaps():
    inputs = [
        DelegationInput(
            kind="fact",
            ref="fact:small",
            summary="Small explicit fact",
            data_labels=["public"],
            allowed_purposes=["research"],
        ),
        DelegationInput(
            kind="artifact",
            ref="artifact:large-report",
            summary="Large report reference",
            data_labels=["public"],
            allowed_purposes=["research"],
        ),
        DelegationInput(
            kind="fact",
            ref="fact:large",
            data_labels=["public"],
            allowed_purposes=["research"],
        ),
        DelegationInput(
            kind="fact",
            ref="fact:secret",
            data_labels=["secret"],
            allowed_purposes=["research"],
        ),
        DelegationInput(
            kind="fact",
            ref="fact:wrong-purpose",
            data_labels=["public"],
            allowed_purposes=["billing"],
        ),
    ]
    composed = SubagentContextComposer(max_inline_bytes=32).compose(
        agent_execution_id="child-1",
        contract=_contract(inputs=inputs),
        effective_scope=_scope(),
        catalog=_catalog(),
        profile_layers=[{"summary": "Applicable managed profile", "rule": "cite sources"}],
        selected_facts={
            "fact:small": "small",
            "fact:large": "x" * 100,
            "fact:secret": "not included",
            "fact:wrong-purpose": "not included",
        },
        created_at=NOW,
    )

    by_ref = {item.ref: item for item in composed.manifest.items if item.ref}
    assert by_ref["fact:small"].content == "small"
    assert by_ref["artifact:large-report"].content is None
    assert by_ref["artifact:large-report"].ref == "artifact:large-report"
    assert "fact:large" not in by_ref
    assert {gap.reason_code for gap in composed.gaps} == {
        "inline_too_large",
        "data_label_denied",
        "purpose_mismatch",
    }
    assert composed.manifest.tool_catalog_digest == "sha256:tools"
    assert composed.manifest.total_estimated_tokens == sum(
        item.estimated_tokens for item in composed.manifest.items
    )

    denied = SubagentContextComposer().compose(
        agent_execution_id="child-1",
        contract=_contract(
            inputs=[
                DelegationInput(
                    kind="artifact",
                    ref="artifact:denied",
                    data_labels=["public"],
                    allowed_purposes=["research"],
                )
            ]
        ),
        effective_scope=_scope(),
        catalog=_catalog(),
        permission_check=lambda _ref, _labels, _purpose: False,
    )
    assert denied.gaps[0].reason_code == "permission_denied"


def test_context_composer_rejects_parent_private_state_and_tiny_budget():
    composer = SubagentContextComposer()
    with pytest.raises(ValueError, match="Forbidden parent-private"):
        composer.compose(
            agent_execution_id="child-1",
            contract=_contract(),
            effective_scope=_scope(),
            catalog=_catalog(),
            selected_facts={"hidden_reasoning": "never copy this"},
        )
    with pytest.raises(ValueError, match="mandatory protocol"):
        SubagentContextComposer(max_total_tokens=1).compose(
            agent_execution_id="child-1",
            contract=_contract(),
            effective_scope=_scope(),
            catalog=_catalog(),
        )


def test_context_checkpoint_and_continuation_are_child_local_bounded_and_versioned():
    composed = SubagentContextComposer().compose(
        agent_execution_id="child-1",
        contract=_contract(),
        effective_scope=_scope(),
        catalog=_catalog(),
        created_at=NOW,
    )
    checkpoint = create_context_checkpoint(
        composed=composed,
        local_summary="Inspected the delegated sources.",
        local_facts=[{"text": "local only"}],
    )
    continuation = SubagentContinuationService("test-secret", max_round_trips=1)
    question = continuation.question(
        checkpoint=checkpoint,
        prompt="Which jurisdiction applies?",
        required_fields=["jurisdiction"],
    )
    resumed = continuation.answer(
        checkpoint=checkpoint,
        question=question,
        values={"jurisdiction": "EU"},
    )

    assert checkpoint.manifest_hash == composed.manifest_hash
    assert resumed.continuation_round_trips == 1
    assert resumed.continuation_answers[0].values == {"jurisdiction": "EU"}
    with pytest.raises(ValueError, match="round-trip limit"):
        continuation.question(
            checkpoint=resumed,
            prompt="Ask again",
            required_fields=[],
        )
    with pytest.raises(ValueError, match="stale or out of order"):
        continuation.answer(
            checkpoint=resumed,
            question=question,
            values={"jurisdiction": "EU"},
        )


async def test_child_artifact_evidence_staging_and_explicit_parent_promotion(session):
    run = await RunUnitOfWork(session).create_task_run("Child exchange", {})
    executions = AgentExecutionRepository(session)
    root = await executions.root_for_run(run.id)
    assert root is not None
    contract = _contract().model_copy(
        update={
            "task_id": run.task_id,
            "run_id": run.id,
            "parent_execution_id": root.id,
        }
    )
    child = await executions.create_child(contract=contract)
    sibling_contract = contract.model_copy(
        update={
            "contract_id": "dc-sibling",
            "contract_hash": "sha256:sibling",
            "request": contract.request.model_copy(
                update={"request_id": "context-sibling", "dedupe_key": "context:sibling"}
            ),
        }
    )
    sibling = await executions.create_child(contract=sibling_contract)
    await session.commit()
    exchange = SubagentExchangeService(session)
    artifact = await exchange.stage_artifact(
        agent_execution_id=child.id,
        relative_name="report.md",
        artifact_type="child_report",
        content_ref="object://child/report",
        mime_type="text/markdown",
        size_bytes=120_000,
        checksum="sha256:report",
        provenance={"tool_call_id": "tool-1"},
    )
    evidence = await exchange.stage_evidence(
        agent_execution_id=child.id,
        fragment=GroundingEvidenceFragment(
            id="ev-1",
            kind=GroundingEvidenceKind.claim,
            evidence_key="child:claim:1",
            payload_digest="digest-1",
            payload={"claim": "Verified"},
        ),
    )
    with pytest.raises(
        GroundingEvidenceConflictError, match="AgentExecution isolation"
    ):
        await exchange.stage_evidence(
            agent_execution_id=sibling.id,
            fragment=GroundingEvidenceFragment(
                id="ev-1",
                kind=GroundingEvidenceKind.claim,
                evidence_key="child:claim:1",
                payload_digest="digest-1",
                payload={"claim": "Verified"},
            ),
        )
    with pytest.raises(ValueError, match="unsafe"):
        await exchange.stage_artifact(
            agent_execution_id=child.id,
            relative_name="../escape.md",
            artifact_type="child_report",
            content_ref="object://child/escape",
            mime_type="text/markdown",
            size_bytes=1,
            checksum="sha256:escape",
            provenance={},
        )
    artifact.security_status = "verified"
    await session.commit()
    promoted = await exchange.promote_artifact(
        parent_execution_id=root.id,
        artifact_id=artifact.id,
        public_path="reports/final.md",
    )
    parent_version = root.state_version
    parent = await exchange.promote_verified_facts(
        parent_execution_id=root.id,
        child_execution_id=child.id,
        facts=[
            {
                "text": "Verified",
                "verified": True,
                "evidence_refs": [evidence.id],
            }
        ],
        expected_parent_state_version=parent_version,
    )

    assert artifact.path.startswith(".astra/subagents/") is False
    assert promoted.path == "reports/final.md"
    assert promoted.provenance["source_agent_execution_id"] == child.id
    assert evidence.agent_execution_id == child.id
    assert parent.checkpoint["promoted_child_facts"][0][
        "source_agent_execution_id"
    ] == child.id
    with pytest.raises(ValueError, match="verified"):
        await exchange.promote_verified_facts(
            parent_execution_id=root.id,
            child_execution_id=child.id,
            facts=[{"text": "Unverified", "verified": False}],
            expected_parent_state_version=parent.state_version,
        )
