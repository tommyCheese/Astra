import json

import pytest

from app.interfaces.ag_ui.delta import bounded_patch, escape_pointer, json_patch
from app.interfaces.ag_ui.projector import AgUiProjectionState, AgUiRunProjection
from app.interfaces.ag_ui.sanitization import MAX_TOOL_CHARS, safe_tool_arguments, sanitize_public
from app.interfaces.ag_ui.schemas import validate_public_event


def projection() -> AgUiRunProjection:
    return AgUiRunProjection(AgUiProjectionState("thread", "protocol", "internal"))


def source(source_id: int, event_type: str, **payload):
    return {"id": source_id, "type": event_type, "payload": payload}


def event_types(events):
    return [event["type"] for event in events]


def test_sanitizer_removes_nested_secrets_paths_traces_and_unsafe_urls() -> None:
    sanitized = sanitize_public(
        {
            "apiKey": "secret",
            "nested": {"continuation_token": "hidden", "password": "hidden", "ok": "visible"},
            "workspace_path": "/Users/alice/private",
            "message": "read /home/alice/secret.txt",
            "traceback": "stack",
            "url": "file:///etc/passwd",
            "content_url": "/api/artifacts/safe/content",
            "huge": "x" * (MAX_TOOL_CHARS + 20),
        }
    )
    assert "apiKey" not in sanitized
    assert sanitized["nested"] == {"ok": "visible"}
    assert "workspace_path" not in sanitized and "traceback" not in sanitized and "url" not in sanitized
    assert sanitized["message"] == "[private path removed]"
    assert sanitized["content_url"] == "/api/artifacts/safe/content"
    assert len(sanitized["huge"]) == MAX_TOOL_CHARS


def test_json_patch_escapes_paths_and_falls_back_when_patch_is_too_large() -> None:
    assert escape_pointer("a~/b") == "a~0~1b"
    assert json_patch({"a/b": 1}, {"a/b": 2}) == [{"op": "replace", "path": "/a~1b", "value": 2}]
    assert bounded_patch({}, {"large": "x" * 100}, ratio=0.01) is None


def test_reasoning_summary_is_separate_bounded_and_hidden_reasoning_is_suppressed() -> None:
    projector = projection()
    assert projector.project(source(1, "provider.reasoning.delta", secret="chain of thought")) == []
    events = projector.project(source(2, "reasoning.summary.delta", turn_index=1, delta="安全摘要"))
    events += projector.project(source(3, "reasoning.summary.completed", turn_index=1, summary="安全摘要"))
    assert event_types(events) == [
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_END",
    ]
    assert events[2]["delta"] == "安全摘要"


def test_tool_lifecycle_is_correlated_sanitized_and_idempotent() -> None:
    projector = projection()
    started = projector.project(
        source(
            1,
            "tool_call.started",
            tool_call_id="call-1",
            tool_name="search",
            tool_input={"query": "Astra", "api_key": "secret", "path": "/Users/alice/private"},
        )
    )
    completed = projector.project(
        source(2, "tool_call.completed", tool_call_id="call-1", tool_name="search", status="failed", error={"token": "x"})
    )
    duplicate = projector.project(
        source(2, "tool_call.completed", tool_call_id="call-1", tool_name="search", status="failed")
    )
    protocol = [event for event in started + completed if not event["type"].startswith("ACTIVITY_")]
    assert event_types(protocol) == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
    arguments = next(event for event in started if event["type"] == "TOOL_CALL_ARGS")
    assert json.loads(arguments["delta"]) == {"query": "Astra", "path": "[private path removed]"}
    result = next(event for event in completed if event["type"] == "TOOL_CALL_RESULT")
    assert json.loads(result["content"]) == {"status": "failed", "error": {}}
    assert duplicate == []


