"""Validated Run read-model boundary conversions."""

from app.common.schemas.agent.api_views import RunView
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.run_view_projection import initial_run_view, run_view


async def run_detail(reader: RunUnitOfWork, run_id: str) -> RunView | None:
    run = await reader.get_run(run_id)
    return run_view(run) if run is not None else None


async def initial_run(reader: RunUnitOfWork, run_id: str) -> RunView | None:
    run, fully_loaded = await reader.get_run_initial(run_id)
    if run is None:
        return None
    return run_view(run) if fully_loaded else initial_run_view(run)


async def recent_runs(reader: RunUnitOfWork, limit: int = 100) -> list[RunView]:
    return [run_view(run) for run in await reader.list_recent_runs(limit)]
