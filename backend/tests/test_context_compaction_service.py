from datetime import datetime, timezone

import pytest

from app.context_compaction import (
    AgentContextCompactionService,
    CompactionGeneration,
    TokenAccountingService,
    build_compaction_policy,
)
from app.context_compaction.parsing import extract_json_object
from app.context_compaction.validation import CheckpointValidationError, validate_checkpoint_payload
from app.core.config import Settings
from app.db.models import AgentExecutionRecord, RunRecord, TaskRecord, utc_now
from app.repositories.context_compaction import ContextCompactionAttemptRepository
from app.schemas.context_compaction import (
    ContextEnvelope,
    ContextItem,
    ContextOwnerRole,
    ContextReference,
    ContinuationManifest,
)


def envelope(*, total_body: int = 1_000) -> ContextEnvelope:
    accounting = TokenAccountingService().account(
        context_window=10_000,
        output_reserve=1_000,
        compaction_output_reserve=1_000,
        protected_prefix=(
            ContextItem(id="request", kind="current_request", content="implement safely", token_count=200, canonical=True),
        ),
        body=(
            ContextItem(id="old", kind="observation", summary="old progress", token_count=total_body),
            ContextItem(id="new", kind="observation", summary="latest failure", token_count=300),
        ),
    )
    return ContextEnvelope(
        owner_type=ContextOwnerRole.root_execution,
        owner_id="root-1",
        purpose="continue root execution",
        protected_prefix=(
            ContextItem(id="request", kind="current_request", content="implement safely", token_count=200, canonical=True),
        ),
        compactable_body=(
            ContextItem(id="old", kind="observation", summary="old progress", token_count=total_body),
            ContextItem(id="new", kind="observation", summary="latest failure", token_count=300),
        ),
        reference_manifest=(
            ContextReference(kind="evidence", ref="evidence:1"),
        ),
        accounting=accounting,
        continuation=ContinuationManifest(
            owner_type=ContextOwnerRole.root_execution,
            owner_id="root-1",
            state_version=3,
            cancellation_epoch=2,
            window_number=1,
            source_item_ids=("old", "new"),
        ),
    )


