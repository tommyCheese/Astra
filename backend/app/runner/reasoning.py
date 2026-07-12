import hashlib
import json
from typing import Any, Dict, Iterable, Optional

from app.schemas.agent import (
    AgentObservation,
    AgentState,
    CompletionDecision,
    CriterionStatus,
    EffectiveReasoningPolicy,
    Evaluation,
    EvaluationOutcome,
    ExecutionMode,
    ExpectedObservation,
    PlanGraph,
    PlanGraphStep,
    PlanningStrategy,
    PolicyAdjustment,
    ReasoningEffort,
    ReasoningPolicySnapshot,
    ReflectionPatch,
    ReflectionTrigger,
    RequestedReasoningPolicy,
    RunBudgets,
    SuccessCriterion,
    TaskContract,
    TerminalState,
    VerificationLevel,
    VerificationRequirement,
)


class StateVersionConflict(RuntimeError):
    pass


class PolicyCompiler:
    BUDGETS = {
        ReasoningEffort.fast: RunBudgets(max_plan_depth=3, max_candidate_strategies=1, max_model_calls=12, max_reflections=1, max_replans=1, max_turns=8, max_tool_calls=5, verification_coverage=1),
        ReasoningEffort.balanced: RunBudgets(),
        ReasoningEffort.deep: RunBudgets(max_plan_depth=12, max_candidate_strategies=4, max_model_calls=48, max_reflections=6, max_replans=4, max_turns=20, max_tool_calls=16, verification_coverage=3),
    }

    def compile(self, requested: RequestedReasoningPolicy, *, risk_level: str = "low", complexity: str = "normal") -> ReasoningPolicySnapshot:
        data = requested.model_dump()
        adjustments: list[PolicyAdjustment] = []
        if risk_level in {"high", "critical"}:
            self._raise(data, adjustments, "planning_strategy", PlanningStrategy.plan_first, "high_risk_minimum_planning", "高风险任务必须先完成完整规划。")
            self._raise(data, adjustments, "execution_mode", ExecutionMode.request_approval, "high_risk_requires_approval", "高风险任务不能自动批准受控行动。")
            self._raise(data, adjustments, "verification_level", VerificationLevel.strict, "high_risk_strict_verification", "高风险任务需要严格验证。")
        elif complexity == "high" and data["planning_strategy"] == PlanningStrategy.direct:
            self._raise(data, adjustments, "planning_strategy", PlanningStrategy.adaptive, "complexity_minimum_planning", "复杂任务至少使用自适应规划。")
        effort = ReasoningEffort(data["reasoning_effort"])
        effective = EffectiveReasoningPolicy(**data, budgets=self.BUDGETS[effort].model_copy(deep=True))
        return ReasoningPolicySnapshot(requested=requested, effective=effective, adjustments=adjustments)

    def _raise(self, data: Dict[str, Any], adjustments: list[PolicyAdjustment], field: str, value: Any, rule: str, reason: str) -> None:
        requested = data[field]
        if requested == value:
            return
        data[field] = value
        adjustments.append(PolicyAdjustment(field=field, requested=requested, effective=value, rule=rule, reason=reason))


def build_default_contract(goal: str, *, risk_level: str = "low") -> TaskContract:
    normalized = goal.strip()
    return TaskContract(
        original_goal=normalized,
        deliverables=[normalized],
        success_criteria=[SuccessCriterion(id="criterion-result", description=f"完成用户目标：{normalized}", verification_method="task_adapter")],
        verification_requirements=[VerificationRequirement(id="verify-result", validator="task_adapter")],
        prohibited_actions=["执行未注册或未授权的工具"],
        risk_level=risk_level,
    )


def normalize_contract(contract: TaskContract, goal: str) -> TaskContract:
    """Fill optional model omissions without weakening the contract boundary."""
    normalized_goal = contract.original_goal.strip() or goal.strip()
    updates: Dict[str, Any] = {"original_goal": normalized_goal}
    if not contract.deliverables:
        updates["deliverables"] = [f"回复用户请求：{normalized_goal}"]
    criteria = []
    seen_ids: set[str] = set()
    for index, criterion in enumerate(contract.success_criteria, start=1):
        criterion_id = criterion.id.strip() or f"criterion-{index}"
        if criterion_id in seen_ids:
            criterion_id = f"criterion-{index}"
        seen_ids.add(criterion_id)
        criteria.append(criterion.model_copy(update={
            "id": criterion_id,
            "verification_method": criterion.verification_method or "task_adapter",
        }))
    if not criteria:
        criteria = [SuccessCriterion(id="criterion-result", description=f"正确回应用户请求：{normalized_goal}", verification_method="task_adapter")]
    updates["success_criteria"] = criteria
    if not contract.verification_requirements:
        updates["verification_requirements"] = [VerificationRequirement(id="verify-result", validator="task_adapter")]
    if contract.ambiguity_status != "clear" and not contract.clarification_question:
        updates["ambiguity_status"] = "clear"
    return contract.model_copy(update=updates)


def validate_contract(contract: TaskContract) -> None:
    if not contract.original_goal.strip():
        raise ValueError("TaskContract original goal is empty")
    if not contract.deliverables:
        raise ValueError("TaskContract requires at least one deliverable")
    if not contract.success_criteria:
        raise ValueError("TaskContract requires success criteria")
    ids = [item.id for item in contract.success_criteria]
    if len(ids) != len(set(ids)) or any(not item.verification_method for item in contract.success_criteria):
        raise ValueError("TaskContract criterion IDs must be unique and verifiable")
    if contract.ambiguity_status != "clear" and not contract.clarification_question:
        raise ValueError("Ambiguous contract requires a clarification question")


