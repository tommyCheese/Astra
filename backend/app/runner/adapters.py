from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, ClassVar
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.schemas.agent import AgentObservation, ValidationIssue, ValidationOutcome


class TaskAdapter(ABC):
    name: str
    allowed_tools: ClassVar[frozenset[str]]

    @abstractmethod
    def normalize_tool_result(self, tool_name: str, output: dict[str, Any]) -> AgentObservation:
        raise NotImplementedError

    @abstractmethod
    def validate(self, result: dict[str, Any], evidence: dict[str, Any]) -> ValidationOutcome:
        raise NotImplementedError


class ToolResultProcessor(ABC):
    tool_names: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def process(
        self, tool_name: str, output: dict[str, Any]
    ) -> tuple[AgentObservation, dict[str, Any]]:
        raise NotImplementedError

    def record_failure(
        self, tool_name: str, tool_input: dict[str, Any], error: dict[str, Any]
    ) -> None:
        return None

    def build_evidence(self, goal: str, attempted: bool) -> dict[str, Any]:
        return {"query": goal, "external_evidence_attempted": attempted}


class ProcessorRegistry:
    def __init__(self, processors: Iterable[ToolResultProcessor]):
        self._processors = list(processors)

    def for_tool(self, tool_name: str) -> ToolResultProcessor | None:
        return next((item for item in self._processors if tool_name in item.tool_names), None)


class WebTaskAdapter(TaskAdapter, ToolResultProcessor):
    name = "web"
    allowed_tools = frozenset({"web_search", "web_fetch"})
    tool_names = allowed_tools

    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = []
        self.fetched_sources: list[dict[str, Any]] = []
        self.failed_sources: list[dict[str, Any]] = []
        self.dedupe: dict[str, Any] = {}
        self.search_warnings: list[str] = []
        self.attempted = False

    def normalize_tool_result(self, tool_name: str, output: dict[str, Any]) -> AgentObservation:
        return AgentObservation(
            kind="tool_result",
            status="succeeded",
            summary=f"{tool_name} completed",
            data={"tool_name": tool_name, **output},
        )

    def process(
        self, tool_name: str, output: dict[str, Any]
    ) -> tuple[AgentObservation, dict[str, Any]]:
        self.attempted = True
        evidence: dict[str, Any] = {}
        if tool_name == "web_search":
            self.candidates, self.dedupe = self.filter_candidates(output.get("candidates", []))
            output["candidates"] = self.candidates
            output["dedupe"] = self.dedupe
            self.search_warnings = output.get("warnings", [])
            evidence = {
                "candidate_count": output.get("candidate_count", len(self.candidates)),
                "deduped_count": len(self.candidates),
                "warnings": self.search_warnings,
            }
        elif tool_name == "web_fetch":
            self.fetched_sources.append(output)
            evidence = {
                "fetched_count": len(self.fetched_sources),
                "last_quality": output.get("quality_score"),
            }
        return self.normalize_tool_result(tool_name, output), evidence

    def record_failure(
        self, tool_name: str, tool_input: dict[str, Any], error: dict[str, Any]
    ) -> None:
        self.attempted = True
        if tool_name == "web_fetch":
            self.failed_sources.append({"url": tool_input.get("url"), **error})

    def validate(self, result: dict[str, Any], evidence: dict[str, Any]) -> ValidationOutcome:
        fetched = evidence.get("fetched_sources", [])
        sources = result.get("sources", [])
        warnings = list(evidence.get("warnings", []))
        if (
            not evidence.get("external_evidence_attempted")
            and not evidence.get("candidates")
            and not evidence.get("failed_sources")
            and (result.get("summary") or result.get("findings"))
        ):
            return ValidationOutcome(
                validator="task_adapter",
                passed=True,
                blocking=True,
                evidence_refs=[],
            )
        issues: list[ValidationIssue] = []
        if not fetched:
            issues.append(
                ValidationIssue(
                    code="web_sources_not_fetched",
                    message="没有成功抓取到可用来源。",
                )
            )
        if not sources:
            issues.append(
                ValidationIssue(
                    code="web_source_citations_missing",
                    message="最终答案缺少来源引用。",
                )
            )
        if issues:
            return ValidationOutcome(
                validator="task_adapter",
                passed=False,
                blocking=True,
                issues=issues,
                warnings=warnings,
            )
        warning_issues: list[ValidationIssue] = []
        if evidence.get("failed_sources"):
            warning_issues.append(
                ValidationIssue(
                    code="web_sources_partially_failed",
                    message="部分来源抓取失败。",
                    severity="warning",
                )
            )
        if any(float(item.get("quality_score") or 0) < 0.5 for item in fetched):
            warning_issues.append(
                ValidationIssue(
                    code="web_source_quality_low",
                    message="部分来源质量较低。",
                    severity="warning",
                )
            )
        return ValidationOutcome(
            validator="task_adapter",
            passed=True,
            blocking=True,
            issues=warning_issues,
            warnings=warnings,
            evidence_refs=[str(item.get("url")) for item in fetched if item.get("url")],
        )

    def filter_candidates(self, candidates: list[dict[str, Any]]):
        filtered: list[dict[str, Any]] = []
        seen: set[str] = set()
        skipped: list[dict[str, Any]] = []
        for candidate in candidates:
            url = candidate.get("url", "")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                skipped.append({"url": url, "reason": "unsupported_url"})
                continue
            if parsed.path.lower().endswith(
                (".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov")
            ):
                skipped.append({"url": url, "reason": "unsupported_content_type"})
                continue
            canonical = self.canonical_url(url)
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

    def canonical_url(self, url: str) -> str:
        parsed = urlparse(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
        ]
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/") or "/",
                "",
                urlencode(query),
                "",
            )
        )

    def build_evidence(self, goal: str, attempted: bool = False) -> dict[str, Any]:
        attempted = self.attempted or attempted
        warnings = list(self.search_warnings)
        for source in self.fetched_sources:
            warnings.extend(source.get("warnings", []))
        if attempted and not self.fetched_sources:
            warnings.append("没有可用于总结的成功抓取来源。")
        return {
            "query": goal,
            "candidates": self.candidates,
            "fetched_sources": self.fetched_sources,
            "failed_sources": self.failed_sources,
            "dedupe": self.dedupe,
            "warnings": warnings,
            "external_evidence_attempted": attempted,
        }


