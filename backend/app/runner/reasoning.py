import hashlib
import json
from collections.abc import Iterable
from typing import Any, ClassVar, Literal

from app.schemas.agent import (
    AgentObservation,
    AgentState,
    AnswerMode,
    AssuranceLevel,
    CompletionDecision,
    ContractMode,
    CriterionStatus,
    EffectiveReasoningPolicy,
    EffectiveSubagentPolicy,
    Evaluation,
    EvaluationOutcome,
    ExecutionMode,
    ExpectedObservation,
    PlanExecution,
    PolicyAdjustment,
    ReasoningEffort,
    ReasoningPolicySnapshot,
    ReflectionPatch,
    ReflectionTrigger,
    RequestedReasoningPolicy,
    RunBudgets,
    RunExecutionProfile,
    SubagentBudgetPolicy,
    SubagentModelRoutingPolicy,
    SuccessCriterion,
    TaskContract,
    TerminalState,
    ValidationOutcome,
    VerificationLevel,
    VerificationRequirement,
)


class StateVersionConflict(RuntimeError):
    pass


class PolicyCompiler:
    BUDGETS: ClassVar[dict[ReasoningEffort, RunBudgets]] = {
        ReasoningEffort.fast: RunBudgets(
            max_plan_depth=3,
            max_candidate_strategies=1,
            max_model_calls=12,
            max_reflections=1,
            max_replans=1,
            max_turns=8,
            max_tool_calls=5,
            verification_coverage=1,
        ),
        ReasoningEffort.balanced: RunBudgets(),
        ReasoningEffort.deep: RunBudgets(
            max_plan_depth=12,
            max_candidate_strategies=4,
            max_model_calls=48,
            max_reflections=6,
            max_replans=4,
            max_turns=20,
            max_tool_calls=None,
            verification_coverage=3,
        ),
    }

    def compile(
        self,
        requested: RequestedReasoningPolicy,
        *,
        risk_level: str = "low",
        complexity: str = "normal",
        subagent_policy: EffectiveSubagentPolicy | None = None,
    ) -> ReasoningPolicySnapshot:
        data = requested.model_dump()
        adjustments: list[PolicyAdjustment] = []
        if risk_level in {"high", "critical"}:
            self._raise(
                data,
                adjustments,
                "execution_mode",
                ExecutionMode.request_approval,
                "high_risk_requires_approval",
                "高风险任务不能自动批准受控行动。",
            )
            self._raise(
                data,
                adjustments,
                "verification_level",
                VerificationLevel.strict,
                "high_risk_strict_verification",
                "高风险任务需要严格验证。",
            )
        effort = ReasoningEffort(data["reasoning_effort"])
        budgets = self.BUDGETS[effort].model_copy(deep=True)
        if requested.max_tool_calls is not None:
            budgets.max_tool_calls = requested.max_tool_calls
            budgets.max_turns = max(budgets.max_turns, requested.max_tool_calls + 1)
        effective = EffectiveReasoningPolicy(
            **data,
            budgets=budgets,
            subagents=subagent_policy or EffectiveSubagentPolicy(),
        )
        return ReasoningPolicySnapshot(
            requested=requested, effective=effective, adjustments=adjustments
        )
    def _raise(
        self,
        data: dict[str, Any],
        adjustments: list[PolicyAdjustment],
        field: str,
        value: Any,
        rule: str,
        reason: str,
    ) -> None:
        requested = data[field]
        if requested == value:
            return
        data[field] = value
        adjustments.append(
            PolicyAdjustment(
                field=field, requested=requested, effective=value, rule=rule, reason=reason
            )
        )


