import shutil
import subprocess

from app.core.config import Settings
from app.plugins.builtin import builtin_contributions
from app.plugins.catalog import PluginCatalogBuilder
from app.plugins.discovery import BuiltinDiscoverySource
from app.tools.base import ToolRegistry


def build_tool_registry(settings: Settings) -> ToolRegistry:
    if not settings.sandbox_enabled or not sandbox_available(settings):
        return ToolRegistry()
    catalog = PluginCatalogBuilder(
        [BuiltinDiscoverySource(builtin_contributions(settings))],
        allowed_providers=settings.trusted_tool_provider_map,
    ).build_static()
    registry = catalog.tool_registry()
    if any(spec.execution_backend != "sandbox.remote" for spec in registry.specs().values()):
        raise RuntimeError("Application tools must use the sandbox.remote execution backend")
    return registry


def sandbox_available(settings: Settings) -> bool:
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
