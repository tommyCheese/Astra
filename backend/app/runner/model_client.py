import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List

import httpx

from app.core.config import Settings
from app.schemas.agent import (
    AgentDecision,
    AgentReflection,
    FinalAnswer,
    Finding,
    MemoryRecord,
    PlanOutput,
    PlanStep,
    SourceReference,
    TaskContract,
)


class ModelConfigurationError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    pass


class ModelClient(ABC):
    @abstractmethod
    async def contract(self, goal: str) -> TaskContract:
        raise NotImplementedError

    @abstractmethod
    async def plan(self, goal: str) -> PlanOutput:
        raise NotImplementedError

    @abstractmethod
    async def synthesize(self, goal: str, tool_outputs: List[Dict[str, Any]]) -> FinalAnswer:
        raise NotImplementedError

    @abstractmethod
    async def decide(self, goal: str, context: Dict[str, Any]) -> AgentDecision:
        raise NotImplementedError

    @abstractmethod
    async def reflect(self, goal: str, context: Dict[str, Any]) -> AgentReflection:
        raise NotImplementedError

    @abstractmethod
    async def finalize(self, goal: str, context: Dict[str, Any]) -> FinalAnswer:
        raise NotImplementedError

    @abstractmethod
    async def extract_memory_candidates(
        self,
        goal: str,
        context: Dict[str, Any],
    ) -> List[MemoryRecord]:
        raise NotImplementedError


