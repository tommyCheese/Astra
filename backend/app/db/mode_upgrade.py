from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RunRecord
from app.schemas.agent import RunExecutionProfile


class ModeUpgradeRequired(RuntimeError):
    pass


async def validate_mode_upgrade(session: AsyncSession) -> None:
    """Reject worker startup when a live Run still uses a deleted contract."""
    rows = (
        await session.execute(
            select(
                RunRecord.id,
                RunRecord.status,
                RunRecord.execution_profile,
                RunRecord.reasoning_policy,
            ).where(
                RunRecord.status.not_in(
                    {
                        "completed",
                        "completed_with_warnings",
                        "failed",
                        "blocked",
                        "cancelled",
                    }
                )
            )
        )
    ).all()
    for run_id, _status, profile, policy in rows:
        requested = policy.get("requested", {}) if isinstance(policy, dict) else {}
        effective = policy.get("effective", {}) if isinstance(policy, dict) else {}
        deleted = (
            "planning_strategy" in requested
            or "planning_strategy" in effective
            or requested.get("execution_mode") == "plan_only"
            or effective.get("execution_mode") == "plan_only"
        )
        try:
            RunExecutionProfile.model_validate(profile)
        except ValueError as exc:
            raise ModeUpgradeRequired(
                f"Run {run_id} has an incompatible execution profile; apply Alembic upgrade"
            ) from exc
        if deleted:
            raise ModeUpgradeRequired(
                f"Run {run_id} contains deleted planning fields; apply Alembic upgrade"
            )
