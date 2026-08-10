from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_result import AgentAnswerFinding, AgentAnswerSourceReference

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
    planning_goal = f"{public_goal}\n{request.strip()}" if isinstance(request, str) and request.strip() else public_goal
    return public_goal, planning_goal


def infer_mock_capabilities(goal: str) -> list[str]:
    normalized_goal = goal.casefold()
    if any(marker in normalized_goal for marker in WEB_MARKERS):
        return ["information.search", "information.read"]
    if any(marker in normalized_goal for marker in ("图表", "绘图", "可视化", "chart", "plot", "visuali")):
        return ["data.visualize"]
    if any(marker in normalized_goal for marker in ("工作区", "文件", "命令", "workspace", "file", "command")):
        return ["workspace.execute"]
    return []


@dataclass
class MockEvidenceSummary:
    sources: list[AgentAnswerSourceReference] = field(default_factory=list)
    findings: list[AgentAnswerFinding] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    failed_sources: list[dict[str, Any]] = field(default_factory=list)
    source_quality: list[dict[str, Any]] = field(default_factory=list)

    def add_source(self, source: dict[str, Any], *, excerpt_length: int) -> None:
        url = source.get("url")
        if not isinstance(url, str) or not url:
            return
        self.sources.append(
            AgentAnswerSourceReference(
                url=url,
                title=source.get("title"),
                retrieved_at=source.get("retrieved_at"),
            )
        )
        content = source.get("content", "")
        excerpt = content[:excerpt_length].strip() or "该来源没有返回可读正文。"
        self.findings.append(AgentAnswerFinding(text=excerpt, source_urls=[url]))
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
    quick_web_goal = context.get("active_node") is None and any(marker in goal.casefold() for marker in WEB_MARKERS)
    search_tools = available_mock_tools(context, "information.search")
    if ("information.search" not in unresolved and (not quick_web_goal or search_observation)) or not search_tools:
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
        },
        expected_observation="返回正文、质量评分、抓取策略和 warning。",
        stop_condition="抓取足够来源后进行综合验证。",
    )


def mock_workspace_decision(goal: str, context: dict[str, Any]) -> AgentDecision | None:
    """Provide deterministic local file actions for browser and smoke-test runs.

    The mock is intentionally small, but it must not claim a file task succeeded
    without exercising the file tool that the task explicitly requests.
    """
    normalized = goal.casefold()
    manifests = context.get("tool_manifests", {})
    observations = context.get("observations", [])
    names = {item.get("data", {}).get("tool_name") for item in observations if isinstance(item, dict)}

    def has_tool(name: str) -> bool:
        return name in manifests

    path_match = re.search(r"([\w./-]+\.(?:txt|md|json|csv|py|ts|tsx|js|yaml|yml))", goal, re.I)
    path = path_match.group(1) if path_match else "result.txt"
    if any(marker in normalized for marker in ("创建", "写入", "create", "write")) and has_tool("workspace.write") and "workspace.write" not in names:
        content_match = re.search(r"(?:内容为|content(?:\s+is|:)?)[\s：:]*[“\"]?([^”\"。\n，,]+)", goal, re.I)
        content = content_match.group(1).strip() if content_match else "Astra workspace smoke test"
        return AgentDecision(
            decision_type="call_tool",
            reasoning_summary="任务明确要求创建文件，先写入受限任务工作区。",
            tool_name="workspace.write",
            tool_input={"path": path, "content": content},
            expected_observation="文件被原子写入任务工作区。",
        )
    if any(marker in normalized for marker in ("读取", "确认文件", "read", "confirm")) and has_tool("workspace.read") and "workspace.read" not in names:
        return AgentDecision(
            decision_type="call_tool",
            reasoning_summary="读取刚创建或指定的文件，以验证其内容。",
            tool_name="workspace.read",
            tool_input={"path": path},
            expected_observation="返回文件文本内容。",
        )
    if any(marker in normalized for marker in ("浏览", "列出", "list")) and has_tool("workspace.list") and "workspace.list" not in names:
        return AgentDecision(
            decision_type="call_tool",
            reasoning_summary="列出任务工作区中的文件。",
            tool_name="workspace.list",
            tool_input={"path": "."},
            expected_observation="返回受限的目录条目。",
        )
    if any(marker in normalized for marker in ("搜索", "查找", "search", "find")) and has_tool("workspace.search") and "workspace.search" not in names:
        query_match = re.search(r"(?:搜索|查找|search|find)[\s：:]*[“\"]?([^”\"。\n]+)", goal, re.I)
        query = query_match.group(1).strip() if query_match else "Astra"
        return AgentDecision(
            decision_type="call_tool",
            reasoning_summary="在任务文件中检索指定文本。",
            tool_name="workspace.search",
            tool_input={"query": query},
            expected_observation="返回匹配位置。",
        )
    return None


def _search_observation(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            observation
            for observation in observations
            if observation.get("kind") == "tool_result" and "candidates" in observation.get("data", {})
        ),
        None,
    )


def _attempted_fetch_urls(observations: list[dict[str, Any]]) -> set[str | None]:
    return {
        observation.get("data", {}).get("url")
        for observation in observations
        if observation.get("kind") in {"tool_result", "tool_error"}
        and observation.get("data", {}).get("url")
        and (
            observation.get("kind") == "tool_error"
            or observation.get("data", {}).get("content")
            or observation.get("data", {}).get("snapshot")
        )
    }
