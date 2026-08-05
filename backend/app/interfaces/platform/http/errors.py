"""Application-wide mapping from domain/infrastructure errors to HTTP envelopes."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.common.core.errors import (
    AstraError,
    ErrorEnvelope,
    InfrastructureError,
    ValidationError,
    run_error_from_exception,
)

logger = logging.getLogger("astra.http")


async def astra_error_handler(_: Request, error: AstraError) -> JSONResponse:
    logger.warning(
        "request.astra_error type=%s code=%s trace_id=%s",
        error.payload.type,
        error.payload.code,
        error.payload.trace_id,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=ErrorEnvelope(error=error.payload).model_dump(mode="json"),
    )


async def validation_error_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    validation_error = ValidationError(
        "REQUEST_INVALID",
        "请求参数不正确。",
        {"fields": [item.get("loc", [])[-1] for item in error.errors()]},
    )
    return JSONResponse(
        status_code=validation_error.status_code,
        content=ErrorEnvelope(error=validation_error.payload).model_dump(mode="json"),
    )


async def database_error_handler(_: Request, error: SQLAlchemyError) -> JSONResponse:
    logger.exception("request.database_error cause=%s", type(error).__name__)
    infrastructure_error = InfrastructureError()
    return JSONResponse(
        status_code=infrastructure_error.status_code,
        content=ErrorEnvelope(error=infrastructure_error.payload).model_dump(mode="json"),
    )


async def unknown_error_handler(_: Request, error: Exception) -> JSONResponse:
    logger.exception("request.unhandled cause=%s", type(error).__name__)
    error_payload = run_error_from_exception(error)
    status_code = (
        503
        if error_payload["type"].startswith(("infrastructure.", "configuration.", "dependency."))
        else 500
    )
    return JSONResponse(
        status_code=status_code,
        content=ErrorEnvelope(error=error_payload).model_dump(mode="json"),
    )


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(AstraError, astra_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(SQLAlchemyError, database_error_handler)
    application.add_exception_handler(Exception, unknown_error_handler)
