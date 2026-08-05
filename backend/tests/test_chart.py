import pytest
from pydantic import ValidationError

from app.application.agent_runtime.policies.reasoning import PolicyCompiler
from app.application.agent_runtime.services.loop import AgentLoop
from app.common.core.config import Settings
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.run_result import FinalAnswer
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.sandbox.runtime import SandboxHandle, SandboxProvider, SandboxResult
from app.infrastructure.tools.chart import ChartRenderTool, ChartRequest, select_backend
from app.infrastructure.tools.registry import build_tool_registry
from app.infrastructure.tools.router import ToolRouter


def request(**updates):
    payload = {
        "data": {"columns": ["x", "y"], "rows": [[1, 2], [2, 3]]},
        "chart_type": "line",
        "x": "x",
        "y": ["y"],
    }
    payload.update(updates)
    return ChartRequest.model_validate(payload)


def test_chart_request_forbids_source_code_and_invalid_encoding():
    with pytest.raises(ValidationError):
        ChartRequest.model_validate(
            {
                "data": {"columns": ["x"], "rows": [[1]]},
                "chart_type": "line",
                "x": "missing",
                "y": ["x"],
                "python": "open('/etc/passwd')",
            }
        )


def test_auto_backend_selection_is_deterministic():
    assert select_backend(request())[0] == "matplotlib"
    assert select_backend(request(chart_type="regression"))[0] == "seaborn"
    assert select_backend(request(outputs=["html"]))[0] == "echarts"


def test_chart_request_rejects_oversized_dataset_and_value():
    with pytest.raises(ValidationError):
        request(data={"columns": ["x", "y"], "rows": [[index, index] for index in range(10001)]})
    with pytest.raises(ValidationError):
        request(data={"columns": ["x", "y"], "rows": [[1, "x" * 10001]]})


@pytest.mark.parametrize(
    "data",
    [
        {"category": ["A", "B"], "value": [10, 20]},
        [{"category": "A", "value": 10}, {"category": "B", "value": 20}],
    ],
)
def test_chart_request_normalizes_common_inline_data_shapes(data):
    parsed = request(data=data, chart_type="bar", x="category", y=["value"])

    assert parsed.data.columns == ["category", "value"]
    assert parsed.data.rows == [["A", 10], ["B", 20]]


@pytest.mark.parametrize(
    "data",
    [
        {"category": ["A", "B"], "value": [10]},
        [{"category": "A", "value": 10}, {"category": "B"}],
    ],
)
def test_chart_request_rejects_ambiguous_inline_data_shapes(data):
    with pytest.raises(ValidationError):
        request(data=data, chart_type="bar", x="category", y=["value"])


def test_chart_request_accepts_workspace_csv_as_data_source():
    parsed = ChartRequest.model_validate(
        {
            "input_workspace_path": "test.csv",
            "chart_type": "line",
            "x": "x",
            "y": ["y"],
        }
    )

    assert parsed.input_workspace_path == "test.csv"


def test_chart_tool_reads_bounded_workspace_csv(tmp_path):
    (tmp_path / "test.csv").write_text("x,y\n1,2\n2,3\n", encoding="utf-8")

    data = ChartRenderTool._load_workspace_csv("test.csv", tmp_path)

    assert data.columns == ["x", "y"]
    assert data.rows == [[1, 2], [2, 3]]


def test_chart_tool_is_registered_only_when_sandbox_enabled():
    assert "chart.render" not in build_tool_registry(Settings(sandbox_enabled=False)).specs()
    assert (
        "chart.render"
        in build_tool_registry(
            Settings(sandbox_enabled=True, sandbox_skip_availability_check=True)
        ).specs()
    )


def test_chart_tool_switch_overrides_available_sandbox():
    settings = Settings(
        tool_chart_render_enabled=False,
        sandbox_enabled=True,
        sandbox_skip_availability_check=True,
    )

    assert "chart.render" not in build_tool_registry(settings).specs()


def test_chart_manifest_availability_does_not_fabricate_invalid_probe_input():
    registry = build_tool_registry(
        Settings(sandbox_enabled=True, sandbox_skip_availability_check=True)
    )

    status = ToolRouter(registry, available_backends={"sandbox.remote"}).availability(
        "chart.render"
    )

    assert status.available is True


class ChartClient(MockModelClient):
    def __init__(self):
        self.calls = 0

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.calls += 1
        if self.calls == 1:
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary="绘图",
                tool_name="chart.render",
                tool_input=request().model_dump(mode="json"),
            ), None
        return AgentDecision(decision_type="finalize", reasoning_summary="完成"), FinalAnswer(
            summary="图表已生成"
        )


class ChartProvider(SandboxProvider):
    name = "mock"

    def __init__(self):
        self.request = None

    async def available(self):
        return True

    async def create(self, request):
        self.request = request
        return SandboxHandle("chart", self.name)

    async def upload(self, handle, local_path, remote_path):
        return None

    async def execute(self, handle, command, timeout, environment):
        return SandboxResult(0)

    async def download_dir(self, handle, remote_dir, local_dir):
        local_dir.mkdir(exist_ok=True)
        output = local_dir / "chart.png"
        output.write_bytes(b"\x89PNG\r\n\x1a\nmock")
        return [output]

    async def metrics(self, handle):
        return {"cpu_count": 1}

    async def terminate(self, handle):
        return None


async def test_chart_only_agent_run_creates_sandbox_artifact_without_web_evidence(
    session, tmp_path
):
    settings = Settings(
        model_provider="mock",
        sandbox_enabled=True,
        sandbox_skip_availability_check=True,
        artifact_store_path=str(tmp_path / "store"),
    )
    repo = RunUnitOfWork(session)
    policy = PolicyCompiler().compile(
        RequestedReasoningPolicy(execution_mode="auto_approval")
    )
    run = await repo.create_task_run(
        "生成折线图",
        settings.model_policy,
        reasoning_policy=policy.model_dump(mode="json"),
    )
    registry = build_tool_registry(settings)
    output = await AgentLoop(
        settings,
        model_client=ChartClient(),
        tool_registry=registry,
        sandbox_provider=ChartProvider(),
    ).run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)
    assert output["status"] == "completed"
    assert any(item.sandbox_job_id for item in loaded.artifacts)
    assert any(call.tool_name == "chart.render" for call in loaded.tool_calls)


def test_chart_tool_is_hidden_when_provider_is_not_configured(monkeypatch):
    monkeypatch.setattr("app.infrastructure.tools.registry.shutil.which", lambda _: None)
    assert "chart.render" not in build_tool_registry(Settings(sandbox_enabled=True)).specs()
