"""Shared loaders and safe projections used by Run persistence stores."""

from sqlalchemy.orm import selectinload

from app.agent_profile import AgentProfile
from app.db.models.executions import NodeExecutionRecord
from app.db.models.plans import PlanRecord
from app.db.models.runs import RunRecord


def run_detail_options():
    return (
        selectinload(RunRecord.steps),
        selectinload(RunRecord.task),
        selectinload(RunRecord.tool_calls),
        selectinload(RunRecord.artifacts),
        selectinload(RunRecord.events),
        selectinload(RunRecord.turns),
        selectinload(RunRecord.memories),
        selectinload(RunRecord.sandbox_jobs),
        selectinload(RunRecord.approval_requests),
        selectinload(RunRecord.approval_grants),
        selectinload(RunRecord.agent_executions),
        selectinload(RunRecord.agent_joins),
        selectinload(RunRecord.node_executions).selectinload(NodeExecutionRecord.resource_leases),
        selectinload(RunRecord.node_executions).selectinload(
            NodeExecutionRecord.budget_reservations
        ),
        selectinload(RunRecord.plans).selectinload(PlanRecord.nodes),
        selectinload(RunRecord.plans).selectinload(PlanRecord.edges),
    )


def safe_agent_profile_manifest(snapshot: dict) -> dict:
    return AgentProfile.from_snapshot(snapshot).manifest.safe_dict()
