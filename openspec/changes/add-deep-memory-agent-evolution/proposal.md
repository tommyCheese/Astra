## Why

Astra currently extracts structured Memory records, but recalls only recent items owned by the current Run, so experience does not persist usefully across conversations and cannot evolve when facts change. The existing execution graph, provenance model, retention controls, and disabled `AUTODREAM.md` protocol now provide the boundaries needed to add cross-Session memory and controlled Agent improvement without turning generated memory into trusted policy or allowing self-modification to bypass evaluation.

## What Changes

- Extend persistent Memory into typed, namespaced, cross-Session records for semantic facts, user preferences, episodic experience, procedures, failure patterns, and evaluation feedback.
- Add temporal lifecycle state, including candidate, active, superseded, revoked, expired, and quarantined records; preserve immutable source evidence and explicit derivation or supersession links.
- Replace current-Run recency lookup with bounded cross-Session recall that applies scope, identity, workspace, lifecycle, expiration, confidence, and provenance filters before deterministic hybrid relevance scoring.
- Record recall decisions, selected Memory IDs, scores, and subsequent utility feedback so retrieval benefit and negative transfer can be evaluated.
- Add an opt-in AutoDream background service that selects bounded Memory regions, proposes deduplication and consolidation generations, validates provenance and isolation, and atomically publishes or rolls back versioned projections without modifying source Runs.
- Add governed evolution candidates for reusable procedures and policy recommendations. Candidates remain non-authoritative until offline replay and explicit promotion; they cannot modify security floors, permissions, credentials, canonical identity, or active Skills directly.
- Expose safe APIs and audit views for Memory inspection, revocation, consolidation jobs, and evolution candidates.
- Keep conversation context compaction independent from long-term Memory and preserve original Run, Turn, ToolCall, Artifact, and evaluation records as the source of truth.

## Capabilities

### New Capabilities

- `memory-consolidation`: Bounded, versioned, auditable AutoDream jobs that consolidate derived Memory while preserving source evidence, tenant isolation, rollback, and deletion propagation.
- `agent-evolution-governance`: Lifecycle, evaluation gates, promotion, rollback, and hard safety boundaries for procedure and policy candidates learned from execution history.

### Modified Capabilities

- `memory-management`: Add cross-Session namespaces, typed temporal lifecycle, safe hybrid recall, supersession and revocation, recall utility feedback, and deletion propagation.
- `agent-profile-management`: Change `AUTODREAM.md` from a permanently disabled placeholder to a packaged governance protocol that is active only for explicitly enabled background consolidation operations.
- `agent-profile-runtime-composition`: Add a dedicated AutoDream model operation that alone may receive `AUTODREAM.md`, while keeping it excluded from synchronous user-facing operations and isolating all recalled content as untrusted data.

## Impact

- Backend database models, Alembic migrations, repositories, Agent context assembly, model operation routing, lifecycle services, APIs, configuration, audit events, and tests.
- Agent Profile validation and role-to-document selection for a new background-only AutoDream operation.
- Frontend types and audit/management surfaces for Memory and evolution state.
- Operational documentation for scheduling, retention, privacy, deletion, replay evaluation, rollout, and rollback.
- PostgreSQL and SQLite remain supported. The initial implementation uses deterministic relational filtering and scoring without requiring a graph database or external vector service; optional semantic indexes can be introduced behind the same retrieval contract later.
