from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.core.errors import AstraInputValidationError
from app.common.schemas.usage import UsageSummary
from app.infrastructure.db.session import get_session
from app.infrastructure.repositories.usage import UsageRepository

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/summary", response_model=UsageSummary, response_model_by_alias=True)
async def get_usage_summary(
    scope: Literal["all", "task", "run"] = "all",
    task_id: str | None = None,
    run_id: str | None = None,
    from_time: datetime | None = Query(default=None, alias="from"),
    to_time: datetime | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_session),
) -> UsageSummary:
    from_time = _as_utc(from_time)
    to_time = _as_utc(to_time)
    if scope == "task" and not task_id:
        raise AstraInputValidationError("TASK_ID_REQUIRED", "查询当前对话用量时必须提供 task_id。")
    if scope == "run" and not run_id:
        raise AstraInputValidationError("RUN_ID_REQUIRED", "查询单次运行用量时必须提供 run_id。")
    if from_time and to_time and from_time >= to_time:
        raise AstraInputValidationError("USAGE_RANGE_INVALID", "用量查询的开始时间必须早于结束时间。")
    return await UsageRepository(session).summary(scope=scope, task_id=task_id, run_id=run_id, from_time=from_time, to_time=to_time)
