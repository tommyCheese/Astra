"""Project committed Astra Run events into ordered public AG-UI events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.interfaces.ag_ui.activities import (
    activity_entity_id,
    activity_group_id,
    activity_snapshot,
    activity_type_for,
    merge_activity_entities,
)
from app.interfaces.ag_ui.delta import bounded_patch
from app.interfaces.ag_ui.identifiers import (
    activity_message_id,
    answer_message_id,
    interrupt_id,
    reasoning_message_id,
    tool_call_id,
    waiting_interrupt_id,
)
from app.interfaces.ag_ui.sanitization import safe_error, safe_reasoning, safe_tool_arguments, sanitize_public

TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed", "blocked", "cancelled"}


@dataclass
class AgUiProjectionState:
    thread_id: str
    protocol_run_id: str
    internal_run_id: str
    source_cursor: int = 0
    seen_source_ids: set[int] = field(default_factory=set)
    text_started: bool = False
    text_ended: bool = False
    terminal_emitted: bool = False
    answer_content: str = ""
    reasoning_open: set[str] = field(default_factory=set)
    reasoning_content: dict[str, str] = field(default_factory=dict)
    tools_started: set[str] = field(default_factory=set)
    tools_completed: set[str] = field(default_factory=set)
    activities: dict[str, dict[str, Any]] = field(default_factory=dict)
    activity_revisions: dict[str, int] = field(default_factory=dict)
    pending_interrupts: list[dict[str, Any]] = field(default_factory=list)
    source_gap: bool = False

    @property
    def message_id(self) -> str:
        return answer_message_id(self.internal_run_id)


class AgUiRunProjection:
    def __init__(self, state: AgUiProjectionState) -> None:
        self.state = state

    def run_started(self) -> dict[str, Any]:
        return {
            "type": "RUN_STARTED",
            "threadId": self.state.thread_id,
            "runId": self.state.protocol_run_id,
        }

    def initial_snapshots(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "STATE_SNAPSHOT",
                "snapshot": {
                    "schemaVersion": 1,
                    "revision": 1,
                    "run": {"status": "running", "answerMode": "standard"},
                    "controls": {"cancellation": True},
                    "pendingInterrupts": [],
                },
            },
            {"type": "MESSAGES_SNAPSHOT", "messages": []},
        ]

    def project(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        source_id = source.get("id")
        if isinstance(source_id, int):
            if source_id in self.state.seen_source_ids or source_id <= self.state.source_cursor:
                return []
            if self.state.source_cursor and source_id > self.state.source_cursor + 1:
                self.state.source_gap = True
            self.state.seen_source_ids.add(source_id)
            self.state.source_cursor = source_id
        event_type = str(source.get("type", ""))
        payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
        cursor = source_id if isinstance(source_id, int) else 0
        handlers = (
            self._project_answer,
            self._project_reasoning,
            lambda name, body: self._project_tool_with_activity(name, body, cursor),
            lambda name, body: self._project_interrupt(name, body, cursor),
            self._project_terminal,
        )
        for handler in handlers:
            events = handler(event_type, payload)
            if events is not None:
                return events
        return self._project_activity(event_type, payload, cursor)

    def _project_tool_with_activity(
        self, event_type: str, payload: dict[str, Any], source_id: int
    ) -> list[dict[str, Any]] | None:
        if event_type.startswith("tool_call.") and self.state.terminal_emitted:
            return []
        tool_events = self._project_tool(event_type, payload)
        if tool_events is None:
            return None
        return [*tool_events, *self._project_activity(event_type, payload, source_id)]

    def _project_answer(self, event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if event_type == "answer.started":
            return self._start_text()
        if event_type == "answer.delta":
            return self._text_delta(str(payload.get("delta", "")))
        if event_type == "answer.content.completed":
            return self._end_text()
        if event_type == "answer.completed":
            return self._correct_and_end(str(payload.get("content", "")))
        return None

    def _project_terminal(self, event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if event_type == "run.error":
            return self._run_error(payload)
        if event_type in {"run.cancelled", "fast.cancelled"}:
            return self.finish("cancelled")
        status = str(payload.get("status", ""))
        if event_type in {"run.completed", "run.failed", "run.blocked", "fast.completed", "fast.blocked"}:
            return self.finish(status or event_type.rsplit(".", 1)[-1])
        if event_type == "run.status_changed" and status in TERMINAL_STATUSES:
            return self.finish(status)
        return None

    def _project_reasoning(self, event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if event_type not in {"reasoning.summary.delta", "reasoning.summary.completed"}:
            return None
        stream_id = str(payload.get("turn_index", "run"))
        message_id = reasoning_message_id(self.state.internal_run_id, stream_id)
        events: list[dict[str, Any]] = []
        was_open = message_id in self.state.reasoning_open
        if not was_open:
            self.state.reasoning_open.add(message_id)
            events.extend(
                [
                    {"type": "REASONING_START", "messageId": message_id},
                    {"type": "REASONING_MESSAGE_START", "messageId": message_id, "role": "reasoning"},
                ]
            )
        content, truncated = safe_reasoning(payload)
        if event_type.endswith("completed") and was_open:
            content = ""
        if content:
            self.state.reasoning_content[message_id] = self.state.reasoning_content.get(message_id, "") + content
            events.append({"type": "REASONING_MESSAGE_CONTENT", "messageId": message_id, "delta": content})
        if truncated:
            events.append({"type": "CUSTOM", "name": "astra.reasoning.truncated", "value": {"messageId": message_id}})
        if event_type.endswith("completed"):
            self.state.reasoning_open.discard(message_id)
            events.extend(
                [
                    {"type": "REASONING_MESSAGE_END", "messageId": message_id},
                    {"type": "REASONING_END", "messageId": message_id},
                ]
            )
        return events

    def _project_tool(self, event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if not event_type.startswith("tool_call."):
            return None
        internal_id = str(payload.get("tool_call_id", ""))
        if not internal_id or self.state.terminal_emitted:
            return []
        public_id = tool_call_id(internal_id)
        name = str(payload.get("tool_name") or "astra_tool")[:200]
        events: list[dict[str, Any]] = []
        events.extend(self._tool_start_events(event_type, payload, public_id, name))
        events.extend(self._tool_result_events(event_type, payload, internal_id, public_id))
        return events

    def _tool_start_events(
        self, event_type: str, payload: dict[str, Any], public_id: str, name: str
    ) -> list[dict[str, Any]]:
        if event_type not in {"tool_call.started", "tool_call.proposed"} or public_id in self.state.tools_started:
            return []
        self.state.tools_started.add(public_id)
        arguments = json.dumps(safe_tool_arguments(payload.get("tool_input")), ensure_ascii=False, separators=(",", ":"))
        return [
            {"type": "TOOL_CALL_START", "toolCallId": public_id, "toolCallName": name},
            {"type": "TOOL_CALL_ARGS", "toolCallId": public_id, "delta": arguments},
            {"type": "TOOL_CALL_END", "toolCallId": public_id},
        ]

    def _tool_result_events(
        self, event_type: str, payload: dict[str, Any], internal_id: str, public_id: str
    ) -> list[dict[str, Any]]:
        status = str(payload.get("status") or "completed")
        terminal = {"succeeded", "failed", "rejected", "cancelled", "completed"}
        if event_type not in {"tool_call.completed", "tool_call.status_changed"}:
            return []
        if public_id in self.state.tools_completed or status not in terminal:
            return []
        self.state.tools_completed.add(public_id)
        content = sanitize_public({"status": status, "error": payload.get("error")})
        return [
            {
                "type": "TOOL_CALL_RESULT",
                "messageId": f"astra-tool-result:{internal_id}",
                "toolCallId": public_id,
                "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            }
        ]

    def _project_activity(self, event_type: str, payload: dict[str, Any], source_id: int) -> list[dict[str, Any]]:
        activity_type = activity_type_for(event_type)
        if activity_type is None:
            return []
        entity_id = activity_entity_id(activity_type, payload, self.state.internal_run_id)
        group_id = activity_group_id(activity_type, payload, self.state.internal_run_id)
        message_id = activity_message_id(activity_type, group_id)
        revision = self.state.activity_revisions.get(message_id, 0) + 1
        current = activity_snapshot(
            activity_type, entity_id, event_type, payload, revision=revision, source_event_id=source_id
        )
        previous = self.state.activities.get(message_id)
        if previous is not None and self._terminal_activity_would_regress(previous, current, entity_id):
            return []
        if previous is not None:
            current = merge_activity_entities(previous, current, entity_id, activity_type)
        self.state.activities[message_id] = current
        self.state.activity_revisions[message_id] = revision
        if previous is None:
            return [
                {
                    "type": "ACTIVITY_SNAPSHOT",
                    "messageId": message_id,
                    "activityType": activity_type,
                    "content": current,
                    "replace": False,
                }
            ]
        try:
            patch = None if self.state.source_gap else bounded_patch(previous, current)
        except (TypeError, ValueError):
            patch = None
        self.state.source_gap = False
        if patch is None:
            return [
                {
                    "type": "ACTIVITY_SNAPSHOT",
                    "messageId": message_id,
                    "activityType": activity_type,
                    "content": current,
                    "replace": True,
                }
            ]
        return [
            {
                "type": "ACTIVITY_DELTA",
                "messageId": message_id,
                "activityType": activity_type,
                "patch": patch,
                "metadata": {
                    "schemaVersion": 1,
                    "baseRevision": revision - 1,
                    "revision": revision,
                    "sourceEventId": source_id,
                },
            }
        ]

    @staticmethod
    def _terminal_activity_would_regress(
        previous: dict[str, Any], current: dict[str, Any], entity_id: str
    ) -> bool:
        terminal = {"completed", "succeeded", "failed", "cancelled", "rejected", "blocked"}
        old_status = str(previous.get("byId", {}).get(entity_id, {}).get("status", ""))
        new_status = str(current.get("byId", {}).get(entity_id, {}).get("status", ""))
        return old_status in terminal and new_status not in terminal

    def _project_interrupt(self, event_type: str, payload: dict[str, Any], source_id: int) -> list[dict[str, Any]] | None:
        if event_type == "approval.requested":
            return self._remember_approval_interrupt(payload)
        if event_type != "run.waiting_user":
            return None
        if not self.state.pending_interrupts:
            self._remember_waiting_interrupt(payload, source_id)
        return self.finish_interrupt()

    def _remember_approval_interrupt(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        approval_id = str(payload.get("approval_id", ""))
        if not approval_id:
            return []
        decisions = ["approve_once", "reject"]
        if payload.get("allow_similar") is True:
            decisions.append("allow_similar")
        self.state.pending_interrupts.append(
            {
                "id": interrupt_id(approval_id),
                "reason": "tool_call",
                "message": str(sanitize_public(payload.get("preview") or "是否允许执行此工具？")),
                "toolCallId": tool_call_id(str(payload.get("tool_call_id", ""))),
                "responseSchema": {
                    "type": "object",
                    "properties": {"decision": {"type": "string", "enum": decisions}},
                    "required": ["decision"],
                    "additionalProperties": False,
                },
            }
        )
        return []

    def _remember_waiting_interrupt(self, payload: dict[str, Any], source_id: int) -> None:
        confirmation = payload.get("approved") is not None or payload.get("confirmation")
        reason = "confirmation" if confirmation else "input_required"
        response_schema = {"type": "boolean"} if confirmation else {"type": "string", "maxLength": 4000}
        message = payload.get("request") or payload.get("message") or "请提供继续执行所需的信息。"
        self.state.pending_interrupts.append(
            {
                "id": waiting_interrupt_id(self.state.internal_run_id, source_id),
                "reason": reason,
                "message": str(sanitize_public(message)),
                "responseSchema": response_schema,
            }
        )

    def finish_interrupt(self) -> list[dict[str, Any]]:
        if self.state.terminal_emitted:
            return []
        events = self._end_text()
        events.extend(
            [
                {
                    "type": "STATE_SNAPSHOT",
                    "snapshot": {
                        "schemaVersion": 1,
                        "revision": 2,
                        "run": {"status": "waiting_user", "answerMode": "standard"},
                        "controls": {"cancellation": True},
                        "pendingInterrupts": self.state.pending_interrupts,
                    },
                },
                {
                    "type": "MESSAGES_SNAPSHOT",
                    "messages": ([{"id": self.state.message_id, "role": "assistant", "content": self.state.answer_content}]
                    if self.state.answer_content else []),
                },
            ]
        )
        self.state.terminal_emitted = True
        events.append(
            {
                "type": "RUN_FINISHED",
                "threadId": self.state.thread_id,
                "runId": self.state.protocol_run_id,
                "outcome": {"type": "interrupt", "interrupts": self.state.pending_interrupts},
            }
        )
        return events

    def projection_error(self) -> list[dict[str, Any]]:
        return self._run_error({"message": "Astra 无法安全投影本次运行。", "code": "PROJECTION_FAILED"})

    def finish(self, status: str) -> list[dict[str, Any]]:
        if self.state.terminal_emitted:
            return []
        events = self._end_text()
        self.state.terminal_emitted = True
        events.append(
            {
                "type": "RUN_FINISHED",
                "threadId": self.state.thread_id,
                "runId": self.state.protocol_run_id,
                "result": {"status": status, "content": self.state.answer_content},
                "outcome": {"type": "success"},
            }
        )
        return events

    def _start_text(self) -> list[dict[str, Any]]:
        if self.state.text_started or self.state.text_ended:
            return []
        self.state.text_started = True
        return [{"type": "TEXT_MESSAGE_START", "messageId": self.state.message_id, "role": "assistant"}]

    def _text_delta(self, delta: str) -> list[dict[str, Any]]:
        if not delta or self.state.text_ended or self.state.terminal_emitted:
            return []
        events = self._start_text()
        self.state.answer_content += delta
        events.append({"type": "TEXT_MESSAGE_CONTENT", "messageId": self.state.message_id, "delta": delta})
        return events

    def _end_text(self) -> list[dict[str, Any]]:
        if not self.state.text_started or self.state.text_ended:
            return []
        self.state.text_ended = True
        return [{"type": "TEXT_MESSAGE_END", "messageId": self.state.message_id}]

    def _correct_and_end(self, content: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if content:
            if not self.state.text_ended and content.startswith(self.state.answer_content):
                events.extend(self._text_delta(content[len(self.state.answer_content) :]))
            elif content != self.state.answer_content:
                self.state.answer_content = content
                events.append(
                    {
                        "type": "MESSAGES_SNAPSHOT",
                        "messages": [{"id": self.state.message_id, "role": "assistant", "content": content}],
                    }
                )
        events.extend(self._end_text())
        return events

    def _run_error(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if self.state.terminal_emitted:
            return []
        events = self._end_text()
        self.state.terminal_emitted = True
        events.append({"type": "RUN_ERROR", **safe_error(payload)})
        return events
