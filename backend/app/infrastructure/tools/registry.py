import shutil
import subprocess
from collections.abc import Iterable

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.plugins.builtin import builtin_contributions
from app.infrastructure.plugins.catalog import PluginCatalog, PluginCatalogBuilder
from app.infrastructure.plugins.contracts import PluginRuntimeBackendContribution
from app.infrastructure.plugins.diagnostics import plugin_diagnostics
from app.infrastructure.plugins.discovery import BuiltinDiscoverySource, PluginDiscoverySource
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.runtime import build_runtime_tool_registry


def build_tool_registry(
    settings: AstraRuntimeSettings,
    *,
    extra_sources: Iterable[PluginDiscoverySource] = (),
    host_runtime_backends: Iterable[PluginRuntimeBackendContribution] = (),
) -> AstraToolRegistry:
    runtime_registry = build_runtime_tool_registry()
    catalog = build_plugin_catalog(
        settings,
        extra_sources=extra_sources,
        host_runtime_backends=host_runtime_backends,
    )
    if catalog is None:
        return runtime_registry
    application_registry = catalog.tool_registry()
    if any(
        catalog.tool_bindings[name].executor_id == "in_process"
        and spec.execution_backend != "sandbox.remote"
        for name, spec in application_registry.specs().items()
    ):
        raise RuntimeError("Application tools must use the sandbox.remote execution backend")
    return AstraToolRegistry.compose(runtime_registry, application_registry)


def build_plugin_catalog(
    settings: AstraRuntimeSettings,
    *,
    extra_sources: Iterable[PluginDiscoverySource] = (),
    host_runtime_backends: Iterable[PluginRuntimeBackendContribution] = (),
) -> PluginCatalog | None:
    if not settings.sandbox_enabled or not sandbox_available(settings):
        return None
    configured_sources = (
        tuple(extra_sources) if settings.tool_plugin_rollout_mode == "configured" else ()
    )
    catalog = PluginCatalogBuilder(
        [BuiltinDiscoverySource(builtin_contributions(settings)), *configured_sources],
        allowed_providers=settings.trusted_tool_provider_map,
        host_runtime_backends=(
            tuple(host_runtime_backends)
            if settings.tool_plugin_rollout_mode == "configured"
            else ()
        ),
    ).build_static()
    plugin_diagnostics.record("catalog_assembled", state=settings.tool_plugin_rollout_mode)
    return catalog


def build_plugin_inventory(settings: AstraRuntimeSettings) -> PluginCatalog:
    """Assemble every built-in provider for settings UI without executing a backend probe."""
    inventory_settings = settings.model_copy(
        update={
            "sandbox_enabled": True,
            "sandbox_skip_availability_check": True,
            "tool_web_search_enabled": True,
            "tool_web_fetch_enabled": True,
            "tool_chart_render_enabled": True,
            "tool_bash_execute_enabled": True,
            "tool_provider_states": {},
        },
        deep=True,
    )
    return PluginCatalogBuilder(
        [BuiltinDiscoverySource(builtin_contributions(inventory_settings))],
        allowed_providers=settings.trusted_tool_provider_map,
    ).build_static()


def sandbox_available(settings: AstraRuntimeSettings) -> bool:
    if settings.sandbox_skip_availability_check:
        return True
    if settings.sandbox_provider != "docker" or shutil.which(settings.docker_binary) is None:
        return False
    try:
        return (
            subprocess.run(
                [settings.docker_binary, "info"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False
