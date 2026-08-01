# Persistence baseline

Astra now has one clean-start persistence baseline: `0001_current_baseline`.
Incremental upgrades from earlier Astra databases are not supported.

## Supported startup state

- A database without an Alembic revision can be initialized with
  `alembic upgrade head`.
- A database stamped with `0001_current_baseline` can start normally.
- Any other revision is rejected with an instruction to reset the database.
  Astra does not rewrite its rows or infer missing fields.

Runtime Profile JSON, Run snapshots, plan graphs, Agent state/results, Memory
records, and browser-local state must use their current schemas. Historical
field aliases, synthesized defaults, schema-v1 readers, and old local-storage
keys have been removed.

The reset does not remove current interoperability or rollout features.
OpenAI-compatible provider protocols, Skill environment compatibility
declarations, model-output normalization, permission-policy simulation, and
Agent Evolution shadow/canary targets remain supported.

## Reset procedure

1. Stop the API, workers, schedulers, and every process holding SQLite.
2. Remove the Astra database, its `-wal` and `-shm` sidecars, and obsolete local
   database backups.
3. Run `alembic upgrade head` from `backend/`.
4. Start Astra and verify that `alembic check` reports no schema drift.

There is no data rollback path. A code rollback requires another empty database
created by that code version.