class MockModelClient(ModelClient):
    async def contract(self, goal: str) -> TaskContract:
        from app.runner.reasoning import build_default_contract
        return build_default_contract(goal)

    async def plan(self, goal: str) -> PlanOutput:
        return PlanOutput(
            steps=[
                PlanStep(
                    title="搜索候选来源",
                    intent=f"围绕用户目标搜索相关来源：{goal}",
                    required_tools=["web_search"],
                    success_criteria=["返回至少一个候选来源"],
                ),
                PlanStep(
                    title="筛选和去重来源",
                    intent="筛选搜索候选来源并去除重复 URL",
                    required_tools=[],
                    success_criteria=["记录筛选和去重数量"],
                ),
                PlanStep(
                    title="抓取来源内容",
                    intent="抓取候选来源并提取可用于回答的文本证据",
                    required_tools=["web_fetch"],
                    success_criteria=["至少一个来源被成功抓取"],
                ),
                PlanStep(
                    title="构造证据包",
                    intent="基于已审计工具调用构造 Evidence Pack",
                    required_tools=[],
                    success_criteria=["Evidence Pack 包含来源质量和失败来源"],
                ),
                PlanStep(
                    title="综合答案",
                    intent="基于已记录的工具输出生成带来源的答案",
                    required_tools=[],
                    success_criteria=["答案包含摘要、发现、来源和限制说明"],
                ),
                PlanStep(
                    title="验证证据",
                    intent="检查来源是否足以支撑最终答案",
                    required_tools=[],
                    success_criteria=["输出验证备注"],
                ),
            ],
            required_tools=["web_search", "web_fetch"],
            success_criteria=["最终答案包含来源和验证备注"],
            risk_level="low",
        )

    async def synthesize(self, goal: str, tool_outputs: List[Dict[str, Any]]) -> FinalAnswer:
        sources: List[SourceReference] = []
        findings: List[Finding] = []
        caveats: List[str] = []
        failed_sources: List[Dict[str, Any]] = []
        source_quality: List[Dict[str, Any]] = []

        evidence_pack = next(
            (output.get("evidence_pack") for output in tool_outputs if output.get("evidence_pack")),
            None,
        )
        if evidence_pack:
            for source in evidence_pack.get("fetched_sources", []):
                url = source.get("url")
                if not url:
                    continue
                sources.append(
                    SourceReference(
                        url=url,
                        title=source.get("title"),
                        retrieved_at=source.get("retrieved_at"),
                    )
                )
                content = source.get("content", "")
                excerpt = content[:260].strip() or "该来源没有返回可读正文。"
                findings.append(Finding(text=excerpt, source_urls=[url]))
                source_quality.append(
                    {
                        "url": url,
                        "quality_score": source.get("quality_score"),
                        "extraction_strategy": source.get("extraction_strategy"),
                        "warnings": source.get("warnings", []),
                    }
                )
            failed_sources = evidence_pack.get("failed_sources", [])
            caveats.extend(evidence_pack.get("warnings", []))
        else:
            for output in tool_outputs:
                if "candidates" in output:
                    continue
                url = output.get("url")
                content = output.get("content", "")
                if url:
                    sources.append(
                        SourceReference(
                            url=url,
                            title=output.get("title"),
                            retrieved_at=output.get("retrieved_at"),
                        )
                    )
                    excerpt = content[:220].strip() or "该来源没有返回可读正文。"
                    findings.append(Finding(text=excerpt, source_urls=[url]))
                    source_quality.append(
                        {
                            "url": url,
                            "quality_score": output.get("quality_score"),
                            "extraction_strategy": output.get("extraction_strategy"),
                            "warnings": output.get("warnings", []),
                        }
                    )

        if not findings:
            caveats.append("未能获取足够的来源内容，结果只能报告证据不足。")

        return FinalAnswer(
            summary=f"已围绕目标完成 Web 数据查询：{goal}",
            findings=findings,
            sources=sources,
            failed_sources=failed_sources,
            source_quality=source_quality,
            conflicts=[],
            caveats=caveats,
            verification_notes=[
                "答案仅基于本次 run 中记录的 ToolCall、Artifact 和验证结果生成。"
            ],
        )

    async def decide(self, goal: str, context: Dict[str, Any]) -> AgentDecision:
        observations = context.get("observations", [])
        fetched_urls = {
            observation.get("data", {}).get("url")
            for observation in observations
            if observation.get("kind") == "tool_result"
            and observation.get("data", {}).get("tool_name") == "web_fetch"
        }
        search_observation = next(
            (
                observation
                for observation in observations
                if observation.get("kind") == "tool_result"
                and observation.get("data", {}).get("tool_name") == "web_search"
            ),
            None,
        )
        if search_observation is None:
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary="先搜索候选来源，建立可抓取的证据候选集。",
                tool_name="web_search",
                tool_input={"query": goal},
                expected_observation="返回候选来源和搜索 warning。",
                stop_condition="获得候选来源后抓取正文。",
            )
        candidates = search_observation.get("data", {}).get("candidates", [])
        for candidate in candidates:
            url = candidate.get("url")
            if url and url not in fetched_urls:
                return AgentDecision(
                    decision_type="call_tool",
                    reasoning_summary="抓取候选来源正文，用于构造证据包和最终回答。",
                    tool_name="web_fetch",
                    tool_input={
                        "url": url,
                        "query": goal,
                        "snippet": candidate.get("snippet", ""),
                        "crawler_plan": context.get("crawler_plan", {}),
                    },
                    expected_observation="返回正文、质量评分、抓取策略和 warning。",
                    stop_condition="抓取足够来源后进行综合验证。",
                )
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="已有搜索和抓取观察，可以基于证据包生成最终回复。",
            expected_observation="最终答案包含来源、限制和验证备注。",
        )

    async def reflect(self, goal: str, context: Dict[str, Any]) -> AgentReflection:
        last_observation = context.get("last_observation") or {}
        return AgentReflection(
            trigger=last_observation.get("status", "unknown"),
            summary="工具结果未满足预期，尝试调整策略或带限制结束。",
            next_action="retry_or_finalize_with_caveats",
            retry=context.get("retry_count", 0) < 1,
        )

    async def finalize(self, goal: str, context: Dict[str, Any]) -> FinalAnswer:
        return await self.synthesize(goal, [{"evidence_pack": context.get("evidence_pack", {})}])

    async def extract_memory_candidates(
        self,
        goal: str,
        context: Dict[str, Any],
    ) -> List[MemoryRecord]:
        evidence_pack = context.get("evidence_pack") or {}
        fetched_sources = evidence_pack.get("fetched_sources", [])
        if not fetched_sources:
            return []
        return [
            MemoryRecord(
                scope="run",
                kind="source_summary",
                content=f"本次任务围绕「{goal}」抓取了 {len(fetched_sources)} 个来源。",
                structured_data={"source_count": len(fetched_sources)},
                provenance={
                    "run_id": context.get("run_id"),
                    "artifact_id": evidence_pack.get("artifact_id"),
                },
                confidence=0.8,
            )
        ]


