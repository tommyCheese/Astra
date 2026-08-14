import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.common.core.config import AstraRuntimeSettings, get_settings
from app.common.core.errors import AstraInputValidationError
from app.infrastructure.db.session import get_session
from app.interfaces.ag_ui.capabilities import capability_document
from app.interfaces.ag_ui.encoder import encode_sse
from app.interfaces.ag_ui.identifiers import (
    agent_tree_activity_id,
    answer_message_id,
    interrupt_id,
    plan_activity_id,
    protocol_run_id,
    protocol_thread_id,
    reasoning_message_id,
    tool_call_id,
)
from app.interfaces.ag_ui.input_adapter import to_create_run_request
from app.interfaces.ag_ui.projector import AgUiProjectionState, AgUiRunProjection
from app.interfaces.ag_ui.routes import _protocol_stream, _resume_bound_run
from app.interfaces.ag_ui.schemas import AgUiRunAgentInput, validate_public_event
from app.interfaces.api.runs import get_run_application_service
from app.main import create_app

ROOT = Path(__file__).parents[2]
GOLDEN = json.loads((ROOT / "backend/tests/fixtures/ag_ui/golden-contract.json").read_text())
PROFILE = json.loads((ROOT / "contracts/ag-ui/profile-v1.json").read_text())
FRONTEND_PACKAGE = json.loads((ROOT / "frontend/package.json").read_text())


def source_event(event_id: int, event_type: str, **payload: object) -> dict[str, object]:
    return {"id": event_id, "type": event_type, "payload": payload}


def projector() -> AgUiRunProjection:
    return AgUiRunProjection(AgUiProjectionState("thread-1", "protocol-run-1", "internal-run-1"))


def test_pinned_profile_matches_frontend_packages_and_golden_input() -> None:
    assert PROFILE["profileVersion"] == "astra-ag-ui-v1"
    assert PROFILE["agUiPackages"] == {
        "@ag-ui/core": FRONTEND_PACKAGE["dependencies"]["@ag-ui/core"],
        "@ag-ui/client": FRONTEND_PACKAGE["dependencies"]["@ag-ui/client"],
    }
    parsed = AgUiRunAgentInput.model_validate(GOLDEN["input"])
    assert to_create_run_request(parsed).goal == "Hello"
    assert GOLDEN["capabilities"] == capability_document()


def test_golden_events_are_accepted_and_sse_preserves_one_boundary() -> None:
    for event in GOLDEN["events"].values():
        assert validate_public_event(event) == event
        frame = encode_sse(event)
        assert frame.startswith("data: ")
        assert frame.endswith("\n\n")
        assert json.loads(frame.removeprefix("data: ").removesuffix("\n\n")) == event


def test_public_event_validation_rejects_unknown_and_invalid_content() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        validate_public_event({"type": "INTERNAL_TRACE", "secret": "hidden"})
    with pytest.raises(ValueError, match="non-empty"):
        validate_public_event({"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": ""})


def test_public_identifiers_are_stable_and_namespaced() -> None:
    assert protocol_thread_id("thread-1") == "thread-1"
    assert protocol_run_id("run-1") == "run-1"
    assert answer_message_id("run-1") == "astra-answer:run-1"
    assert reasoning_message_id("run-1", "summary") == "astra-reasoning:run-1:summary"
    assert tool_call_id("call-1") == "astra-tool:call-1"
    assert plan_activity_id("plan-1") == "astra-plan:plan-1"
    assert agent_tree_activity_id("run-1") == "astra-agent-tree:run-1"
    assert interrupt_id("approval-1") == "astra-interrupt:approval-1"


def test_input_is_strict_and_client_tools_cannot_become_backend_tools() -> None:
    forged = {**GOLDEN["input"], "tools": [{"name": "shell", "parameters": {}}]}
    with pytest.raises(AstraInputValidationError) as error:
        to_create_run_request(AgUiRunAgentInput.model_validate(forged))
    assert error.value.payload.code == "AG_UI_CLIENT_TOOLS_UNSUPPORTED"

    with pytest.raises(ValidationError):
        AgUiRunAgentInput.model_validate({**GOLDEN["input"], "unexpected": True})
    with pytest.raises(ValidationError):
        AgUiRunAgentInput.model_validate(
            {**GOLDEN["input"], "forwardedProps": {"astra": {"profileVersion": "astra-ag-ui-v1", "token": "x"}}}
        )


