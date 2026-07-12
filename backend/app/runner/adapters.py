from abc import ABC, abstractmethod
from typing import Any, Dict
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


class WebTaskAdapter(TaskAdapter):
    name = "web"
    allowed_tools = {"web_search", "web_fetch"}

    def normalize_tool_result(self, tool_name: str, output: Dict[str, Any]) -> AgentObservation:
        return AgentObservation(kind="tool_result", status="succeeded", summary=f"{tool_name} completed", data={"tool_name": tool_name, **output})

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

    def build_evidence(self, goal: str, candidates: list[Dict[str, Any]], fetched_sources: list[Dict[str, Any]], failed_sources: list[Dict[str, Any]], dedupe: Dict[str, Any], search_warnings: list[str]) -> Dict[str, Any]:
        warnings = list(search_warnings)
        for source in fetched_sources:
            warnings.extend(source.get("warnings", []))
        if not fetched_sources:
            warnings.append("没有可用于总结的成功抓取来源。")
        return {"query": goal, "candidates": candidates, "fetched_sources": fetched_sources, "failed_sources": failed_sources, "dedupe": dedupe, "warnings": warnings}
