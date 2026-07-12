import logging
from typing import Any

from app.db.session import SessionLocal
from app.repositories.usage import UsageRepository

logger = logging.getLogger("astra.usage")


class DatabaseUsageRecorder:
    def __init__(self, run_id: str):
        self.run_id = run_id

    async def start(self, *, provider: str, model: str, operation: str, attempt: int) -> str | None:
        try:
            async with SessionLocal() as session:
                return await UsageRepository(session).create_invocation(run_id=self.run_id, provider=provider, model=model, operation=operation, attempt=attempt)
        except Exception:
            logger.exception("usage.start.failed run_id=%s", self.run_id)
            return None

    async def finish(self, invocation_id: str | None, *, status: str, duration_ms: int, request_id: str | None = None, usage: dict[str, Any] | None = None, error: BaseException | None = None) -> None:
        if invocation_id is None:
            return
        try:
            async with SessionLocal() as session:
                await UsageRepository(session).finish_invocation(invocation_id, status=status, duration_ms=duration_ms, request_id=request_id, usage=usage, error=error)
        except Exception:
            logger.exception("usage.finish.failed invocation_id=%s", invocation_id)