def semantic_payload() -> dict:
    return {
        "schema_version": 2,
        "checkpoint_role": "root_execution",
        "user_intent": "implement safely",
        "current_constraints": ["provider neutral"],
        "key_decisions": [],
        "verified_facts": [{"text": "verified", "evidence_refs": ["evidence:1"]}],
        "global_progress": [],
        "workspace_changes": [],
        "child_results": [],
        "recent_failures": [],
        "open_issues": [],
        "next_steps": ["continue"],
        "trust": {
            "lossy": True,
            "trusted_for_authorization": False,
            "trusted_for_completion": False,
            "untrusted_inputs": [],
            "generated_from_canonical_state": False,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def policy():
    return build_compaction_policy(
        Settings(
            context_compaction_v2_enabled=True,
            context_compaction_root_enabled=True,
            context_compaction_shadow_mode=False,
        ),
        ContextOwnerRole.root_execution,
    )


def test_parser_accepts_fenced_json_and_bounded_trailing_comma_repair():
    parsed = extract_json_object('```json\n{"a": 1,}\n```')
    assert parsed == {"a": 1}


def test_reference_and_forbidden_content_validation():
    payload = semantic_payload()
    payload["verified_facts"][0]["evidence_refs"] = ["evidence:missing"]
    with pytest.raises(CheckpointValidationError, match="reference_inaccessible"):
        validate_checkpoint_payload(payload, envelope())


def test_astra_checkpoint_is_portable_across_provider_budget_recalculation():
    checkpoint = validate_checkpoint_payload(semantic_payload(), envelope())
    small_provider = TokenAccountingService(tokenizer=lambda text: len(text) // 5).account(
        context_window=32_000,
        output_reserve=4_000,
        compaction_output_reserve=2_000,
        checkpoint=(
            ContextItem(
                id="checkpoint",
                kind="checkpoint",
                content=checkpoint.model_dump(mode="json"),
            ),
        ),
    )
    large_provider = TokenAccountingService(tokenizer=lambda text: len(text) // 4).account(
        context_window=128_000,
        output_reserve=8_000,
        compaction_output_reserve=4_000,
        checkpoint=(
            ContextItem(
                id="checkpoint",
                kind="checkpoint",
                content=checkpoint.model_dump(mode="json"),
            ),
        ),
    )
    assert checkpoint.model_dump(mode="json") == validate_checkpoint_payload(
        checkpoint.model_dump(mode="json"), envelope()
    ).model_dump(mode="json")
    assert small_provider.total_tokens != large_provider.total_tokens
    assert small_provider.source == "configured_tokenizer"
    assert large_provider.source == "configured_tokenizer"
    payload = semantic_payload()
    payload["secret"] = "nope"
    with pytest.raises(CheckpointValidationError, match="forbidden_checkpoint_field"):
        validate_checkpoint_payload(payload, envelope())


@pytest.mark.asyncio
async def test_service_installs_with_cas_and_reuses_completed_attempt(session):
    service = AgentContextCompactionService(ContextCompactionAttemptRepository(session))
    generation_calls = 0
    installs = 0

    async def generate(_prompt: str) -> CompactionGeneration:
        nonlocal generation_calls
        generation_calls += 1
        return CompactionGeneration(
            output=semantic_payload(), provider="ordinary-provider", model="ordinary-model"
        )

    async def install(snapshot, checkpoint, tail_ids):
        nonlocal installs
        installs += 1
        assert snapshot.continuation.state_version == 3
        assert snapshot.continuation.cancellation_epoch == 2
        assert checkpoint.user_intent == "implement safely"
        assert tail_ids
        return True

    first = await service.compact(envelope(), policy(), generate=generate, install=install)
    second = await service.compact(envelope(), policy(), generate=generate, install=install)
    assert first.status.value == "completed"
    assert second.reused is True
    assert generation_calls == 1
    assert installs == 1


@pytest.mark.asyncio
async def test_service_marks_stale_install_superseded(session):
    service = AgentContextCompactionService(ContextCompactionAttemptRepository(session))

    async def generate(_prompt: str) -> CompactionGeneration:
        return CompactionGeneration(output=semantic_payload(), provider="p", model="m")

    async def stale_install(_snapshot, _checkpoint, _tail_ids):
        return False

    result = await service.compact(
        envelope(total_body=2_000), policy(), generate=generate, install=stale_install
    )
    assert result.status.value == "superseded"
    assert result.checkpoint is None


@pytest.mark.asyncio
async def test_repository_agent_install_is_state_and_cancellation_cas(session):
    now = utc_now()
    task = TaskRecord(title="t", description="t", context_state={}, created_at=now, updated_at=now)
    session.add(task)
    await session.flush()
    run = RunRecord(task_id=task.id, status="executing", mode="web_agent", answer_mode="trusted", created_at=now, updated_at=now)
    session.add(run)
    await session.flush()
    execution = AgentExecutionRecord(
        run_id=run.id,
        task_id=task.id,
        execution_type="root",
        root_slot="root",
        request_id="root",
        status="running",
        phase="executing",
        state_version=3,
        cancellation_epoch=2,
    )
    session.add(execution)
    await session.flush()
    bound = envelope().model_copy(update={"owner_id": execution.id, "continuation": envelope().continuation.model_copy(update={"owner_id": execution.id})})
    checkpoint = validate_checkpoint_payload(semantic_payload(), bound)
    repository = ContextCompactionAttemptRepository(session)
    assert await repository.install_agent_checkpoint(bound, checkpoint, ("new",)) is True
    stale = await repository.install_agent_checkpoint(bound, checkpoint, ("new",))
    assert stale is False
