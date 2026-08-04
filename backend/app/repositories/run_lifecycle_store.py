from app.repositories.run_cancellation_store import RunCancellationStore
from app.repositories.run_core_store import RunCoreStore
from app.repositories.run_plan_revision_store import RunPlanRevisionStore
from app.repositories.run_query_store import RunQueryStore


class RunLifecycleStore(RunCoreStore, RunPlanRevisionStore, RunQueryStore, RunCancellationStore):
    pass
