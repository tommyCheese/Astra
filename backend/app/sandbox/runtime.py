import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


ERROR_CATEGORIES = {"sandbox_unavailable", "runtime_image_missing", "sandbox_timeout", "sandbox_oom", "sandbox_policy_violation", "artifact_limit_exceeded", "invalid_artifact", "render_failed"}
TRANSITIONS = {"queued": {"preparing", "cancelled"}, "preparing": {"running", "failed", "cancelled"}, "running": {"collecting", "failed", "timed_out", "cancelled"}, "collecting": {"succeeded", "failed"}}


class SandboxError(RuntimeError):
    def __init__(self, category: str, message: str):
        if category not in ERROR_CATEGORIES:
            category = "render_failed"
        self.category, self.safe_message = category, message
        super().__init__(message)


def transition(current: str, target: str) -> str:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Illegal sandbox transition: {current} -> {target}")
    return target


def sanitize_log(value: str, limit: int = 4000) -> str:
    value = re.sub(r"(?i)(api[_-]?key|token|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    value = re.sub(r"/(Users|home|var|tmp)/[^\s]+", "[PATH]", value)
    return value[:limit]


@dataclass
class SandboxRequest:
    image: str
    command: list[str]
    input_dir: Path
    output_dir: Path
    wall_time_seconds: int = 30
    memory_mb: int = 512
    cpus: float = 1.0
    pids: int = 64
    runtime: Optional[str] = None
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    runtime: str = ""
    image: str = ""


class SandboxExecutor(ABC):
    @abstractmethod
    async def available(self) -> bool: ...
    @abstractmethod
    async def prepare(self, request: SandboxRequest) -> Any: ...
    @abstractmethod
    async def start(self, prepared: Any) -> Any: ...
    @abstractmethod
    async def wait(self, handle: Any, timeout: int) -> SandboxResult: ...
    @abstractmethod
    async def terminate(self, handle: Any) -> None: ...
    @abstractmethod
    async def collect(self, handle: Any) -> SandboxResult: ...
    @abstractmethod
    async def cleanup(self, handle: Any) -> None: ...


class SandboxSupervisor:
    def __init__(self, executor: SandboxExecutor):
        self.executor = executor

    async def run(self, request: SandboxRequest) -> SandboxResult:
        if not await self.executor.available():
            raise SandboxError("sandbox_unavailable", "Sandbox executor is unavailable")
        handle = None
        try:
            prepared = await self.executor.prepare(request)
            handle = await self.executor.start(prepared)
            result = await self.executor.wait(handle, request.wall_time_seconds)
            if result.exit_code != 0:
                category = "sandbox_oom" if result.exit_code in {137, 143} else "render_failed"
                raise SandboxError(category, "Sandbox process failed")
            return await self.executor.collect(handle)
        except asyncio.TimeoutError as exc:
            if handle is not None:
                await self.executor.terminate(handle)
            raise SandboxError("sandbox_timeout", "Sandbox process timed out") from exc
        finally:
            if handle is not None:
                await self.executor.cleanup(handle)


class SandboxJobService:
    def __init__(self, repo, supervisor: SandboxSupervisor, artifact_service):
        self.repo, self.supervisor, self.artifact_service = repo, supervisor, artifact_service

    async def execute(self, request: SandboxRequest, *, run_id: str, tool_call_id: str, runtime_profile: dict, resource_limits: dict):
        started = time.perf_counter()
        job = await self.repo.create_sandbox_job(run_id, tool_call_id=tool_call_id, executor=type(self.supervisor.executor).__name__, runtime_profile=runtime_profile, resource_limits=resource_limits)
        await self.repo.transition_sandbox_job(job.id, "preparing")
        await self.repo.transition_sandbox_job(job.id, "running")
        try:
            result = await self.supervisor.run(request)
            await self.repo.transition_sandbox_job(job.id, "collecting", runtime_name=result.runtime, image_digest=result.image)
            refs = await self.artifact_service.persist_output(run_id=run_id, tool_call_id=tool_call_id, sandbox_job_id=job.id, output_dir=request.output_dir, provenance={"runtime": result.runtime, "image": result.image, "trace_id": runtime_profile.get("trace_id")})
            await self.repo.transition_sandbox_job(job.id, "succeeded", output_artifact_ids=[ref.id for ref in refs], stdout_summary=sanitize_log(result.stdout), stderr_summary=sanitize_log(result.stderr))
            await self.repo.add_event(run_id, "sandbox_job.metrics", {"sandbox_job_id": job.id, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "artifact_bytes": sum(ref.size_bytes for ref in refs), "backend": runtime_profile.get("backend"), "status": "succeeded"})
            await self.repo.session.commit()
            return job, refs
        except SandboxError as exc:
            status = "timed_out" if exc.category == "sandbox_timeout" else "failed"
            await self.repo.transition_sandbox_job(job.id, status, exit_reason=exc.category, error={"category": exc.category, "message": exc.safe_message})
            await self.repo.add_event(run_id, "sandbox_job.metrics", {"sandbox_job_id": job.id, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "artifact_bytes": 0, "backend": runtime_profile.get("backend"), "status": status, "error_category": exc.category})
            await self.repo.session.commit()
            raise
