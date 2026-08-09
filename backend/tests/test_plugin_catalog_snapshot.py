import hashlib
import json

import pytest

from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.permissions.effects import DefaultEffectAnalyzer
from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.plugins.catalog import PluginCatalogBuilder
from app.infrastructure.plugins.contracts import (
    PluginApplicabilityBinding,
    PluginComponentContribution,
    PluginComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    PluginToolContribution,
)
from app.infrastructure.plugins.discovery import BuiltinDiscoverySource
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraTool, AstraToolSpec, ToolResultEnvelope


class SnapshotTool(AstraTool):
    def __init__(self, *, description="display A", version="1"):
        self.spec = AstraToolSpec(
            name="snapshot.read",
            version=version,
            description=description,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission="network_read",
            side_effect_level="read_only",
            provider_id="snapshot.provider",
            provider_digest="sha256:snapshot",
            trust_level="managed",
        )

    async def run(self, tool_input, *, context=None):
        return ToolResultEnvelope(data={}).model_dump(mode="json")


def catalog(*, description="display A", version="1", component_digest="sha256:effect"):
    descriptor = PluginDescriptor(
        plugin_id="snapshot.plugin",
        provider_id="snapshot.provider",
        version="1",
        digest="sha256:snapshot",
        source="builtin",
        trust_level="managed",
        configuration_revision="config-7",
    )
    tool = SnapshotTool(description=description, version=version)
    contribution = PluginContribution(
        descriptor=descriptor,
        tools=(PluginToolContribution(tool=tool, executor_id="in_process"),),
        effect_analyzers=(
            PluginComponentContribution(
                identity=PluginComponentIdentity(
                    component_id="snapshot.effect",
                    provider_id=descriptor.provider_id,
                    version="1",
                    digest=component_digest,
                ),
                applicability=PluginApplicabilityBinding(tool_names=(tool.spec.name,)),
                factory=DefaultEffectAnalyzer,
            ),
        ),
    )
    return PluginCatalogBuilder(
        [BuiltinDiscoverySource((contribution,))],
        allowed_providers={"snapshot.provider": {"sha256:snapshot"}},
    ).build_static()


def legacy_catalog(registry):
    payload = [spec.model_dump(mode="json") for _, spec in sorted(registry.specs().items())]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload, digest


async def run_record(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    return await RunUnitOfWork(session).create_task_run(
        "snapshot test",
        settings.model_policy,
        reasoning_policy={},
    )


async def test_snapshot_freezes_resolved_plugin_component_and_configuration_identities(session):
    run = await run_record(session)
    frozen = catalog()
    registry = frozen.tool_registry()
    runtime = PluginRuntimeState(frozen)
    display, legacy_digest = legacy_catalog(registry)
    behavioral = runtime.snapshot_catalog(registry)

    snapshot = await PermissionRepository(session).freeze_tool_catalog(
        run.id,
        catalog=display,
        digest=legacy_digest,
        behavioral_catalog=behavioral,
        behavioral_digest=runtime.behavioral_digest(registry),
        display_digest=runtime.display_digest(registry),
    )

    entry = snapshot.behavioral_catalog[0]
    assert snapshot.schema_version == 2
    assert entry["provider"]["configuration_revision"] == "config-7"
    assert entry["executor"]["id"] == "in_process"
    assert entry["components"]["analyzers"][0]["identity"]["digest"] == "sha256:effect"
    assert entry["components"]["analyzers"][0]["applicability"]["tool_names"] == ["snapshot.read"]


async def test_snapshot_allows_display_only_change_but_rejects_behavioral_drift(session):
    run = await run_record(session)
    first = catalog(description="display A")
    first_registry = first.tool_registry()
    first_runtime = PluginRuntimeState(first)
    first_display, first_legacy_digest = legacy_catalog(first_registry)
    repository = PermissionRepository(session)
    await repository.freeze_tool_catalog(
        run.id,
        catalog=first_display,
        digest=first_legacy_digest,
        behavioral_catalog=first_runtime.snapshot_catalog(first_registry),
        behavioral_digest=first_runtime.behavioral_digest(first_registry),
        display_digest=first_runtime.display_digest(first_registry),
    )

    renamed = catalog(description="display B")
    renamed_registry = renamed.tool_registry()
    renamed_runtime = PluginRuntimeState(renamed)
    renamed_display, renamed_legacy_digest = legacy_catalog(renamed_registry)
    resumed = await repository.freeze_tool_catalog(
        run.id,
        catalog=renamed_display,
        digest=renamed_legacy_digest,
        behavioral_catalog=renamed_runtime.snapshot_catalog(renamed_registry),
        behavioral_digest=renamed_runtime.behavioral_digest(renamed_registry),
        display_digest=renamed_runtime.display_digest(renamed_registry),
    )
    assert resumed.display_digest == first_runtime.display_digest(first_registry)

    drifted = catalog(component_digest="sha256:changed")
    drifted_registry = drifted.tool_registry()
    drifted_runtime = PluginRuntimeState(drifted)
    with pytest.raises(ValueError, match="behavioral identity changed"):
        await repository.freeze_tool_catalog(
            run.id,
            catalog=renamed_display,
            digest=renamed_legacy_digest,
            behavioral_catalog=drifted_runtime.snapshot_catalog(drifted_registry),
            behavioral_digest=drifted_runtime.behavioral_digest(drifted_registry),
            display_digest=drifted_runtime.display_digest(drifted_registry),
        )


async def test_legacy_snapshot_is_readable_and_upgraded_when_behavior_is_equivalent(session):
    run = await run_record(session)
    frozen = catalog()
    registry = frozen.tool_registry()
    runtime = PluginRuntimeState(frozen)
    display, digest = legacy_catalog(registry)
    repository = PermissionRepository(session)

    legacy = await repository.freeze_tool_catalog(run.id, catalog=display, digest=digest)
    assert legacy.schema_version == 1

    upgraded = await repository.freeze_tool_catalog(
        run.id,
        catalog=display,
        digest=digest,
        behavioral_catalog=runtime.snapshot_catalog(registry),
        behavioral_digest=runtime.behavioral_digest(registry),
        display_digest=runtime.display_digest(registry),
    )
    assert upgraded.id == legacy.id
    assert upgraded.schema_version == 2
    assert upgraded.behavioral_digest
