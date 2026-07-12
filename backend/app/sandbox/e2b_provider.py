import asyncio
import inspect
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

from app.sandbox.runtime import SandboxError, SandboxHandle, SandboxProvider, SandboxRequest, SandboxResult


class E2BSandboxProvider(SandboxProvider):
    """E2B adapter. SDK objects remain confined to this module."""

    name = "e2b"

    def __init__(self, api_key: str, *, factory: Optional[Callable[..., Any]] = None):
        self.api_key = api_key
        self._factory = factory

    async def available(self) -> bool:
        return bool(self.api_key and (self._factory or self._load_factory()))

    def _load_factory(self):
        if self._factory:
            return self._factory
        try:
            from e2b import Sandbox
        except ImportError:
            return None
        return Sandbox.create

    async def _call(self, function, *args, **kwargs):
        if inspect.iscoroutinefunction(function):
            return await function(*args, **kwargs)
        return await asyncio.to_thread(function, *args, **kwargs)

    async def create(self, request: SandboxRequest) -> SandboxHandle:
        factory = self._load_factory()
        if factory is None:
            raise SandboxError("sandbox_unavailable", "E2B SDK is not installed")
        try:
            raw = await self._call(factory, template=request.template, api_key=self.api_key, timeout=request.wall_time_seconds + 10, secure=request.secure, allow_internet_access=request.allow_internet_access, metadata=request.metadata)
        except Exception as exc:
            raise SandboxError("sandbox_unavailable", "E2B Sandbox creation failed") from exc
        return SandboxHandle(id=str(getattr(raw, "sandbox_id", getattr(raw, "id", "unknown"))), provider=self.name, raw=raw)

    async def upload(self, handle: SandboxHandle, local_path: Path, remote_path: str) -> None:
        await self._call(handle.raw.files.write, remote_path, local_path.read_bytes())

    async def execute(self, handle: SandboxHandle, command: list[str], timeout: int, environment: dict[str, str]) -> SandboxResult:
        command_text = " ".join(shlex.quote(part) for part in command)
        result = await self._call(handle.raw.commands.run, command_text, timeout=timeout, envs=environment)
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        return SandboxResult(exit_code=int(getattr(result, "exit_code", 1)), stdout=str(stdout), stderr=str(stderr))

    async def download_dir(self, handle: SandboxHandle, remote_dir: str, local_dir: Path) -> list[Path]:
        local_dir.mkdir(parents=True, exist_ok=True)
        entries = await self._call(handle.raw.files.list, remote_dir, depth=None)
        downloaded = []
        for entry in entries:
            remote_path = str(getattr(entry, "path", PurePosixPath(remote_dir) / str(getattr(entry, "name", entry))))
            entry_type = getattr(getattr(entry, "type", None), "value", str(getattr(entry, "type", "")))
            if entry_type in {"dir", "directory"} or bool(getattr(entry, "is_dir", False)):
                continue
            relative = PurePosixPath(remote_path).relative_to(PurePosixPath(remote_dir))
            destination = local_dir / relative.as_posix()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(await self._call(handle.raw.files.read, remote_path, format="bytes"))
            downloaded.append(destination)
        return downloaded

    async def metrics(self, handle: SandboxHandle) -> dict[str, Any]:
        getter = getattr(handle.raw, "get_metrics", None)
        if getter is None:
            return {}
        metrics = await self._call(getter)
        if isinstance(metrics, dict):
            return metrics
        return {"samples": len(metrics)} if isinstance(metrics, list) else {}

    async def terminate(self, handle: SandboxHandle) -> None:
        killer = getattr(handle.raw, "kill", None)
        if killer:
            await self._call(killer)


def build_sandbox_provider(settings):
    if settings.sandbox_provider != "e2b":
        raise SandboxError("sandbox_unavailable", f"Unsupported sandbox provider: {settings.sandbox_provider}")
    return E2BSandboxProvider(settings.e2b_api_key)
