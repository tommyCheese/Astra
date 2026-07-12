import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.runs import router as runs_router
from app.api.runtime import router as runtime_router
from app.api.tools import router as tools_router
from app.api.usage import router as usage_router
from app.core.config import Settings, get_settings
from app.core.errors import (
    AstraError,
    ErrorEnvelope,
    InfrastructureError,
    ValidationError,
    run_error_from_exception,
)
from app.db.session import SessionLocal
from app.repositories.usage import UsageRepository
from app.runtime_profiles import RuntimeProfileService

logger = logging.getLogger("astra.http")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            async with SessionLocal() as session:
                interrupted = await UsageRepository(session).reconcile_interrupted()
                if interrupted:
                    logger.warning("usage.reconciled_interrupted count=%s", interrupted)
            yield
        finally:
            await app.state.runtime_profile_service.shutdown()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = FastAPI(title="Astra", version="0.1.0", lifespan=lifespan)
    app.state.runtime_profile_service = RuntimeProfileService(
        settings,
        recover_interrupted=True,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(runs_router)
    app.include_router(runtime_router)
    app.include_router(tools_router)
    app.include_router(usage_router)

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        started = time.perf_counter()
        logger.info("request.start method=%s path=%s", request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request.failed method=%s path=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise
        logger.info(
            "request.complete method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.exception_handler(AstraError)
    async def astra_error_handler(_: Request, exc: AstraError) -> JSONResponse:
        logger.warning(
            "request.astra_error type=%s code=%s trace_id=%s",
            exc.payload.type,
            exc.payload.code,
            exc.payload.trace_id,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorEnvelope(error=exc.payload).model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        error = ValidationError(
            "REQUEST_INVALID",
            "请求参数不正确。",
            {"fields": [item.get("loc", [])[-1] for item in exc.errors()]},
        )
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorEnvelope(error=error.payload).model_dump(mode="json"),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("request.database_error cause=%s", type(exc).__name__)
        error = InfrastructureError()
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorEnvelope(error=error.payload).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def unknown_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("request.unhandled cause=%s", type(exc).__name__)
        payload = run_error_from_exception(exc)
        status = (
            503
            if payload["type"].startswith(("infrastructure.", "configuration.", "dependency."))
            else 500
        )
        return JSONResponse(
            status_code=status, content=ErrorEnvelope(error=payload).model_dump(mode="json")
        )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
