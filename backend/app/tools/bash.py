import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.sandbox.runtime import SandboxError, SandboxRequest, sanitize_log
from app.schemas.agent import BashExecuteResult
from app.tools.base import Tool, ToolExecutionError, ToolResultEnvelope, ToolSpec

RUNNER = r'''import json
import subprocess

request = json.load(open("/input/request.json", encoding="utf-8"))
completed = subprocess.run(
    ["/bin/bash", "--noprofile", "--norc", "-c", request["command"]],
    cwd="/workspace",
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
limit = request["output_max_chars"]
print(json.dumps({
    "exit_code": completed.returncode,
    "stdout": completed.stdout.decode("utf-8", errors="replace")[:limit],
    "stderr": completed.stderr.decode("utf-8", errors="replace")[:limit],
}))
'''


class BashExecuteRequest(BaseModel):
    command: str = Field(min_length=1, max_length=8000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)


class BashExecuteTool(Tool):
    spec = ToolSpec(
        name="bash_execute",
        version="1.0",
        description=(
            "Execute an offline Bash command in the current Task Workspace. "
            "Files written under /workspace persist for later tools and Runs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "maxLength": 8000},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        permission="command_execute",
        side_effect_level="external_side_effect",
        capabilities=["command_execute"],
        permissions=[
            "command_execute",
            "process_execute",
            "temporary_compute",
            "workspace_read",
            "workspace_write",
            "workspace_delete",
            "process_execute_unknown",
            "network_write",
        ],
        risk="high",
        execution_backend="sandbox.remote",
        timeout_seconds=120,
        retry_policy={"max_attempts": 1},
        error_categories=["invalid_input", "sandbox_unavailable", "sandbox_timeout"],
        idempotent=False,
        resource_profile={"runtime": "oci", "network": "none", "workspace": "read_write"},
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(
        self, tool_input: dict[str, Any], *, context=None
    ) -> dict[str, Any]:
        if context is None:
            raise ToolExecutionError("invalid_decision", "bash_execute requires execution context")
        if context.workspace_path is None:
            raise ToolExecutionError(
                "sandbox_policy_violation", "bash_execute requires a Task Workspace"
            )
        try:
            parsed = BashExecuteRequest.model_validate(tool_input)
        except Exception as exc:
            raise ToolExecutionError("invalid_input", "Invalid Bash command request") from exc
        timeout = min(
            parsed.timeout_seconds or self.settings.sandbox_wall_time_seconds,
            self.settings.sandbox_wall_time_seconds,
        )
        root = Path(tempfile.mkdtemp(prefix="astra-bash-"))
        input_dir, output_dir = root / "input", root / "output"
        input_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        (input_dir / "request.json").write_text(
            json.dumps(
                {
                    "command": parsed.command,
                    "timeout_seconds": timeout,
                    "output_max_chars": self.settings.bash_output_max_chars,
                }
            ),
            encoding="utf-8",
        )
        (input_dir / "runner.py").write_text(RUNNER, encoding="utf-8")
        workspace_mode = (
            context.workspace_mode if context.effect_plan is not None else "read_write"
        )
        request = SandboxRequest(
            template=self.settings.sandbox_runtime_image,
            command=["python", "/input/runner.py"],
            input_dir=input_dir,
            output_dir=output_dir,
            wall_time_seconds=timeout,
            secure=True,
            allow_internet_access=False,
            record_stdout=False,
            environment={"TZ": "UTC", "PYTHONHASHSEED": "0", "HOME": "/tmp"},
            metadata={"tool": "bash_execute", "protocol": "1"},
            workspace_dir=context.workspace_path if workspace_mode != "none" else None,
            workspace_mode=workspace_mode,
        )
        try:
            _job, _refs, result = await context.sandbox_service.execute(
                request,
                run_id=context.run_id,
                tool_call_id=context.tool_call_id,
                runtime_profile={
                    "backend": "sandboxed-bash",
                    "image": self.settings.sandbox_runtime_image,
                    "network": "none",
                    "workspace": workspace_mode,
                    "workspace_path": "/workspace",
                    "trace_id": context.trace_id,
                },
                resource_limits={
                    "wall_time_seconds": timeout,
                    "memory_mb": self.settings.sandbox_memory_mb,
                    "cpus": self.settings.sandbox_cpus,
                    "pids": self.settings.sandbox_pids,
                    "network": "none",
                },
            )
            payload = json.loads(result.stdout)
            normalized = BashExecuteResult(
                exit_code=int(payload["exit_code"]),
                stdout=sanitize_log(str(payload.get("stdout", "")), self.settings.bash_output_max_chars),
                stderr=sanitize_log(str(payload.get("stderr", "")), self.settings.bash_output_max_chars),
            )
        except SandboxError as exc:
            raise ToolExecutionError(exc.category, exc.safe_message) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError(
                "sandbox_policy_violation", "Sandbox returned an invalid Bash result"
            ) from exc
        finally:
            shutil.rmtree(root, ignore_errors=True)
        return ToolResultEnvelope(data=normalized.model_dump()).model_dump(mode="json")
