# Astra release bundle

## Start

Prerequisites: Docker Engine with Docker Compose v2.

```bash
./install.sh
```

Open <http://127.0.0.1:8080>. The default configuration uses Astra's mock
model so the service can be verified without credentials.

To use a real OpenAI-compatible model, edit `.env`, set `MODEL_PROVIDER`,
`MODEL_NAME`, `MODEL_API_KEY`, and `MODEL_BASE_URL`, then run:

```bash
docker compose up -d
```

## Upgrade

Pass the new release version without or with a leading `v`:

```bash
./install.sh v0.2.0
```

Application state is stored under `./data`. Back it up before a major upgrade.

## Conversation retention

Database conversation aging is disabled by default. To opt in, back up `./data`,
then configure the retention age and bounded sweep in `.env`:

```text
CONVERSATION_RETENTION_ENABLED=true
CONVERSATION_RETENTION_DAYS=180
CONVERSATION_RETENTION_SWEEP_SECONDS=86400
CONVERSATION_RETENTION_BATCH_SIZE=100
```

Pinned conversations, active shares, and conversations with non-terminal runs
are protected. Deletion is permanent; disabling the flag only stops later
sweeps. See `docs/conversation-retention-operations.md` in the source
repository for the full policy and log reference.

## Scheduled tasks and heartbeat

The scheduler is enabled by default and persists definitions and fire history in
the Astra database. Each unattended run revalidates its signed permission bundle;
expired or invalid bundles are blocked instead of being downgraded to interactive
execution. Heartbeat is a separate, system-managed schedule: it observes the
configured active-hours window, defers while its target chat is busy, and records
`HEARTBEAT_OK` as a silent history entry.

The main operational controls are:

```text
SCHEDULER_ENABLED=true
SCHEDULER_POLL_SECONDS=1
SCHEDULER_LEASE_SECONDS=30
SCHEDULER_BATCH_SIZE=20
SCHEDULER_MAX_DISPATCH_CONCURRENCY=4
SCHEDULER_HISTORY_RETENTION_DAYS=90
SCHEDULER_HEARTBEAT_MIN_INTERVAL_SECONDS=300
```

`GET /api/health` reports scanner state; `GET /api/ready` returns 503 when an
enabled scheduler is not running or its latest scan failed. Before disabling the
scheduler for maintenance, pause user schedules or expect due runs to follow their
configured misfire policy after restart. Schedule idempotency prevents duplicate
fire records but does not claim exactly-once behavior for external side effects.

## Database concurrency boundary

The bundled SQLite database is supported for one backend process. Astra tests
concurrent workers inside that process with separate sessions and compare-and-swap
claims, but SQLite is not the supported coordination layer for multiple backend
replicas. Use PostgreSQL through `DATABASE_URL` before scaling the backend beyond
one process; keep all replicas on the same database and artifact/workspace stores.

## Tool Provider plugin rollout

External and managed-package discovery are disabled by default. Keep the safe rollout mode while upgrading:

```text
TOOL_PLUGIN_ROLLOUT_MODE=builtin_only
TOOL_MANAGED_PLUGIN_DISCOVERY_ENABLED=false
TOOL_EXTERNAL_PLUGIN_DISCOVERY_ENABLED=false
```

Switch to `configured` only after the Provider identity and digest are allowlisted, a Host-managed isolated Runtime Backend is configured, and deployment tests cover health, timeout, cancellation and rollback. To roll back, restore `builtin_only` and restart. Preserve Tool Catalog Snapshot rows: paused Runs that froze an external behavioral catalog must remain failed closed rather than silently resuming against different tools. See `docs/tool-provider-plugins.md` for the trust model and troubleshooting codes.

## Stop

```bash
docker compose down
```

The Compose endpoint binds to `127.0.0.1` by default. Place an authenticated
TLS reverse proxy in front of Astra before exposing it to a network.
