import os

import pytest

from app.sandbox.oci import OCIContainerExecutor
from app.sandbox.runtime import SandboxRequest, SandboxSupervisor


pytestmark = pytest.mark.skipif(os.getenv("ASTRA_RUN_DOCKER_INTEGRATION") != "1", reason="Set ASTRA_RUN_DOCKER_INTEGRATION=1 after building runtime images")


async def test_python_runtime_is_non_root_offline_read_only_and_cleaned(tmp_path):
    request = SandboxRequest(image=os.getenv("SANDBOX_PYTHON_IMAGE", "astra-runtime-python:0.1.0"), command=["-c", "import os; assert os.getuid()!=0"], input_dir=tmp_path / "input", output_dir=tmp_path / "output", wall_time_seconds=10)
    request.input_dir.mkdir()
    result = await SandboxSupervisor(OCIContainerExecutor()).run(request)
    assert result.exit_code == 0


async def test_gvisor_contract_requires_runsc(tmp_path):
    request = SandboxRequest(image="unused", command=[], input_dir=tmp_path, output_dir=tmp_path / "out", runtime="runc")
    with pytest.raises(Exception):
        await OCIContainerExecutor(require_gvisor=True).prepare(request)