def compile_subagent_policy(settings: Any) -> EffectiveSubagentPolicy:
    """Freeze deployment settings into a Run-scoped, non-escalating subagent policy."""
    enabled = bool(settings.agent_subagent_execution_enabled) and not bool(
        settings.agent_subagent_kill_switch
    )
    return EffectiveSubagentPolicy(
        enabled=enabled,
        kill_switch=bool(settings.agent_subagent_kill_switch),
        rollout_cohort=str(settings.agent_subagent_rollout_cohort),
        read_only=bool(settings.agent_subagent_read_only),
        allowed_join_policies=("required", "optional", "first_success"),
        budgets=SubagentBudgetPolicy(
            max_children_total=settings.agent_subagent_max_children_total if enabled else 0,
            max_children_per_parent=(
                settings.agent_subagent_max_children_per_parent if enabled else 0
            ),
            max_parallel_children=(
                settings.agent_subagent_max_parallel_children if enabled else 0
            ),
            max_depth=settings.agent_subagent_max_depth,
            max_parent_round_trips=settings.agent_subagent_max_parent_round_trips,
            max_wall_time_seconds=settings.agent_subagent_max_wall_time_seconds,
            max_tokens=settings.agent_subagent_max_tokens if enabled else 0,
            max_model_calls=settings.agent_subagent_max_model_calls if enabled else 0,
            max_tool_calls=settings.agent_subagent_max_tool_calls if enabled else 0,
            max_cost_usd=settings.agent_subagent_max_cost_usd if enabled else 0,
            parent_token_reserve=settings.agent_subagent_parent_token_reserve,
            parent_model_call_reserve=settings.agent_subagent_parent_model_call_reserve,
            parent_tool_call_reserve=settings.agent_subagent_parent_tool_call_reserve,
            parent_cost_reserve_usd=settings.agent_subagent_parent_cost_reserve_usd,
        ),
        model_routing=SubagentModelRoutingPolicy(
            allowed_providers=(settings.model_provider,),
            allowed_models=(settings.model_name,),
            require_same_provider=True,
            allow_lower_cost_model=True,
            max_reasoning_effort=ReasoningEffort.balanced,
        ),
    )


class RunProfileResolver:
    """Resolve product answer modes into immutable runtime facts."""

    STANDARD_VALIDATORS: ClassVar[list[str]] = ["artifact_reference"]
    TRUSTED_VALIDATORS: ClassVar[list[str]] = ["task_adapter", "artifact_reference"]

    def resolve(
        self,
        answer_mode: AnswerMode,
        requested: RequestedReasoningPolicy,
        *,
        plan_execution: PlanExecution | None = None,
        risk_level: str = "low",
        complexity: str = "normal",
        subagent_policy: EffectiveSubagentPolicy | None = None,
        subagent_mode: Literal["auto", "required"] = "auto",
    ) -> RunExecutionProfile:
        if answer_mode == AnswerMode.standard:
            effective_request = requested.model_copy(
                update={
                    "reasoning_effort": ReasoningEffort.fast,
                    "max_tool_calls": None,
                    "reflection_enabled": False,
                    "reflection_trigger": ReflectionTrigger.failure_only,
                    "verification_level": VerificationLevel.basic,
                }
            )
            contract_mode = ContractMode.system_minimal
            assurance_level = AssuranceLevel.basic
            validators = self.STANDARD_VALIDATORS
            resolved_plan_execution = None
            effective_subagent_policy = EffectiveSubagentPolicy()
        else:
            effective_request = requested.model_copy(
                update={"verification_level": VerificationLevel.strict}
            )
            contract_mode = ContractMode.model
            assurance_level = AssuranceLevel.full
            validators = self.TRUSTED_VALIDATORS
            resolved_plan_execution = plan_execution or PlanExecution.confirm
            effective_subagent_policy = subagent_policy or EffectiveSubagentPolicy()
        policy = PolicyCompiler().compile(
            effective_request,
            risk_level=risk_level,
            complexity=complexity,
            subagent_policy=effective_subagent_policy,
        )
        if answer_mode == AnswerMode.standard:
            budgets = policy.effective.budgets.model_copy(
                update={"max_turns": None, "max_tool_calls": None}
            )
            policy = policy.model_copy(
                update={
                    "effective": policy.effective.model_copy(update={"budgets": budgets})
                }
            )
        return RunExecutionProfile(
            answer_mode=answer_mode,
            contract_mode=contract_mode,
            assurance_level=assurance_level,
            reasoning_policy=policy,
            plan_execution=resolved_plan_execution,
            validators=list(validators),
            subagent_mode=subagent_mode,
        )


