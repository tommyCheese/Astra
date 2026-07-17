import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from app.sandbox.runtime import (
    SandboxError,
    SandboxHandle,
    SandboxProvider,
    SandboxRequest,
    SandboxResult,
)


class DockerSandboxProvider(SandboxProvider):
    """Local OCI sandbox provider using Docker Engine's CLI."""

    name = "docker"

    def __init__(
        self, binary: str = "docker", *, memory_mb: int = 1024, cpus: float = 1.0, pids: int = 128
    ):
        self.binary, self.memory_mb, self.cpus, self.pids = binary, memory_mb, cpus, pids

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _run(
        self, *args: str, timeout: int = 30, check: bool = True, input_data: bytes | None = None
    ):
        process = None
        try:
            stdin = (
                asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL
            )
            process = await asyncio.create_subprocess_exec(
                self.binary,
                *args,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", "")},
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_data), timeout=timeout
            )
        except FileNotFoundError as exc:
            raise SandboxError("sandbox_unavailable", "Docker Engine is unavailable") from exc
        except (asyncio.CancelledError, asyncio.TimeoutError):
            if process is not None:
                with contextlib.suppress(ProcessLookupError):
                    await self._stop_process(process)
            raise
        if check and process.returncode != 0:
            message = stderr.decode(errors="replace")
            category = "runtime_image_missing" if "image" in message.lower() else "render_failed"
            raise SandboxError(category, "Docker operation failed")
        return process.returncode, stdout, stderr

    async def available(self) -> bool:
        if shutil.which(self.binary) is None:
            return False
        try:
            code, _, _ = await self._run(
                "info", "--format", "{{json .ServerVersion}}", timeout=3, check=False
            )
            return code == 0
        except (SandboxError, asyncio.TimeoutError):
            return False

    async def create(self, request: SandboxRequest) -> SandboxHandle:
        network = "none" if not request.allow_internet_access else "bridge"
        create_args = [
            "create",
            "--rm",
            "--network",
            network,
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids),
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            str(self.cpus),
            "--tmpfs",
            "/input:rw,noexec,nosuid,nodev,mode=1777",
            "--tmpfs",
            "/output:rw,noexec,nosuid,nodev,mode=1777",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,mode=1777",
            "--env",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "--env",
            "HOME=/tmp/astra-home",
            "--env",
            "LANG=C.UTF-8",
            "--env",
            "LC_ALL=C.UTF-8",
            "--env",
            "GIT_CONFIG_NOSYSTEM=1",
            "--env",
            "GIT_CONFIG_GLOBAL=/dev/null",
            "--env",
            "BASH_ENV=/dev/null",
            "--env",
            "ENV=/dev/null",
            "--env",
            "GIT_CONFIG_COUNT=1",
            "--env",
            "GIT_CONFIG_KEY_0=core.hooksPath",
            "--env",
            "GIT_CONFIG_VALUE_0=/dev/null",
            "--env",
            "npm_config_ignore_scripts=true",
            "--env",
            "YARN_IGNORE_SCRIPTS=1",
            "--env",
            "PIP_NO_CONFIG_FILE=1",
            "--env",
            "PYTHONNOUSERSITE=1",
            "--env",
            "PYTHONSAFEPATH=1",
        ]
        if request.workspace_mode not in {"none", "read_only", "read_write"}:
            raise SandboxError("sandbox_policy_violation", "Invalid Workspace mount mode")
        if request.workspace_mode != "none":
            if request.workspace_dir is None:
                raise SandboxError(
                    "sandbox_policy_violation", "Workspace mount path is required"
                )
            workspace_dir = request.workspace_dir.resolve(strict=True)
            if workspace_dir.is_symlink() or not workspace_dir.is_dir():
                raise SandboxError(
                    "sandbox_policy_violation", "Workspace mount path is invalid"
                )
            mount = f"type=bind,src={workspace_dir},dst=/workspace"
            if request.workspace_mode == "read_only":
                mount += ",readonly"
            create_args.extend(["--mount", mount])
            if request.workspace_mode == "read_write":
                for relative in request.protected_workspace_paths:
                    protected = (workspace_dir / relative).resolve(strict=True)
                    if not protected.is_relative_to(workspace_dir):
                        raise SandboxError(
                            "sandbox_policy_violation",
                            "Protected Workspace path escaped its root",
                        )
                    create_args.extend([
                        "--mount",
                        f"type=bind,src={protected},dst=/workspace/{relative},readonly",
                    ])
        else:
            create_args.extend(["--tmpfs", "/workspace:rw,nosuid,nodev,mode=1777"])
        create_args.extend(
            [
            request.template,
            "sleep",
            "infinity",
            ]
        )
        _, stdout, _ = await self._run(*create_args)
        container_id = stdout.decode().strip()
        if not container_id:
            raise SandboxError("render_failed", "Docker did not return a container ID")
        try:
            await self._run("start", container_id)
        except BaseException:
            await self._run("rm", "--force", container_id, timeout=10, check=False)
            raise
        return SandboxHandle(container_id, self.name)

    async def upload(self, handle: SandboxHandle, local_path: Path, remote_path: str) -> None:
        await self._run(
            "exec",
            "-i",
            handle.id,
            "sh",
            "-c",
            'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"',
            "sh",
            remote_path,
            input_data=local_path.read_bytes(),
        )

    async def execute(
        self, handle: SandboxHandle, command: list[str], timeout: int, environment: dict[str, str]
    ) -> SandboxResult:
        safe_environment = {
            **{
                key: value
                for key, value in environment.items()
                if key
                not in {
                    "PATH", "HOME", "LANG", "LC_ALL", "BASH_ENV", "ENV",
                    "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "RUBYOPT",
                    "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
                }
            },
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp/astra-home",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "BASH_ENV": "/dev/null",
            "ENV": "/dev/null",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "/dev/null",
            "npm_config_ignore_scripts": "true",
            "YARN_IGNORE_SCRIPTS": "1",
            "PIP_NO_CONFIG_FILE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
        }
        args = ["exec", "--workdir", "/workspace" if command else "/tmp"]
        for key, value in safe_environment.items():
            args.extend(["--env", f"{key}={value}"])
        args.extend([handle.id, *command])
        code, stdout, stderr = await self._run(*args, timeout=timeout, check=False)
        return SandboxResult(code, stdout.decode(errors="replace"), stderr.decode(errors="replace"))

    async def download_dir(
        self, handle: SandboxHandle, remote_dir: str, local_dir: Path
    ) -> list[Path]:
        local_dir.mkdir(parents=True, exist_ok=True)
        _, stdout, _ = await self._run(
            "exec", handle.id, "find", remote_dir, "-type", "f", "-print"
        )
        downloaded = []
        for remote_path in stdout.decode().splitlines():
            relative = Path(remote_path).relative_to(remote_dir)
            if ".." in relative.parts:
                raise SandboxError(
                    "sandbox_policy_violation", "Sandbox output path escaped its directory"
                )
            destination = local_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            _, content, _ = await self._run("exec", handle.id, "cat", remote_path)
            destination.write_bytes(content)
            downloaded.append(destination)
        return downloaded

    async def metrics(self, handle: SandboxHandle) -> dict[str, Any]:
        code, stdout, _ = await self._run(
            "stats", "--no-stream", "--format", "{{json .}}", handle.id, check=False
        )
        if code != 0:
            return {}
        try:
            return json.loads(stdout.decode().splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            return {}

    async def terminate(self, handle: SandboxHandle) -> None:
        await self._run("rm", "--force", handle.id, timeout=10, check=False)


def build_sandbox_provider(settings):
    if settings.sandbox_provider != "docker":
        raise SandboxError(
            "sandbox_unavailable", f"Unsupported sandbox provider: {settings.sandbox_provider}"
        )
    return DockerSandboxProvider(
        settings.docker_binary,
        memory_mb=settings.sandbox_memory_mb,
        cpus=settings.sandbox_cpus,
        pids=settings.sandbox_pids,
    )
