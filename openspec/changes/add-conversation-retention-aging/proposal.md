## Why

Astra persists every conversation and its execution graph indefinitely, while the UI only exposes a bounded recent list. Long-running deployments therefore accumulate unreachable database rows, workspace files, and artifacts without an operational retention policy or an audit trail for cleanup.

## What Changes

- Add a configurable background conversation-aging service with a disabled-by-default retention period, bounded batch size, and sweep interval.
- Age only terminal, unpinned, unshared conversations whose last activity is older than the configured cutoff.
- Reuse the canonical conversation deletion path so runs, audit records, workspaces, shares, and artifact content are cleaned consistently.
- Record aggregate sweep outcomes in structured application logs and isolate per-conversation failures so one bad record does not stop a batch.
- Run one retention sweep during application startup and continue periodic sweeps while the backend is running.
- Clarify that the sidebar limit controls visibility rather than database retention.

## Capabilities

### New Capabilities

- `conversation-retention`: Configurable, safe, bounded, and observable aging of persisted conversations and their owned resources.

### Modified Capabilities

- `agent-chat-ui`: Clarify that the recent-conversation limit is a display limit and does not itself delete database history.

## Impact

- Backend settings, application lifespan, conversation repository/service, artifact and workspace cleanup.
- Database query behavior, two retention-scan indexes, and operational logs; no new external dependency is required.
- Frontend history copy and tests.
- Deployment configuration documentation and backend retention tests.