async def test_capabilities_are_feature_gated() -> None:
    disabled = create_app(AstraRuntimeSettings(ag_ui_enabled=False))
    enabled = create_app(AstraRuntimeSettings(ag_ui_enabled=True))
    disabled.dependency_overrides[get_settings] = lambda: AstraRuntimeSettings(ag_ui_enabled=False)
    enabled.dependency_overrides[get_settings] = lambda: AstraRuntimeSettings(ag_ui_enabled=True)
    async with AsyncClient(transport=ASGITransport(app=disabled), base_url="http://test") as client:
        response = await client.get("/api/ag-ui/capabilities")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "AG_UI_DISABLED"
    async with AsyncClient(transport=ASGITransport(app=enabled), base_url="http://test") as client:
        response = await client.get("/api/ag-ui/capabilities")
        assert response.status_code == 200
        assert response.json() == capability_document()
        assert response.json()["tools"]["clientProvided"] is False


def test_projector_streams_text_immediately_and_finishes_once() -> None:
    projection = projector()
    emitted = [projection.run_started()]
    emitted += projection.project(source_event(1, "answer.started"))
    emitted += projection.project(source_event(2, "answer.delta", delta="Hel"))
    emitted += projection.project(source_event(3, "answer.delta", delta="lo"))
    emitted += projection.project(source_event(4, "answer.completed", content="Hello"))
    emitted += projection.project(source_event(5, "run.status_changed", status="completed"))
    emitted += projection.project(source_event(5, "run.status_changed", status="completed"))

    assert [event["type"] for event in emitted] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert emitted[2]["delta"] == "Hel"
    assert emitted[-1]["result"]["content"] == "Hello"


def test_projector_corrects_final_content_and_closes_partial_text_before_error() -> None:
    correction = projector()
    correction.project(source_event(1, "answer.delta", delta="draft"))
    correction.project(source_event(2, "answer.content.completed"))
    events = correction.project(source_event(3, "answer.completed", content="final"))
    assert events == [
        {
            "type": "MESSAGES_SNAPSHOT",
            "messages": [{"id": "astra-answer:internal-run-1", "role": "assistant", "content": "final"}],
        }
    ]

    failed = projector()
    failed.project(source_event(1, "answer.delta", delta="partial"))
    events = failed.project(source_event(2, "run.error", message="safe failure", code="MODEL_ERROR"))
    assert [event["type"] for event in events] == ["TEXT_MESSAGE_END", "RUN_ERROR"]
    assert events[-1]["message"] == "safe failure"


