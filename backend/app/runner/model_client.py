import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List

import httpx

from app.core.config import Settings
from app.schemas.agent import FinalAnswer, Finding, PlanOutput, PlanStep, SourceReference


class ModelConfigurationError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    pass


class ModelClient(ABC):
    @abstractmethod
    async def plan(self, goal: str) -> PlanOutput:
        raise NotImplementedError

    @abstractmethod
    async def synthesize(self, goal: str, tool_outputs: List[Dict[str, Any]]) -> FinalAnswer:
        raise NotImplementedError


class MockModelClient(ModelClient):
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

    async def synthesize(self, goal: str, tool_outputs: List[Dict[str, Any]]) -> FinalAnswer:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Astra's synthesis engine. Return JSON only with keys: "
                        "summary, findings, sources, failed_sources, source_quality, "
                        "conflicts, caveats, verification_notes. "
                        "Each finding has text and source_urls. Each source has url, title, retrieved_at. "
                        "Only use the audited evidence_pack from the provided tool_outputs. "
                        "Do not use raw web content that is not in evidence_pack. "
                        "Every important finding must cite source URLs."
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
