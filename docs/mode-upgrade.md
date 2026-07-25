# Answer-mode upgrade runbook

This release is a one-way contract and schema upgrade. It removes planning
strategy, plan-only execution, and the legacy plan activation endpoint.

1. Stop every API process, worker, scheduler, and background Run producer.
2. Take and verify a restorable database backup.
3. Deploy the new application and run `alembic upgrade head` before starting
   any process.
4. Start API and worker processes only after the migration succeeds. Startup
   validation deliberately refuses live records using an old Profile version
   or deleted planning fields.
5. Verify that non-terminal legacy Runs are `cancelled` with terminal reason
   `MODE_UPGRADE_CANCELLED`. Completed history remains readable with rewritten
   version-2 policy and Profile snapshots.

There is no supported down migration. If rollback is required, stop all new
processes, restore the pre-upgrade application build and the matching database
backup together, then verify both before reopening traffic.
