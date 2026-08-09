import pytest

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.loop import (
    record_progress_signature,
    validate_transition,
)
from app.application.agent_runtime.policies.reasoning import (
    AgentObservationEvaluator,
    AgentReasoningPolicyCompiler,
    AgentReflectionGate,
    StateVersionConflict,
    apply_reflection_patch,
    apply_validation_outcomes,
    build_default_contract,
    failure_fingerprint,
    normalize_contract,
    resolve_run_profile,
    validate_contract,
)
from app.common.schemas.agent.execution_state import (
    AgentObservation,
    AgentState,
    NodeResult,
    ReflectionPatch,
)
from app.common.schemas.agent.planning import ExpectedObservation, TaskContract
from app.common.schemas.agent.run_policy import ReasoningPolicySnapshot, RequestedReasoningPolicy
from app.common.schemas.agent.run_result import AgentValidationIssue, AgentValidationOutcome
from app.common.schemas.agent.types import (
    AnswerMode,
    CriterionStatus,
    ExecutionMode,
    PlanExecution,
    ReasoningEffort,
    ReflectionTrigger,
    TerminalState,
)


def test_policy_defaults_and_safety_floor():
    snapshot = AgentReasoningPolicyCompiler().compile(RequestedReasoningPolicy())
    assert snapshot.effective.reasoning_effort == ReasoningEffort.balanced
    high = AgentReasoningPolicyCompiler().compile(
        RequestedReasoningPolicy(reasoning_effort="fast", execution_mode="auto_approval"),
        risk_level="high",
    )
    assert high.effective.execution_mode == ExecutionMode.request_approval
    assert len(high.adjustments) == 2


def test_standard_profile_is_fixed_and_preserves_execution_approval():
    profile = resolve_run_profile(
        AnswerMode.standard,
        RequestedReasoningPolicy(
            reasoning_effort="deep",
            max_tool_calls=None,
            reflection_enabled=True,
            execution_mode="auto_approval",
        ),
    )
    policy = profile.reasoning_policy.effective
    assert profile.answer_mode == AnswerMode.standard
    assert profile.assurance_level.value == "basic"
    assert profile.contract_mode.value == "system_minimal"
    assert policy.reasoning_effort == ReasoningEffort.fast
    assert profile.plan_execution is None
    assert policy.reflection_enabled is False
    assert policy.budgets.max_tool_calls is None
    assert policy.budgets.max_turns is None
    assert policy.execution_mode == ExecutionMode.auto_approval
    assert policy.verification_level.value == "basic"


def test_trusted_profile_uses_complete_plan_and_full_assurance():
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(
            reasoning_effort="deep",
            max_tool_calls=None,
            reflection_enabled=False,
        ),
    )
    policy = profile.reasoning_policy.effective
    assert profile.answer_mode == AnswerMode.trusted
    assert profile.assurance_level.value == "full"
    assert profile.contract_mode.value == "model"
    assert policy.reasoning_effort == ReasoningEffort.deep
    assert profile.plan_execution == PlanExecution.confirm
    assert policy.budgets.max_tool_calls is None
    assert policy.verification_level.value == "strict"


def test_removed_planning_fields_are_strictly_rejected():
    with pytest.raises(ValueError):
        RequestedReasoningPolicy(planning_strategy="direct")
    with pytest.raises(ValueError):
        RequestedReasoningPolicy(planning_strategy="adaptive")
    snapshot = AgentReasoningPolicyCompiler().compile(RequestedReasoningPolicy()).model_dump(mode="json")
    snapshot["requested"]["planning_strategy"] = "adaptive"
    with pytest.raises(ValueError):
        ReasoningPolicySnapshot.model_validate(snapshot)


@pytest.mark.parametrize(
    ("effort", "limit"),
    [("fast", 0), ("fast", 5), ("balanced", 6), ("balanced", 15)],
)
def test_policy_compiler_uses_custom_tool_call_limit(effort, limit):
    snapshot = AgentReasoningPolicyCompiler().compile(RequestedReasoningPolicy(reasoning_effort=effort, max_tool_calls=limit))
    assert snapshot.requested.max_tool_calls == limit
    assert snapshot.effective.budgets.max_tool_calls == limit
    assert snapshot.effective.budgets.max_turns >= limit + 1


