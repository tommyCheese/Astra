import logging
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class ErrorPayload(BaseModel):
    type: str
    code: str
    message: str
    retryable: bool = False
    trace_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex}")
    details: Dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorPayload


class AstraError(Exception):
    def __init__(self, *, error_type: str, code: str, message: str, status_code: int, retryable: bool = False, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.payload = ErrorPayload(type=error_type, code=code, message=message, retryable=retryable, details=details or {})
        self.status_code = status_code


class ValidationError(AstraError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(error_type="validation.input_invalid", code=code, message=message, status_code=422, details=details)


class ResourceError(AstraError):
    def __init__(self, code: str, message: str):
        super().__init__(error_type="resource.not_found", code=code, message=message, status_code=404)


class StateError(AstraError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(error_type="state.conflict", code=code, message=message, status_code=409, details=details)


class InfrastructureError(AstraError):
    def __init__(self, code: str = "DATABASE_UNAVAILABLE", message: str = "服务暂时无法访问数据存储，请稍后重试。", retryable: bool = True):
        super().__init__(error_type="infrastructure.database_unavailable", code=code, message=message, status_code=503, retryable=retryable)


def internal_error(exc: Exception) -> ErrorPayload:
    payload = ErrorPayload(type="runtime.internal_error", code="INTERNAL_ERROR", message="服务暂时出现异常，请稍后重试。", retryable=True)
    logger.exception("astra_error trace_id=%s type=%s cause=%s", payload.trace_id, payload.type, type(exc).__name__)
    return payload


def run_error_from_exception(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, AstraError):
        return exc.payload.model_dump(mode="json")
    name = type(exc).__name__
    if name == "ModelConfigurationError":
        return ErrorPayload(type="configuration.model_not_configured", code="MODEL_NOT_CONFIGURED", message="大模型服务尚未完成配置，无法执行该任务。", retryable=False).model_dump(mode="json")
    if name == "ModelOutputError":
        return ErrorPayload(
            type="dependency.model_response_invalid",
            code="MODEL_RESPONSE_INVALID",
            message="大模型服务返回了无法处理的结果，请稍后重试。",
            retryable=True,
            details={"reason": str(exc)[:600]},
        ).model_dump(mode="json")
    if name == "ToolExecutionError":
        category = getattr(exc, "category", "tool_failed")
        if category in {"search_failed", "missing_credentials"}:
            return ErrorPayload(type="dependency.search_unavailable", code="SEARCH_UNAVAILABLE", message="搜索服务暂时不可用或尚未配置。", retryable=category == "search_failed").model_dump(mode="json")
        if category in {"fetch_failed", "permission_denied"}:
            return ErrorPayload(type="dependency.fetch_unavailable", code="FETCH_UNAVAILABLE", message="网页访问服务暂时不可用或当前不被允许。", retryable=category == "fetch_failed").model_dump(mode="json")
    if isinstance(exc, (SQLAlchemyError, OSError, ConnectionError)):
        return InfrastructureError().payload.model_dump(mode="json")
    return internal_error(exc).model_dump(mode="json")
