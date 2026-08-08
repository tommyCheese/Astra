from __future__ import annotations

import re
import shlex
from typing import Any

from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.agent.run_result import AgentValidationIssue, AgentValidationOutcome
from app.infrastructure.plugins.interfaces import (
    PluginApprovalPresenter,
    PluginResultProcessingOutput,
    PluginResultProcessor,
    PluginResultValidator,
)
from app.infrastructure.tools.base import AstraToolSpec

_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|authorization|password)\s*[:=]\s*(?:bearer\s+)?\S+"
)
_SHELL_META = re.compile(r"(?:&&|\|\||[|;&<>`]|\$\(|\$\{|\n|\r)")


class ChartResultProcessor(PluginResultProcessor):
    def process(self, spec, tool_input, result):
        artifacts = list(result.get("artifacts", []))
        if not artifacts or any(
            not item.get("mime_type")
            or not item.get("checksum")
            or int(item.get("size_bytes", 0)) <= 0
            for item in artifacts
        ):
            raise ValueError("chart result contains invalid artifacts")
        return PluginResultProcessingOutput(
            observation=AgentObservation(
                kind="tool_result",
                status="succeeded",
                summary=f"{spec.name} completed",
                data={"tool_name": spec.name, **result},
            ),
            evidence={"domain": "chart", "kind": "artifacts", "artifacts": artifacts},
            validation_input={"domain": "chart"},
            completion_signals=("artifact_generated",),
        )


class ChartArtifactValidator(PluginResultValidator):
    def validate(self, result, evidence):
        fragments = evidence.get("fragments", [])
        artifacts = [
            artifact
            for item in fragments
            if item.get("domain") == "chart"
            for artifact in item.get("artifacts", [])
        ]
        if not any(item.get("domain") == "chart" for item in fragments):
            return AgentValidationOutcome(validator="chart_artifact", passed=True, blocking=True)
        if not artifacts:
            return AgentValidationOutcome(
                validator="chart_artifact",
                passed=False,
                blocking=True,
                issues=[
                    AgentValidationIssue(
                        code="chart_artifact_missing", message="图表没有产生有效 Artifact。"
                    )
                ],
            )
        return AgentValidationOutcome(
            validator="chart_artifact",
            passed=True,
            blocking=True,
            evidence_refs=[str(item.get("id")) for item in artifacts if item.get("id")],
        )


class BashResultProcessor(PluginResultProcessor):
    def process(self, spec, tool_input, result):
        data = dict(result.get("data") or {})
        changes = list(data.get("workspace_changes", []))
        return PluginResultProcessingOutput(
            observation=AgentObservation(
                kind="tool_result",
                status="succeeded",
                summary=f"{spec.name} completed",
                data={"tool_name": spec.name, **result},
            ),
            evidence={"domain": "workspace", "workspace_changes": changes} if changes else {},
            completion_signals=("workspace_changed",) if changes else (),
        )


class BashApprovalPresenter(PluginApprovalPresenter):
    def safe_preview(self, spec: AstraToolSpec, tool_input: dict[str, Any]) -> str:
        return _SECRET_VALUE.sub(
            r"\1=[REDACTED]",
            str(tool_input.get("command", "")),
        )[:1000]

    def similar_matcher(self, spec: AstraToolSpec, tool_input: dict[str, Any]):
        command = str(tool_input.get("command", "")).strip()
        if not command or _SHELL_META.search(command):
            return None
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return None
        if not tokens or "=" in tokens[0]:
            return None
        multi_part_commands = {"npm", "pnpm", "yarn", "git", "python", "python3"}
        prefix_length = 2 if tokens[0] in multi_part_commands and len(tokens) > 1 else 1
        return {"kind": "command_prefix", "tokens": tokens[:prefix_length]}
