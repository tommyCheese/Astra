import pytest

from app.context_compaction.tool_outputs import (
    ToolOutputGovernanceService,
    ToolOutputStorageError,
)
from app.core.config import Settings
from app.schemas.context_compaction import ContextOwnerRole, ContextReference


@pytest.mark.asyncio
async def test_large_child_output_is_externalized_with_bounded_preview():
    service = ToolOutputGovernanceService(
        Settings(
            context_compaction_child_inline_bytes=1_024,
            context_compaction_child_inline_tokens=256,
        )
    )
    persisted = {}

    async def persist(content: bytes, checksum: str):
        persisted[checksum] = content
        return ContextReference(
            kind="artifact", ref="artifact:large-report", content_hash=checksum
        )

    result = await service.normalize(
        role=ContextOwnerRole.child_execution,
        tool_name="report.read",
        status="succeeded",
        output={"report": "x" * 20_000},
        key_fields={"rows": 42},
        persist=persist,
    )
    assert result.externalized is True
    assert result.output is None
    assert result.reference.ref == "artifact:large-report"
    assert len(result.preview.encode()) <= 4_096
    assert persisted[result.checksum]


@pytest.mark.asyncio
async def test_large_output_storage_failure_is_classified_and_never_silently_truncated():
    service = ToolOutputGovernanceService(
        Settings(context_compaction_root_inline_bytes=1_024, context_compaction_root_inline_tokens=256)
    )

    async def fail(_content: bytes, _checksum: str):
        return None

    with pytest.raises(ToolOutputStorageError, match="storage_failed"):
        await service.normalize(
            role=ContextOwnerRole.root_execution,
            tool_name="unsafe.large",
            status="succeeded",
            output="x" * 20_000,
            persist=fail,
        )


@pytest.mark.asyncio
async def test_small_output_stays_inline_and_errors_are_sanitized():
    service = ToolOutputGovernanceService(Settings())

    async def unused(_content: bytes, _checksum: str):
        raise AssertionError("small output must not be persisted")

    result = await service.normalize(
        role=ContextOwnerRole.root_execution,
        tool_name="small",
        status="failed",
        output={"message": "safe"},
        error={"category": "network", "code": "timeout", "secret": "drop-me"},
        persist=unused,
    )
    assert result.output == {"message": "safe"}
    assert result.error == {"category": "network", "code": "timeout", "retryable": False}
