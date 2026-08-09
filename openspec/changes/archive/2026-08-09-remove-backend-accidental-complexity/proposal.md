## Why

The backend implements a broad agent platform, but its current size is also inflated by duplicated internal contracts, single-use service and projection types, fragmented repositories, and transitional compatibility paths. The next refactor must reduce accidental complexity primarily by deleting code while preserving supported external behavior and making core execution flows readable from fewer files.

## What Changes

- Remove obsolete compatibility branches, adapters, re-exports, and transitional representations that no supported runtime path requires.
- Collapse stateless single-use service classes and their dedicated input/result wrappers into cohesive functions or existing owners.
- Consolidate repository fragments and projections that split one aggregate without providing an independent transaction, policy, or substitution boundary.
- Reuse canonical internal models instead of copying the same state through API, runtime, projection, and persistence-specific containers.
- Co-locate consecutive runtime steps when separate modules only forward data and make the execution path harder to follow.
- Add architecture checks and a documented complexity budget so new one-use abstractions and compatibility layers require explicit justification.
- Preserve public API behavior, persistence compatibility, permission enforcement, auditability, and recovery guarantees.

## Capabilities

### New Capabilities

- `backend-accidental-complexity-control`: Defines deletion-first architecture rules, canonical internal contracts, justified abstraction boundaries, and measurable complexity budgets for the backend.

### Modified Capabilities

None. This change simplifies implementation without changing supported product behavior.

## Impact

- Affects `backend/app`, especially agent runtime orchestration, run repositories and projections, compatibility adapters, and internal data-transfer objects.
- Updates backend architecture validation and refactoring documentation.
- Requires broad import migration and regression testing because internal module and class paths may be removed without compatibility re-exports.
- Does not intentionally change HTTP contracts, database schema, authorization outcomes, persisted state semantics, or user-visible agent behavior.
