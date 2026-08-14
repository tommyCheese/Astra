"""Feature-gated AG-UI HTTP/SSE surface."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.run_management.lifecycle.commands import RunApplicationService
from app.application.run_management.projections.events import run_event_broker
from app.common.core.config import AstraRuntimeSettings, get_settings
from app.common.core.errors import AstraInputValidationError, AstraResourceNotFoundError
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.permissions import ApprovalRequestRecord
from app.infrastructure.db.session import SessionLocal, get_session
from app.infrastructure.repositories.ag_ui_bindings import (
    AgUiBindingRepository,
    InterruptBindingCreate,
    RunBindingCreate,
)
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.interfaces.ag_ui.capabilities import capability_document
from app.interfaces.ag_ui.encoder import encode_sse
from app.interfaces.ag_ui.identifiers import interrupt_id, waiting_interrupt_id
from app.interfaces.ag_ui.input_adapter import (
    input_fingerprint,
    to_approval_decision,
    to_continue_request,
    to_create_run_request,
)
from app.interfaces.ag_ui.metrics import ag_ui_metrics
from app.interfaces.ag_ui.projector import AgUiProjectionState, AgUiRunProjection
from app.interfaces.ag_ui.sanitization import sanitize_public
from app.interfaces.ag_ui.schemas import AgUiRunAgentInput
from app.interfaces.api.runs import SSE_HEADERS, get_run_application_service

router = APIRouter(prefix="/api/ag-ui", tags=["ag-ui"])
POLL_SECONDS = 0.2
LOCAL_PRINCIPAL = "local-user"


def _require_enabled(settings: AstraRuntimeSettings) -> None:
    if not settings.ag_ui_enabled:
        raise AstraResourceNotFoundError("AG_UI_DISABLED", "AG-UI 接口未启用。")


def get_ag_ui_principal() -> str:
    """Authentication seam; the current local-only deployment has one principal."""
    return LOCAL_PRINCIPAL


async def _authorize_thread(session: AsyncSession, thread_id: str, principal_id: str) -> TaskRecord:
    task = await session.get(TaskRecord, thread_id)
    if task is None or task.created_by not in {None, principal_id}:
        raise AstraResourceNotFoundError("AG_UI_THREAD_NOT_FOUND", "找不到指定 AG-UI thread。")
    return task


@router.get("/capabilities")
async def get_capabilities(settings: AstraRuntimeSettings = Depends(get_settings)) -> dict[str, object]:
    _require_enabled(settings)
    return capability_document()


@router.get("/metrics")
async def get_protocol_metrics(settings: AstraRuntimeSettings = Depends(get_settings)) -> dict[str, object]:
    _require_enabled(settings)
    return ag_ui_metrics.snapshot()


@router.post("")
async def run_agent(
    payload: AgUiRunAgentInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: AstraRuntimeSettings = Depends(get_settings),
    service: RunApplicationService = Depends(get_run_application_service),
    principal_id: str = Depends(get_ag_ui_principal),
) -> StreamingResponse:
    _require_enabled(settings)
    _enforce_request_size(payload, request, settings)
    task = await _authorize_thread(session, payload.threadId, principal_id)
    bindings = AgUiBindingRepository(session)
    fingerprint = input_fingerprint(payload)
    existing = await bindings.get_run_binding(principal_id, payload.threadId, payload.runId)
    prepared = None
    after_id = 0
    if existing is None:
        if payload.resume:
            internal_run_id, after_id = await _resume_bound_run(payload, principal_id, bindings, service)
        else:
            prepared = await service.prepare(to_create_run_request(payload), commit=False)
            internal_run_id = prepared.response.run_id
        binding, _ = await bindings.create_run_binding(
            RunBindingCreate(
                principal_id=principal_id,
                thread_id=payload.threadId,
                protocol_run_id=payload.runId,
                parent_protocol_run_id=payload.parentRunId,
                internal_task_id=task.id,
                internal_run_id=internal_run_id,
                profile_version=payload.forwardedProps.astra.profileVersion,
                input_fingerprint=fingerprint,
            )
        )
        await session.commit()
    else:
        binding, _ = await bindings.create_run_binding(
            RunBindingCreate(
                principal_id=principal_id,
                thread_id=payload.threadId,
                protocol_run_id=payload.runId,
                parent_protocol_run_id=payload.parentRunId,
                internal_task_id=task.id,
                internal_run_id=existing.internal_run_id,
                profile_version=payload.forwardedProps.astra.profileVersion,
                input_fingerprint=fingerprint,
            )
        )
    stream = _protocol_stream(
        thread_id=payload.threadId,
        protocol_run_id=payload.runId,
        internal_run_id=binding.internal_run_id,
        after_id=after_id,
        start_after_ready=(lambda: service.start(prepared)) if prepared is not None else None,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


def _enforce_request_size(payload: AgUiRunAgentInput, request: Request, settings: AstraRuntimeSettings) -> None:
    content_length = request.headers.get("content-length")
    try:
        declared_size = int(content_length) if content_length is not None else 0
    except ValueError as error:
        raise AstraInputValidationError("AG_UI_REQUEST_SIZE_INVALID", "AG-UI 请求大小标头无效。") from error
    encoded_size = len(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode())
    if max(declared_size, encoded_size) > settings.ag_ui_max_request_bytes:
        raise AstraInputValidationError("AG_UI_REQUEST_TOO_LARGE", "AG-UI 请求超过允许的大小。")


async def _resume_bound_run(
    payload: AgUiRunAgentInput,
    principal_id: str,
    bindings: AgUiBindingRepository,
    service: RunApplicationService,
) -> tuple[str, int]:
    if not payload.resume:
        raise AstraInputValidationError("AG_UI_RESUME_REQUIRED", "恢复请求缺少 Interrupt 响应。")
    resolved = [
        await bindings.require_interrupt_for_principal(item.interruptId, principal_id, payload.threadId)
        for item in payload.resume
    ]
    internal_run_ids = {interrupt.internal_run_id for interrupt, _ in resolved}
    if len(internal_run_ids) != 1:
        raise AstraInputValidationError("AG_UI_RESUME_MISMATCH", "Interrupt 响应不属于同一个内部运行。")
    resume_after = max(int(interrupt.server_binding.get("resume_after_event_id", 0)) for interrupt, _ in resolved)
    for item, (interrupt, _) in zip(payload.resume, resolved, strict=True):
        await _resolve_interrupt(item, interrupt, service)
        await bindings.consume_interrupt(
            interrupt_id=interrupt.interrupt_id,
            run_binding_id=interrupt.run_binding_id,
            expected_version=interrupt.version,
            outcome={"status": item.status, "payload": sanitize_public(item.payload)},
        )
        ag_ui_metrics.increment("interrupt_outcomes", event_type=item.status)
    return internal_run_ids.pop(), resume_after


async def _resolve_interrupt(item, interrupt, service: RunApplicationService) -> None:
    token = str(interrupt.server_binding.get("continuation_token") or "")
    if item.status == "cancelled":
        await service.cancel(interrupt.internal_run_id)
    elif interrupt.approval_id:
        await service.decide_approval_and_start(
            interrupt.internal_run_id,
            interrupt.approval_id,
            to_approval_decision(item.payload, token),
        )
    else:
        await service.resume_and_start(
            interrupt.internal_run_id,
            to_continue_request(item.payload, token),
        )


@router.post("/runs/{protocol_run_id}/cancel")
async def cancel_protocol_run(
    protocol_run_id: str,
    thread_id: str = Query(alias="threadId", min_length=1, max_length=200),
    session: AsyncSession = Depends(get_session),
    settings: AstraRuntimeSettings = Depends(get_settings),
    service: RunApplicationService = Depends(get_run_application_service),
    principal_id: str = Depends(get_ag_ui_principal),
) -> dict[str, str]:
    _require_enabled(settings)
    repository = AgUiBindingRepository(session)
    binding = await repository.require_run_binding(principal_id, thread_id, protocol_run_id)
    binding_id = binding.id
    run = await service.cancel(binding.internal_run_id)
    await repository.set_run_status(binding_id, run.status)
    await session.commit()
    ag_ui_metrics.increment("cancellations", event_type=run.status)
    return {"threadId": thread_id, "runId": protocol_run_id, "status": run.status}


async def _database_events(run_id: str, after_id: int) -> tuple[list[dict[str, object]], str | None]:
    async with SessionLocal() as session:
        events, status = await RunUnitOfWork(session).list_events_with_status(run_id, after_id)
        return [
            {"id": event.id, "type": event.type, "payload": event.payload, "created_at": event.created_at.isoformat()}
            for event in events
        ], status


async def _protocol_stream(
    *,
    thread_id: str,
    protocol_run_id: str,
    internal_run_id: str,
    after_id: int = 0,
    start_after_ready: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    projector = AgUiRunProjection(
        AgUiProjectionState(
            thread_id=thread_id,
            protocol_run_id=protocol_run_id,
            internal_run_id=internal_run_id,
        )
    )
    broker_version = run_event_broker.subscribe(internal_run_id)
    ag_ui_metrics.gauge("active_streams", 1)
    last_id = after_id
    projector.state.source_cursor = after_id
    status: str | None = None
    completed = False
    if after_id:
        ag_ui_metrics.increment("reconnects")
    try:
        yield encode_sse(projector.run_started())
        for snapshot in projector.initial_snapshots():
            yield encode_sse(snapshot)
        await asyncio.sleep(0)
        if start_after_ready is not None:
            start_after_ready()
            await asyncio.sleep(0)
        while True:
            events, status = await _database_events(internal_run_id, last_id)
            frames, last_id = await _project_source_events(events, projector, internal_run_id, last_id)
            for frame in frames:
                yield frame
            if status in RunUnitOfWork.TERMINAL_STATUSES:
                for event in projector.finish(status or "completed"):
                    yield encode_sse(event)
                break
            broker_version = await run_event_broker.wait_for_change(
                internal_run_id,
                broker_version,
                timeout=POLL_SECONDS,
            )
        completed = True
    finally:
        if not completed:
            ag_ui_metrics.increment("stream_disconnects")
        run_event_broker.unsubscribe(internal_run_id)
        ag_ui_metrics.gauge("active_streams", -1)


async def _project_source_events(
    sources: list[dict[str, object]],
    projector: AgUiRunProjection,
    internal_run_id: str,
    last_id: int,
) -> tuple[list[str], int]:
    frames: list[str] = []
    for source in sources:
        source_id = source.get("id")
        if isinstance(source_id, int):
            last_id = source_id
        try:
            projected = projector.project(source)
        except (TypeError, ValueError, KeyError):
            ag_ui_metrics.increment("projection_errors", event_type=str(source.get("type", "")))
            projected = projector.projection_error()
        if not projected:
            ag_ui_metrics.increment("suppressed_events", event_type=str(source.get("type", "")))
        frames.extend(encode_sse(event) for event in projected)
        await _persist_interrupt_source(internal_run_id, source, projector)
    return frames, last_id


async def _persist_interrupt_source(
    internal_run_id: str,
    source: dict[str, object],
    projector: AgUiRunProjection,
) -> None:
    event_type = str(source.get("type", ""))
    if event_type not in {"approval.requested", "run.waiting_user"}:
        return
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    async with SessionLocal() as session:
        repository = AgUiBindingRepository(session)
        run_binding = await repository.get_run_binding_by_internal(internal_run_id)
        if run_binding is None:
            return
        command = await _interrupt_binding_command(session, run_binding.id, internal_run_id, source, payload, projector)
        if command is not None:
            await repository.create_interrupt(command)
            await session.commit()
        elif event_type == "run.waiting_user" and projector.state.pending_interrupts:
            pending_id = projector.state.pending_interrupts[0]["id"]
            await repository.update_interrupt_server_binding(
                pending_id,
                {
                    "continuation_token": payload.get("continuation_token"),
                    "resume_after_event_id": int(source.get("id", 0)),
                },
            )
            await session.commit()


async def _interrupt_binding_command(
    session: AsyncSession,
    run_binding_id: str,
    internal_run_id: str,
    source: dict[str, object],
    payload: dict,
    projector: AgUiRunProjection,
) -> InterruptBindingCreate | None:
    event_type = str(source.get("type", ""))
    approval_id = str(payload.get("approval_id", "")) or None
    if event_type == "approval.requested":
        approval = await session.get(ApprovalRequestRecord, approval_id)
        if approval is None:
            return None
        public_id = interrupt_id(approval.id)
        token = approval.continuation_token
        waiting_kind = "tool_call"
    else:
        public_id = waiting_interrupt_id(internal_run_id, int(source.get("id", 0)))
        token = payload.get("continuation_token")
        waiting_kind = "confirmation" if payload.get("confirmation") else "input_required"
    public = next((item for item in projector.state.pending_interrupts if item["id"] == public_id), None)
    if public is None:
        return None
    return InterruptBindingCreate(
        interrupt_id=public_id,
        run_binding_id=run_binding_id,
        internal_run_id=internal_run_id,
        approval_id=approval_id,
        waiting_kind=waiting_kind,
        response_schema=public.get("responseSchema", {}),
        server_binding={"continuation_token": token, "resume_after_event_id": int(source.get("id", 0))},
        expected_state_version=payload.get("state_version"),
    )