def test_tool_arguments_are_complete_bounded_json_and_malformed_input_falls_back() -> None:
    oversized = safe_tool_arguments({"keep": "visible", "large": "界" * MAX_TOOL_CHARS})
    encoded = json.dumps(oversized, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) <= MAX_TOOL_CHARS
    assert oversized == {"_truncated": True, "keep": "visible"}

    malformed = projection().project(
        source(1, "tool_call.started", tool_call_id="call-malformed", tool_name="search", tool_input="not-json")
    )
    arguments = next(event for event in malformed if event["type"] == "TOOL_CALL_ARGS")
    assert json.loads(arguments["delta"]) == {}


def test_reasoning_and_tool_events_preserve_order_across_success_failure_and_terminal_state() -> None:
    projector = projection()
    assert projector.project(source(1, "reasoning.summary.unavailable", reason="provider_hidden")) == []
    events = projector.project(source(2, "reasoning.summary.delta", turn_index=1, delta="摘要"))
    events += projector.project(source(3, "reasoning.summary.completed", turn_index=1, summary="摘要"))
    events += projector.project(
        source(4, "tool_call.started", tool_call_id="success", tool_name="search", tool_input={"query": "safe"})
    )
    events += projector.project(
        source(5, "tool_call.completed", tool_call_id="success", tool_name="search", status="succeeded")
    )
    events += projector.project(
        source(6, "tool_call.started", tool_call_id="failure", tool_name="shell", tool_input={"token": "hidden"})
    )
    events += projector.project(
        source(
            7,
            "tool_call.completed",
            tool_call_id="failure",
            tool_name="shell",
            status="failed",
            error={"message": "/home/alice/private", "authorization": "secret"},
        )
    )
    events += projector.project(source(8, "run.status_changed", status="completed"))
    after_terminal = projector.project(
        source(9, "tool_call.completed", tool_call_id="late", tool_name="search", status="succeeded")
    )

    protocol = [event for event in events if not event["type"].startswith("ACTIVITY_")]
    assert event_types(protocol) == [
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "RUN_FINISHED",
    ]
    failure_args = [event for event in protocol if event["type"] == "TOOL_CALL_ARGS"][1]
    assert json.loads(failure_args["delta"]) == {}
    failure_result = [event for event in protocol if event["type"] == "TOOL_CALL_RESULT"][1]
    assert json.loads(failure_result["content"]) == {"status": "failed", "error": {"message": "[private path removed]"}}
    assert after_terminal == []


def test_activity_uses_snapshot_then_sanitized_delta_or_replacement() -> None:
    projector = projection()
    first = projector.project(source(1, "plan.node.updated", plan_id="plan-1", plan_node_id="n1", status="running"))
    second = projector.project(source(2, "plan.node.updated", plan_id="plan-1", plan_node_id="n1", status="completed"))
    assert event_types(first) == ["ACTIVITY_SNAPSHOT"]
    assert first[0]["content"]["schemaVersion"] == 1
    assert first[0]["content"]["fallbackText"]
    assert event_types(second)[0] in {"ACTIVITY_DELTA", "ACTIVITY_SNAPSHOT"}
    if second[0]["type"] == "ACTIVITY_DELTA":
        assert second[0]["metadata"] == {
            "schemaVersion": 1,
            "baseRevision": 1,
            "revision": 2,
            "sourceEventId": 2,
        }


def test_interrupt_follows_snapshots_and_omits_unsafe_similar_decision() -> None:
    projector = projection()
    assert projector.project(
        source(
            1,
            "approval.requested",
            approval_id="approval-1",
            tool_call_id="call-1",
            preview="run command",
            allow_similar=False,
        )
    ) == []
    events = projector.project(source(2, "run.waiting_user", request="Approve?"))
    assert event_types(events) == ["STATE_SNAPSHOT", "MESSAGES_SNAPSHOT", "RUN_FINISHED"]
    interrupt = events[-1]["outcome"]["interrupts"][0]
    assert interrupt["reason"] == "tool_call"
    assert interrupt["responseSchema"]["properties"]["decision"]["enum"] == ["approve_once", "reject"]

    allowed = projection()
    allowed.project(
        source(
            1,
            "approval.requested",
            approval_id="approval-safe",
            tool_call_id="call-safe",
            allow_similar=True,
        )
    )
    safe_events = allowed.project(source(2, "run.waiting_user", request="Approve?"))
    safe_decisions = safe_events[-1]["outcome"]["interrupts"][0]["responseSchema"]["properties"]["decision"]["enum"]
    assert safe_decisions == ["approve_once", "reject", "allow_similar"]