@pytest.mark.parametrize(
    ("effort", "limit"),
    [("fast", 6), ("balanced", 5), ("balanced", 16), ("deep", 15), ("deep", 50)],
)
def test_policy_rejects_custom_tool_call_limit_outside_effort_range(effort, limit):
    with pytest.raises(ValueError):
        RequestedReasoningPolicy(reasoning_effort=effort, max_tool_calls=limit)


def test_contract_is_verifiable():
    contract = build_default_contract("总结证据")
    validate_contract(contract)


def test_contract_normalization_supports_simple_conversation():
    contract = normalize_contract(TaskContract(original_goal="你好"), "你好")
    validate_contract(contract)
    assert contract.deliverables == ["回复用户请求：你好"]
    assert contract.success_criteria[0].verification_method == "task_adapter"


def test_evaluation_does_not_treat_failure_as_success():
    evaluation = AgentObservationEvaluator().evaluate(
        AgentObservation(kind="tool_result", status="failed", summary="bad"),
        ExpectedObservation(kind="tool_result", success_condition="ok"),
        ["criterion-result"],
    )
    assert evaluation.outcome.value == "mismatch"
    assert not evaluation.criterion_updates


def test_reflection_policy_patch_and_versioning():
    policy = (
        AgentReasoningPolicyCompiler()
        .compile(RequestedReasoningPolicy(reflection_trigger=ReflectionTrigger.adaptive))
        .effective
    )
    assert AgentReflectionGate().should_reflect(policy, "expectation_mismatch", 0)
    state = AgentState(task_contract=build_default_contract("goal"))
    patch = ReflectionPatch(level="goal", criterion_updates={"criterion-result": CriterionStatus.satisfied})
    updated = apply_reflection_patch(state, patch, expected_version=1)
    assert updated.version == 2
    with pytest.raises(StateVersionConflict):
        apply_reflection_patch(updated, patch, expected_version=1)


def test_failure_fingerprints_are_stable():
    assert failure_fingerprint("x", {"a": 1}, "bad") == failure_fingerprint("x", {"a": 1}, "bad")
    assert failure_fingerprint("x", {"a": 1}, "bad") != failure_fingerprint("x", {"a": 2}, "bad")


def test_completion_gate_requires_criteria():
    contract = build_default_contract("goal")
    state = AgentState(task_contract=contract)
    failed = AgentValidationOutcome(
        validator="task_adapter",
        passed=False,
        blocking=True,
        issues=[AgentValidationIssue(code="missing", message="missing evidence")],
    )
    state = apply_validation_outcomes(state, [failed])
    assert AgentCompletionGate().evaluate(state, validation_outcomes=[failed]).state == TerminalState.blocked
    passed = AgentValidationOutcome(validator="task_adapter", passed=True, blocking=True)
    state = apply_validation_outcomes(state, [passed])
    assert AgentCompletionGate().evaluate(state, validation_outcomes=[passed]).state == TerminalState.completed


def test_completion_gate_waits_for_parallel_execution_approval_and_budget_barriers():
    contract = build_default_contract("goal")
    state = AgentState(task_contract=contract)
    passed = AgentValidationOutcome(validator="task_adapter", passed=True, blocking=True)
    state = apply_validation_outcomes(state, [passed])

    decision = AgentCompletionGate().evaluate(
        state,
        validation_outcomes=[passed],
        active_executions=[{"execution_id": "execution-1", "status": "active"}],
        unresolved_approvals=1,
        unmerged_budgets=1,
    )

    assert decision.state == TerminalState.continue_run
    assert decision.unmet_criteria == [
        "node-execution:execution-1",
        "approval:pending",
        "budget:unmerged",
    ]


