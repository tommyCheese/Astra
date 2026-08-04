"""Application-facing Run read service."""

from __future__ import annotations

from app.repositories.run_unit_of_work import RunUnitOfWork
from app.repositories.run_view_projection import RunViewProjector
from app.schemas.agent.api_views import RunView


class RunQueryService:
    """Load Runs and return validated public read models."""

    def __init__(
        self,
        reader: RunUnitOfWork,
        projector: RunViewProjector | None = None,
    ) -> None:
        self._reader = reader
        self._projector = projector or RunViewProjector()

    async def detail(self, run_id: str) -> RunView | None:
        run = await self._reader.get_run(run_id)
        return self._projector.project(run) if run is not None else None

    async def initial(self, run_id: str) -> RunView | None:
        run, fully_loaded = await self._reader.get_run_initial(run_id)
        if run is None:
            return None
        return (
            self._projector.project(run) if fully_loaded else self._projector.project_initial(run)
        )

    async def recent(self, limit: int = 100) -> list[RunView]:
        return [self._projector.project(run) for run in await self._reader.list_recent_runs(limit)]
