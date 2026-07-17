import asyncio
import re
import sys
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ERROR_CATEGORIES = {
    "sandbox_unavailable",
    "runtime_image_missing",
    "sandbox_timeout",
    "sandbox_oom",
    "sandbox_policy_violation",
    "artifact_limit_exceeded",
    "invalid_artifact",
    "render_failed",
}
PROTECTED_WORKSPACE_PATHS = (".astra", ".git", ".codex")
TRANSITIONS = {
    "queued": {"preparing", "failed", "cancelled"},
    "preparing": {"running", "failed", "cancelled"},
    "running": {"collecting", "failed", "timed_out", "cancelled"},
    "collecting": {"succeeded", "failed", "cancelled"},
}


class SandboxError(RuntimeError):
    def __init__(self, category: str, message: str):
        self.category = category if category in ERROR_CATEGORIES else "render_failed"
        self.safe_message = message
        super().__init__(message)


def transition(current: str, target: str) -> str:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Illegal sandbox transition: {current} -> {target}")
    return target


def sanitize_log(value: str, limit: int = 4000) -> str:
    value = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", value)
    value = re.sub(
        r"(?i)(authorization)\s*[:=]\s*(?:bearer\s+)?\S+",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)(api[_-]?key|token)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    value = re.sub(r"/(Users|home|var|tmp)/[^\s]+", "[PATH]", value)
    return value[:limit]


@dataclass
class SandboxRequest:
    template: str
    command: list[str]
    input_dir: Path
    output_dir: Path
    wall_time_seconds: int = 30
    secure: bool = True
    allow_internet_access: bool = False
    record_stdout: bool = True
    collect_output_artifacts: bool = True
    environment: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    workspace_dir: Path | None = None
    workspace_mode: str = "none"


@dataclass
class SandboxHandle:
    id: str
    provider: str
    raw: Any = None


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    provider: str = ""
    template: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


class SandboxProvider(ABC):
    name = "unknown"

    @abstractmethod
    async def available(self) -> bool: ...
    @abstractmethod
    async def create(self, request: SandboxRequest) -> SandboxHandle: ...
    @abstractmethod
    async def upload(self, handle: SandboxHandle, local_path: Path, remote_path: str) -> None: ...
    @abstractmethod
    async def execute(
        self, handle: SandboxHandle, command: list[str], timeout: int, environment: dict[str, str]
    ) -> SandboxResult: ...
    @abstractmethod
    async def download_dir(
        self, handle: SandboxHandle, remote_dir: str, local_dir: Path
    ) -> list[Path]: ...
    @abstractmethod
    async def metrics(self, handle: SandboxHandle) -> dict[str, Any]: ...
    @abstractmethod
    async def terminate(self, handle: SandboxHandle) -> None: ...


class SandboxSupervisor:
    def __init__(self, provider: SandboxProvider):
        self.provider = provider

    async def run(self, request: SandboxRequest) -> SandboxResult:
        if not await self.provider.available():
            raise SandboxError("sandbox_unavailable", "Sandbox provider is unavailable")
        handle = None
        try:
            handle = await self.provider.create(request)
            for path in request.input_dir.rglob("*"):
                if path.is_file():
                    await self.provider.upload(
                        handle, path, f"/input/{path.relative_to(request.input_dir).as_posix()}"
                    )
            result = await asyncio.wait_for(
                self.provider.execute(
                    handle, request.command, request.wall_time_seconds, request.environment
                ),
                timeout=request.wall_time_seconds + 2,
            )
            if result.exit_code != 0:
                category = "sandbox_oom" if result.exit_code in {137, 143} else "render_failed"
                raise SandboxError(category, "Sandbox process failed")
            await self.provider.download_dir(handle, "/output", request.output_dir)
            result.metrics = await self.provider.metrics(handle)
            result.provider = self.provider.name
            result.template = request.template
            return result
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise SandboxError("sandbox_timeout", "Sandbox process timed out") from exc
        finally:
            if handle is not None:
                had_error = sys.exc_info()[0] is not None
                try:
                    await self.provider.terminate(handle)
                except Exception:
                    if not had_error:
                        raise


