import subprocess

from app.core.config import Settings
from app.tools.base import ToolRegistry
from app.tools.chart import ChartRenderTool
from app.tools.web import build_web_registry


def build_tool_registry(settings: Settings) -> ToolRegistry:
    registries = [build_web_registry(settings)]
    if settings.sandbox_enabled and sandbox_available(settings):
        chart = ToolRegistry().extend([ChartRenderTool(settings)])
        registries.append(chart)
    return ToolRegistry.compose(*registries)


def sandbox_available(settings: Settings) -> bool:
    if settings.sandbox_skip_availability_check:
        return True
    try:
        result = subprocess.run([settings.sandbox_executor, "info"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2, check=False)
        if result.returncode != 0:
            return False
        if settings.sandbox_require_gvisor:
            runtime = subprocess.run([settings.sandbox_executor, "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True, timeout=2, check=False)
            return runtime.returncode == 0 and "runsc" in runtime.stdout
        return True
    except (OSError, subprocess.SubprocessError):
        return False
