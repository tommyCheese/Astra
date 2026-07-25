"""replace planning modes with fixed quick and trusted paths

Revision ID: 0017_simplify_modes
Revises: 0016_deep_unlimited_tool_calls
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0017_simplify_modes"
down_revision = "0016_deep_unlimited_tool_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TERMINAL_STATUSES = {
    "completed",
    "completed_with_warnings",
    "failed",
    "blocked",
    "cancelled",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rewrite_policy(raw: Any) -> dict[str, Any]:
    policy = _mapping(raw)
    requested = _mapping(policy.get("requested"))
    effective = _mapping(policy.get("effective"))
    requested.pop("planning_strategy", None)
    effective.pop("planning_strategy", None)
    if requested.get("execution_mode") == "plan_only":
        requested["execution_mode"] = "request_approval"
    if effective.get("execution_mode") == "plan_only":
        effective["execution_mode"] = "request_approval"
    return {
        "requested": requested,
        "effective": effective,
        "adjustments": [
            item
            for item in policy.get("adjustments", [])
            if isinstance(item, dict) and item.get("field") != "planning_strategy"
        ],
        "version": 2,
    }


def _rewrite_profile(
    raw: Any,
    *,
    answer_mode: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    profile = _mapping(raw)
    trusted = answer_mode == "trusted"
    return {
        "answer_mode": answer_mode,
        "contract_mode": "model" if trusted else "system_minimal",
        "assurance_level": "full" if trusted else "basic",
        "reasoning_policy": policy,
        "plan_execution": "auto" if trusted else None,
        "validators": (
            ["task_adapter", "artifact_reference"]
            if trusted
            else ["artifact_reference"]
        ),
        "interactive": bool(profile.get("interactive", True)),
        "permission_bundle": profile.get("permission_bundle"),
        "version": 2,
    }


def _rewrite_run(row: dict[str, Any]) -> dict[str, Any]:
    answer_mode = (
        row["answer_mode"]
        if row.get("answer_mode") in {"standard", "trusted"}
        else "trusted"
    )
    policy = _rewrite_policy(row.get("reasoning_policy"))
    values: dict[str, Any] = {
        "reasoning_policy": policy,
        "execution_profile": _rewrite_profile(
            row.get("execution_profile"),
            answer_mode=answer_mode,
            policy=policy,
        ),
    }
    if row.get("status") not in TERMINAL_STATUSES:
        values.update(
            {
                "status": "cancelled",
                "terminal_reason": {
                    "code": "MODE_UPGRADE_CANCELLED",
                    "message": "运行使用已删除的规划模式，已在不兼容升级期间取消。",
                    "profile_version": 2,
                },
            }
        )
    return values


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("LOCK TABLE runs IN ACCESS EXCLUSIVE MODE")

    runs = sa.table(
        "runs",
        sa.column("id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("answer_mode", sa.String()),
        sa.column("reasoning_policy", sa.JSON()),
        sa.column("execution_profile", sa.JSON()),
        sa.column("terminal_reason", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(
            runs.c.id,
            runs.c.status,
            runs.c.answer_mode,
            runs.c.reasoning_policy,
            runs.c.execution_profile,
        )
    ).mappings()
    for row in rows:
        values = _rewrite_run(dict(row))
        bind.execute(
            runs.update().where(runs.c.id == row["id"]).values(**values)
        )

    with op.batch_alter_table("conversation_strategy_preferences") as batch:
        batch.drop_column("planning_strategy")
    with op.batch_alter_table("plans") as batch:
        batch.drop_column("strategy")


def downgrade() -> None:
    raise RuntimeError(
        "0017_simplify_modes is intentionally irreversible; restore the pre-upgrade backup"
    )
