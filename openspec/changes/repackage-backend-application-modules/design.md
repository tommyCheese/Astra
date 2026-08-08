## Context

`agent_runtime/services` currently contains 26 peer modules spanning context construction, model decisions, tool governance, completion, recovery, and loop composition. `run_management` contains 13 peers spanning run commands, conversation lifecycle, dispatch, recovery, and read projections. Their flat layouts hide dependency direction and make unrelated responsibilities appear interchangeable.

The refactor must preserve a dirty worktree, frozen Fast/Trusted runtime behavior, internal test coverage, and all existing API and persistence contracts. It must not leave old-path re-export modules because those would preserve the flat surface and duplicate ownership.

## Goals / Non-Goals

**Goals:**

- Give each module one discoverable capability package.
- Establish a mostly acyclic Agent Runtime package dependency direction.
- Keep package roots nearly empty and enforce this with architecture checks.
- Move files and update imports without semantic edits.

**Non-Goals:**

- Splitting individual large classes or changing their behavior.
- Reorganizing `subagents`, `context_compaction`, `skills`, or infrastructure in this change.
- Adding compatibility aliases for old internal paths.
- Changing HTTP APIs, events, schemas, migrations, or runtime policies.

## Decisions

### Agent Runtime package map

- `services/shared`: `progress.py`.
- `services/context`: `assembler.py` (old `context.py`), `memory.py` (old `context_memory.py`), `turn_preparation.py`.
- `services/decisions`: `action_resolution.py`, `control.py`, `model.py`, `root.py`, `skills.py`.
- `services/tooling`: `approval.py`, `authorization.py`, `failure.py`, `invocation.py`, `observation.py`, `plugin_runtime.py`.
- `services/completion`: `verification.py` (old `completion.py`), `gate.py`, `finalization.py`, `memory_candidates.py`, `node_completion.py`.
- `services/execution`: `loop.py`, `root_iteration.py`, `runtime_builder.py`, `runtime_composition.py`, `recovery.py`, `tool_action.py`.

The intended dependency direction is `shared`, then `context/tooling`, then `decisions/completion`, then `execution`. `execution` owns orchestration and may compose all lower packages; lower packages must not import `execution`.

### Run Management package map

- `run_management/lifecycle`: `service.py` (old `application.py`), `contracts.py`, `creation.py`, `continuation.py`, `settings.py`.
- `run_management/execution`: `dispatcher.py`, `recovery.py`.
- `run_management/conversations`: `commands.py`, `context.py`, `lifecycle.py`, `retention.py`.
- `run_management/projections`: `events.py`, `query_service.py`.

All consumers migrate to concrete canonical modules. Subpackage `__init__.py` files remain empty so imports continue to expose ownership rather than a second facade.

### Scope boundary

Other application packages are not included merely because they have many files. `subagents` and `context_compaction` need separate dependency designs; combining them with this change would make failures harder to localize and review.

## Risks / Trade-offs

- [Missed import path] → Repository-wide old-prefix scans, compileall, collection, and complete tests catch it.
- [Import cycle introduced by grouping] → Keep progress in `shared`, tool action in `execution`, and enforce lower-to-execution forbidden dependencies.
- [External code imports internal paths] → Application modules are internal; no compatibility shims are retained. HTTP and persisted contracts remain stable.
- [Large rename obscures semantic changes] → Restrict implementation to moves, import rewrites, package markers, and architecture rules.

## Migration Plan

1. Create empty package markers.
2. Move Agent Runtime modules according to the map and rewrite every import atomically.
3. Move Run Management modules and rewrite every import atomically.
4. Add dependency and root-flatness checks.
5. Run old-path scans, import compilation, targeted suites, and the complete backend suite.

Rollback is a source-level revert; no data or deployment migration is required.

## Open Questions

None.
