import os
import pytest

from app.sandbox.e2b_provider import E2BSandboxProvider
from app.sandbox.runtime import SandboxRequest, SandboxSupervisor


@pytest.mark.skipif(os.getenv("ASTRA_RUN_E2B_INTEGRATION") != "1", reason="requires explicit E2B integration opt-in")
async def test_real_e2b_template_renders_static_chart(tmp_path):
    api_key, template = os.environ["E2B_API_KEY"], os.environ["E2B_TEMPLATE_ID"]
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text('{"version":"1","data":{"columns":["x","y"],"rows":[[1,2],[2,3]]},"chart_type":"line","x":"x","y":["y"],"title":"E2B smoke","backend":"matplotlib","outputs":["png"],"width":640,"height":480}')
    result = await SandboxSupervisor(E2BSandboxProvider(api_key)).run(SandboxRequest(template, ["/opt/astra/bin/render"], input_dir, output_dir, wall_time_seconds=60))
    assert result.exit_code == 0
    assert (output_dir / "chart.png").read_bytes().startswith(b"\x89PNG")