async def test_protocol_stream_emits_run_started_before_starting_and_then_text(monkeypatch) -> None:
    started = False
    calls = 0

    async def fake_database_events(run_id: str, after_id: int):
        nonlocal calls
        calls += 1
        assert run_id == "internal-run-1"
        assert after_id == 0
        return [
            source_event(1, "answer.delta", delta="first"),
            source_event(2, "run.status_changed", status="completed"),
        ], "completed"

    def start() -> None:
        nonlocal started
        started = True

    monkeypatch.setattr("app.interfaces.ag_ui.routes._database_events", fake_database_events)
    stream = _protocol_stream(
        thread_id="thread-1",
        protocol_run_id="protocol-run-1",
        internal_run_id="internal-run-1",
        start_after_ready=start,
    )
    first = await anext(stream)
    assert json.loads(first[6:]) == GOLDEN["events"]["runStarted"]
    assert started is False
    remaining = [frame async for frame in stream]
    assert started is True
    assert calls == 1
    assert [json.loads(frame[6:])["type"] for frame in remaining] == [
        "STATE_SNAPSHOT",
        "MESSAGES_SNAPSHOT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]


async def test_enabled_post_route_streams_sse_with_safe_headers(monkeypatch) -> None:
    class FakeSession:
        async def rollback(self) -> None:
            return None

        async def commit(self) -> None:
            return None

    class FakeService:
        def __init__(self) -> None:
            self.started = False

        async def prepare(self, request, *, commit=True):
            assert request.goal == "Hello"
            assert commit is False
            return SimpleNamespace(response=SimpleNamespace(run_id="internal-run-1"))

        def start(self, prepared) -> None:
            assert prepared.response.run_id == "internal-run-1"
            self.started = True

    async def override_session():
        yield FakeSession()

    async def terminal_events(run_id: str, after_id: int):
        assert (run_id, after_id) == ("internal-run-1", 0)
        return [
            source_event(1, "answer.delta", delta="Hello"),
            source_event(2, "run.status_changed", status="completed"),
        ], "completed"

    class FakeBindings:
        def __init__(self, session) -> None:
            self.session = session

        async def get_run_binding(self, principal_id, thread_id, protocol_run_id):
            return None

        async def create_run_binding(self, command):
            return SimpleNamespace(internal_run_id=command.internal_run_id), True

    async def authorize_thread(session, thread_id, principal_id):
        return SimpleNamespace(id=thread_id)

    settings = AstraRuntimeSettings(ag_ui_enabled=True)
    service = FakeService()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_run_application_service] = lambda: service
    monkeypatch.setattr("app.interfaces.ag_ui.routes._database_events", terminal_events)
    monkeypatch.setattr("app.interfaces.ag_ui.routes.AgUiBindingRepository", FakeBindings)
    monkeypatch.setattr("app.interfaces.ag_ui.routes._authorize_thread", authorize_thread)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ag-ui", json=GOLDEN["input"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert service.started is True
    events = [json.loads(frame.removeprefix("data: ")) for frame in response.text.strip().split("\n\n")]
    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "STATE_SNAPSHOT",
        "MESSAGES_SNAPSHOT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]


async def test_resume_uses_server_binding_and_original_internal_run() -> None:
    interrupt = SimpleNamespace(
        interrupt_id="interrupt-1",
        internal_run_id="internal-run-1",
        run_binding_id="binding-1",
        version=1,
        approval_id="approval-1",
        server_binding={"continuation_token": "server-secret", "resume_after_event_id": 12},
    )

    class Bindings:
        def __init__(self) -> None:
            self.consumed = []

        async def require_interrupt_for_principal(self, interrupt_id, principal_id, thread_id):
            assert (interrupt_id, principal_id, thread_id) == ("interrupt-1", "local-user", "thread-1")
            return interrupt, SimpleNamespace()

        async def consume_interrupt(self, **kwargs):
            self.consumed.append(kwargs)

    class Service:
        def __init__(self) -> None:
            self.decision = None

        async def decide_approval_and_start(self, run_id, approval_id, request):
            assert (run_id, approval_id, request.continuation_token) == (
                "internal-run-1", "approval-1", "server-secret"
            )
            self.decision = request.decision.value

    payload = AgUiRunAgentInput.model_validate(
        {
            "threadId": "thread-1",
            "runId": "resume-run-1",
            "state": {},
            "messages": [],
            "tools": [],
            "context": [],
            "resume": [{"interruptId": "interrupt-1", "status": "resolved", "payload": {"decision": "approve_once"}}],
        }
    )
    bindings = Bindings()
    service = Service()
    run_id, after_id = await _resume_bound_run(payload, "local-user", bindings, service)
    assert (run_id, after_id, service.decision) == ("internal-run-1", 12, "approve_once")
    assert bindings.consumed[0]["outcome"]["payload"] == {"decision": "approve_once"}


async def test_resumed_stream_starts_after_old_interrupt_cursor(monkeypatch) -> None:
    async def events_after(run_id: str, after_id: int):
        assert after_id == 12
        return [
            source_event(13, "tool_call.completed", tool_call_id="call-1", tool_name="shell", status="succeeded"),
            source_event(14, "run.status_changed", status="completed"),
        ], "completed"

    monkeypatch.setattr("app.interfaces.ag_ui.routes._database_events", events_after)
    stream = _protocol_stream(
        thread_id="thread-1",
        protocol_run_id="resume-run-1",
        internal_run_id="internal-run-1",
        after_id=12,
    )
    frames = [json.loads(frame[6:]) async for frame in stream]
    assert frames[-1]["type"] == "RUN_FINISHED"
    tool_result = next(event for event in frames if event["type"] == "TOOL_CALL_RESULT")
    assert tool_result["toolCallId"] == "astra-tool:call-1"
    assert all(event.get("outcome", {}).get("type") != "interrupt" for event in frames)