def build_default_contract(goal: str, *, risk_level: str = "low") -> TaskContract:
    normalized = goal.strip()
    return TaskContract(
        original_goal=normalized,
        deliverables=[normalized],
        success_criteria=[
            SuccessCriterion(
                id="criterion-result",
                description=f"完成用户目标：{normalized}",
                verification_method="task_adapter",
            )
        ],
        verification_requirements=[
            VerificationRequirement(id="verify-result", validator="task_adapter")
        ],
        prohibited_actions=["执行未注册或未授权的工具"],
        risk_level=risk_level,
    )


def normalize_contract(contract: TaskContract, goal: str) -> TaskContract:
    """Fill optional model omissions without weakening the contract boundary."""
    normalized_goal = contract.original_goal.strip() or goal.strip()
    updates: dict[str, Any] = {"original_goal": normalized_goal}
    if not contract.deliverables:
        updates["deliverables"] = [f"回复用户请求：{normalized_goal}"]
    criteria = []
    seen_ids: set[str] = set()
    for index, criterion in enumerate(contract.success_criteria, start=1):
        criterion_id = criterion.id.strip() or f"criterion-{index}"
        if criterion_id in seen_ids:
            criterion_id = f"criterion-{index}"
        seen_ids.add(criterion_id)
        criteria.append(
            criterion.model_copy(
                update={
                    "id": criterion_id,
                    "verification_method": criterion.verification_method or "task_adapter",
                }
            )
        )
    if not criteria:
        criteria = [
            SuccessCriterion(
                id="criterion-result",
                description=f"正确回应用户请求：{normalized_goal}",
                verification_method="task_adapter",
            )
        ]
    updates["success_criteria"] = criteria
    if not contract.verification_requirements:
        updates["verification_requirements"] = [
            VerificationRequirement(id="verify-result", validator="task_adapter")
        ]
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
    if len(ids) != len(set(ids)) or any(
        not item.verification_method for item in contract.success_criteria
    ):
        raise ValueError("TaskContract criterion IDs must be unique and verifiable")
    if contract.ambiguity_status != "clear" and not contract.clarification_question:
        raise ValueError("Ambiguous contract requires a clarification question")


class ObservationEvaluator:
    def evaluate(
        self,
        observation: AgentObservation,
        expected: ExpectedObservation | None,
        criterion_refs: Iterable[str] = (),
    ) -> Evaluation:
        outcome = EvaluationOutcome.inconclusive
        if observation.status == "failed":
            outcome = EvaluationOutcome.mismatch
        elif expected is None:
            outcome = EvaluationOutcome.inconclusive
        elif observation.kind in {expected.kind, "tool_result", "validator_result"}:
            missing = [field for field in expected.required_fields if field not in observation.data]
            outcome = EvaluationOutcome.partial if missing else EvaluationOutcome.matched
        criterion_updates = (
            dict.fromkeys(criterion_refs, CriterionStatus.satisfied)
            if outcome == EvaluationOutcome.matched
            else {}
        )
        return Evaluation(
            plan_node_id=observation.plan_node_id,
            outcome=outcome,
            summary=f"Observation evaluated as {outcome.value}",
            expected=expected,
            criterion_updates=criterion_updates,
        )


class ReflectionGate:
    ADAPTIVE_SIGNALS: ClassVar[frozenset[str]] = frozenset(
        {
            "tool_failed",
            "model_output_failed",
            "model_requested",
            "expectation_mismatch",
            "evidence_conflict",
            "low_confidence",
            "no_progress",
            "dependency_broken",
            "completion_gate_failed",
        }
    )

    def should_reflect(self, policy: EffectiveReasoningPolicy, signal: str, used: int) -> bool:
        if not policy.reflection_enabled or used >= policy.budgets.max_reflections:
            return False
        if policy.reflection_trigger == ReflectionTrigger.every_turn:
            return True
        if policy.reflection_trigger == ReflectionTrigger.failure_only:
            return signal in {"tool_failed", "model_output_failed", "completion_gate_failed"}
        return signal in self.ADAPTIVE_SIGNALS


