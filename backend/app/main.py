"""Stable ASGI entry point; application composition lives in ``app.infrastructure.bootstrap``."""

from app.infrastructure.bootstrap.application import application_version, create_app

__all__ = ["app", "application_version", "create_app"]

app = create_app()
