## Context

Conversation history is represented by `TaskRecord`; each task owns Runs and their execution, approval, memory, artifact, and workspace records. The existing `DELETE /api/conversations/{id}` path performs a hard database delete and then best-effort cleanup of artifact content and the task workspace. The sidebar independently limits its recent list to 100 entries, so older rows can remain persisted but undiscoverable.

The backend already has a FastAPI lifespan and a shared async SQLAlchemy session factory. The retention mechanism must work with SQLite and PostgreSQL, must not hold a transaction while sleeping, and must avoid unbounded startup work. Candidate selection requires indexes on `(tasks.pinned_at, tasks.updated_at)` and `(runs.task_id, runs.status)` so a growing history does not turn each sweep into avoidable full scans.

## Goals / Non-Goals

**Goals:**

- Provide an opt-in, deployment-configured retention policy for persisted conversations.
- Select candidates from database state using last activity, terminal status, pin state, and active-share state.
- Delete in bounded batches through one canonical lifecycle service shared by the API and background worker.
- Make startup, periodic execution, per-item failures, and aggregate outcomes observable.
- Shut the worker down promptly and cleanly.

**Non-Goals:**

- Archive or restore conversations.
- Summarize old turns or change the six-Run model-context window.
- Age standalone memories independently of their owning conversation.
- Provide end-user retention controls in this change.
- Guarantee deletion of external backup copies or database pages already captured by infrastructure.

## Decisions

### Retention is explicit and disabled by default

Add `conversation_retention_enabled`, `conversation_retention_days`, `conversation_retention_sweep_seconds`, and `conversation_retention_batch_size`. The default retention age is 180 days, but the worker performs no deletion until explicitly enabled. This prevents an application upgrade from unexpectedly destroying existing history while still providing a safe production default once enabled.

Alternative: enable deletion automatically on upgrade. Rejected because existing deployments have no established user expectation that old conversations are ephemeral.

### Eligibility uses conversation state, not Run creation time

A candidate must have `TaskRecord.updated_at <= cutoff`, no `pinned_at`, no active `ConversationShareRecord`, at least one Run, and no Run outside terminal states. Empty conversations are not aged because they cannot currently be created through the normal API and may represent imported or administrative state.

Using `TaskRecord.updated_at` makes follow-up messages, renames, pin changes, and answer-mode changes refresh retention. Active shares are protected because deleting their source would silently invalidate an intentionally published snapshot-management workflow.

Alternative: use `created_at` or the latest Run timestamp. Rejected because both can expire a conversation soon after recent user activity.

### Candidate selection and deletion are separate, bounded operations

The repository returns only candidate IDs ordered oldest-first, limited by the configured batch size. The worker opens a fresh transaction for each ID, reloads and revalidates eligibility against the same cutoff, then deletes it. This reduces lock duration, isolates failures, and makes concurrent sweeps idempotent enough for the supported single-service deployment: a candidate already removed by another worker is counted as skipped.

No cross-process lease table is added. Astra's packaged deployment runs one backend process; if multi-worker deployment is introduced, a database-backed scheduler lease should be added before enabling retention in every worker.

### One lifecycle service owns hard deletion

Move API-level external cleanup into a reusable `ConversationLifecycleService`. It calls the existing repository cascade deletion, removes returned artifact storage keys, and removes the validated task workspace path. Artifact/workspace cleanup remains best-effort after the database commit and emits warnings on failure.

Alternative: duplicate cleanup in the worker. Rejected because the API and background path would drift and could leave different orphan sets.

### Lifespan owns a cancellable periodic worker

`ConversationRetentionService.startup()` performs one bounded sweep and starts an `asyncio` task. The loop waits on a stop event with a timeout instead of sleeping blindly. `shutdown()` signals the event and awaits the task. Disabled configuration creates no task.

The worker catches sweep-level exceptions, rolls forward to the next interval, and logs them. Individual deletion failures are caught inside the batch so later candidates continue.

## Risks / Trade-offs

- [Risk] A bad retention configuration causes unwanted deletion. → Retention is disabled by default, age and batch values are clamped to positive bounds, and pinned/shared/active conversations are excluded.
- [Risk] External artifact or workspace deletion fails after database commit. → Log the exact conversation and resource class, continue the batch, and preserve the current best-effort semantics.
- [Risk] Concurrent backend workers select the same candidate. → Revalidate each ID immediately before deletion and treat missing rows as skipped; document the single-worker operational assumption.
- [Risk] A very large backlog takes time to drain. → Process oldest-first in bounded batches on every interval rather than looping without limit.
- [Risk] Startup sweep delays readiness. → The batch is bounded; deployments can choose a small batch and subsequent periodic sweeps drain the backlog.

## Migration Plan

1. Back up the database and run Alembic upgrade to add the two retention scan indexes.
2. Deploy code with retention disabled and confirm logs show `conversation_retention.disabled`.
3. Configure a conservative age, interval, and batch size, then set `CONVERSATION_RETENTION_ENABLED=true`.
4. Restart one backend instance and monitor sweep counts and cleanup warnings before enabling on another environment.
5. Roll back deletion by setting the enabled flag to false and restarting. The indexes can remain safely; removing them requires the Alembic downgrade. Already deleted conversations cannot be restored except from infrastructure backups.

## Open Questions

- A future administrative preview endpoint could expose candidate counts before enablement.
- A future two-stage archive/tombstone model could support restore windows and multi-worker leases.