def test_activity_cursor_gap_and_large_change_fall_back_to_snapshot() -> None:
    projector = projection()
    projector.project(source(1, "plan.node.updated", plan_id="plan-1", status="running"))
    gap = projector.project(source(3, "plan.node.updated", plan_id="plan-1", status="completed"))
    assert gap[0]["type"] == "ACTIVITY_SNAPSHOT"
    assert gap[0]["replace"] is True

    reconnect = projection().initial_snapshots()
    assert event_types(reconnect) == ["STATE_SNAPSHOT", "MESSAGES_SNAPSHOT"]


def test_agent_tree_terminal_status_cannot_regress() -> None:
    projector = projection()
    projector.project(source(1, "subagent.progress", execution_id="child-1", status="completed"))
    stale = projector.project(source(2, "subagent.progress", execution_id="child-1", status="running"))
    assert stale == []


def test_parallel_plan_and_agent_tree_keep_stable_entities_and_safe_fallbacks() -> None:
    projector = projection()
    projector.project(source(1, "plan.node.updated", plan_id="plan-1", plan_node_id="node-1", status="running"))
    plan_update = projector.project(
        source(2, "plan.node.updated", plan_id="plan-1", plan_node_id="node-2", status="completed")
    )
    plan = projector.state.activities["astra-activity:astra.plan:plan-1"]
    assert plan["order"] == ["node-1", "node-2"]
    assert set(plan["byId"]) == {"node-1", "node-2"}
    assert plan_update

    projector.project(
        source(3, "subagent.progress", execution_id="child-1", status="running", objective="research", private="/Users/x")
    )
    projector.project(source(4, "subagent.progress", execution_id="child-2", status="completed", objective="verify"))
    tree = projector.state.activities["astra-activity:astra.agent_tree:internal"]
    assert tree["counts"] == {"active": 1, "waiting": 0, "completed": 1, "failed": 0}
    assert tree["fallbackText"].startswith("Agent 协作：")
    assert tree["byId"]["child-1"]["details"]["private"] == "[private path removed]"


def test_artifact_and_verification_activity_remove_unsafe_links_and_keep_warning() -> None:
    projector = projection()
    artifact = projector.project(
        source(1, "artifact.created", artifact_id="artifact-1", status="pending", url="file:///private/data")
    )[0]
    assert "url" not in artifact["content"]["byId"]["artifact-1"]["details"]
    verification = projector.project(
        source(2, "verification.created", verification_id="verify-1", status="warning", summary="Needs review")
    )[0]
    assert verification["content"]["summary"] == "warning"
    assert verification["content"]["fallbackText"]


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ([source(1, "run.status_changed", status="completed")], ["RUN_FINISHED"]),
        ([source(1, "run.error", message="failed")], ["RUN_ERROR"]),
        (
            [source(1, "answer.delta", delta="partial"), source(2, "run.cancelled")],
            ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_FINISHED"],
        ),
        (
            [source(1, "answer.delta", delta="draft"), source(2, "answer.completed", content="final")],
            ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "MESSAGES_SNAPSHOT", "TEXT_MESSAGE_END"],
        ),
    ],
)
def test_golden_terminal_streams(sources, expected) -> None:
    projector = projection()
    events = [event for item in sources for event in projector.project(item)]
    assert event_types(events) == expected
    for event in events:
        validate_public_event(event)


def test_public_encoder_rejects_oversized_custom_event() -> None:
    with pytest.raises(ValueError, match="size limit"):
        validate_public_event({"type": "CUSTOM", "name": "too-big", "value": "x" * 300_000})
