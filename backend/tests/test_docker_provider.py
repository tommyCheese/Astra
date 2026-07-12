import asyncio
import contextlib
import json

import pytest

from app.sandbox.docker_provider import DockerSandboxProvider
from app.sandbox.runtime import SandboxError, SandboxRequest, SandboxSupervisor


class RecordingDockerProvider(DockerSandboxProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def _run(self, *args, timeout=30, check=True, input_data=None):
        self.calls.append(args)
        if args[0] == "create":
            return 0, b"container-id\n", b""
        if args[0] == "exec":
            if "find" in args:
                return 0, b"", b""
            return 0, b"ok", b""
        if args[0] == "stats":
            return 0, json.dumps({"CPUPerc": "1%"}).encode(), b""
        return 0, b"", b""

    async def available(self):
        return True


async def test_docker_provider_hardens_and_always_removes(tmp_path):
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text("{}")
    provider = RecordingDockerProvider()
    await SandboxSupervisor(provider).run(
        SandboxRequest("astra-data-viz:0.1.0", ["/opt/astra/bin/render"], input_dir, output_dir)
    )
    create = next(call for call in provider.calls if call[0] == "create")
    assert create[create.index("--network") : create.index("--network") + 2] == (
        "--network",
        "none",
    )
    assert "--read-only" in create and "--cap-drop" in create and "no-new-privileges" in create
    assert any(call[:2] == ("rm", "--force") for call in provider.calls)


class TimeoutDockerProvider(RecordingDockerProvider):
    async def execute(self, handle, command, timeout, environment):
        await asyncio.sleep(timeout + 1)


async def test_docker_provider_removes_container_on_timeout(tmp_path):
    provider = TimeoutDockerProvider()
    with contextlib.suppress(Exception):
        await SandboxSupervisor(provider).run(
            SandboxRequest("image", ["render"], tmp_path, tmp_path / "out", wall_time_seconds=0)
        )
    assert any(call[:2] == ("rm", "--force") for call in provider.calls)


class StartFailureDockerProvider(RecordingDockerProvider):
    async def _run(self, *args, timeout=30, check=True, input_data=None):
        self.calls.append(args)
        if args[0] == "create":
            return 0, b"partially-created\n", b""
        if args[0] == "start":
            raise SandboxError("render_failed", "start failed")
        return 0, b"", b""


async def test_docker_provider_removes_partially_created_container(tmp_path):
    provider = StartFailureDockerProvider()

    with pytest.raises(SandboxError):
        await provider.create(SandboxRequest("image", ["render"], tmp_path, tmp_path / "out"))

    assert ("rm", "--force", "partially-created") in provider.calls
