"""FastAPI application factory backed by the typed composition root."""

from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bootstrap.container import build_application_container
from app.bootstrap.lifecycle import create_application_lifespan
from app.bootstrap.routes import register_routes
from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.platform.http.errors import register_exception_handlers
from app.platform.http.middleware import register_http_middleware


def application_version() -> str:
    try:
        return version("astra-backend")
    except PackageNotFoundError:
        return "0.0.0+local"


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    container = build_application_container(resolved_settings, session_factory)
    application = FastAPI(
        title="Astra",
        version=application_version(),
        lifespan=create_application_lifespan(container),
    )
    application.state.container = container
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_routes(application)
    register_http_middleware(application, resolved_settings)
    register_exception_handlers(application)
    return application
