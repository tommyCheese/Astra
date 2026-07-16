import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.sandbox.docker_provider import DockerSandboxProvider
from app.sandbox.runtime import SandboxRequest, SandboxSupervisor
from app.tools.bash import RUNNER
from app.tools.chart import ChartRenderTool

DOCKER_INTEGRATION = pytest.mark.skipif(
    os.getenv("ASTRA_RUN_DOCKER_INTEGRATION") != "1" or shutil.which("docker") is None,
    reason="requires local Docker integration opt-in",
)


@DOCKER_INTEGRATION
@pytest.mark.parametrize(
    ("backend", "chart_type"),
    [("matplotlib", "line"), ("seaborn", "regression"), ("echarts", "line")],
)
async def test_local_docker_runtime_renders_offline(tmp_path, backend, chart_type):
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text(
        f'{{"version":"1","data":{{"columns":["x","y"],"rows":[[1,2],[2,3]]}},"chart_type":"{chart_type}","x":"x","y":["y"],"title":"Docker smoke","backend":"{backend}","outputs":["png"],"width":640,"height":480}}'
    )
    image = os.getenv("SANDBOX_RUNTIME_IMAGE", "astra-data-viz:0.1.0")
    result = await SandboxSupervisor(DockerSandboxProvider()).run(
        SandboxRequest(
            image, ["/opt/astra/bin/render"], input_dir, output_dir, wall_time_seconds=60
        )
    )
    assert result.exit_code == 0
    assert (output_dir / "chart.png").read_bytes().startswith(b"\x89PNG")


@DOCKER_INTEGRATION
async def test_local_docker_echarts_html_is_self_contained(tmp_path):
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text(
        '{"version":"1","data":{"columns":["x","y"],"rows":[[1,2],[2,3]]},"chart_type":"line","x":"x","y":["y"],"title":"安全交互图","backend":"echarts","outputs":["html"],"width":640,"height":480}'
    )
    await SandboxSupervisor(DockerSandboxProvider()).run(
        SandboxRequest(
            "astra-data-viz:0.1.0",
            ["/opt/astra/bin/render"],
            input_dir,
            output_dir,
            wall_time_seconds=60,
        )
    )
    html = (output_dir / "chart.html").read_text()
    assert "default-src 'none'" in html
    assert "nonce-astra-chart" in html
    assert 'src="http' not in html and 'href="http' not in html


@DOCKER_INTEGRATION
async def test_bash_workspace_csv_is_available_to_later_chart_render(tmp_path):
    image = os.getenv("SANDBOX_RUNTIME_IMAGE", "astra-data-viz:0.1.0")
    workspace = Path(__file__).parents[1] / ".test-workspaces" / uuid.uuid4().hex
    bash_input = tmp_path / "bash-input"
    bash_output = tmp_path / "bash-output"
    workspace.mkdir(parents=True, mode=0o777)
    workspace.chmod(0o777)
    bash_input.mkdir()
    (bash_input / "request.json").write_text(
        json.dumps(
            {
                "command": "printf 'x,y\\n1,2\\n2,3\\n' > test.csv",
                "output_max_chars": 1000,
            }
        ),
        encoding="utf-8",
    )
    (bash_input / "runner.py").write_text(RUNNER, encoding="utf-8")
    try:
        await SandboxSupervisor(DockerSandboxProvider()).run(
            SandboxRequest(
                image,
                ["python", "/input/runner.py"],
                bash_input,
                bash_output,
                workspace_dir=workspace,
                workspace_mode="read_write",
            )
        )

        data = ChartRenderTool._load_workspace_csv("test.csv", workspace)
        chart_input = tmp_path / "chart-input"
        chart_output = tmp_path / "chart-output"
        chart_input.mkdir()
        (chart_input / "request.json").write_text(
            json.dumps(
                {
                    "version": "1",
                    "data": data.model_dump(mode="json"),
                    "chart_type": "line",
                    "x": "x",
                    "y": ["y"],
                    "title": "Shared Workspace",
                    "backend": "matplotlib",
                    "outputs": ["png"],
                    "width": 640,
                    "height": 480,
                }
            ),
            encoding="utf-8",
        )
        await SandboxSupervisor(DockerSandboxProvider()).run(
            SandboxRequest(
                image,
                ["/opt/astra/bin/render"],
                chart_input,
                chart_output,
                workspace_dir=workspace,
                workspace_mode="read_only",
                wall_time_seconds=60,
            )
        )

        assert (workspace / "test.csv").read_text(encoding="utf-8").startswith("x,y")
        assert (chart_output / "chart.png").read_bytes().startswith(b"\x89PNG")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@DOCKER_INTEGRATION
@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("web_fetch", {"url": "https://example.com/"}),
        ("web_search", {"query": "OpenAI", "num_results": 2}),
    ],
)
async def test_local_docker_web_runtime_returns_protocol_envelope(tmp_path, tool_name, tool_input):
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text(
        json.dumps({"version": "1", "tool": tool_name, "input": tool_input})
    )
    (input_dir / "runtime-config.json").write_text(
        json.dumps(
            {
                "ALLOW_NETWORK_READ": "true",
                "WEB_SEARCH_PROVIDER": "auto",
                "CRAWLER_ALLOW_PROXY_FAKE_IP": "true",
            }
        )
    )
    result = await SandboxSupervisor(DockerSandboxProvider()).run(
        SandboxRequest(
            os.getenv("SANDBOX_WEB_RUNTIME_IMAGE", "astra-web-tools:0.1.0"),
            ["/opt/astra/bin/tool-runtime"],
            input_dir,
            output_dir,
            wall_time_seconds=30,
            allow_internet_access=True,
            environment={"TZ": "UTC", "PYTHONHASHSEED": "0"},
        )
    )
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    if tool_name == "web_fetch":
        assert envelope["output"]["status_code"] == 200
    else:
        assert envelope["output"]["candidate_count"] > 0
        assert envelope["output"]["provider_mode"] == "auto"
        assert envelope["output"]["degraded"] is True
        assert envelope["output"]["provider_attempts"]
