import json
import time

from app.interfaces.ag_ui.encoder import encode_sse
from app.interfaces.ag_ui.projector import AgUiProjectionState, AgUiRunProjection


def projector() -> AgUiRunProjection:
    return AgUiRunProjection(AgUiProjectionState("thread", "protocol", "internal"))


def source(source_id: int, event_type: str, **payload):
    return {"id": source_id, "type": event_type, "payload": payload}


def test_first_content_projection_overhead_and_event_size_budget() -> None:
    projection = projector()
    started = time.perf_counter()
    events = []
    for index in range(1, 1_001):
        events.extend(projection.project(source(index, "answer.delta", delta="x")))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5
    content_events = [event for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"]
    assert len(content_events) == 1_000
    assert max(len(encode_sse(event).encode()) for event in content_events) < 2_048


def test_activity_projection_uses_deltas_and_bounded_snapshot_fallback() -> None:
    projection = projector()
    event_types = []
    for index in range(1, 21):
        emitted = projection.project(
            source(index, "plan.node.updated", plan_id="plan-1", plan_node_id=f"node-{index}", status="running")
        )
        event_types.extend(event["type"] for event in emitted)
    emitted = projection.project(
        source(21, "plan.node.updated", plan_id="plan-1", plan_node_id="node-1", status="completed")
    )
    event_types.extend(event["type"] for event in emitted)
    assert event_types[0] == "ACTIVITY_SNAPSHOT"
    assert set(event_types) <= {"ACTIVITY_SNAPSHOT", "ACTIVITY_DELTA"}
    assert "ACTIVITY_DELTA" in event_types


def test_native_visible_answer_and_ag_ui_result_converge() -> None:
    native_events = [
        source(1, "answer.delta", delta="Hello "),
        source(2, "answer.delta", delta="world"),
        source(3, "answer.completed", content="Hello world"),
        source(4, "run.status_changed", status="completed"),
    ]
    native_visible = "".join(str(event["payload"].get("delta", "")) for event in native_events)
    projection = projector()
    public = [event for native in native_events for event in projection.project(native)]
    final = next(event for event in public if event["type"] == "RUN_FINISHED")
    assert native_visible == final["result"]["content"] == "Hello world"
    assert len(json.dumps(public, ensure_ascii=False).encode()) < 16_000


def test_projector_cache_loss_and_delayed_terminal_recover_deterministically() -> None:
    lost = projector()
    assert [event["type"] for event in lost.initial_snapshots()] == ["STATE_SNAPSHOT", "MESSAGES_SNAPSHOT"]
    lost.project(source(10, "answer.delta", delta="late"))
    terminal = lost.project(source(20, "run.status_changed", status="completed"))
    assert terminal[-1]["type"] == "RUN_FINISHED"
    assert lost.project(source(20, "run.status_changed", status="completed")) == []
