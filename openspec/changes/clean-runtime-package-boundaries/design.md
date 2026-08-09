## Context

The previous runtime convergence established `AgentLoop` as the single public control loop and made Standard and Trusted behavior composable through fixed capability slots. The remaining implementation still contains transitional structures: production APIs used only by tests, concrete runtime adapters under `infrastructure.bootstrap`, a Node Runtime cycle between application and infrastructure, and Trusted assembly based on `dict[str, Any]`, Builder/Composer objects, and Stage classes. Several projections, telemetry repositories, route modules, and generic `models.py` files also live under misleading owners.

This change is behavior-preserving. HTTP and SSE contracts, database schema, persisted runtime state, permissions, audit records, recovery, rollback, and model/tool behavior are constraints rather than migration targets.

## Goals / Non-Goals

**Goals:**

- Make the shortest reading path `contracts.py -> composition.py -> loop.py`.
- Give runtime adapters, projections, persistence, planning, and tool execution one canonical, role-accurate owner.
- Remove unused speculative surfaces and field-for-field or pass-through abstractions.
- Replace the Node Runtime private-method bridge and application/infrastructure cycle with a typed application contract.
- Replace untyped Trusted assembly with typed capability construction and retire transitional Builder/Composer ownership where it adds no independent policy.
- Produce a measurable net reduction while preserving all supported behavior.

**Non-Goals:**

- Add AutoDream, Evolution, Credential Broker, hooks, or other future capabilities to the core Runtime.
- Change public API schemas, database migrations, execution policy, or rollout behavior.
- Merge boundary models merely because their fields currently resemble internal models.
- Redesign memory, subagent, or DAG semantics beyond the ownership changes needed here.

## Decisions

### Delete production code only after consumer classification

A symbol is removable when static production usage, registrations, dynamic resource loading, persisted-state decoding, and active change artifacts show no supported consumer. Dedicated tests for the removed private surface are deleted; no compatibility re-export is added.

Alternative: retain speculative APIs for possible future integration. Rejected because it makes the current architecture describe intentions rather than behavior.

### Keep the Agent Runtime root structural

Only the loop, its contracts, and capability composition remain at the package root. Concrete action-boundary behavior moves under `agent_runtime.tooling`; environment-specific composition moves from `infrastructure.bootstrap` to `infrastructure.runtime`.

Alternative: leave files in place and document their roles. Rejected because navigation and import boundaries should enforce the design.

### Use typed contracts at application/infrastructure seams

Planning defines the public Node execution operations it needs. Infrastructure implements those operations without calling executor-private methods. Trusted capability construction uses a typed dependency object or explicit parameters rather than a generic value bag.

Alternative: rename current adapters and Stage objects without changing their interaction. Rejected because that preserves the hidden second pipeline.

### Consolidate into owners, not utility buckets

Exact duplicates move to the closest semantic owner: lifecycle response construction stays with run lifecycle contracts, port identity stays with runtime composition, model finalization gets a default client implementation, and database time/lookup behavior stays with database/repository owners. No generic `utils.py` package is introduced.

### Migrate canonical imports atomically

Moved modules receive no forwarding files or package-level compatibility aliases. Production code, tests, scripts, architecture checks, and documentation are updated in the same change.

## Risks / Trade-offs

- [Dynamic import or external private API consumer is missed] -> scan string imports and registration tables, preserve only documented public/plugin boundaries, and run the full suite.
- [Large module moves obscure behavioral changes] -> separate deletion, moves, typed-boundary changes, and assembly simplification into independently verified cohorts.
- [Active OpenSpec changes touch adjacent packages] -> avoid changing their declared behavior and run their focused verification tests after each cohort.
- [Typed composition initially adds a type] -> allow one cohesive dependency value object only if it eliminates generic dictionaries and multiple transfer models overall.
- [Removing Stage classes increases function size] -> keep operations grouped by capability modules and extract only cohesive stateful or substitutable behavior.

## Migration Plan

1. Record current metrics and remove high-confidence unused surfaces.
2. Move runtime adapters, action boundary, telemetry persistence, projections, and route modules to canonical owners; rewrite imports atomically.
3. Introduce the typed Node execution boundary and remove the dependency cycle/private calls.
4. Construct Trusted slots directly from typed dependencies and remove obsolete Builder/Composer/Assembly/Stage code as consumers disappear.
5. Consolidate exact duplicates and generic filenames, update architecture guards and documentation, then record final metrics.
6. Run formatting, type/static checks, architecture checks, focused suites, and the complete backend suite.

Rollback is commit/cohort based. No database rollback or compatibility shim is required because external and persisted contracts do not change.

## Open Questions

None. The target boundaries and behavior-preservation constraints are established by the prior runtime convergence and this audit.
