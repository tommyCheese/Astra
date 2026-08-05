from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_result import Finding, SourceReference

WEB_MARKERS = (
    "搜索",
    "查询",
    "查找",
    "来源",
    "最新",
    "网页",
    "search",
    "research",
    "source",
    "current",
    "latest",
)


def parse_mock_planning_goal(goal: str) -> tuple[str, str]:
    try:
        revision = json.loads(goal)
    except (TypeError, ValueError):
        return goal, goal
    if not isinstance(revision, dict):
        return goal, goal
    original = revision.get("original_goal")
    public_goal = original.strip() if isinstance(original, str) and original.strip() else goal
    request = revision.get("revision_request")
    planning_goal = (
        f"{public_goal}\n{request.strip()}"
        if isinstance(request, str) and request.strip()
        else public_goal
    )
    return public_goal, planning_goal


def infer_mock_capabilities(goal: str) -> list[str]:
    normalized_goal = goal.casefold()
    if any(marker in normalized_goal for marker in WEB_MARKERS):
        return ["information.search", "information.read"]
    if any(
        marker in normalized_goal
        for marker in ("图表", "绘图", "可视化", "chart", "plot", "visuali")
    ):
        return ["data.visualize"]
    if any(
        marker in normalized_goal
        for marker in ("工作区", "文件", "命令", "workspace", "file", "command")
    ):
        return ["workspace.execute"]
    return []


@dataclass
class MockEvidenceSummary:
    sources: list[SourceReference] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    failed_sources: list[dict[str, Any]] = field(default_factory=list)
    source_quality: list[dict[str, Any]] = field(default_factory=list)

    def add_source(self, source: dict[str, Any], *, excerpt_length: int) -> None:
        url = source.get("url")
        if not isinstance(url, str) or not url:
            return
        self.sources.append(
            SourceReference(
                url=url,
                title=source.get("title"),
                retrieved_at=source.get("retrieved_at"),
            )
        )
        content = source.get("content", "")
        excerpt = content[:excerpt_length].strip() or "该来源没有返回可读正文。"
        self.findings.append(Finding(text=excerpt, source_urls=[url]))
        self.source_quality.append(
            {
                "url": url,
                "quality_score": source.get("quality_score"),
                "extraction_strategy": source.get("extraction_strategy"),
                "warnings": source.get("warnings", []),
            }
        )


def summarize_mock_evidence(tool_outputs: list[dict[str, Any]]) -> MockEvidenceSummary:
    summary = MockEvidenceSummary()
    evidence_pack = next(
        (output.get("evidence_pack") for output in tool_outputs if output.get("evidence_pack")),
        None,
    )
    if isinstance(evidence_pack, dict):
        for source in evidence_pack.get("fetched_sources", []):
            if isinstance(source, dict):
                summary.add_source(source, excerpt_length=260)
        summary.failed_sources = evidence_pack.get("failed_sources", [])
        summary.caveats.extend(evidence_pack.get("warnings", []))
        return summary
    for output in tool_outputs:
        if "candidates" not in output:
            summary.add_source(output, excerpt_length=220)
    return summary


def available_mock_tools(context: dict[str, Any], capability: str) -> list[str]:
    tools = [
        name
        for name, manifest in context.get("tool_manifests", {}).items()
        if capability in manifest.get("task_capabilities", [])
    ]
    if "tool_manifests" not in context:
        return ["web_search" if capability == "information.search" else "web_fetch"]
    return tools


def mock_terminal_decision(context: dict[str, Any]) -> AgentDecision | None:
    active_node = context.get("active_node")
    unresolved = set((context.get("tool_selection") or {}).get("unresolved_capabilities") or [])
    if active_node is not None and (not active_node.get("required_capabilities") or not unresolved):
        return AgentDecision(
            decision_type="complete_node",
            reasoning_summary="当前节点的预期工作和能力要求已经满足。",
            node_result={"status": "completed"},
            expected_observation="节点结果满足预期。",
        )
    if active_node is None and context.get("plan_graph", {}).get("nodes"):
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="计划节点已经完成，可以生成最终回复。",
            expected_observation="最终答案包含结果、限制和验证备注。",
        )
    return None


def mock_search_decision(goal: str, context: dict[str, Any]) -> AgentDecision | None:
    search_observation = _search_observation(context.get("observations", []))
    unresolved = set((context.get("tool_selection") or {}).get("unresolved_capabilities") or [])
    quick_web_goal = context.get("active_node") is None and any(
        marker in goal.casefold() for marker in WEB_MARKERS
    )
    search_tools = available_mock_tools(context, "information.search")
    if (
        "information.search" not in unresolved and (not quick_web_goal or search_observation)
    ) or not search_tools:
        return None
    return AgentDecision(
        decision_type="call_tool",
        reasoning_summary="先搜索候选来源，建立可抓取的证据候选集。",
        tool_name=search_tools[0],
        tool_input={"query": goal},
        expected_observation="返回候选来源和搜索 warning。",
        stop_condition="获得候选来源后抓取正文。",
    )


def mock_fetch_decision(goal: str, context: dict[str, Any]) -> AgentDecision | None:
    observations = context.get("observations", [])
    attempted_urls = _attempted_fetch_urls(observations)
    search_result = _search_observation(observations)
    read_tools = available_mock_tools(context, "information.read")
    candidates = search_result.get("data", {}).get("candidates", []) if search_result else []
    candidate = next((item for item in candidates if item.get("url") not in attempted_urls), None)
    if not candidate or not read_tools:
        return None
    return AgentDecision(
        decision_type="call_tool",
        reasoning_summary="抓取候选来源正文，用于构造证据包和最终回答。",
        tool_name=read_tools[0],
        tool_input={
            "url": candidate.get("url"),
            "query": goal,
            "snippet": candidate.get("snippet", ""),
            "crawler_plan": context.get("crawler_plan", {}),
        },
        expected_observation="返回正文、质量评分、抓取策略和 warning。",
        stop_condition="抓取足够来源后进行综合验证。",
    )


def _search_observation(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            observation
            for observation in observations
            if observation.get("kind") == "tool_result"
            and observation.get("data", {}).get("tool_name") == "web_search"
        ),
        None,
    )


def _attempted_fetch_urls(observations: list[dict[str, Any]]) -> set[str | None]:
    return {
        observation.get("data", {}).get("url")
        for observation in observations
        if observation.get("kind") in {"tool_result", "tool_error"}
        and observation.get("data", {}).get("tool_name") == "web_fetch"
    }
