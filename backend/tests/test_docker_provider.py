import asyncio
import contextlib
import json

import pytest

from app.infrastructure.sandbox.docker_provider import DockerSandboxProvider
from app.infrastructure.sandbox.runtime import SandboxError, SandboxRequest, SandboxSupervisor


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


async def test_docker_provider_uses_bridge_only_for_explicit_internet_access(tmp_path):
    provider = RecordingDockerProvider()
    await SandboxSupervisor(provider).run(
        SandboxRequest(
            "astra-data-viz:0.1.0",
            ["/opt/astra/bin/render"],
            tmp_path,
            tmp_path / "out",
            allow_internet_access=True,
        )
    )

    create = next(call for call in provider.calls if call[0] == "create")
    assert create[create.index("--network") : create.index("--network") + 2] == (
        "--network",
        "bridge",
    )
    assert "--mount" not in create and "--volume" not in create and "-v" not in create
    assert not any(part.startswith("/Users/") for part in create)


async def test_docker_provider_mounts_managed_workspace_with_requested_access(tmp_path):
    provider = RecordingDockerProvider()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    await provider.create(
        SandboxRequest(
            "image",
            ["render"],
            tmp_path,
            tmp_path / "out",
            workspace_dir=workspace,
            workspace_mode="read_only",
        )
    )

    create = next(call for call in provider.calls if call[0] == "create")
    mount = create[create.index("--mount") + 1]
    assert f"src={workspace.resolve()}" in mount
    assert "dst=/workspace" in mount
    assert mount.endswith(",readonly")


async def test_docker_provider_overlays_protected_workspace_paths_read_only(tmp_path):
    provider = RecordingDockerProvider()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for relative in (".astra", ".git", ".codex"):
        (workspace / relative).mkdir()
    (workspace / "nested").mkdir()
    (workspace / "nested" / ".git").mkdir()

    await provider.create(
        SandboxRequest(
            "image",
            ["render"],
            tmp_path,
            tmp_path / "out",
            workspace_dir=workspace,
            workspace_mode="read_write",
        )
    )

    create = next(call for call in provider.calls if call[0] == "create")
    mounts = [create[index + 1] for index, value in enumerate(create) if value == "--mount"]
    for relative in (".astra", ".git", ".codex"):
        assert any(f"dst=/workspace/{relative}" in mount and mount.endswith(",readonly") for mount in mounts)
    assert any("dst=/workspace/nested/.git" in mount and mount.endswith(",readonly") for mount in mounts)


async def test_docker_provider_sanitizes_startup_hooks_and_language_autoloading():
    provider = RecordingDockerProvider()
    await provider.execute(
        type("Handle", (), {"id": "container-id"})(),
        ["sh", "-c", "true"],
        10,
        {
            "PATH": "/workspace/bin",
            "BASH_ENV": "/workspace/.bashrc",
            "PYTHONPATH": "/workspace",
            "SAFE_INPUT": "ok",
        },
    )
    execute = provider.calls[-1]
    joined = " ".join(execute)
    assert "PATH=/usr/local/bin:/usr/bin:/bin" in joined
    assert "BASH_ENV=/dev/null" in joined
    assert "PYTHONPATH=" not in joined
    assert "GIT_CONFIG_VALUE_0=/dev/null" in joined
    assert "npm_config_ignore_scripts=true" in joined
    assert "SAFE_INPUT=ok" in joined


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
