"""Translate allowlisted AG-UI input into existing Astra application commands."""

from __future__ import annotations

import hashlib
import json

from app.common.core.errors import AstraInputValidationError
from app.common.schemas.agent.api_views import ContinueRunRequest, CreateRunRequest
from app.common.schemas.agent.tool_invocation import ApprovalDecisionRequest
from app.common.schemas.agent.types import AnswerMode, PlanExecution
from app.common.schemas.model_providers import RunModelConfig
from app.interfaces.ag_ui.schemas import AgUiMessage, AgUiRunAgentInput


def input_fingerprint(payload: AgUiRunAgentInput) -> str:
    canonical = payload.model_dump(mode="json", exclude={"runId"})
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _text_content(message: AgUiMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    text_parts = [
        str(part.get("text", ""))
        for part in message.content
        if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
    ]
    return "".join(text_parts).strip()


def latest_user_text(payload: AgUiRunAgentInput) -> str:
    for message in reversed(payload.messages):
        if message.role == "user":
            content = _text_content(message)
            if content:
                return content
    raise AstraInputValidationError("AG_UI_USER_MESSAGE_REQUIRED", "AG-UI 请求缺少可用的用户消息。")


def to_create_run_request(payload: AgUiRunAgentInput) -> CreateRunRequest:
    if payload.resume:
        raise AstraInputValidationError("AG_UI_RESUME_REQUIRES_BINDING", "恢复请求必须通过持久化 Interrupt 绑定处理。")
    if payload.tools:
        raise AstraInputValidationError(
            "AG_UI_CLIENT_TOOLS_UNSUPPORTED",
            "AG-UI 客户端提供的工具不能注册为 Astra 执行能力。",
        )
    properties = payload.forwardedProps.astra
    model = RunModelConfig.model_validate(properties.model) if properties.model is not None else None
    return CreateRunRequest(
        goal=latest_user_text(payload),
        task_id=payload.threadId,
        session_id=properties.sessionId,
        answer_mode=AnswerMode(properties.answerMode),
        plan_execution=PlanExecution(properties.planExecution) if properties.planExecution else None,
        model=model,
        skill_ids=properties.skillIds,
        subagent_mode=properties.subagentMode,
    )


def to_approval_decision(payload: object, continuation_token: str) -> ApprovalDecisionRequest:
    if not isinstance(payload, dict):
        raise AstraInputValidationError("AG_UI_RESUME_INVALID", "工具审批响应格式无效。")
    try:
        return ApprovalDecisionRequest(
            decision=payload.get("decision"),
            continuation_token=continuation_token,
            guidance=payload.get("guidance"),
        )
    except ValueError as error:
        raise AstraInputValidationError("AG_UI_RESUME_INVALID", "工具审批响应不在允许范围内。") from error


def to_continue_request(payload: object, continuation_token: str) -> ContinueRunRequest:
    content = payload.get("content") if isinstance(payload, dict) else payload
    if not isinstance(content, (str, bool, int, float)):
        raise AstraInputValidationError("AG_UI_RESUME_INVALID", "继续执行响应格式无效。")
    return ContinueRunRequest(content=str(content), continuation_token=continuation_token)
