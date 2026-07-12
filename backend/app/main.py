from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from fastapi.middleware.cors import CORSMiddleware

from app.api.runs import router as runs_router
from app.core.config import get_settings
from app.core.errors import AstraError, ErrorEnvelope, InfrastructureError, ValidationError, run_error_from_exception


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Astra", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(runs_router)

    @app.exception_handler(AstraError)
    async def astra_error_handler(_: Request, exc: AstraError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=ErrorEnvelope(error=exc.payload).model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        error = ValidationError("REQUEST_INVALID", "请求参数不正确。", {"fields": [item.get("loc", [])[-1] for item in exc.errors()]})
        return JSONResponse(status_code=error.status_code, content=ErrorEnvelope(error=error.payload).model_dump(mode="json"))

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        error = InfrastructureError()
        return JSONResponse(status_code=error.status_code, content=ErrorEnvelope(error=error.payload).model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def unknown_error_handler(_: Request, exc: Exception) -> JSONResponse:
        payload = run_error_from_exception(exc)
        status = 503 if payload["type"].startswith(("infrastructure.", "configuration.", "dependency.")) else 500
        return JSONResponse(status_code=status, content=ErrorEnvelope(error=payload).model_dump(mode="json"))

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
