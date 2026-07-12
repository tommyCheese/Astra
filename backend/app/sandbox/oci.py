import asyncio
import shutil
import uuid

from app.sandbox.runtime import SandboxError, SandboxExecutor, SandboxRequest, SandboxResult


class OCIContainerExecutor(SandboxExecutor):
    def __init__(self, binary: str = "docker", *, require_gvisor: bool = False):
        self.binary = binary
        self.require_gvisor = require_gvisor

    async def available(self) -> bool:
        if shutil.which(self.binary) is None:
            return False
        proc = await asyncio.create_subprocess_exec(self.binary, "info", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        return await proc.wait() == 0

    async def prepare(self, request: SandboxRequest):
        if self.require_gvisor and request.runtime != "runsc":
            raise SandboxError("sandbox_unavailable", "gVisor runtime is required")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        return request

    async def start(self, request: SandboxRequest):
        name = f"astra-sandbox-{uuid.uuid4().hex}"
        args = [self.binary, "run", "--name", name, "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65532:65532", "--memory", f"{request.memory_mb}m", "--cpus", str(request.cpus), "--pids-limit", str(request.pids), "--ulimit", "nofile=256:256", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m", "--mount", f"type=bind,src={request.input_dir.resolve()},dst=/input,readonly", "--mount", f"type=bind,src={request.output_dir.resolve()},dst=/output"]
        if request.runtime:
            args.extend(["--runtime", request.runtime])
        for key, value in request.environment.items():
            args.extend(["--env", f"{key}={value}"])
        args.extend([request.image, *request.command])
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        return {"name": name, "proc": proc, "request": request, "result": None}

    async def wait(self, handle, timeout: int):
        stdout, stderr = await asyncio.wait_for(handle["proc"].communicate(), timeout=timeout)
        handle["result"] = SandboxResult(handle["proc"].returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace"), handle["request"].runtime or "runc", handle["request"].image)
        return handle["result"]

    async def terminate(self, handle) -> None:
        proc = await asyncio.create_subprocess_exec(self.binary, "kill", handle["name"], stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()

    async def collect(self, handle):
        return handle["result"]

    async def cleanup(self, handle) -> None:
        proc = await asyncio.create_subprocess_exec(self.binary, "rm", "-f", handle["name"], stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
