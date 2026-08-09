from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.application.subagents.context import (
    SubagentContextComposer,
    SubagentContinuationService,
)
from app.application.subagents.governance import FrozenChildCatalog
from app.common.schemas.subagents import (
    DelegationContract,
    DelegationInput,
    DelegationRequest,
    DelegationScope,
    EffectiveDelegationScope,
    SubagentBudgetEnvelope,
    SubagentContextCheckpoint,
)

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
        tools=("catalog_search",),
        skills=("managed:research",),
        data_labels=("public",),
        allowed_purposes=("research",),
        network_destinations=("https://example.com/**",),
        workspace_read_roots=("inputs/**",),
        private_staging_root=".astra/subagents/child-1/staging",
    )


def _catalog() -> FrozenChildCatalog:
    return FrozenChildCatalog(
        tools=({"name": "catalog_search", "version": "1"},),
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
    assert composed.manifest.total_estimated_tokens == sum(item.estimated_tokens for item in composed.manifest.items)

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
    checkpoint = SubagentContextCheckpoint(
        agent_execution_id=composed.manifest.agent_execution_id,
        manifest_hash=composed.manifest_hash,
        local_summary="Inspected the delegated sources.",
        local_facts=({"text": "local only"},),
        created_at=NOW,
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