class SandboxJobService:
    def __init__(self, repo, supervisor: SandboxSupervisor, artifact_service, workspace_service=None):
        self.repo, self.supervisor, self.artifact_service = repo, supervisor, artifact_service
        self.workspace_service = workspace_service

    async def _record_terminal_state(
        self,
        job,
        *,
        run_id: str,
        started: float,
        status: str,
        category: str,
        message: str,
    ) -> None:
        if job.status in {"succeeded", "failed", "timed_out", "cancelled"}:
            return
        await self.repo.transition_sandbox_job(
            job.id,
            status,
            exit_reason=category,
            error={"category": category, "message": message},
        )
        await self.repo.add_event(
            run_id,
            "sandbox_job.metrics",
            {
                "sandbox_job_id": job.id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "artifact_bytes": 0,
                "provider": self.supervisor.provider.name,
                "status": status,
                "error_category": category,
            },
        )
        await self.repo.session.commit()

    async def execute(
        self,
        request: SandboxRequest,
        *,
        run_id: str,
        tool_call_id: str,
        runtime_profile: dict,
        resource_limits: dict,
    ):
        started = time.perf_counter()
        provider = self.supervisor.provider
        job = await self.repo.create_sandbox_job(
            run_id,
            tool_call_id=tool_call_id,
            executor=provider.name,
            runtime_profile=runtime_profile,
            resource_limits=resource_limits,
        )
        guard = (
            self.workspace_service.access_guard(request.workspace_dir, request.workspace_mode)
            if request.workspace_dir is not None and self.workspace_service is not None
            else _empty_guard()
        )
        try:
            async with guard:
                return await self._execute_locked(
                    request,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    runtime_profile=runtime_profile,
                    resource_limits=resource_limits,
                    job=job,
                    started=started,
                )
        except SandboxError as exc:
            status = "timed_out" if exc.category == "sandbox_timeout" else "failed"
            await self._record_terminal_state(
                job, run_id=run_id, started=started, status=status,
                category=exc.category, message=exc.safe_message,
            )
            raise
        except asyncio.CancelledError:
            await self._record_terminal_state(
                job, run_id=run_id, started=started, status="cancelled",
                category="cancelled", message="Sandbox job was cancelled",
            )
            raise
        except Exception as exc:
            await self._record_terminal_state(
                job, run_id=run_id, started=started, status="failed",
                category=getattr(exc, "category", "render_failed"),
                message=getattr(exc, "message", "Sandbox job failed"),
            )
            raise

    async def _execute_locked(
        self,
        request: SandboxRequest,
        *,
        run_id: str,
        tool_call_id: str,
        runtime_profile: dict,
        resource_limits: dict,
        job,
        started: float,
    ):
        try:
            before_manifest = None
            before_protected_paths = None
            if request.workspace_dir is not None and self.workspace_service is not None:
                before_manifest = self.workspace_service.scan(request.workspace_dir)
                before_protected_paths = self.workspace_service.protected_paths(
                    request.workspace_dir
                )
            await self.repo.transition_sandbox_job(job.id, "preparing")
            await self.repo.transition_sandbox_job(job.id, "running")
            result = await self.supervisor.run(request)
            await self.repo.transition_sandbox_job(
                job.id, "collecting", runtime_name=result.provider, image_digest=result.template
            )
            provenance = {
                "provider": result.provider,
                "template": result.template,
                "metrics": result.metrics,
                **runtime_profile,
            }
            refs = (
                await self.artifact_service.persist_output(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    sandbox_job_id=job.id,
                    output_dir=request.output_dir,
                    provenance=provenance,
                )
                if request.collect_output_artifacts
                else []
            )
            workspace_changes = []
            if (
                request.workspace_dir is not None
                and self.workspace_service is not None
                and before_manifest is not None
            ):
                workspace_changes = await self.workspace_service.capture_changes(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    workspace_dir=request.workspace_dir,
                    before=before_manifest,
                    before_protected_paths=before_protected_paths,
                )
            await self.repo.transition_sandbox_job(
                job.id,
                "succeeded",
                output_artifact_ids=[ref.id for ref in refs],
                stdout_summary=sanitize_log(result.stdout) if request.record_stdout else "",
                stderr_summary=sanitize_log(result.stderr),
            )
            await self.repo.add_event(
                run_id,
                "sandbox_job.metrics",
                {
                    "sandbox_job_id": job.id,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "artifact_bytes": sum(ref.size_bytes for ref in refs),
                    "backend": runtime_profile.get("backend"),
                    "provider": result.provider,
                    "status": "succeeded",
                    "workspace_change_count": len(workspace_changes),
                    **result.metrics,
                },
            )
            await self.repo.session.commit()
            return job, refs, result
        except Exception:
            raise


@asynccontextmanager
async def _empty_guard():
    yield
