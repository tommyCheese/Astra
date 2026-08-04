"""Project internal Run activity into the public conversation process feed."""

from app.db.models.permissions import ToolCallRecord
from app.db.models.runs import AgentTurnRecord, RunRecord


def build_public_process(run: RunRecord) -> list[dict]:
    calls_by_id = {call.id: call for call in run.tool_calls}
    included_call_ids: set[str] = set()
    items = [
        item
        for turn in sorted(run.turns, key=lambda item: item.turn_index)
        for item in _turn_process_items(turn, calls_by_id, included_call_ids)
    ]
    items.extend(
        _tool_process_item(call)
        for call in run.tool_calls
        if call.id not in included_call_ids
    )
    items.extend(_verification_process_items(run))
    return items


def _turn_process_items(
    turn: AgentTurnRecord,
    calls_by_id: dict[str, ToolCallRecord],
    included_call_ids: set[str],
) -> list[dict]:
    items: list[dict] = []
    detail = _turn_detail(turn)
    if detail:
        is_reflection = turn.decision_type == "reflect"
        items.append(
            {
                "kind": "reflection" if is_reflection else "reasoning",
                "title": "反思" if is_reflection else "思考",
                "detail": str(detail)[:4000],
                "status": _public_status(turn.status),
            }
        )
    call = calls_by_id.get(turn.tool_call_id or "")
    if call:
        included_call_ids.add(call.id)
        items.append(_tool_process_item(call))
    return items


def _turn_detail(turn: AgentTurnRecord):
    reflection = (turn.reflection or {}).get("summary")
    return reflection if turn.decision_type == "reflect" and reflection else turn.reasoning_summary


def _tool_process_item(call: ToolCallRecord) -> dict:
    return {"kind": "tool", "title": call.tool_name, "status": _public_status(call.status)}


def _verification_process_items(run: RunRecord) -> list[dict]:
    run_result = run.result or {}
    report = run_result.get("verification_report") or {}
    notes = dict.fromkeys(
        [*(run_result.get("verification_notes") or []), *(report.get("notes") or [])]
    )
    return [
        {"kind": "verification", "title": "验证", "detail": str(note)[:4000]}
        for note in notes
        if note
    ]


def _public_status(status: str) -> str:
    return status if status in {"failed", "cancelled"} else "completed"
