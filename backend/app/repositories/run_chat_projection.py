"""Build the chronological chat projection for a persisted Run."""

from __future__ import annotations

from typing import Any

from app.db.models.runs import RunRecord


def build_chat_messages(run: RunRecord) -> list[dict[str, Any]]:
    messages = [_user_request_message(run)]
    timeline = [_turn_timeline_entry(run, turn) for turn in run.turns]
    timeline.extend(
        entry for event in run.events if (entry := _resume_timeline_entry(run, event)) is not None
    )
    messages.extend(entry[2] for entry in sorted(timeline, key=lambda entry: (entry[0], entry[1])))
    _append_terminal_message(run, messages)
    _append_waiting_message(run, messages)
    return messages


def _user_request_message(run: RunRecord) -> dict[str, Any]:
    goal = str(run.model_policy.get("conversation_goal", run.task.description))
    command = (
        "/subagent" if (run.execution_profile or {}).get("subagent_mode") == "required" else ""
    )
    visible_goal = f"{command} {goal}" if command else goal
    return {
        "id": f"{run.id}-user",
        "role": "user",
        "content": visible_goal,
        "status": "completed",
        "metadata": {"task_id": run.task_id, **({"command": command} if command else {})},
    }


def _turn_timeline_entry(run: RunRecord, turn: object) -> tuple[Any, int, dict[str, Any]]:
    role, content = _turn_role_and_content(run, turn)
    return (
        turn.created_at,
        0,
        {
            "id": turn.id,
            "role": role,
            "content": content,
            "status": turn.status,
            "metadata": _turn_metadata(turn),
        },
    )


def _turn_role_and_content(run: RunRecord, turn: object) -> tuple[str, str]:
    projectors = {
        "call_tool": _tool_turn,
        "reflect": _reflection_turn,
        "finalize": _final_turn,
        "ask_user": _question_turn,
    }
    return projectors.get(turn.decision_type, _assistant_turn)(run, turn)


def _tool_turn(run: RunRecord, turn: object) -> tuple[str, str]:
    return "tool", turn.reasoning_summary


def _reflection_turn(run: RunRecord, turn: object) -> tuple[str, str]:
    return "reflection", (turn.reflection or {}).get("summary", turn.reasoning_summary)


def _final_turn(run: RunRecord, turn: object) -> tuple[str, str]:
    return "assistant", (run.result or {}).get("summary") or turn.reasoning_summary


def _question_turn(run: RunRecord, turn: object) -> tuple[str, str]:
    requested_input = str((turn.decision or {}).get("expected_observation") or "").strip()
    return "assistant", requested_input or "请告诉我你希望我完成的具体任务或问题。"


def _assistant_turn(run: RunRecord, turn: object) -> tuple[str, str]:
    return "assistant", turn.reasoning_summary


def _turn_metadata(turn: object) -> dict[str, Any]:
    fields = (
        "turn_index",
        "decision_type",
        "selected_tool",
        "observation",
        "reflection",
        "memory_reads",
        "memory_writes",
    )
    return {field: getattr(turn, field) for field in fields}


def _resume_timeline_entry(run: RunRecord, event: object) -> tuple[Any, int, dict[str, Any]] | None:
    if event.type != "run.resumed":
        return None
    observation = (event.payload or {}).get("observation") or {}
    content = observation.get("summary")
    if (
        observation.get("kind") != "user_response"
        or not isinstance(content, str)
        or not content.strip()
    ):
        return None
    return (
        event.created_at,
        1,
        {
            "id": f"{run.id}-resume-{event.id}",
            "role": "user",
            "content": content.strip(),
            "status": "completed",
            "metadata": {"event_id": event.id, "kind": "user_response"},
        },
    )


def _append_terminal_message(run: RunRecord, messages: list[dict[str, Any]]) -> None:
    terminal_statuses = {"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}
    has_answer = any(message["role"] == "assistant" for message in messages[1:])
    if run.status not in terminal_statuses or not run.result or has_answer:
        return
    terminal_failure = run.status in {"blocked", "failed", "cancelled"}
    messages.append(
        {
            "id": f"{run.id}-terminal" if terminal_failure else f"{run.id}-answer",
            "role": "assistant",
            "content": run.result.get("summary") or run.summary or "任务已完成。",
            "status": run.status,
            "metadata": {"error": run.result.get("error")},
        }
    )


def _append_waiting_message(run: RunRecord, messages: list[dict[str, Any]]) -> None:
    if (
        run.status != "waiting_user"
        or not run.waiting_state
        or not run.waiting_state.get("request")
    ):
        return
    messages.append(
        {
            "id": f"{run.id}-waiting",
            "role": "assistant",
            "content": str(run.waiting_state["request"]),
            "status": "waiting_user",
            "metadata": {"waiting_state": run.waiting_state},
        }
    )
