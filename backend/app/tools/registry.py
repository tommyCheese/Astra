import shutil
import subprocess

from app.core.config import Settings
from app.tools.base import ToolRegistry
from app.tools.bash import BashExecuteTool
from app.tools.chart import ChartRenderTool
from app.tools.sandboxed import SandboxedWebTool
from app.tools.web import build_web_registry


def build_tool_registry(settings: Settings) -> ToolRegistry:
    if not settings.sandbox_enabled or not sandbox_available(settings):
        return ToolRegistry()

    native_web = build_web_registry(settings)
    web = ToolRegistry().extend(
        SandboxedWebTool(native_web.get(name), settings) for name in native_web.specs()
    )
    registries = [web]
    if settings.tool_chart_render_enabled:
        chart = ToolRegistry().extend([ChartRenderTool(settings)])
        registries.append(chart)
    if settings.tool_bash_execute_enabled:
        registries.append(ToolRegistry().extend([BashExecuteTool(settings)]))
    registry = ToolRegistry.compose(*registries)
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
