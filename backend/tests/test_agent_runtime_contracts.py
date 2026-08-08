from dataclasses import dataclass
from typing import assert_never

import pytest

from app.application.agent_runtime.services.action_resolution import (
    ActionResolutionInput,
    ActionResolutionStage,
)
from app.application.agent_runtime.services.loop import execute_turns
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.domain.execution.contracts import (
    BlockedOutcome,
    CompletedOutcome,
    ContinueOutcome,
    ExecutionBudget,
    ExecutionContext,
    FailedOutcome,
    StageOutcome,
    WaitingOutcome,
)


def outcome_label(outcome: StageOutcome) -> str:
    match outcome:
        case ContinueOutcome():
            return "continue"
        case WaitingOutcome():
            return "waiting"
        case CompletedOutcome():
            return "completed"
        case BlockedOutcome():
            return "blocked"
        case FailedOutcome():
            return "failed"
    assert_never(outcome)


def test_stage_outcome_union_is_exhaustive():
    outcomes: list[StageOutcome] = [
        ContinueOutcome(),
        WaitingOutcome(reason="input required", waiting_state={}),
        CompletedOutcome(answer=AgentFinalAnswer(summary="done")),
        BlockedOutcome(reason="denied", error_code="PERMISSION_DENIED"),
        FailedOutcome(reason="provider unavailable", error_code="MODEL_ERROR"),
    ]
    assert [outcome_label(outcome) for outcome in outcomes] == [
        "continue",
        "waiting",
        "completed",
        "blocked",
        "failed",
    ]


def test_action_resolution_rejects_tools_outside_capability_candidates():
    resolved = ActionResolutionStage().execute(
        ActionResolutionInput(
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
    )

    assert resolved.invocation is not None
    assert resolved.rejected_observation is not None
    assert resolved.rejected_observation.kind == "tool_selection_rejected"


@dataclass
class OutcomeStage:
    outcome: StageOutcome

    async def execute(self, _context: ExecutionContext) -> StageOutcome:
        return self.outcome


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        WaitingOutcome(reason="wait", waiting_state={}),
        CompletedOutcome(answer=AgentFinalAnswer(summary="done")),
        BlockedOutcome(reason="blocked", error_code="BLOCKED"),
        FailedOutcome(reason="failed", error_code="FAILED"),
    ],
)
async def test_orchestrator_routes_every_terminal_outcome(outcome):
    context = ExecutionContext(
        run_id="run-1",
        task_id="task-1",
        goal="test",
        budget=ExecutionBudget(2, 1, 0, 0),
    )

    assert await execute_turns(OutcomeStage(outcome), context) is outcome


@pytest.mark.asyncio
async def test_orchestrator_blocks_when_turn_budget_is_exhausted():
    context = ExecutionContext(
        run_id="run-1",
        task_id="task-1",
        goal="test",
        budget=ExecutionBudget(2, 1, 0, 0),
    )

    outcome = await execute_turns(OutcomeStage(ContinueOutcome()), context)

    assert isinstance(outcome, BlockedOutcome)
    assert outcome.error_code == "TURN_BUDGET_EXHAUSTED"
