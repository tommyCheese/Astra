## Why

The unified Agent Loop now provides one public execution path, but transitional runtime assembly, test-only production APIs, misplaced adapters, and application-to-infrastructure dependency cycles still obscure the actual architecture. Removing those remnants now keeps the Loop lightweight, makes package ownership self-explanatory, and prevents future capabilities from accumulating inside the core Runtime.

## What Changes

- Remove production symbols that have no runtime consumers and exist only for obsolete or speculative tests.
- Keep the Agent Loop root limited to contracts, composition, and loop control; move concrete tool execution and runtime adapters to semantically owned packages.
- Replace the Node Runtime application/infrastructure cycle and private-method bridge with an explicit typed application port.
- Replace Trusted Runtime's untyped assembly dictionary and transitional Builder/Composer ownership with typed capability composition.
- Consolidate exact duplicate response, model finalization, identity, outcome, observation, and repository helper behavior where doing so preserves clear ownership.
- Reorganize API projections, skill routes, telemetry persistence, and repository modules so names and directory hierarchy reflect their roles.
- Preserve public API behavior, persistence compatibility, execution semantics, auditability, recovery, and rollback behavior throughout the cleanup.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `backend-code-organization`: Require runtime adapters, projections, repositories, and application services to live under role-accurate packages with dependency direction enforced.
- `backend-runtime-surface-hygiene`: Require test-only and transitional runtime surfaces to be removed once they have no production consumer.
- `application-package-cohesion`: Require the Agent Runtime root and planning package to expose small typed boundaries instead of concrete infrastructure dependencies or private-method bridges.
- `backend-accidental-complexity-control`: Require duplicate model, projection, response, and runtime assembly layers to be consolidated without adding generic wrapper abstractions.

## Impact

- Affects `backend/app/application/agent_runtime`, `application/planning`, `application/subagents`, `application/run_management`, memory consolidation, API projections/routes, infrastructure runtime composition, model clients, and repositories.
- Internal Python import paths will change; repository-internal consumers and tests will be migrated atomically.
- HTTP contracts, database schema, persisted run state, tool behavior, and user-visible execution semantics remain unchanged.
- Active context-compaction, governed-hooks, DAG-execution, and trusted-workbench changes remain behaviorally compatible and must continue passing their existing verification suites.
