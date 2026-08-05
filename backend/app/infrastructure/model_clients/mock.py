import logging
from typing import Any

from app.common.schemas.agent.execution_state import AgentDecision, AgentReflection
from app.common.schemas.agent.planning import (
    ExpectedObservation,
    PlanDraft,
    PlanNodeDraft,
    TaskContract,
)
from app.common.schemas.agent.run_result import (
    AgentAnswerFinding,
    AgentFinalAnswer,
    AgentRunMemoryCandidate,
)
from app.infrastructure.model_clients.contracts import (
    AnswerDeltaCallback,
    ModelClient,
    ModelOutputError,
)
from app.infrastructure.model_clients.mock_support import (
    infer_mock_capabilities,
    mock_fetch_decision,
    mock_search_decision,
    mock_terminal_decision,
    parse_mock_planning_goal,
    summarize_mock_evidence,
)

logger = logging.getLogger("astra.model")


class MockModelClient(ModelClient):
    def bind_skills(self, skills: list[dict[str, Any]]) -> None:
        self.skill_blocks = list(skills)

    async def generate_context_checkpoint(self, prompt: str):
        # The mock intentionally has no semantic summarizer. Runtime policy may
        # exercise the deterministic emergency path without Provider features.
        raise ModelOutputError("Mock model has no semantic checkpoint generator")

    async def contract(self, goal: str) -> TaskContract:
        from app.application.agent_runtime.policies.reasoning import build_default_contract

        return build_default_contract(goal)

    async def plan(
        self,
        goal: str,
        *,
        contract: TaskContract,
    ) -> PlanDraft:
        criterion_ids = [item.id for item in contract.success_criteria]
        public_goal, planning_goal = parse_mock_planning_goal(goal)
        task_capabilities = infer_mock_capabilities(planning_goal)
        definitions = [
            {
                "title": "分析目标与约束",
                "intent": f"明确用户目标、交付物和成功条件：{public_goal}",
                "required_capabilities": [],
                "depends_on": [],
            },
            {
                "title": "完成目标所需工作",
                "intent": "根据节点需求和当前可用能力完成主要交付物，不预先指定实现工具。",
                "required_capabilities": task_capabilities,
                "depends_on": ["step-1"],
            },
            {
                "title": "验证并交付结果",
                "intent": "依据成功条件检查结果，说明证据、限制和未满足项。",
                "required_capabilities": [],
                "depends_on": ["step-2"],
            },
        ]
        return PlanDraft(
            nodes=[
                PlanNodeDraft(
                    node_key=f"step-{index}",
                    title=item["title"],
                    intent=item["intent"],
                    depends_on=item["depends_on"],
                    required_capabilities=item["required_capabilities"],
                    success_criteria_refs=criterion_ids,
                    expected_outcome=ExpectedObservation(
                        kind="step_result",
                        success_condition="step completed with accepted evidence",
                    ),
                )
                for index, item in enumerate(definitions, start=1)
            ],
        )

    async def synthesize(
        self,
        goal: str,
        tool_outputs: list[dict[str, Any]],
        *,
        on_delta: AnswerDeltaCallback | None = None,
    ) -> AgentFinalAnswer:
        evidence = summarize_mock_evidence(tool_outputs)
        artifact_ids = [
            str(artifact["id"])
            for output in tool_outputs
            for artifact in output.get("artifacts", [])
            if isinstance(artifact, dict) and isinstance(artifact.get("id"), str)
        ]

        if not evidence.findings:
            if artifact_ids:
                evidence.findings.append(
                    AgentAnswerFinding(
                        text="工具已生成可用于查看结果的输出。",
                        artifact_ids=list(dict.fromkeys(artifact_ids)),
                    )
                )
            else:
                evidence.caveats.append("未能获取足够的来源内容，结果只能报告证据不足。")
        elif artifact_ids:
            evidence.findings[0] = evidence.findings[0].model_copy(
                update={"artifact_ids": list(dict.fromkeys(artifact_ids))}
            )

        answer = AgentFinalAnswer(
            summary=f"已围绕目标完成 Web 数据查询：{goal}",
            findings=evidence.findings,
            sources=evidence.sources,
            failed_sources=evidence.failed_sources,
            source_quality=evidence.source_quality,
            conflicts=[],
            caveats=evidence.caveats,
            verification_notes=["答案仅基于本次 run 中记录的 ToolCall、Artifact 和验证结果生成。"],
        )
        if on_delta:
            await on_delta(answer.summary)
        return answer

    async def decide(self, goal: str, context: dict[str, Any]) -> AgentDecision:
        decision = (
            mock_terminal_decision(context)
            or mock_search_decision(goal, context)
            or mock_fetch_decision(goal, context)
        )
        if decision is not None:
            return decision
        if context.get("active_node") is not None:
            return AgentDecision(
                decision_type="blocked",
                reasoning_summary="当前节点仍有任务能力需求，但没有可安全执行的候选行动。",
                expected_observation="需要启用匹配的工具能力或调整任务约束。",
            )
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="已有观察足以生成最终回复。",
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
    ) -> AgentFinalAnswer:
        return await self.synthesize(
            goal, [{"evidence_pack": context.get("evidence_pack", {})}], on_delta=on_delta
        )

    async def extract_memory_candidates(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[AgentRunMemoryCandidate]:
        evidence_pack = context.get("evidence_pack") or {}
        fetched_sources = evidence_pack.get("fetched_sources", [])
        if not fetched_sources:
            return []
        return [
            AgentRunMemoryCandidate(
                scope="run",
                kind="episodic_experience",
                memory_key=(f"run:{context.get('run_id') or 'unknown'}:source-summary"),
                content=f"本次任务围绕「{goal}」抓取了 {len(fetched_sources)} 个来源。",
                structured_data={"source_count": len(fetched_sources)},
                provenance={
                    "run_id": context.get("run_id"),
                    "artifact_id": evidence_pack.get("artifact_id"),
                },
                confidence=0.8,
            )
        ]
