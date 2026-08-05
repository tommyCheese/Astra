from __future__ import annotations

import re
import shlex
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.agent.run_result import AgentValidationIssue, AgentValidationOutcome
from app.domain.grounding.fragments import fragments_from_web_result
from app.domain.grounding.schemas import GroundingEvidenceLineage
from app.infrastructure.plugins.interfaces import (
    PluginApprovalPresenter,
    PluginResultProcessingOutput,
    PluginResultProcessor,
    PluginResultValidator,
    PluginResultAdapter,
)
from app.infrastructure.tools.base import AstraToolSpec

_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|authorization|password)\s*[:=]\s*(?:bearer\s+)?\S+"
)
_SHELL_META = re.compile(r"(?:&&|\|\||[|;&<>`]|\$\(|\$\{|\n|\r)")


class LegacyRawResultAdapter(PluginResultAdapter):
    """Compatibility boundary for built-in tools that still return their legacy payload."""

    def adapt(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol_version": "1",
            "status": "succeeded",
            "data": result,
            "warnings": list(result.get("warnings", [])),
            "metrics": {},
            "artifacts": list(result.get("artifacts", [])),
        }


class LegacyAutoResultAdapter(PluginResultAdapter):
    """Accept an existing envelope or wrap a legacy direct-registry payload."""

    def adapt(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("protocol_version") == "1" and result.get("status") in {
            "succeeded",
            "failed",
        }:
            return result
        return LegacyRawResultAdapter().adapt(result)


class WebResultProcessor(PluginResultProcessor):
    def __init__(self):
        self._candidates: list[dict[str, Any]] = []

    def process(self, spec, tool_input, result):
        data = dict(result.get("data") or {})
        fragments = fragments_from_web_result(
            spec.name,
            data,
            lineage=GroundingEvidenceLineage(
                plan_node_id=str(result.get("plan_node_id") or "") or None,
                node_execution_id=str(result.get("node_execution_id") or "") or None,
                tool_call_id=str(result.get("tool_call_id") or "") or None,
            ),
        )
        evidence: dict[str, Any]
        if "candidates" in data:
            candidates, dedupe = self._filter_candidates(
                [*self._candidates, *data.get("candidates", [])]
            )
            self._candidates = candidates
            data["candidates"] = candidates
            data["dedupe"] = dedupe
            evidence = {
                "domain": "web",
                "kind": "search",
                "candidates": candidates,
                "warnings": list(data.get("warnings", [])),
                "fragments": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in fragments
                ],
            }
        else:
            evidence = {
                "domain": "web",
                "kind": "fetch",
                "source": data,
                "fragments": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in fragments
                ],
            }
        return PluginResultProcessingOutput(
            observation=AgentObservation(
                kind="tool_result",
                status="succeeded",
                summary=f"{spec.name} completed",
                data={"tool_name": spec.name, **data},
            ),
            evidence=evidence,
            validation_input={"domain": "web"},
        )

    @staticmethod
    def _filter_candidates(candidates):
        filtered = []
        seen = set()
        skipped = []
        for candidate in candidates:
            url = str(candidate.get("url") or "")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                skipped.append({"url": url, "reason": "unsupported_url"})
                continue
            if parsed.path.lower().endswith(
                (".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov")
            ):
                skipped.append({"url": url, "reason": "unsupported_content_type"})
                continue
            query = [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
                and key.lower() not in {"fbclid", "gclid"}
            ]
            canonical = urlunparse(
                (
                    parsed.scheme.lower(),
                    parsed.netloc.lower(),
                    parsed.path.rstrip("/") or "/",
                    "",
                    urlencode(query),
                    "",
                )
            )
            if canonical in seen:
                skipped.append({"url": url, "reason": "duplicate"})
                continue
            seen.add(canonical)
            filtered.append({**candidate, "canonical_url": canonical})
        return filtered, {
            "candidate_count": len(candidates),
            "deduped_count": len(filtered),
            "skipped_count": len(skipped),
            "skipped": skipped[:8],
        }

    def process_failure(self, spec, tool_input, error):
        return {
            "domain": "web",
            "kind": "failure",
            "source": {
                **({"url": tool_input.get("url")} if tool_input.get("url") else {}),
                **error,
            },
        }


class WebEvidenceValidator(PluginResultValidator):
    def validate(self, result, evidence):
        fragments = evidence.get("fragments", [])
        fetched = [item["source"] for item in fragments if item.get("kind") == "fetch"]
        attempted = any(item.get("domain") == "web" for item in fragments)
        if not attempted:
            return AgentValidationOutcome(validator="web_evidence", passed=True, blocking=True)
        issues = []
        if not fetched:
            issues.append(
                AgentValidationIssue(code="web_sources_not_fetched", message="没有成功抓取到可用来源。")
            )
        if not result.get("sources"):
            issues.append(
                AgentValidationIssue(
                    code="web_source_citations_missing", message="最终答案缺少来源引用。"
                )
            )
        return AgentValidationOutcome(
            validator="web_evidence",
            passed=not issues,
            blocking=True,
            issues=issues,
            evidence_refs=[str(item.get("url")) for item in fetched if item.get("url")],
        )


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