def test_basic_completion_ignores_full_contract_but_preserves_warnings_and_blockers():
    gate = AgentCompletionGate()
    warning = AgentValidationOutcome(
        validator="artifact_reference",
        passed=True,
        blocking=False,
        warnings=["已清洗无效引用"],
    )
    assert gate.evaluate_basic(validation_outcomes=[warning]).state == TerminalState.completed_with_warnings
    blocked = AgentValidationOutcome(validator="safety", passed=False, blocking=True)
    decision = gate.evaluate_basic(validation_outcomes=[blocked])
    assert decision.state == TerminalState.blocked
    assert decision.unmet_criteria == ["validator:safety"]


def test_completion_gate_distinguishes_waiting_failure_and_warning():
    contract = build_default_contract("goal")
    state = AgentState(task_contract=contract)
    gate = AgentCompletionGate()
    failed = AgentValidationOutcome(validator="task_adapter", passed=False, blocking=True)
    assert (
        gate.evaluate(state, validation_outcomes=[failed], required_user_action="请选择范围").state
        == TerminalState.waiting_user
    )
    assert (
        gate.evaluate(state, validation_outcomes=[failed], runtime_error="database unavailable").state == TerminalState.failed
    )
    warning = AgentValidationOutcome(
        validator="task_adapter",
        passed=True,
        blocking=True,
        warnings=["low quality"],
    )
    state = apply_validation_outcomes(state, [warning])
    assert gate.evaluate(state, validation_outcomes=[warning]).state == TerminalState.completed_with_warnings


def test_completion_gate_blocks_when_mandatory_validator_is_missing():
    contract = build_default_contract("goal")
    state = AgentState(task_contract=contract)
    unrelated = AgentValidationOutcome(validator="artifact_reference", passed=True, blocking=False)

    decision = AgentCompletionGate().evaluate(state, validation_outcomes=[unrelated])

    assert decision.state == TerminalState.blocked
    assert "verification:verify-result" in decision.unmet_criteria


def test_validation_outcomes_update_only_matching_success_criteria():
    contract = build_default_contract("goal")
    contract.success_criteria.append(
        contract.success_criteria[0].model_copy(
            update={"id": "criterion-security", "verification_method": "security_validator"}
        )
    )
    state = AgentState(task_contract=contract)

    updated = apply_validation_outcomes(state, [AgentValidationOutcome(validator="task_adapter", passed=True)])

    assert updated.task_contract.success_criteria[0].status == CriterionStatus.satisfied
    assert updated.task_contract.success_criteria[1].status == CriterionStatus.pending


def test_orchestrator_rejects_shortcuts_and_unauthorized_patches():
    with pytest.raises(RuntimeError):
        validate_transition("select_action", NodeResult(next_node="completed"))
    with pytest.raises(RuntimeError):
        validate_transition("evaluate", NodeResult(next_node="update_state", state_patch={"terminal_reason": {}}))


def test_no_progress_detection():
    signatures = []
    assert not record_progress_signature(
        signatures, threshold=2, evidence_refs=[], criterion_changes={}, completed_steps=[], plan_version=1
    )
    assert record_progress_signature(
        signatures, threshold=2, evidence_refs=[], criterion_changes={}, completed_steps=[], plan_version=1
    )


def test_reflection_gate_modes_and_exhaustion():
    compiler = AgentReasoningPolicyCompiler()
    disabled = compiler.compile(RequestedReasoningPolicy(reflection_enabled=False)).effective
    assert not AgentReflectionGate().should_reflect(disabled, "tool_failed", 0)
    every = compiler.compile(RequestedReasoningPolicy(reflection_trigger="every_turn")).effective
    assert AgentReflectionGate().should_reflect(every, "ordinary", 0)
    assert not AgentReflectionGate().should_reflect(every, "ordinary", every.budgets.max_reflections)


def test_non_actionable_reflection_is_rejected():
    state = AgentState(task_contract=build_default_contract("goal"))
    with pytest.raises(ValueError, match="not actionable"):
        apply_reflection_patch(state, ReflectionPatch(level="local"), expected_version=1)
