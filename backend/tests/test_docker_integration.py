import os
import shutil
import pytest
from app.sandbox.docker_provider import DockerSandboxProvider
from app.sandbox.runtime import SandboxRequest, SandboxSupervisor

@pytest.mark.skipif(os.getenv("ASTRA_RUN_DOCKER_INTEGRATION") != "1" or shutil.which("docker") is None, reason="requires local Docker integration opt-in")
@pytest.mark.parametrize(("backend", "chart_type"), [("matplotlib", "line"), ("seaborn", "regression"), ("echarts", "line")])
async def test_local_docker_runtime_renders_offline(tmp_path, backend, chart_type):
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text(f'{{"version":"1","data":{{"columns":["x","y"],"rows":[[1,2],[2,3]]}},"chart_type":"{chart_type}","x":"x","y":["y"],"title":"Docker smoke","backend":"{backend}","outputs":["png"],"width":640,"height":480}}')
    image = os.getenv("SANDBOX_RUNTIME_IMAGE", "astra-data-viz:0.1.0")
    result = await SandboxSupervisor(DockerSandboxProvider()).run(SandboxRequest(image, ["/opt/astra/bin/render"], input_dir, output_dir, wall_time_seconds=60))
    assert result.exit_code == 0
    assert (output_dir / "chart.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.skipif(os.getenv("ASTRA_RUN_DOCKER_INTEGRATION") != "1" or shutil.which("docker") is None, reason="requires local Docker integration opt-in")
async def test_local_docker_echarts_html_is_self_contained(tmp_path):
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text('{"version":"1","data":{"columns":["x","y"],"rows":[[1,2],[2,3]]},"chart_type":"line","x":"x","y":["y"],"title":"安全交互图","backend":"echarts","outputs":["html"],"width":640,"height":480}')
    await SandboxSupervisor(DockerSandboxProvider()).run(SandboxRequest("astra-data-viz:0.1.0", ["/opt/astra/bin/render"], input_dir, output_dir, wall_time_seconds=60))
    html = (output_dir / "chart.html").read_text()
    assert "default-src 'none'" in html
    assert "nonce-astra-chart" in html
    assert 'src="http' not in html and 'href="http' not in html