async def test_resume_handles_multiple_bound_inputs_and_cancelled_response() -> None:
    interrupts = {
        "approval": SimpleNamespace(
            interrupt_id="approval",
            internal_run_id="internal-run-1",
            run_binding_id="binding-1",
            version=1,
            approval_id="approval-1",
            server_binding={"continuation_token": "approval-token", "resume_after_event_id": 10},
        ),
        "question": SimpleNamespace(
            interrupt_id="question",
            internal_run_id="internal-run-1",
            run_binding_id="binding-1",
            version=1,
            approval_id=None,
            server_binding={"continuation_token": "question-token", "resume_after_event_id": 12},
        ),
    }

    class Bindings:
        def __init__(self) -> None:
            self.outcomes = []

        async def require_interrupt_for_principal(self, interrupt_id, principal_id, thread_id):
            assert (principal_id, thread_id) == ("local-user", "thread-1")
            return interrupts[interrupt_id], SimpleNamespace()

        async def consume_interrupt(self, **kwargs):
            self.outcomes.append(kwargs["outcome"])

    class Service:
        def __init__(self) -> None:
            self.decisions = []
            self.inputs = []
            self.cancelled = []

        async def decide_approval_and_start(self, run_id, approval_id, request):
            self.decisions.append((run_id, approval_id, request.decision.value, request.continuation_token))

        async def resume_and_start(self, run_id, request):
            self.inputs.append((run_id, request.content, request.continuation_token))

        async def cancel(self, run_id):
            self.cancelled.append(run_id)

    payload = AgUiRunAgentInput.model_validate(
        {
            "threadId": "thread-1",
            "runId": "resume-multiple",
            "state": {},
            "messages": [],
            "tools": [],
            "context": [],
            "resume": [
                {"interruptId": "approval", "status": "resolved", "payload": {"decision": "reject"}},
                {"interruptId": "question", "status": "resolved", "payload": "补充信息"},
            ],
        }
    )
    bindings, service = Bindings(), Service()
    assert await _resume_bound_run(payload, "local-user", bindings, service) == ("internal-run-1", 12)
    assert service.decisions == [("internal-run-1", "approval-1", "reject", "approval-token")]
    assert service.inputs == [("internal-run-1", "补充信息", "question-token")]

    cancelled = payload.model_copy(
        update={
            "runId": "resume-cancelled",
            "resume": [payload.resume[1].model_copy(update={"status": "cancelled"})],
        }
    )
    await _resume_bound_run(cancelled, "local-user", Bindings(), service)
    assert service.cancelled == ["internal-run-1"]


async def test_resume_rejects_interrupts_from_different_internal_runs() -> None:
    class Bindings:
        async def require_interrupt_for_principal(self, interrupt_id, principal_id, thread_id):
            return SimpleNamespace(
                interrupt_id=interrupt_id,
                internal_run_id=f"internal-{interrupt_id}",
                run_binding_id=f"binding-{interrupt_id}",
                version=1,
                approval_id=None,
                server_binding={},
            ), SimpleNamespace()

    payload = AgUiRunAgentInput.model_validate(
        {
            "threadId": "thread-1",
            "runId": "resume-mismatch",
            "state": {},
            "messages": [],
            "tools": [],
            "context": [],
            "resume": [
                {"interruptId": "one", "status": "resolved", "payload": "x"},
                {"interruptId": "two", "status": "resolved", "payload": "y"},
            ],
        }
    )
    with pytest.raises(AstraInputValidationError) as error:
        await _resume_bound_run(payload, "local-user", Bindings(), SimpleNamespace())
    assert error.value.payload.code == "AG_UI_RESUME_MISMATCH"
