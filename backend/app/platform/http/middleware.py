"""Named HTTP middleware with no business-service lookup."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from ipaddress import ip_address

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.errors import ErrorEnvelope

logger = logging.getLogger("astra.http")
RequestHandler = Callable[[Request], Awaitable[Response]]
HttpMiddleware = Callable[[Request, RequestHandler], Awaitable[Response]]


def is_local_client(client_host: str) -> bool:
    try:
        address = ip_address(client_host)
        mapped_address = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or (mapped_address is not None and mapped_address.is_loopback)
    except ValueError:
        return client_host in {"localhost", "testclient"}


def create_local_api_boundary(settings: Settings) -> HttpMiddleware:
    async def local_api_boundary(request: Request, call_next: RequestHandler) -> Response:
        is_protected_api = request.url.path.startswith("/api") and not settings.api_allow_remote
        client_host = request.client.host if request.client else ""
        if is_protected_api and not is_local_client(client_host):
            error_envelope = ErrorEnvelope.model_validate(
                {
                    "error": {
                        "type": "policy.remote_api_denied",
                        "code": "REMOTE_API_DENIED",
                        "message": "Astra API 默认仅允许本机访问。",
                    }
                }
            )
            return JSONResponse(
                status_code=403,
                content=error_envelope.model_dump(mode="json"),
            )
        return await call_next(request)

    return local_api_boundary


def create_request_logger() -> HttpMiddleware:
    async def request_logger(request: Request, call_next: RequestHandler) -> Response:
        started_at = time.perf_counter()
        logger.info("request.start method=%s path=%s", request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request.failed method=%s path=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                (time.perf_counter() - started_at) * 1000,
            )
            raise
        logger.info(
            "request.complete method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started_at) * 1000,
        )
        return response

    return request_logger


def register_http_middleware(application: FastAPI, settings: Settings) -> None:
    application.middleware("http")(create_local_api_boundary(settings))
    application.middleware("http")(create_request_logger())
