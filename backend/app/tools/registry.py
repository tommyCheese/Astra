import shutil
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