class OpenAICompatibleModelClient(ModelClient):
    def __init__(self, settings: Settings):
        if not settings.model_api_key:
            raise ModelConfigurationError("MODEL_API_KEY is required for real model providers")
        self.settings = settings

    async def plan(self, goal: str) -> PlanOutput:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Astra's planner. Return JSON only with keys: "
                        "steps, required_tools, success_criteria, risk_level. "
                        "Each step has title, intent, required_tools, success_criteria."
                    ),
                },
                {"role": "user", "content": goal},
            ]
        )
        try:
            return PlanOutput.model_validate(payload)
        except Exception as exc:
            raise ModelOutputError(f"Invalid plan output: {exc}") from exc

    async def contract(self, goal: str) -> TaskContract:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Create an audit-safe task contract. Return JSON only with keys: original_goal, "
                        "deliverables, constraints, prohibited_actions, assumptions, success_criteria, "
                        "verification_requirements, risk_level, ambiguity_status, clarification_question. "
                        "Each success criterion needs a stable id, description, mandatory, and verification_method."
                    ),
                },
                {"role": "user", "content": goal},
            ]
        )
        try:
            return TaskContract.model_validate(payload)
        except Exception as exc:
            raise ModelOutputError(f"Invalid task contract output: {exc}") from exc

    async def synthesize(self, goal: str, tool_outputs: List[Dict[str, Any]]) -> FinalAnswer:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Astra's general answer engine. Return JSON only with keys: "
                        "summary, findings, sources, failed_sources, source_quality, "
                        "conflicts, caveats, verification_notes. "
                        "Each finding has text and source_urls. Each source has url, title, retrieved_at. "
                        "When audited tool evidence exists, ground claims in it and cite source URLs. "
                        "When no tool was needed, answer from general model knowledge, leave sources empty, "
                        "and state limitations for time-sensitive or uncertain claims."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"goal": goal, "tool_outputs": tool_outputs},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        try:
            return FinalAnswer.model_validate(payload)
        except Exception as exc:
            raise ModelOutputError(f"Invalid final answer output: {exc}") from exc

    async def decide(self, goal: str, context: Dict[str, Any]) -> AgentDecision:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Astra's general Agent loop controller. Return JSON only. "
                        "Required keys: decision_type, reasoning_summary. "
                        "Allowed decision_type values: call_tool, reflect, replan, finalize, ask_user, blocked. "
                        "Choose among the tools in context.tool_manifests only when external or current evidence is needed. "
                        "For stable general knowledge, explanation, writing, or conversation, finalize without tools. "
                        "Use web_search for current or externally verifiable information and web_fetch only for a URL from context. "
                        "For call_tool include tool_name and tool_input. "
                        "Do not include hidden chain-of-thought; reasoning_summary must be concise and user-auditable."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"goal": goal, "context": context}, ensure_ascii=False),
                },
            ]
        )
        try:
            return AgentDecision.model_validate(payload)
        except Exception as exc:
            raise ModelOutputError(f"Invalid agent decision output: {exc}") from exc

    async def reflect(self, goal: str, context: Dict[str, Any]) -> AgentReflection:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Astra's reflector. Return JSON only with keys: "
                        "trigger, summary, next_action, retry, revised_tool_input. "
                        "Use concise audit-safe summaries."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"goal": goal, "context": context}, ensure_ascii=False),
                },
            ]
        )
        try:
            return AgentReflection.model_validate(payload)
        except Exception as exc:
            raise ModelOutputError(f"Invalid reflection output: {exc}") from exc

    async def finalize(self, goal: str, context: Dict[str, Any]) -> FinalAnswer:
        return await self.synthesize(goal, [{"evidence_pack": context.get("evidence_pack", {})}])

    async def extract_memory_candidates(
        self,
        goal: str,
        context: Dict[str, Any],
    ) -> List[MemoryRecord]:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract durable memory candidates. Return JSON only with key memories. "
                        "Each memory has scope, kind, content, structured_data, provenance, confidence. "
                        "Only include memories with provenance."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"goal": goal, "context": context}, ensure_ascii=False),
                },
            ]
        )
        try:
            return [MemoryRecord.model_validate(item) for item in payload.get("memories", [])]
        except Exception as exc:
            raise ModelOutputError(f"Invalid memory extraction output: {exc}") from exc

    async def _chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        url = self.settings.model_base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.model_api_key}"},
                json={
                    "model": self.settings.model_name,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelOutputError("Model returned non-JSON content") from exc


def build_model_client(settings: Settings) -> ModelClient:
    if settings.model_provider == "mock":
        return MockModelClient()
    return OpenAICompatibleModelClient(settings)
