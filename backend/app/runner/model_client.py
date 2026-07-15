import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.agent_profile import AgentProfile, ModelOperation, load_agent_profile
from app.agent_profile.prompts import PromptComposer
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

logger = logging.getLogger("astra.model")
AnswerDeltaCallback = Callable[[str], Awaitable[None]]
StreamFieldCallbacks = dict[str, AnswerDeltaCallback]


class ModelConfigurationError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    pass


class ModelClient(ABC):
    def bind_agent_profile(self, profile: AgentProfile) -> None:
        """Bind the immutable Profile selected for the current Run."""
        return None

    @abstractmethod
    async def contract(self, goal: str) -> TaskContract:
        raise NotImplementedError

    @abstractmethod
    async def plan(self, goal: str) -> PlanOutput:
        raise NotImplementedError

    @abstractmethod
    async def synthesize(
        self,
        goal: str,
        tool_outputs: list[dict[str, Any]],
        *,
        on_delta: AnswerDeltaCallback | None = None,
    ) -> FinalAnswer:
        raise NotImplementedError

    @abstractmethod
    async def decide(self, goal: str, context: dict[str, Any]) -> AgentDecision:
        raise NotImplementedError

    async def decide_with_answer(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        on_delta: AnswerDeltaCallback | None = None,
        on_reasoning_delta: AnswerDeltaCallback | None = None,
    ) -> tuple[AgentDecision, FinalAnswer | None]:
        decision = await self.decide(goal, context)
        if on_reasoning_delta:
            await on_reasoning_delta(decision.reasoning_summary)
            await on_reasoning_delta("\1")
        return decision, None

    @abstractmethod
    async def reflect(self, goal: str, context: dict[str, Any]) -> AgentReflection:
        raise NotImplementedError

    @abstractmethod
    async def finalize(
        self, goal: str, context: dict[str, Any], *, on_delta: AnswerDeltaCallback | None = None
    ) -> FinalAnswer:
        raise NotImplementedError

    @abstractmethod
    async def extract_memory_candidates(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[MemoryRecord]:
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

    async def synthesize(
        self,
        goal: str,
        tool_outputs: list[dict[str, Any]],
        *,
        on_delta: AnswerDeltaCallback | None = None,
    ) -> FinalAnswer:
        sources: list[SourceReference] = []
        findings: list[Finding] = []
        caveats: list[str] = []
        failed_sources: list[dict[str, Any]] = []
        source_quality: list[dict[str, Any]] = []
        artifact_ids = [
            str(artifact["id"])
            for output in tool_outputs
            for artifact in output.get("artifacts", [])
            if isinstance(artifact, dict) and isinstance(artifact.get("id"), str)
        ]

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
            if artifact_ids:
                findings.append(
                    Finding(
                        text="工具已生成可用于查看结果的输出。",
                        artifact_ids=list(dict.fromkeys(artifact_ids)),
                    )
                )
            else:
                caveats.append("未能获取足够的来源内容，结果只能报告证据不足。")
        elif artifact_ids:
            findings[0] = findings[0].model_copy(
                update={"artifact_ids": list(dict.fromkeys(artifact_ids))}
            )

        answer = FinalAnswer(
            summary=f"已围绕目标完成 Web 数据查询：{goal}",
            findings=findings,
            sources=sources,
            failed_sources=failed_sources,
            source_quality=source_quality,
            conflicts=[],
            caveats=caveats,
            verification_notes=["答案仅基于本次 run 中记录的 ToolCall、Artifact 和验证结果生成。"],
        )
        if on_delta:
            await on_delta(answer.summary)
        return answer

    async def decide(self, goal: str, context: dict[str, Any]) -> AgentDecision:
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

    async def reflect(self, goal: str, context: dict[str, Any]) -> AgentReflection:
        last_observation = context.get("last_observation") or {}
        return AgentReflection(
            trigger=last_observation.get("status", "unknown"),
            summary="工具结果未满足预期，尝试调整策略或带限制结束。",
            next_action="retry_or_finalize_with_caveats",
            retry=context.get("retry_count", 0) < 1,
        )

    async def finalize(
        self, goal: str, context: dict[str, Any], *, on_delta: AnswerDeltaCallback | None = None
    ) -> FinalAnswer:
        return await self.synthesize(
            goal, [{"evidence_pack": context.get("evidence_pack", {})}], on_delta=on_delta
        )

    async def extract_memory_candidates(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[MemoryRecord]:
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
        if not settings.model_api_key and settings.model_provider != "compatible":
            raise ModelConfigurationError("MODEL_API_KEY is required for real model providers")
        self.settings = settings
        self.usage_recorder = None
        self.agent_profile = load_agent_profile()
        self.prompt_composer = PromptComposer(self.agent_profile)

    def bind_agent_profile(self, profile: AgentProfile) -> None:
        self.agent_profile = profile
        self.prompt_composer = PromptComposer(profile)

    async def plan(self, goal: str) -> PlanOutput:
        operation = ModelOperation.PLAN
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": self.prompt_composer.compose(
                        operation,
                        "You are the planner. Return JSON only with keys: "
                        "steps, required_tools, success_criteria, risk_level. "
                        "Each step has title, intent, required_tools, success_criteria.",
                    ),
                },
                {"role": "user", "content": self.prompt_composer.user_request(goal)},
            ],
            operation=operation,
        )
        try:
            return PlanOutput.model_validate(normalize_plan_payload(payload))
        except Exception as exc:
            raise ModelOutputError(f"Invalid plan output: {exc}") from exc

    async def contract(self, goal: str) -> TaskContract:
        operation = ModelOperation.CONTRACT
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": self.prompt_composer.compose(
                        operation,
                        "Create an audit-safe task contract. Return JSON only with keys: original_goal, "
                        "deliverables, constraints, prohibited_actions, assumptions, success_criteria, "
                        "verification_requirements, risk_level, ambiguity_status, clarification_question. "
                        "Each success criterion needs a stable id, description, mandatory, and verification_method.",
                    ),
                },
                {"role": "user", "content": self.prompt_composer.user_request(goal)},
            ],
            operation=operation,
        )
        try:
            return TaskContract.model_validate(normalize_contract_payload(payload, goal))
        except Exception as exc:
            raise ModelOutputError(f"Invalid task contract output: {exc}") from exc

    async def synthesize(
        self,
        goal: str,
        tool_outputs: list[dict[str, Any]],
        *,
        on_delta: AnswerDeltaCallback | None = None,
    ) -> FinalAnswer:
        operation = ModelOperation.SYNTHESIS
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": self.prompt_composer.compose(
                        operation,
                        "You are the general answer engine. Return JSON only with keys: "
                        "summary, findings, sources, failed_sources, source_quality, "
                        "conflicts, caveats, verification_notes. "
                        "Each finding has text, source_urls, and artifact_ids. Each source has url, title, retrieved_at. "
                        "artifact_ids may only contain Artifact IDs that appear in tool_outputs and directly support that finding; "
                        "never invent an ID, and use an empty list when no Artifact supports the finding. "
                        "When audited tool evidence exists, ground claims in it and cite source URLs. "
                        "When no tool was needed, answer from general model knowledge, leave sources empty, "
                        "and state limitations for time-sensitive or uncertain claims.",
                    ),
                },
                {
                    "role": "user",
                    "content": self.prompt_composer.runtime_context(
                        goal, tool_outputs=tool_outputs
                    ),
                },
            ],
            operation=operation,
            stream_field="summary",
            on_field_delta=on_delta,
        )
        try:
            return FinalAnswer.model_validate(normalize_final_answer_payload(payload))
        except Exception as exc:
            raise ModelOutputError(f"Invalid final answer output: {exc}") from exc

    async def decide(self, goal: str, context: dict[str, Any]) -> AgentDecision:
        operation = ModelOperation.DECISION
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": self.prompt_composer.compose(
                        operation,
                        "You are the general Agent loop controller. Return JSON only. "
                        "Required keys: decision_type, reasoning_summary. "
                        "Allowed decision_type values: call_tool, complete_node, reflect, replan, finalize, ask_user, blocked. "
                        "Choose among the tools in context.tool_manifests only when external or current evidence is needed. "
                        "For stable general knowledge, explanation, writing, or conversation, finalize without tools. "
                        "Select tools only from context.tool_manifests and follow each manifest's description, schema, capabilities, and permissions. "
                        "For call_tool include tool_name and tool_input. "
                        "Do not include hidden chain-of-thought; reasoning_summary must be concise and user-auditable.",
                    ),
                },
                {
                    "role": "user",
                    "content": self.prompt_composer.runtime_context(goal, context=context),
                },
            ],
            operation=operation,
        )
        try:
            return AgentDecision.model_validate(payload)
        except Exception as exc:
            raise ModelOutputError(f"Invalid agent decision output: {exc}") from exc

    async def decide_with_answer(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        on_delta: AnswerDeltaCallback | None = None,
        on_reasoning_delta: AnswerDeltaCallback | None = None,
    ) -> tuple[AgentDecision, FinalAnswer | None]:
        operation = ModelOperation.DECISION_WITH_ANSWER
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": self.prompt_composer.compose(
                        operation,
                        "You are the general Agent controller and answer engine. Return one JSON object. "
                        "Always include decision_type and reasoning_summary. Allowed decision_type values: "
                        "call_tool, complete_node, reflect, replan, finalize, ask_user, blocked. Work only on "
                        "context.active_node when it is present. Use complete_node after its expected outcome is "
                        "satisfied and include node_result fields required by its expected_outcome; use finalize "
                        "only when context.active_node is null and the plan has no "
                        "unfinished required node. Use tools only for current, "
                        "external, or otherwise unverifiable information. For stable knowledge, explanation, "
                        "writing, and conversation, choose finalize and also include final_answer with keys: "
                        "summary, findings, sources, failed_sources, source_quality, conflicts, caveats, "
                        "verification_notes. Put final_answer immediately after reasoning_summary. "
                        "Each finding must contain text, source_urls, and artifact_ids. artifact_ids may only "
                        "reference Artifact IDs present in the supplied context that directly support the finding; "
                        "never invent IDs, and use an empty list when there is no supporting Artifact. "
                        "The summary must contain the complete user-facing answer, not an introduction or preview; "
                        "use findings only for optional supporting details. "
                        "For call_tool include tool_name and tool_input and omit final_answer. For complete_node "
                        "omit final_answer. "
                        "Do not expose hidden chain-of-thought; reasoning_summary must be concise.",
                    ),
                },
                {
                    "role": "user",
                    "content": self.prompt_composer.runtime_context(goal, context=context),
                },
            ],
            operation=operation,
            stream_callbacks={
                field: callback
                for field, callback in {
                    "reasoning_summary": on_reasoning_delta,
                    "summary": on_delta,
                }.items()
                if callback is not None
            },
        )
        try:
            decision = AgentDecision.model_validate(payload)
            raw_answer = payload.get("final_answer")
            answer = (
                FinalAnswer.model_validate(normalize_final_answer_payload(raw_answer))
                if decision.decision_type == "finalize" and isinstance(raw_answer, dict)
                else None
            )
            return decision, answer
        except Exception as exc:
            raise ModelOutputError(f"Invalid combined decision output: {exc}") from exc

    async def reflect(self, goal: str, context: dict[str, Any]) -> AgentReflection:
        operation = ModelOperation.REFLECTION
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": self.prompt_composer.compose(
                        operation,
                        "You are the reflector. Return JSON only with keys: "
                        "trigger, summary, next_action, retry, revised_tool_input, and optional patch. "
                        "patch may contain level, invalidated_assumption_ids, fact_updates, "
                        "criterion_updates, plan_patch, added_verification_requirements, "
                        "or terminal_intent. Only include changes justified by the supplied context. "
                        "Use concise audit-safe summaries.",
                    ),
                },
                {
                    "role": "user",
                    "content": self.prompt_composer.runtime_context(goal, context=context),
                },
            ],
            operation=operation,
        )
        try:
            return AgentReflection.model_validate(normalize_reflection_payload(payload))
        except Exception as exc:
            raise ModelOutputError(f"Invalid reflection output: {exc}") from exc

    async def finalize(
        self, goal: str, context: dict[str, Any], *, on_delta: AnswerDeltaCallback | None = None
    ) -> FinalAnswer:
        return await self.synthesize(
            goal, [{"evidence_pack": context.get("evidence_pack", {})}], on_delta=on_delta
        )

    async def extract_memory_candidates(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[MemoryRecord]:
        operation = ModelOperation.MEMORY
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": self.prompt_composer.compose(
                        operation,
                        "Extract durable memory candidates. Return JSON only with key memories. "
                        "Each memory has scope, kind, content, structured_data, provenance, confidence. "
                        "Only include memories with provenance.",
                    ),
                },
                {
                    "role": "user",
                    "content": self.prompt_composer.runtime_context(goal, context=context),
                },
            ],
            operation=operation,
        )
        try:
            return [
                MemoryRecord.model_validate(normalized)
                for item in payload.get("memories", [])
                if isinstance(item, dict)
                and (normalized := normalize_memory_payload(item)) is not None
            ]
        except Exception as exc:
            raise ModelOutputError(f"Invalid memory extraction output: {exc}") from exc

    async def _chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        operation: ModelOperation,
        attempt: int = 0,
        stream_field: str | None = None,
        on_field_delta: AnswerDeltaCallback | None = None,
        stream_callbacks: StreamFieldCallbacks | None = None,
    ) -> dict[str, Any]:
        url = self.settings.model_base_url.rstrip("/") + "/chat/completions"
        started = time.perf_counter()
        invocation_id = None
        if self.usage_recorder is not None:
            invocation_id = await self.usage_recorder.start(
                provider=self.settings.model_provider,
                model=self.settings.model_name,
                operation=operation.value,
                attempt=attempt + 1,
            )
        usage: dict[str, Any] | None = None
        request_id: str | None = None
        logger.info(
            "model.request.start operation=%s provider=%s model=%s endpoint=%s messages=%s",
            operation,
            self.settings.model_provider,
            self.settings.model_name,
            url,
            len(messages),
        )
        async with (
            httpx.AsyncClient(timeout=60) as client,
            client.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {self.settings.model_api_key}"},
                json={
                    "model": self.settings.model_name,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            ) as response,
        ):
            request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "model.request.http_error operation=%s status=%s duration_ms=%.1f",
                    operation,
                    response.status_code,
                    (time.perf_counter() - started) * 1000,
                )
                if self.usage_recorder is not None:
                    await self.usage_recorder.finish(
                        invocation_id,
                        status="failed",
                        duration_ms=round((time.perf_counter() - started) * 1000),
                        request_id=request_id,
                        error=exc,
                    )
                raise ModelOutputError(
                    f"Model endpoint returned HTTP {response.status_code}"
                ) from exc
            if "text/event-stream" in response.headers.get("content-type", ""):
                chunks: list[str] = []
                callbacks = dict(stream_callbacks or {})
                if stream_field and on_field_delta:
                    callbacks[stream_field] = on_field_delta
                streamed_values = dict.fromkeys(callbacks, "")
                completed_fields: set[str] = set()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        delta = event["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    if delta:
                        chunks.append(delta)
                        if callbacks:
                            streamed_content = "".join(chunks)
                            for field, callback in callbacks.items():
                                current_value = extract_partial_json_string(streamed_content, field)
                                previous_value = streamed_values[field]
                                if len(current_value) > len(previous_value):
                                    await callback(current_value[len(previous_value) :])
                                    streamed_values[field] = current_value
                                if field not in completed_fields and json_string_field_complete(
                                    streamed_content, field
                                ):
                                    await callback("\1")
                                    completed_fields.add(field)
                content = "".join(chunks)
                chunk_count = len(chunks)
            else:
                try:
                    body = json.loads((await response.aread()).decode())
                    if isinstance(body.get("usage"), dict):
                        usage = body["usage"]
                    content = body["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError) as exc:
                    if self.usage_recorder is not None:
                        await self.usage_recorder.finish(
                            invocation_id,
                            status="failed",
                            duration_ms=round((time.perf_counter() - started) * 1000),
                            request_id=request_id,
                            usage=usage,
                            error=exc,
                        )
                    raise ModelOutputError(
                        "Model endpoint returned an unsupported response shape"
                    ) from exc
                chunk_count = 1
        logger.info(
            "model.request.complete operation=%s status=%s chunks=%s content_chars=%s duration_ms=%.1f",
            operation,
            response.status_code,
            chunk_count,
            len(content),
            (time.perf_counter() - started) * 1000,
        )
        content = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
        if fenced:
            content = fenced.group(1)
        try:
            payload = parse_json_object(content)
            if self.usage_recorder is not None:
                await self.usage_recorder.finish(
                    invocation_id,
                    status="succeeded",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    request_id=request_id,
                    usage=usage,
                )
            return payload
        except (json.JSONDecodeError, ValueError) as exc:
            if self.usage_recorder is not None:
                await self.usage_recorder.finish(
                    invocation_id,
                    status="failed",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    request_id=request_id,
                    usage=usage,
                    error=exc,
                )
            if attempt == 0:
                logger.warning("model.response.retry operation=%s reason=non_json", operation)
                return await self._chat_json(
                    [
                        *messages,
                        {
                            "role": "user",
                            "content": "Your previous response was not valid JSON. Return only one valid JSON object matching the requested schema, with no prose or markdown.",
                        },
                    ],
                    attempt=1,
                    operation=operation,
                    stream_field=stream_field,
                    on_field_delta=on_field_delta,
                    stream_callbacks=stream_callbacks,
                )
            raise ModelOutputError("Model returned non-JSON content") from exc


def build_model_client(settings: Settings) -> ModelClient:
    if settings.model_provider == "mock":
        return MockModelClient()
    return OpenAICompatibleModelClient(settings)


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        if start < 0:
            raise
        payload, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(payload, dict):
        raise ValueError("Model JSON root must be an object")
    return payload


def normalize_reflection_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["trigger"] = str(payload.get("trigger") or "adaptive")
    normalized["summary"] = str(payload.get("summary") or "已检查当前结果。")
    normalized["next_action"] = str(payload.get("next_action") or "continue")
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        normalized["patch"] = None
        return normalized
    clean_patch = dict(patch)
    clean_patch["level"] = str(patch.get("level") or payload.get("level") or "local")
    for field in ("invalidated_assumption_ids",):
        if not isinstance(clean_patch.get(field), list):
            clean_patch[field] = []
    if not isinstance(clean_patch.get("criterion_updates"), dict):
        clean_patch["criterion_updates"] = {}
    terminal_intent = clean_patch.get("terminal_intent")
    if terminal_intent is not None and not isinstance(terminal_intent, str):
        clean_patch["terminal_intent"] = json.dumps(terminal_intent, ensure_ascii=False)
    facts = []
    for index, fact in enumerate(clean_patch.get("fact_updates") or []):
        if not isinstance(fact, dict):
            continue
        statement = fact.get("statement") or fact.get("add")
        if not statement:
            continue
        facts.append(
            {
                "id": str(fact.get("id") or f"reflection-fact-{index + 1}"),
                "statement": str(statement),
                "provenance": fact.get("provenance")
                if isinstance(fact.get("provenance"), dict)
                else {"source": "model_reflection"},
                "confidence": fact.get("confidence", 0.5),
                "conflicts_with": fact.get("conflicts_with")
                if isinstance(fact.get("conflicts_with"), list)
                else [],
            }
        )
    clean_patch["fact_updates"] = facts
    requirements = []
    for index, requirement in enumerate(clean_patch.get("added_verification_requirements") or []):
        if isinstance(requirement, str):
            requirements.append(
                {"id": f"reflection-validator-{index + 1}", "validator": requirement}
            )
        elif isinstance(requirement, dict) and requirement.get("validator"):
            requirements.append(
                {
                    **requirement,
                    "id": str(requirement.get("id") or f"reflection-validator-{index + 1}"),
                }
            )
    clean_patch["added_verification_requirements"] = requirements
    normalized["patch"] = clean_patch
    return normalized


def normalize_memory_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload.get("content"):
        return None
    normalized = dict(payload)
    normalized["scope"] = str(payload.get("scope") or "run")
    normalized["kind"] = str(payload.get("kind") or "fact")
    if not isinstance(payload.get("structured_data"), dict):
        normalized["structured_data"] = {}
    if not isinstance(payload.get("provenance"), dict):
        normalized["provenance"] = {"source": str(payload.get("provenance") or "model")}
    try:
        normalized["confidence"] = min(1.0, max(0.0, float(payload.get("confidence", 0.5))))
    except (TypeError, ValueError):
        normalized["confidence"] = 0.5
    return normalized


def extract_partial_json_string(content: str, field: str) -> str:
    """Return the safely decoded portion of a JSON string field before the object is complete."""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', content)
    if not match:
        return ""
    index = match.end()
    decoded: list[str] = []
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(content):
        char = content[index]
        if char == '"':
            break
        if char != "\\":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= len(content):
            break
        escaped = content[index + 1]
        if escaped == "u":
            if index + 6 > len(content):
                break
            codepoint = content[index + 2 : index + 6]
            if not re.fullmatch(r"[0-9a-fA-F]{4}", codepoint):
                break
            decoded.append(chr(int(codepoint, 16)))
            index += 6
            continue
        if escaped not in escapes:
            break
        decoded.append(escapes[escaped])
        index += 2
    return "".join(decoded)


def json_string_field_complete(content: str, field: str) -> bool:
    """Return whether a streamed JSON string field has received its closing quote."""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', content)
    if not match:
        return False
    escaped = False
    for char in content[match.end() :]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return True
    return False


def normalize_contract_payload(payload: dict[str, Any], goal: str) -> dict[str, Any]:
    reported_goal = str(payload.get("original_goal") or "").strip()
    if reported_goal and normalize_goal_text(reported_goal) != normalize_goal_text(goal):
        logger.warning(
            "model.contract.goal_mismatch expected_chars=%s reported_chars=%s fallback=default",
            len(goal),
            len(reported_goal),
        )
        return {
            "original_goal": goal.strip(),
            "deliverables": [f"回复用户请求：{goal.strip()}"],
            "constraints": [],
            "prohibited_actions": ["执行未注册或未授权的工具"],
            "assumptions": [],
            "success_criteria": [
                {
                    "id": "criterion-result",
                    "description": f"正确回应用户请求：{goal.strip()}",
                    "mandatory": True,
                    "verification_method": "task_adapter",
                }
            ],
            "verification_requirements": [{"id": "verify-result", "validator": "task_adapter"}],
            "risk_level": "low",
            "ambiguity_status": "clear",
            "clarification_question": None,
        }
    normalized = dict(payload)
    normalized["original_goal"] = goal.strip()
    for field in ("deliverables", "constraints", "prohibited_actions"):
        value = normalized.get(field, [])
        normalized[field] = value if isinstance(value, list) else [str(value)]
    assumptions = normalized.get("assumptions") or []
    normalized["assumptions"] = [
        item if isinstance(item, dict) else {"id": f"assumption-{index}", "statement": str(item)}
        for index, item in enumerate(
            assumptions if isinstance(assumptions, list) else [assumptions], start=1
        )
    ]
    for index, item in enumerate(normalized["assumptions"], start=1):
        item["id"] = str(item.get("id") or f"assumption-{index}")
        item["statement"] = str(item.get("statement") or item.get("description") or "未声明的假设")
    criteria = normalized.get("success_criteria") or []
    normalized["success_criteria"] = [
        item
        if isinstance(item, dict)
        else {
            "id": f"criterion-{index}",
            "description": str(item),
            "verification_method": "task_adapter",
        }
        for index, item in enumerate(
            criteria if isinstance(criteria, list) else [criteria], start=1
        )
    ]
    for index, item in enumerate(normalized["success_criteria"], start=1):
        item["id"] = str(item.get("id") or f"criterion-{index}")
        item["description"] = str(
            item.get("description") or item.get("criterion") or f"正确回应用户请求：{goal}"
        )
        item["verification_method"] = str(item.get("verification_method") or "task_adapter")
    requirements = normalized.get("verification_requirements") or []
    normalized["verification_requirements"] = [
        item
        if isinstance(item, dict)
        else {"id": f"verify-{index}", "validator": str(item) or "task_adapter"}
        for index, item in enumerate(
            requirements if isinstance(requirements, list) else [requirements], start=1
        )
    ]
    for index, item in enumerate(normalized["verification_requirements"], start=1):
        item["id"] = str(item.get("id") or f"verify-{index}")
        item["validator"] = str(item.get("validator") or "task_adapter")
    ambiguity = str(normalized.get("ambiguity_status") or "clear").lower()
    normalized["ambiguity_status"] = ambiguity if ambiguity in {"clear", "ambiguous"} else "clear"
    if normalized["ambiguity_status"] == "clear":
        normalized["clarification_question"] = None
    return normalized


def normalize_goal_text(value: str) -> str:
    return "".join(value.lower().split()).strip("。！？!?.,，")


def normalize_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for field in ("required_tools", "success_criteria"):
        value = normalized.get(field) or []
        normalized[field] = value if isinstance(value, list) else [str(value)]
    steps = normalized.get("steps") or []
    normalized["steps"] = [
        item if isinstance(item, dict) else {"title": str(item), "intent": str(item)}
        for item in (steps if isinstance(steps, list) else [steps])
    ]
    for index, item in enumerate(normalized["steps"], start=1):
        item.setdefault("title", f"步骤 {index}")
        item.setdefault("intent", item["title"])
        for field in ("required_tools", "success_criteria"):
            value = item.get(field) or []
            item[field] = value if isinstance(value, list) else [str(value)]
    return normalized


def normalize_final_answer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["summary"] = str(normalized.get("summary") or "已完成回复。")
    findings = normalized.get("findings") or []
    normalized["findings"] = [
        item if isinstance(item, dict) else {"text": str(item), "source_urls": []}
        for item in (findings if isinstance(findings, list) else [findings])
    ]
    for item in normalized["findings"]:
        item["text"] = str(item.get("text") or item.get("finding") or "")
        urls = item.get("source_urls") or []
        item["source_urls"] = urls if isinstance(urls, list) else [str(urls)]
        artifact_ids = item.get("artifact_ids") or []
        item["artifact_ids"] = (
            [str(artifact_id) for artifact_id in artifact_ids if isinstance(artifact_id, str)]
            if isinstance(artifact_ids, list)
            else []
        )
    sources = normalized.get("sources") or []
    normalized["sources"] = [
        item if isinstance(item, dict) else {"url": str(item)}
        for item in (sources if isinstance(sources, list) else [sources])
    ]
    for field in (
        "failed_sources",
        "source_quality",
        "conflicts",
        "caveats",
        "verification_notes",
        "memory_references",
    ):
        value = normalized.get(field) or []
        normalized[field] = value if isinstance(value, list) else [value]
    normalized["failed_sources"] = [
        item for item in normalized["failed_sources"] if isinstance(item, dict)
    ]
    normalized["source_quality"] = [
        item for item in normalized["source_quality"] if isinstance(item, dict)
    ]
    normalized["conflicts"] = [item for item in normalized["conflicts"] if isinstance(item, dict)]
    normalized["memory_references"] = [
        item for item in normalized["memory_references"] if isinstance(item, dict)
    ]
    normalized["caveats"] = [str(item) for item in normalized["caveats"]]
    normalized["verification_notes"] = [str(item) for item in normalized["verification_notes"]]
    return normalized
