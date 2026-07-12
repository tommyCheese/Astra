import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from app.artifacts import ArtifactCollector, LocalArtifactStore, prune_store
from app.sandbox.runtime import SandboxError, SandboxExecutor, SandboxRequest, SandboxResult, SandboxSupervisor, sanitize_log, transition
from app.tools.base import ToolExecutionError


def test_local_artifact_store_rejects_path_escape(tmp_path):
    store = LocalArtifactStore(str(tmp_path / "store"))
    with pytest.raises(ToolExecutionError):
        store.resolve("../../secret")


def test_artifact_collector_rejects_symlink_and_fake_png(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "fake.png").write_bytes(b"not png")
    with pytest.raises(ToolExecutionError) as exc_info:
        ArtifactCollector(output, max_files=2, max_bytes=100).collect()
    assert exc_info.value.category == "invalid_artifact"

    (output / "fake.png").unlink()
    (output / "link.svg").symlink_to(tmp_path / "outside.svg")
    with pytest.raises(ToolExecutionError) as symlink_error:
        ArtifactCollector(output, max_files=2, max_bytes=100).collect()
    assert symlink_error.value.category == "sandbox_policy_violation"


def test_artifact_retention_removes_content_but_preserves_record(tmp_path):
    store = LocalArtifactStore(str(tmp_path / "store"))
    source = tmp_path / "chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nmock")
    key = store.put(source, ".png")
    record = type("Record", (), {"created_at": datetime.now(timezone.utc) - timedelta(days=31), "storage_key": key, "security_status": "verified"})()
    assert prune_store(store, [record], 30) == 1
    assert record.security_status == "expired"
    assert not store.resolve(key).exists()


@pytest.mark.parametrize("name,body", [("evil.svg", b'<svg onload="alert(1)"></svg>'), ("evil.html", b"<html><script>alert(1)</script></html>")])
def test_artifact_collector_rejects_active_svg_and_html_without_csp(tmp_path, name, body):
    output = tmp_path / "output"
    output.mkdir()
    (output / name).write_bytes(body)
    with pytest.raises(ToolExecutionError) as exc_info:
        ArtifactCollector(output, max_files=2, max_bytes=1000).collect()
    assert exc_info.value.category == "invalid_artifact"


def test_sandbox_state_machine_rejects_illegal_transition():
    assert transition("queued", "preparing") == "preparing"
    assert transition("queued", "cancelled") == "cancelled"
    with pytest.raises(ValueError):
        transition("queued", "succeeded")


def test_sandbox_log_is_redacted_and_truncated():
    output = sanitize_log("api_key=secret /Users/example/private/file", limit=40)
    assert "secret" not in output
    assert "/Users" not in output


class TimeoutExecutor(SandboxExecutor):
    cleaned = False
    terminated = False

    async def available(self): return True
    async def prepare(self, request): return request
    async def start(self, prepared): return object()
    async def wait(self, handle, timeout): raise asyncio.TimeoutError
    async def terminate(self, handle): self.terminated = True
    async def collect(self, handle): return SandboxResult(0)
    async def cleanup(self, handle): self.cleaned = True


async def test_supervisor_terminates_and_cleans_timeout(tmp_path):
    executor = TimeoutExecutor()
    request = SandboxRequest("image@sha256:test", ["render"], tmp_path, tmp_path / "out")
    with pytest.raises(SandboxError) as exc_info:
        await SandboxSupervisor(executor).run(request)
    assert exc_info.value.category == "sandbox_timeout"
    assert executor.terminated and executor.cleaned


class CrashExecutor(TimeoutExecutor):
    async def wait(self, handle, timeout):
        raise RuntimeError("worker crashed")


async def test_supervisor_always_cleans_worker_crash(tmp_path):
    executor = CrashExecutor()
    request = SandboxRequest("image", ["render"], tmp_path, tmp_path / "out")
    with pytest.raises(RuntimeError):
        await SandboxSupervisor(executor).run(request)
    assert executor.cleaned


async def test_executor_collect_and_cleanup_are_repeatable(tmp_path):
    executor = TimeoutExecutor()
    handle = object()
    assert (await executor.collect(handle)).exit_code == 0
    assert (await executor.collect(handle)).exit_code == 0
    await executor.cleanup(handle)
    await executor.cleanup(handle)
    assert executor.cleaned
