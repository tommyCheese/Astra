from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.schemas.agent import AgentObservation, CompletionDecision, TerminalState


class TaskAdapter(ABC):
    name: str
    allowed_tools: set[str]

    @abstractmethod
    def normalize_tool_result(self, tool_name: str, output: Dict[str, Any]) -> AgentObservation:
        raise NotImplementedError

    @abstractmethod
    def validate(self, result: Dict[str, Any], evidence: Dict[str, Any]) -> CompletionDecision:
        raise NotImplementedError


class ToolResultProcessor(ABC):
    tool_names: set[str] = set()

    @abstractmethod
    def process(self, tool_name: str, output: Dict[str, Any]) -> tuple[AgentObservation, Dict[str, Any]]:
        raise NotImplementedError

    def record_failure(self, tool_name: str, tool_input: Dict[str, Any], error: Dict[str, Any]) -> None:
        return None

    def build_evidence(self, goal: str, attempted: bool) -> Dict[str, Any]:
        return {"query": goal, "external_evidence_attempted": attempted}


class ProcessorRegistry:
    def __init__(self, processors: Iterable[ToolResultProcessor]):
        self._processors = list(processors)

    def for_tool(self, tool_name: str) -> Optional[ToolResultProcessor]:
        return next((item for item in self._processors if tool_name in item.tool_names), None)


class WebTaskAdapter(TaskAdapter, ToolResultProcessor):
    name = "web"
    allowed_tools = {"web_search", "web_fetch"}
    tool_names = allowed_tools

    def __init__(self) -> None:
        self.candidates: list[Dict[str, Any]] = []
        self.fetched_sources: list[Dict[str, Any]] = []
        self.failed_sources: list[Dict[str, Any]] = []
        self.dedupe: Dict[str, Any] = {}
        self.search_warnings: list[str] = []
        self.attempted = False

    def normalize_tool_result(self, tool_name: str, output: Dict[str, Any]) -> AgentObservation:
        return AgentObservation(kind="tool_result", status="succeeded", summary=f"{tool_name} completed", data={"tool_name": tool_name, **output})

    def process(self, tool_name: str, output: Dict[str, Any]) -> tuple[AgentObservation, Dict[str, Any]]:
        self.attempted = True
        evidence: Dict[str, Any] = {}
        if tool_name == "web_search":
            self.candidates, self.dedupe = self.filter_candidates(output.get("candidates", []))
            output["candidates"] = self.candidates
            output["dedupe"] = self.dedupe
            self.search_warnings = output.get("warnings", [])
            evidence = {"candidate_count": output.get("candidate_count", len(self.candidates)), "deduped_count": len(self.candidates), "warnings": self.search_warnings}
        elif tool_name == "web_fetch":
            self.fetched_sources.append(output)
            evidence = {"fetched_count": len(self.fetched_sources), "last_quality": output.get("quality_score")}
        return self.normalize_tool_result(tool_name, output), evidence

    def record_failure(self, tool_name: str, tool_input: Dict[str, Any], error: Dict[str, Any]) -> None:
        self.attempted = True
        if tool_name == "web_fetch":
            self.failed_sources.append({"url": tool_input.get("url"), **error})

    def validate(self, result: Dict[str, Any], evidence: Dict[str, Any]) -> CompletionDecision:
        fetched = evidence.get("fetched_sources", [])
        sources = result.get("sources", [])
        warnings = list(evidence.get("warnings", []))
        if not evidence.get("external_evidence_attempted") and not evidence.get("candidates") and not evidence.get("failed_sources") and (result.get("summary") or result.get("findings")):
            return CompletionDecision(state=TerminalState.completed, reason="通用问答已直接完成，无需调用外部工具。")
        if not fetched or not sources:
            return CompletionDecision(state=TerminalState.blocked, reason="没有足够的已审计 Web 证据。", unmet_criteria=["criterion-result"], warnings=warnings)
        if evidence.get("failed_sources") or any(float(item.get("quality_score") or 0) < 0.5 for item in fetched):
            return CompletionDecision(state=TerminalState.completed_with_warnings, reason="Web 结果已验证，但存在来源质量或抓取警告。", warnings=warnings)
        return CompletionDecision(state=TerminalState.completed, reason="Web 结果具有已审计来源支持。")

    def filter_candidates(self, candidates: list[Dict[str, Any]]):
        filtered: list[Dict[str, Any]] = []
        seen: set[str] = set()
        skipped: list[Dict[str, Any]] = []
        for candidate in candidates:
            url = candidate.get("url", "")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                skipped.append({"url": url, "reason": "unsupported_url"})
                continue
            if parsed.path.lower().endswith((".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov")):
                skipped.append({"url": url, "reason": "unsupported_content_type"})
                continue
            canonical = self.canonical_url(url)
            if canonical in seen:
                skipped.append({"url": url, "reason": "duplicate"})
                continue
            seen.add(canonical)
            filtered.append({**candidate, "canonical_url": canonical})
        return filtered, {"candidate_count": len(candidates), "deduped_count": len(filtered), "skipped_count": len(skipped), "skipped": skipped[:8]}

    def canonical_url(self, url: str) -> str:
        parsed = urlparse(url)
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", "", urlencode(query), ""))

    def build_evidence(self, goal: str, attempted: bool = False) -> Dict[str, Any]:
        attempted = self.attempted or attempted
        warnings = list(self.search_warnings)
        for source in self.fetched_sources:
            warnings.extend(source.get("warnings", []))
        if attempted and not self.fetched_sources:
            warnings.append("没有可用于总结的成功抓取来源。")
        return {"query": goal, "candidates": self.candidates, "fetched_sources": self.fetched_sources, "failed_sources": self.failed_sources, "dedupe": self.dedupe, "warnings": warnings, "external_evidence_attempted": attempted}


class ChartTaskAdapter(TaskAdapter, ToolResultProcessor):
    name = "chart"
    allowed_tools = {"chart.render"}
    tool_names = allowed_tools

    def __init__(self):
        self.attempted = False
        self.artifacts: list[Dict[str, Any]] = []
        self.warnings: list[str] = []

    def normalize_tool_result(self, tool_name: str, output: Dict[str, Any]) -> AgentObservation:
        return AgentObservation(kind="tool_result", status="succeeded", summary="chart.render completed", data={"tool_name": tool_name, **output})

    def process(self, tool_name: str, output: Dict[str, Any]) -> tuple[AgentObservation, Dict[str, Any]]:
        self.attempted = True
        self.artifacts = list(output.get("artifacts", []))
        self.warnings = list(output.get("warnings", []))
        if not self.artifacts or any(not item.get("mime_type") or not item.get("checksum") or int(item.get("size_bytes", 0)) <= 0 for item in self.artifacts):
            raise ValueError("chart.render returned invalid artifacts")
        return self.normalize_tool_result(tool_name, output), {"artifact_count": len(self.artifacts), "warnings": self.warnings}

    def validate(self, result: Dict[str, Any], evidence: Dict[str, Any]) -> CompletionDecision:
        if not self.attempted:
            return CompletionDecision(state=TerminalState.completed, reason="未请求图表能力。")
        if not self.artifacts:
            return CompletionDecision(state=TerminalState.blocked, reason="图表没有产生有效 Artifact。")
        state = TerminalState.completed_with_warnings if self.warnings else TerminalState.completed
        return CompletionDecision(state=state, reason="图表 Artifact 已通过完整性校验。", warnings=self.warnings)
