from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.application.context_compaction.accounting import TokenAccountingService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.context_compaction import CompactionContextReference, ContextOwnerRole


class ToolOutputStorageError(RuntimeError):
    pass


class NormalizedToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    tool_name: str
    key_fields: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    preview: str | None = None
    checksum: str | None = None
    reference: CompactionContextReference | None = None
    externalized: bool = False
    original_bytes: int = Field(default=0, ge=0)
    original_tokens: int = Field(default=0, ge=0)
    error: dict[str, Any] | None = None


PersistToolOutput = Callable[[bytes, str], Awaitable[CompactionContextReference | None]]


class ToolOutputGovernanceService:
    def __init__(self, settings: AstraRuntimeSettings, *, accounting: TokenAccountingService | None = None):
        self.settings = settings
        self.accounting = accounting or TokenAccountingService()

    async def normalize(
        self,
        *,
        role: ContextOwnerRole,
        tool_name: str,
        status: str,
        output: Any,
        key_fields: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        persist: PersistToolOutput,
    ) -> NormalizedToolObservation:
        serialized = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
        token_count, _, _ = self.accounting.count_text(serialized.decode(errors="replace"))
        child = role == ContextOwnerRole.child_execution
        byte_limit = (
            self.settings.context_compaction_child_inline_bytes if child else self.settings.context_compaction_root_inline_bytes
        )
        token_limit = (
            self.settings.context_compaction_child_inline_tokens
            if child
            else self.settings.context_compaction_root_inline_tokens
        )
        if len(serialized) <= byte_limit and token_count <= token_limit:
            return NormalizedToolObservation(
                status=status,
                tool_name=tool_name,
                key_fields=key_fields or {},
                output=output,
                original_bytes=len(serialized),
                original_tokens=token_count,
                error=_classify_error(error),
            )
        checksum = hashlib.sha256(serialized).hexdigest()
        reference = await persist(serialized, checksum)
        if reference is None or not reference.accessible:
            raise ToolOutputStorageError("oversized_tool_output_storage_failed")
        preview_bytes = serialized[: max(256, min(byte_limit // 4, 4_096))]
        return NormalizedToolObservation(
            status=status,
            tool_name=tool_name,
            key_fields=key_fields or {},
            preview=preview_bytes.decode(errors="replace"),
            checksum=checksum,
            reference=reference,
            externalized=True,
            original_bytes=len(serialized),
            original_tokens=token_count,
            error=_classify_error(error),
        )


def _classify_error(error: dict[str, Any] | None) -> dict[str, Any] | None:
    if not error:
        return None
    return {
        "category": str(error.get("category") or error.get("type") or "tool_error")[:120],
        "code": str(error.get("code") or "unknown")[:160],
        "retryable": bool(error.get("retryable", False)),
    }
