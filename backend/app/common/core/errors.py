import logging
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class AstraApiErrorPayload(BaseModel):
    type: str
    code: str
    message: str
    retryable: bool = False
    trace_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex}")
    details: dict[str, Any] = Field(default_factory=dict)


class AstraApiErrorEnvelope(BaseModel):
    error: AstraApiErrorPayload


class AstraError(Exception):
    def __init__(
        self,
        *,
        error_type: str,
        code: str,
        message: str,
        status_code: int,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.payload = AstraApiErrorPayload(
            type=error_type, code=code, message=message, retryable=retryable, details=details or {}
        )
        self.status_code = status_code


class AstraInputValidationError(AstraError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            error_type="validation.input_invalid",
            code=code,
            message=message,
            status_code=422,
            details=details,
        )


class AstraResourceNotFoundError(AstraError):
    def __init__(self, code: str, message: str):
        super().__init__(error_type="resource.not_found", code=code, message=message, status_code=404)


class AstraStateConflictError(AstraError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            error_type="state.conflict",
            code=code,
            message=message,
            status_code=409,
            details=details,
        )


class AstraInfrastructureError(AstraError):
    def __init__(
        self,
        code: str = "DATABASE_UNAVAILABLE",
        message: str = "服务暂时无法访问数据存储，请稍后重试。",
        retryable: bool = True,
    ):
        super().__init__(
            error_type="infrastructure.database_unavailable",
            code=code,
            message=message,
            status_code=503,
            retryable=retryable,
        )


class AstraConfigurationError(AstraError):
    def __init__(self, code: str, message: str):
        super().__init__(
            error_type="configuration.invalid",
            code=code,
            message=message,
            status_code=503,
            retryable=False,
        )


def internal_error(exc: Exception) -> AstraApiErrorPayload:
    payload = AstraApiErrorPayload(
        type="runtime.internal_error",
        code="INTERNAL_ERROR",
        message="服务暂时出现异常，请稍后重试。",
        retryable=True,
    )
    logger.exception(
        "astra_error trace_id=%s type=%s cause=%s",
        payload.trace_id,
        payload.type,
        type(exc).__name__,
    )
    return payload


def _payload(
    error_type: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return AstraApiErrorPayload(
        type=error_type,
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
    ).model_dump(mode="json")


def _tool_error(exc: Exception) -> dict[str, Any]:
    category = getattr(exc, "category", "tool_failed")
    if category in {"search_failed", "missing_credentials", "provider_not_configured"}:
        return _payload(
            "dependency.search_unavailable",
            "SEARCH_UNAVAILABLE",
            "搜索服务暂时不可用或尚未配置。",
            retryable=category == "search_failed",
        )
    if category in {"fetch_failed", "permission_denied", "extract_failed"}:
        return _payload(
            "dependency.fetch_unavailable",
            "FETCH_UNAVAILABLE",
            "网页访问服务暂时不可用或当前不被允许。",
            retryable=category == "fetch_failed",
        )
    if category == "invalid_input":
        return _payload("validation.tool_input_invalid", "TOOL_INPUT_INVALID", "工具输入参数不正确。")
    if category == "tool_not_allowed":
        return _payload("policy.tool_not_allowed", "TOOL_NOT_ALLOWED", "当前任务不允许使用该工具。")
    return _payload("runtime.tool_failed", "TOOL_EXECUTION_FAILED", "工具执行失败，请稍后重试。")


def run_error_from_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AstraError):
        return exc.payload.model_dump(mode="json")
    name = type(exc).__name__
    if name == "ModelConfigurationError":
        return _payload(
            "configuration.model_not_configured",
            "MODEL_NOT_CONFIGURED",
            "大模型服务尚未完成配置，无法执行该任务。",
        )
    if name == "AgentProfileConfigurationError":
        return _payload(
            "configuration.agent_profile_invalid",
            "AGENT_PROFILE_INVALID",
            "Astra 身份配置无效，暂时无法执行该任务。",
        )
    if name == "ModelOutputError":
        return _payload(
            "dependency.model_response_invalid",
            "MODEL_RESPONSE_INVALID",
            "大模型服务返回了无法处理的结果，请稍后重试。",
            retryable=True,
            details={"reason": str(exc)[:600]},
        )
    if isinstance(exc, httpx.RequestError):
        return _payload(
            "dependency.model_unavailable",
            "MODEL_ENDPOINT_UNAVAILABLE",
            "暂时无法连接大模型服务，请稍后重试。",
            retryable=True,
            details={"reason": type(exc).__name__},
        )
    if name == "ToolExecutionError":
        return _tool_error(exc)
    if isinstance(exc, (SQLAlchemyError, OSError, ConnectionError)):
        return AstraInfrastructureError().payload.model_dump(mode="json")
    return internal_error(exc).model_dump(mode="json")
