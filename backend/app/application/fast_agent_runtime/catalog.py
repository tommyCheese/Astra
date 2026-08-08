from __future__ import annotations

import hashlib
import json

from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.tools.base import AstraToolRegistry


class FastToolCatalogBoundary:
    """Freeze the same immutable tool inventory without constructing trusted runtime state."""

    def __init__(self, registry: AstraToolRegistry) -> None:
        self._registry = registry
        self._plugins = PluginRuntimeState.from_registry(registry)

    async def freeze(self, session, run_id: str) -> None:
        catalog = [
            spec.model_dump(mode="json")
            for _, spec in sorted(self._registry.specs().items())
        ]
        digest = hashlib.sha256(
            json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        await PermissionRepository(session).freeze_tool_catalog(
            run_id,
            catalog=catalog,
            digest=digest,
            behavioral_catalog=self._plugins.snapshot_catalog(self._registry),
            behavioral_digest=self._plugins.behavioral_digest(self._registry),
            display_digest=self._plugins.display_digest(self._registry),
        )

    async def ensure_root_identity(self, session, run) -> None:
        await PermissionRepository(session).get_or_create_identity(
            identity_type="main_agent",
            principal="astra.fast-agent",
            task_id=run.task_id,
            run_id=run.id,
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
