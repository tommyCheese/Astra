from app.application.agent_runtime.contracts import (
    BlockLoop,
    CompleteLoop,
    ContinueLoop,
    FailLoop,
    LoopOutcome,
    WaitLoop,
)
from app.application.agent_runtime.services.decisions.action_resolution import resolve_action
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation


def test_loop_outcome_union_is_canonical():
    outcomes: list[LoopOutcome] = [
        ContinueLoop(),
        WaitLoop(reason="input required"),
        CompleteLoop(answer="done"),
        BlockLoop(reason="denied", error_code="PERMISSION_DENIED"),
        FailLoop(reason="provider unavailable", error_code="MODEL_ERROR"),
    ]
    assert [outcome.kind for outcome in outcomes] == [
        "continue",
        "waiting",
        "completed",
        "blocked",
        "failed",
    ]


def test_action_resolution_rejects_tools_outside_capability_candidates():
    resolved = resolve_action(
        run_id="run-1",
        turn_index=2,
        decision=AgentDecision(
            decision_type="call_tool",
            tool_name="unsafe_tool",
            reasoning_summary="test",
        ),
        tool_selection={"candidate_names": ["catalog_search"]},
        has_canonical_plan=False,
        active_plan_node_id=None,
        active_plan_node_key=None,
        active_node_execution_id=None,
    )

    assert isinstance(resolved, AgentObservation)
    assert resolved.kind == "tool_selection_rejected"