def build_plan_graph(contract: TaskContract, strategy: PlanningStrategy, steps: Optional[Iterable[Dict[str, Any]]] = None) -> PlanGraph:
    criterion_ids = [item.id for item in contract.success_criteria]
    source = list(steps or [])
    if not source:
        source = [{"title": "执行任务", "intent": contract.original_goal, "required_tools": [], "success_criteria": criterion_ids}]
    graph_steps = []
    prior: list[str] = []
    for index, item in enumerate(source, start=1):
        step_id = f"step-{index}"
        dependencies = [] if strategy == PlanningStrategy.direct else prior[-1:]
        graph_steps.append(PlanGraphStep(id=step_id, title=item.get("title", step_id), intent=item.get("intent", ""), depends_on=dependencies, required_capabilities=item.get("required_tools", []), success_criteria_refs=item.get("success_criteria_refs", criterion_ids), expected_outcome=ExpectedObservation(kind="step_result", success_condition="step completed with accepted evidence")))
        prior.append(step_id)
    return PlanGraph(strategy=strategy, steps=graph_steps)


class ObservationEvaluator:
    def evaluate(self, observation: AgentObservation, expected: Optional[ExpectedObservation], criterion_refs: Iterable[str] = ()) -> Evaluation:
        outcome = EvaluationOutcome.inconclusive
        if observation.status == "failed":
            outcome = EvaluationOutcome.mismatch
        elif expected is None:
            outcome = EvaluationOutcome.inconclusive
        elif observation.kind in {expected.kind, "tool_result", "validator_result"}:
            missing = [field for field in expected.required_fields if field not in observation.data]
            outcome = EvaluationOutcome.partial if missing else EvaluationOutcome.matched
        criterion_updates = {item: CriterionStatus.satisfied for item in criterion_refs} if outcome == EvaluationOutcome.matched else {}
        return Evaluation(outcome=outcome, summary=f"Observation evaluated as {outcome.value}", expected=expected, criterion_updates=criterion_updates)


class ReflectionGate:
    ADAPTIVE_SIGNALS = {"tool_failed", "expectation_mismatch", "evidence_conflict", "low_confidence", "no_progress", "dependency_broken", "completion_gate_failed"}

    def should_reflect(self, policy: EffectiveReasoningPolicy, signal: str, used: int) -> bool:
        if not policy.reflection_enabled or used >= policy.budgets.max_reflections:
            return False
        if policy.reflection_trigger == ReflectionTrigger.every_turn:
            return True
        if policy.reflection_trigger == ReflectionTrigger.failure_only:
            return signal in {"tool_failed", "completion_gate_failed"}
        return signal in self.ADAPTIVE_SIGNALS


def apply_reflection_patch(state: AgentState, patch: ReflectionPatch, *, expected_version: int) -> AgentState:
    if state.version != expected_version:
        raise StateVersionConflict(f"Expected state version {expected_version}, got {state.version}")
    if not patch.actionable():
        raise ValueError("Reflection patch is not actionable")
    updated = state.model_copy(deep=True)
    for assumption in updated.task_contract.assumptions:
        if assumption.id in patch.invalidated_assumption_ids:
            assumption.valid = False
    updated.accepted_facts.extend(patch.fact_updates)
    for criterion in updated.task_contract.success_criteria:
        if criterion.id in patch.criterion_updates:
            criterion.status = patch.criterion_updates[criterion.id]
    if patch.replacement_plan:
        if patch.replacement_plan.version <= updated.plan.version:
            raise ValueError("Replacement plan version must increase")
        updated.plan = patch.replacement_plan
    updated.task_contract.verification_requirements.extend(patch.added_verification_requirements)
    if patch.terminal_intent:
        updated.terminal_intent = patch.terminal_intent
    updated.version += 1
    return updated


def failure_fingerprint(tool_name: Optional[str], tool_input: Dict[str, Any], error_category: str, intent: str = "") -> str:
    payload = json.dumps({"tool": tool_name, "input": tool_input, "error": error_category, "intent": intent}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class CompletionGate:
    def evaluate(self, state: AgentState, *, validator_passed: bool, warnings: Optional[list[str]] = None, required_user_action: Optional[str] = None, runtime_error: Optional[str] = None) -> CompletionDecision:
        warnings = warnings or []
        if runtime_error:
            return CompletionDecision(state=TerminalState.failed, reason=runtime_error)
        if required_user_action or state.task_contract.ambiguity_status != "clear":
            return CompletionDecision(state=TerminalState.waiting_user, reason="需要用户输入后才能继续。", required_user_action=required_user_action or state.task_contract.clarification_question)
        mandatory = [item for item in state.task_contract.success_criteria if item.mandatory]
        unmet = [item.id for item in mandatory if item.status != CriterionStatus.satisfied]
        if not unmet and validator_passed:
            return CompletionDecision(state=TerminalState.completed_with_warnings if warnings else TerminalState.completed, reason="任务契约与验证要求已满足。", warnings=warnings)
        if warnings and validator_passed and all(not item.mandatory for item in state.task_contract.success_criteria if item.id in unmet):
            return CompletionDecision(state=TerminalState.completed_with_warnings, reason="已生成允许的部分结果。", unmet_criteria=unmet, warnings=warnings)
        return CompletionDecision(state=TerminalState.blocked, reason="仍有强制成功准则未满足。", unmet_criteria=unmet, warnings=warnings)
