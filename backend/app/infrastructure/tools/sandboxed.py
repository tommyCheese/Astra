import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.sandbox.runtime import SandboxError, SandboxRequest
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolSpec,
    ToolExecutionError,
    ToolResultEnvelope,
    materialize_skill_inputs,
)

MAX_SANDBOX_REQUEST_BYTES = 256 * 1024
MAX_SANDBOX_CONFIG_BYTES = 64 * 1024
MAX_SANDBOX_RESPONSE_BYTES = 4 * 1024 * 1024


class SandboxedWebTool(AstraTool):
    """Host-side plugin descriptor and proxy for a container-only web tool."""

    def __init__(
        self,
        native_tool: AstraTool,
        settings: AstraRuntimeSettings,
        runtime_config: dict[str, str],
    ):
        self.settings = settings
        self.runtime_config = dict(runtime_config)
        self.spec = AstraToolSpec.model_validate(
            native_tool.spec.model_dump()
            | {
                "risk": "sandboxed",
                "execution_backend": "sandbox.remote",
                "resource_profile": {
                    "runtime": "oci",
                    "network": "public_web",
                    "image": settings.sandbox_web_runtime_image,
                },
            }
        )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        if context is None:
            raise ToolExecutionError(
                "invalid_decision", f"{self.spec.name} requires execution context"
            )
        payload = json.dumps(
            {"version": "1", "tool": self.spec.name, "input": tool_input},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_SANDBOX_REQUEST_BYTES:
            raise ToolExecutionError("invalid_input", "Sandbox tool request is too large")

        root = Path(tempfile.mkdtemp(prefix="astra-web-tool-"))
        input_dir, output_dir = root / "input", root / "output"
        input_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        (input_dir / "request.json").write_bytes(payload)
        runtime_config = json.dumps(
            self.runtime_config,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(runtime_config) > MAX_SANDBOX_CONFIG_BYTES:
            shutil.rmtree(root, ignore_errors=True)
            raise ToolExecutionError("invalid_input", "Sandbox tool configuration is too large")
        (input_dir / "runtime-config.json").write_bytes(runtime_config)
        try:
            skill_inputs = await materialize_skill_inputs(context, input_dir)
        except ValueError as exc:
            shutil.rmtree(root, ignore_errors=True)
            raise ToolExecutionError(
                "sandbox_policy_violation",
                "Skill sandbox inputs failed immutable binding validation",
            ) from exc
        request = SandboxRequest(
            template=self.settings.sandbox_web_runtime_image,
            command=["/opt/astra/bin/tool-runtime"],
            input_dir=input_dir,
            output_dir=output_dir,
            wall_time_seconds=min(
                self.settings.sandbox_wall_time_seconds, self.spec.timeout_seconds + 5
            ),
            secure=True,
            allow_internet_access=True,
            record_stdout=False,
            environment={"TZ": "UTC", "PYTHONHASHSEED": "0"},
            metadata={
                "tool": self.spec.name,
                "protocol": "1",
                "skill_input_count": str(len(skill_inputs)),
            },
        )
        try:
            _job, _refs, result = await context.sandbox_service.execute(
                request,
                run_id=context.run_id,
                tool_call_id=context.tool_call_id,
                runtime_profile={
                    "backend": "container-tool",
                    "image": self.settings.sandbox_web_runtime_image,
                    "tool": self.spec.name,
                    "protocol": "1",
                    "trace_id": context.trace_id,
                },
                resource_limits={
                    "wall_time_seconds": request.wall_time_seconds,
                    "memory_mb": self.settings.sandbox_memory_mb,
                    "cpus": self.settings.sandbox_cpus,
                    "pids": self.settings.sandbox_pids,
                    "network": "public_web",
                },
            )
        except SandboxError as exc:
            raise ToolExecutionError(exc.category, exc.safe_message) from exc
        finally:
            shutil.rmtree(root, ignore_errors=True)

        output = _parse_sandbox_response(result.stdout)
        return ToolResultEnvelope(
            data=output,
            warnings=list(output.get("warnings", [])),
            artifacts=list(output.get("artifacts", [])),
        ).model_dump(mode="json", exclude_none=True)


def _parse_sandbox_response(stdout: str) -> dict[str, Any]:
    raw = stdout.encode("utf-8")
    if len(raw) > MAX_SANDBOX_RESPONSE_BYTES:
        raise ToolExecutionError("sandbox_policy_violation", "Sandbox response is too large")
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ToolExecutionError(
            "sandbox_policy_violation", "Sandbox returned an invalid response"
        ) from exc
    if not isinstance(envelope, dict):
        raise ToolExecutionError(
            "sandbox_policy_violation", "Sandbox returned an invalid response"
        )
    if envelope.get("ok") is not True:
        error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
        raise ToolExecutionError(
            str(error.get("category") or "tool_failed"),
            str(error.get("message") or "Sandboxed tool failed"),
        )
    output = envelope.get("output")
    if not isinstance(output, dict):
        raise ToolExecutionError(
            "sandbox_policy_violation", "Sandbox returned an invalid tool output"
        )
    return output
