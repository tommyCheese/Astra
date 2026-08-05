from __future__ import annotations

from typing import Any

from app.application.agent_runtime.result_adapters import WebTaskAdapter
from app.application.agent_runtime.services.approval import safe_preview, similar_matcher
from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.agent.run_result import AgentValidationIssue, AgentValidationOutcome
from app.domain.grounding.fragments import fragments_from_web_result
from app.infrastructure.plugins.interfaces import (
    PluginApprovalPresenter,
    PluginResultProcessingOutput,
    PluginResultProcessor,
    PluginResultValidator,
)
from app.infrastructure.tools.base import AstraToolSpec


class WebResultProcessor(PluginResultProcessor):
    def process(self, spec, tool_input, result):
        data = dict(result.get("data") or {})
        fragments = fragments_from_web_result(spec.name, data)
        evidence: dict[str, Any]
        if "candidates" in data:
            candidates, dedupe = WebTaskAdapter().filter_candidates(data.get("candidates", []))
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
        return safe_preview(spec.name, tool_input)

    def similar_matcher(self, spec: AstraToolSpec, tool_input: dict[str, Any]):
        return similar_matcher(spec.name, tool_input)
