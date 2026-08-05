"""Public contracts implemented by local and future remote subagent executors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.subagents.budget import HierarchicalBudgetManager
from app.application.subagents.context import SubagentContinuationService
from app.application.subagents.governance import FrozenChildCatalog
from app.common.schemas.permissions import PermissionPolicySet
from app.common.schemas.subagents import (
    DelegatedExecutionContext,
    DelegationContract,
    SubagentContextManifest,
    SubagentResult,
)

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class AgentExecutorRuntime:
    session: AsyncSession
    execution_context: DelegatedExecutionContext
    frozen_catalog: FrozenChildCatalog
    permission_policies: PermissionPolicySet | None = None
    worker_id: str = "local-subagent"
    artifact_service: Any = None
    sandbox_service: Any = None
    on_event: EventCallback | None = None
    continuation_service: SubagentContinuationService | None = None
    budget_manager: HierarchicalBudgetManager | None = None


class AgentExecutor(ABC):
    """Stable adapter boundary for local and future remote child runtimes."""

    @abstractmethod
    async def execute(
        self,
        *,
        contract: DelegationContract,
        context_manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
        checkpoint: dict[str, Any] | None = None,
    ) -> SubagentResult:
        raise NotImplementedError
