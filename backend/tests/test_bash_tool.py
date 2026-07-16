import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.sandbox.runtime import SandboxError, SandboxResult
from app.tools.base import ToolExecutionContext, ToolExecutionError
from app.tools.bash import BashExecuteTool
from app.tools.registry import build_tool_registry


class BashSandboxService:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"exit_code": 0, "stdout": "ok", "stderr": ""}
        self.error = error
        self.request = None
        self.kwargs = None

    async def execute(self, request, **kwargs):
        self.request = request
        self.kwargs = kwargs
        assert json.loads((request.input_dir / "request.json").read_text())["command"]
        assert (request.input_dir / "runner.py").is_file()
        if self.error:
            raise self.error
        return SimpleNamespace(id="job-bash"), [], SandboxResult(0, stdout=json.dumps(self.payload))


def context(service, workspace_path):
    return ToolExecutionContext(
        run_id="run-1",
        tool_call_id="call-1",
        step_id=None,
        trace_id="trace-1",
        artifact_service=None,
        sandbox_service=service,
        task_id="task-1",
        workspace_path=workspace_path,
    )


def test_bash_execute_is_disabled_by_default_and_registered_explicitly():
    disabled = build_tool_registry(Settings(sandbox_skip_availability_check=True))
    enabled = build_tool_registry(
        Settings(sandbox_skip_availability_check=True, tool_bash_execute_enabled=True)
    )

    assert "bash_execute" not in disabled.specs()
    spec = enabled.specs()["bash_execute"]
    assert spec.execution_backend == "sandbox.remote"
    assert {
        "command_execute",
        "process_execute",
        "temporary_compute",
        "workspace_read",
        "workspace_write",
        "workspace_delete",
    } <= set(spec.permissions)
    assert spec.risk == "high"


async def test_bash_execute_uses_offline_sandbox_and_returns_nonzero_result(tmp_path):
    service = BashSandboxService(
        {"exit_code": 7, "stdout": "partial", "stderr": "token=secret /tmp/private"}
    )
    tool = BashExecuteTool(Settings())

    output = await tool.run(
        {"command": "printf partial; exit 7"},
        context=context(service, tmp_path),
    )

    assert output["data"]["exit_code"] == 7
    assert output["data"]["stdout"] == "partial"
    assert "secret" not in output["data"]["stderr"]
    assert "private" not in output["data"]["stderr"]
    assert service.request.allow_internet_access is False
    assert service.request.command == ["python", "/input/runner.py"]
    assert service.request.workspace_dir == tmp_path
    assert service.request.workspace_mode == "read_write"
    assert service.kwargs["runtime_profile"]["workspace"] == "read_write"
    assert service.kwargs["resource_limits"]["network"] == "none"


async def test_bash_execute_propagates_sandbox_timeout(tmp_path):
    service = BashSandboxService(error=SandboxError("sandbox_timeout", "timed out"))

    with pytest.raises(ToolExecutionError) as error:
        await BashExecuteTool(Settings()).run(
            {"command": "sleep 60", "timeout_seconds": 1},
            context=context(service, tmp_path),
        )

    assert error.value.category == "sandbox_timeout"
