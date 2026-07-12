import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.tools.chart import ChartRequest, select_backend
from app.tools.registry import build_tool_registry
from app.repositories.runs import RunRepository
from app.runner.agent_loop import AgentLoop
from app.runner.model_client import MockModelClient
from app.sandbox.runtime import SandboxHandle, SandboxProvider, SandboxResult
from app.schemas.agent import AgentDecision, FinalAnswer


def request(**updates):
    payload = {"data": {"columns": ["x", "y"], "rows": [[1, 2], [2, 3]]}, "chart_type": "line", "x": "x", "y": ["y"]}
    payload.update(updates)
    return ChartRequest.model_validate(payload)


def test_chart_request_forbids_source_code_and_invalid_encoding():
    with pytest.raises(ValidationError):
        ChartRequest.model_validate({"data": {"columns": ["x"], "rows": [[1]]}, "chart_type": "line", "x": "missing", "y": ["x"], "python": "open('/etc/passwd')"})


def test_auto_backend_selection_is_deterministic():
    assert select_backend(request())[0] == "matplotlib"
    assert select_backend(request(chart_type="regression"))[0] == "seaborn"
    assert select_backend(request(outputs=["html"]))[0] == "echarts"


def test_chart_request_rejects_oversized_dataset_and_value():
    with pytest.raises(ValidationError):
        request(data={"columns": ["x", "y"], "rows": [[index, index] for index in range(10001)]})
    with pytest.raises(ValidationError):
        request(data={"columns": ["x", "y"], "rows": [[1, "x" * 10001]]})


def test_chart_tool_is_registered_only_when_sandbox_enabled():
    assert "chart.render" not in build_tool_registry(Settings(sandbox_enabled=False)).specs()
    assert "chart.render" in build_tool_registry(Settings(sandbox_enabled=True, sandbox_skip_availability_check=True)).specs()


class ChartClient(MockModelClient):
    def __init__(self): self.calls = 0
    async def decide_with_answer(self, goal, context, *, on_delta=None):
        self.calls += 1
        if self.calls == 1:
            return AgentDecision(decision_type="call_tool", reasoning_summary="绘图", tool_name="chart.render", tool_input=request().model_dump(mode="json")), None
        return AgentDecision(decision_type="finalize", reasoning_summary="完成"), FinalAnswer(summary="图表已生成")


class ChartProvider(SandboxProvider):
    name = "mock"
    def __init__(self): self.request = None
    async def available(self): return True
    async def create(self, request):
        self.request = request
        return SandboxHandle("chart", self.name)
    async def upload(self, handle, local_path, remote_path): return None
    async def execute(self, handle, command, timeout, environment): return SandboxResult(0)
    async def download_dir(self, handle, remote_dir, local_dir):
        local_dir.mkdir(exist_ok=True)
        output = local_dir / "chart.png"
        output.write_bytes(b"\x89PNG\r\n\x1a\nmock")
        return [output]
    async def metrics(self, handle): return {"cpu_count": 1}
    async def terminate(self, handle): return None


async def test_chart_only_agent_run_creates_sandbox_artifact_without_web_evidence(session, tmp_path):
    settings = Settings(model_provider="mock", sandbox_enabled=True, sandbox_skip_availability_check=True, artifact_store_path=str(tmp_path / "store"))
    repo = RunRepository(session)
    run = await repo.create_task_run("生成折线图", settings.model_policy)
    registry = build_tool_registry(settings)
    output = await AgentLoop(settings, model_client=ChartClient(), tool_registry=registry, sandbox_provider=ChartProvider()).run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)
    assert output["status"] == "completed"
    assert any(item.sandbox_job_id for item in loaded.artifacts)
    assert any(call.tool_name == "chart.render" for call in loaded.tool_calls)


def test_chart_tool_is_hidden_when_provider_is_not_configured():
    assert "chart.render" not in build_tool_registry(Settings(sandbox_enabled=True)).specs()
