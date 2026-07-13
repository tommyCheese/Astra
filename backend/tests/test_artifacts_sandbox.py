import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.artifacts import ArtifactCollector, LocalArtifactStore, prune_store
from app.sandbox.runtime import (
    SandboxError,
    SandboxHandle,
    SandboxJobService,
    SandboxProvider,
    SandboxRequest,
    SandboxResult,
    SandboxSupervisor,
    sanitize_log,
    transition,
)
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
    record = type(
        "Record",
        (),
        {
            "created_at": datetime.now(timezone.utc) - timedelta(days=31),
            "storage_key": key,
            "security_status": "verified",
        },
    )()
    assert prune_store(store, [record], 30) == 1
    assert record.security_status == "expired"
    assert not store.resolve(key).exists()


@pytest.mark.parametrize(
    "name,body",
    [
        ("evil.svg", b'<svg onload="alert(1)"></svg>'),
        ("evil.html", b"<html><script>alert(1)</script></html>"),
    ],
)
def test_artifact_collector_rejects_active_svg_and_html_without_csp(tmp_path, name, body):
    output = tmp_path / "output"
    output.mkdir()
    (output / name).write_bytes(body)
    with pytest.raises(ToolExecutionError) as exc_info:
        ArtifactCollector(output, max_files=2, max_bytes=1000).collect()
    assert exc_info.value.category == "invalid_artifact"


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("invalid.json", b"{not-json}"),
        (
            "late-csp.html",
            b"<script>doSomething()</script><meta http-equiv='content-security-policy' "
            b"content=\"default-src 'none'\">",
        ),
    ],
)
def test_artifact_collector_rejects_malformed_json_and_ineffective_csp(tmp_path, name, body):
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
    output = sanitize_log(
        "\x1b[91mapi_key=secret Authorization: Bearer another-secret "
        "/Users/example/private/file\x1b[0m",
        limit=80,
    )
    assert "secret" not in output
    assert "/Users" not in output
    assert "\x1b" not in output


class TimeoutProvider(SandboxProvider):
    name = "mock"
    terminated = False

    async def available(self):
        return True

    async def create(self, request):
        return SandboxHandle("test", self.name)

    async def upload(self, handle, local_path, remote_path):
        return None

    async def execute(self, handle, command, timeout, environment):
        raise asyncio.TimeoutError

    async def download_dir(self, handle, remote_dir, local_dir):
        return []

    async def metrics(self, handle):
        return {}

    async def terminate(self, handle):
        self.terminated = True


async def test_supervisor_terminates_and_cleans_timeout(tmp_path):
    provider = TimeoutProvider()
    request = SandboxRequest("template-test", ["render"], tmp_path, tmp_path / "out")
    with pytest.raises(SandboxError) as exc_info:
        await SandboxSupervisor(provider).run(request)
    assert exc_info.value.category == "sandbox_timeout"
    assert provider.terminated


class CrashProvider(TimeoutProvider):
    async def execute(self, handle, command, timeout, environment):
        raise RuntimeError("worker crashed")


async def test_supervisor_always_cleans_worker_crash(tmp_path):
    provider = CrashProvider()
    request = SandboxRequest("template", ["render"], tmp_path, tmp_path / "out")
    with pytest.raises(RuntimeError):
        await SandboxSupervisor(provider).run(request)
    assert provider.terminated


class RecordingSession:
    async def commit(self):
        return None


class RecordingJobRepository:
    def __init__(self):
        self.job = SimpleNamespace(id="job-1", status="queued")
        self.session = RecordingSession()

    async def create_sandbox_job(self, *args, **kwargs):
        return self.job

    async def transition_sandbox_job(self, job_id, status, **updates):
        self.job.status = transition(self.job.status, status)
        for key, value in updates.items():
            setattr(self.job, key, value)
        return self.job

    async def add_event(self, *args, **kwargs):
        return None


class SuccessfulSupervisor:
    provider = SimpleNamespace(name="mock")

    async def run(self, request):
        return SandboxResult(0, provider="mock", template=request.template)


class InvalidArtifactService:
    async def persist_output(self, **kwargs):
        raise ToolExecutionError("invalid_artifact", "Artifact validation failed")


async def test_sandbox_job_records_artifact_validation_failure(tmp_path):
    repo = RecordingJobRepository()
    service = SandboxJobService(repo, SuccessfulSupervisor(), InvalidArtifactService())
    request = SandboxRequest("template", ["render"], tmp_path, tmp_path / "out")

    with pytest.raises(ToolExecutionError):
        await service.execute(
            request,
            run_id="run-1",
            tool_call_id="call-1",
            runtime_profile={},
            resource_limits={},
        )

    assert repo.job.status == "failed"
    assert repo.job.exit_reason == "invalid_artifact"


class BlockingSupervisor(SuccessfulSupervisor):
    async def run(self, request):
        await asyncio.Event().wait()


async def test_sandbox_job_records_cancellation(tmp_path):
    repo = RecordingJobRepository()
    service = SandboxJobService(repo, BlockingSupervisor(), InvalidArtifactService())
    request = SandboxRequest("template", ["render"], tmp_path, tmp_path / "out")
    task = asyncio.create_task(
        service.execute(
            request,
            run_id="run-1",
            tool_call_id="call-1",
            runtime_profile={},
            resource_limits={},
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert repo.job.status == "cancelled"