class ChartTaskAdapter(TaskAdapter, ToolResultProcessor):
    name = "chart"
    allowed_tools = frozenset({"chart.render"})
    tool_names = allowed_tools

    def __init__(self):
        self.attempted = False
        self.artifacts: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    def normalize_tool_result(self, tool_name: str, output: dict[str, Any]) -> AgentObservation:
        return AgentObservation(
            kind="tool_result",
            status="succeeded",
            summary="chart.render completed",
            data={"tool_name": tool_name, **output},
        )

    def process(
        self, tool_name: str, output: dict[str, Any]
    ) -> tuple[AgentObservation, dict[str, Any]]:
        self.attempted = True
        self.artifacts = list(output.get("artifacts", []))
        self.warnings = list(output.get("warnings", []))
        if not self.artifacts or any(
            not item.get("mime_type")
            or not item.get("checksum")
            or int(item.get("size_bytes", 0)) <= 0
            for item in self.artifacts
        ):
            raise ValueError("chart.render returned invalid artifacts")
        return self.normalize_tool_result(tool_name, output), {
            "artifact_count": len(self.artifacts),
            "warnings": self.warnings,
        }

    def validate(self, result: dict[str, Any], evidence: dict[str, Any]) -> ValidationOutcome:
        if not self.attempted:
            return ValidationOutcome(validator="task_adapter", passed=True, blocking=True)
        if not self.artifacts:
            return ValidationOutcome(
                validator="task_adapter",
                passed=False,
                blocking=True,
                issues=[
                    ValidationIssue(
                        code="chart_artifact_missing",
                        message="图表没有产生有效 Artifact。",
                    )
                ],
            )
        return ValidationOutcome(
            validator="task_adapter",
            passed=True,
            blocking=True,
            warnings=self.warnings,
            evidence_refs=[str(item.get("id")) for item in self.artifacts if item.get("id")],
        )
