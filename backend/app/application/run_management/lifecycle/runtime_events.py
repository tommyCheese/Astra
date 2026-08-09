"""Public Run event publication for canonical Runtime events."""

from dataclasses import dataclass
from typing import ClassVar

from app.application.agent_runtime.contracts import PortIdentity, RuntimeEvent
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry


@dataclass
class RunRuntimeEventPort:
    repository: RunUnitOfWork
    run_id: str
    run: RunRecord
    registry: AstraToolRegistry
    identity: ClassVar[PortIdentity] = PortIdentity(
        name="run-events",
        version=1,
        digest="5" * 64,
    )

    async def publish(self, event: RuntimeEvent) -> None:
        if event.name == "loop.started":
            await self.repository.update_run_status(
                self.run_id,
                "executing",
                loaded_run=self.run,
            )
            await self.repository.add_event(
                self.run_id,
                "fast.started",
                {"runtime": "fast-v1", "version": 1},
            )
        elif event.name == "decision.selected":
            await self._ensure_identity()
            kind = str(event.payload["kind"])
            await self.repository.add_event(
                self.run_id,
                "fast.action.decided",
                {
                    "turn_index": event.payload.get("turn", 0),
                    "action": "call_tool" if kind == "tool" else kind,
                    "tool_name": event.payload.get("name"),
                },
            )
        else:
            return
        await self.repository.session.commit()

    async def _ensure_identity(self) -> None:
        await PermissionRepository(self.repository.session).get_or_create_identity(
            identity_type="main_agent",
            principal="astra.agent",
            task_id=self.run.task_id,
            run_id=self.run.id,
            trust_level="platform",
            attributes={
                "runtime": "fast-v1",
                "permission_scope": {
                    "actions": ["*"],
                    "resources": ["*"],
                    "effect_kinds": ["*"],
                    "tools": ["*"],
                },
            },
        )
