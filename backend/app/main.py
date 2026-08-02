import logging
import time
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.agent_profile import configure_agent_profile_resolver
from app.api.conversations import router as conversations_router
from app.api.evolution import router as evolution_router
from app.api.memories import recall_router as memory_recall_router
from app.api.memories import router as memories_router
from app.api.memory_consolidation import router as memory_consolidation_router
from app.api.models import router as models_router
from app.api.permissions import router as permissions_router
from app.api.preferences import router as preferences_router
from app.api.runs import router as runs_router
from app.api.runtime import router as runtime_router
from app.api.schedules import router as schedules_router
from app.api.skills import router as skills_router
from app.api.tools import router as tools_router
from app.api.usage import router as usage_router
from app.conversation_retention import ConversationRetentionService
from app.core.config import Settings, get_settings
from app.core.errors import (
    AstraError,
    ErrorEnvelope,
    InfrastructureError,
    ValidationError,
    run_error_from_exception,
)
from app.db.session import SessionLocal
from app.memory.autodream import AutoDreamService
from app.repositories.tool_settings import (
    ToolSettingsRepository,
    apply_tool_states,
    default_tool_states,
)
from app.repositories.usage import UsageRepository
from app.runner.engine import (
    close_shared_model_http_clients,
    shared_model_http_client,
    shared_tool_registry,
)
from app.runtime_profiles import RuntimeProfileService
from app.skills.storage import ensure_builtin_skills

logger = logging.getLogger("astra.http")


def application_version() -> str:
    try:
        return version("astra-backend")
    except PackageNotFoundError:
        return "0.0.0+local"


def create_app(settings: Settings | None = None, *, session_factory=SessionLocal) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            # Build the SSL context and provider transport before the first user
            # request. No network request is made until a Run starts.
            shared_model_http_client(settings)
            await app.state.runtime_profile_service.startup()
            async with session_factory() as session:
                await ensure_builtin_skills(session, settings)
                tool_states = await ToolSettingsRepository(session).get_or_create(
                    default_tool_states(settings)
                )
                shared_tool_registry(apply_tool_states(settings, tool_states))
                await session.commit()
                interrupted = await UsageRepository(session).reconcile_interrupted()
                if interrupted:
                    logger.warning("usage.reconciled_interrupted count=%s", interrupted)
            await app.state.conversation_retention_service.startup()
            await app.state.autodream_service.startup()
            yield
        finally:
            await app.state.autodream_service.shutdown()
            await app.state.conversation_retention_service.shutdown()
            await app.state.runtime_profile_service.shutdown()
            await close_shared_model_http_clients()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = FastAPI(title="Astra", version=application_version(), lifespan=lifespan)
    app.state.runtime_profile_service = RuntimeProfileService(
        settings,
        recover_interrupted=True,
    )
    configure_agent_profile_resolver(app.state.runtime_profile_service.active_agent_profile)
    app.state.conversation_retention_service = ConversationRetentionService(
        settings,
        session_factory,
    )
    app.state.autodream_service = AutoDreamService(settings, session_factory)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(runs_router)
    app.include_router(conversations_router)
    app.include_router(evolution_router)
    app.include_router(memories_router)
    app.include_router(memory_recall_router)
    app.include_router(memory_consolidation_router)
    app.include_router(models_router)
    app.include_router(preferences_router)
    app.include_router(permissions_router)
    app.include_router(runtime_router)
    app.include_router(schedules_router)
    app.include_router(tools_router)
    app.include_router(usage_router)
    app.include_router(skills_router)

    @app.middleware("http")
    async def local_api_boundary(request: Request, call_next):
        if request.url.path.startswith("/api") and not settings.api_allow_remote:
            client_host = request.client.host if request.client else ""
            try:
                address = ip_address(client_host)
                mapped = getattr(address, "ipv4_mapped", None)
                is_loopback = address.is_loopback or (mapped is not None and mapped.is_loopback)
            except ValueError:
                is_loopback = client_host in {"localhost", "testclient"}
            if not is_loopback:
                payload = ErrorEnvelope.model_validate(
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
                    content=payload.model_dump(mode="json"),
                )
        return await call_next(request)

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