def apply_reflection_patch(
    state: AgentState, patch: ReflectionPatch, *, expected_version: int
) -> AgentState:
    if state.version != expected_version:
        raise StateVersionConflict(
            f"Expected state version {expected_version}, got {state.version}"
        )
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
    updated.task_contract.verification_requirements.extend(patch.added_verification_requirements)
    if patch.terminal_intent:
        updated.terminal_intent = patch.terminal_intent
    updated.version += 1
    return updated


def apply_validation_outcomes(state: AgentState, outcomes: list[ValidationOutcome]) -> AgentState:
    updated = state.model_copy(deep=True)
    by_validator: dict[str, list[ValidationOutcome]] = {}
    for outcome in outcomes:
        by_validator.setdefault(outcome.validator, []).append(outcome)
    for criterion in updated.task_contract.success_criteria:
        matches = by_validator.get(criterion.verification_method, [])
        if any(item.passed for item in matches):
            criterion.status = CriterionStatus.satisfied
        elif any(not item.passed and item.blocking for item in matches):
            criterion.status = CriterionStatus.failed
    return updated


def failure_fingerprint(
    tool_name: str | None, tool_input: dict[str, Any], error_category: str, intent: str = ""
) -> str:
    payload = json.dumps(
        {"tool": tool_name, "input": tool_input, "error": error_category, "intent": intent},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class CompletionGate:
    def evaluate_basic(
        self,
        *,
        validation_outcomes: list[ValidationOutcome],
        required_user_action: str | None = None,
        runtime_error: str | None = None,
    ) -> CompletionDecision:
        if runtime_error:
            return CompletionDecision(state=TerminalState.failed, reason=runtime_error)
        if required_user_action:
            return CompletionDecision(
                state=TerminalState.waiting_user,
                reason="需要用户输入后才能继续。",
                required_user_action=required_user_action,
            )
        blocking = [
            outcome.validator
            for outcome in validation_outcomes
            if not outcome.passed and outcome.blocking
        ]
        warnings = list(
            dict.fromkeys(
                [
                    warning
                    for outcome in validation_outcomes
                    for warning in outcome.warnings
                ]
                + [
                    issue.message
                    for outcome in validation_outcomes
                    for issue in outcome.issues
                    if issue.severity == "warning"
                ]
            )
        )
        if blocking:
            return CompletionDecision(
                state=TerminalState.blocked,
                reason="基础保障存在阻塞问题。",
                unmet_criteria=[f"validator:{validator}" for validator in blocking],
                warnings=warnings,
            )
        return CompletionDecision(
            state=TerminalState.completed_with_warnings
            if warnings
            else TerminalState.completed,
            reason="快速回答已完成基础保障检查。",
            warnings=warnings,
        )

    def evaluate(
        self,
        state: AgentState,
        *,
        validation_outcomes: list[ValidationOutcome],
        plan: Any | None = None,
        warnings: list[str] | None = None,
        required_user_action: str | None = None,
        runtime_error: str | None = None,
        active_executions: list[Any] | None = None,
        unresolved_approvals: int = 0,
        unmerged_budgets: int = 0,
        descendant_executions: list[Any] | None = None,
        required_joins: list[Any] | None = None,
    ) -> CompletionDecision:
        combined_warnings = list(warnings or [])
        for outcome in validation_outcomes:
            combined_warnings.extend(outcome.warnings)
            combined_warnings.extend(
                issue.message for issue in outcome.issues if issue.severity == "warning"
            )
        combined_warnings = list(dict.fromkeys(combined_warnings))
        if runtime_error:
            return CompletionDecision(state=TerminalState.failed, reason=runtime_error)
        descendant_barriers = [
            item
            for item in (descendant_executions or [])
            if (
                getattr(item, "status", None)
                if not isinstance(item, dict)
                else item.get("status")
            )
            not in {
                "completed",
                "completed_with_warnings",
                "blocked",
                "failed",
                "cancelled",
            }
        ]
        blocked_joins = [
            item
            for item in (required_joins or [])
            if (
                getattr(item, "status", None)
                if not isinstance(item, dict)
                else item.get("status")
            )
            == "blocked"
        ]
        waiting_joins = [
            item
            for item in (required_joins or [])
            if (
                getattr(item, "status", None)
                if not isinstance(item, dict)
                else item.get("status")
            )
            not in {"ready", "blocked"}
        ]
        if blocked_joins:
            return CompletionDecision(
                state=TerminalState.blocked,
                reason="必需的子 Agent 汇合失败。",
                unmet_criteria=[
                    f"agent-join:{item.get('id') if isinstance(item, dict) else getattr(item, 'id', None)}"
                    for item in blocked_joins
                ],
            )
        if descendant_barriers or waiting_joins:
            return CompletionDecision(
                state=TerminalState.continue_run,
                reason="子 Agent 终态或必需汇合屏障尚未清空。",
                unmet_criteria=[
                    *[
                        f"agent-execution:{item.get('id') if isinstance(item, dict) else getattr(item, 'id', None)}"
                        for item in descendant_barriers
                    ],
                    *[
                        f"agent-join:{item.get('id') if isinstance(item, dict) else getattr(item, 'id', None)}"
                        for item in waiting_joins
                    ],
                ],
            )
        execution_barriers = [
            execution
            for execution in (active_executions or [])
            if getattr(execution, "status", None) in {"active", "waiting"}
            or (
                isinstance(execution, dict)
                and execution.get("status") in {"active", "waiting"}
            )
        ]
        if execution_barriers or unresolved_approvals or unmerged_budgets:
            return CompletionDecision(
                state=TerminalState.continue_run,
                reason="并行执行屏障尚未清空。",
                unmet_criteria=[
                    *(
                        f"node-execution:{getattr(item, 'id', None) or item.get('execution_id')}"
                        for item in execution_barriers
                    ),
                    *(["approval:pending"] if unresolved_approvals else []),
                    *(["budget:unmerged"] if unmerged_budgets else []),
                ],
            )
        if required_user_action or state.task_contract.ambiguity_status != "clear":
            return CompletionDecision(
                state=TerminalState.waiting_user,
                reason="需要用户输入后才能继续。",
                required_user_action=required_user_action
                or state.task_contract.clarification_question,
            )
        if plan is not None:
            nodes = list(getattr(plan, "nodes", []) or [])
            failed_nodes = [
                node.node_key
                for node in nodes
                if not node.optional and node.status.value in {"failed", "blocked"}
            ]
            if failed_nodes:
                return CompletionDecision(
                    state=TerminalState.blocked,
                    reason="活动计划存在失败或阻塞的必需节点。",
                    unmet_criteria=[f"plan-node:{key}" for key in failed_nodes],
                )
            unfinished_nodes = [
                node.node_key
                for node in nodes
                if not node.optional and node.status.value in {"pending", "running"}
            ]
            if unfinished_nodes:
                return CompletionDecision(
                    state=TerminalState.continue_run,
                    reason="活动计划仍有未完成的必需节点。",
                    unmet_criteria=[f"plan-node:{key}" for key in unfinished_nodes],
                )
        mandatory = [item for item in state.task_contract.success_criteria if item.mandatory]
        unmet = [item.id for item in mandatory if item.status != CriterionStatus.satisfied]
        missing_or_failed_requirements: list[str] = []
        for requirement in state.task_contract.verification_requirements:
            if not requirement.mandatory:
                continue
            matches = [
                outcome
                for outcome in validation_outcomes
                if outcome.validator == requirement.validator
                or requirement.id in outcome.requirement_ids
            ]
            if not matches or not any(outcome.passed for outcome in matches):
                missing_or_failed_requirements.append(f"verification:{requirement.id}")
        blocking_failures = [
            outcome.validator
            for outcome in validation_outcomes
            if not outcome.passed and outcome.blocking
        ]
        unmet = list(
            dict.fromkeys(
                [
                    *unmet,
                    *missing_or_failed_requirements,
                    *(f"validator:{validator}" for validator in blocking_failures),
                ]
            )
        )
        if not unmet:
            return CompletionDecision(
                state=TerminalState.completed_with_warnings
                if combined_warnings
                else TerminalState.completed,
                reason="任务契约与验证要求已满足。",
                warnings=combined_warnings,
            )
        return CompletionDecision(
            state=TerminalState.blocked,
            reason="仍有强制成功准则或验证要求未满足。",
            unmet_criteria=unmet,
            warnings=combined_warnings,
        )
