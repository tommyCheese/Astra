## Why

The retired built-in Web tool left an unreachable raw-output normalization path in production, together with grounding identity and schema types used only by that path. Removing this residue reduces backend surface area without changing active Fast, Trusted, plugin, or evidence-persistence behavior.

## What Changes

- Delete the unreferenced built-in Web output normalizer.
- Delete the test-only raw search/read result-to-fragment adapter.
- Reduce grounding identity helpers and schemas to the types still consumed by active plugin evidence ingestion, persistence, projection, and validation.
- Remove tests that exercise only the retired conversion path while retaining ledger, persistence, projection, validation, and runtime integration coverage.
- Do not retire `legacy-standard-v1`, isolated provider contracts, or any user-visible subsystem in this change.

## Capabilities

### New Capabilities

- `backend-runtime-surface-hygiene`: Production backend modules must remain reachable from an active runtime or declared dynamic resource boundary, and retirement changes remove provider-specific conversion residue without weakening generic contracts.

### Modified Capabilities

None.

## Impact

Affected code is limited to `app/infrastructure/tools/web`, `app/domain/grounding`, and their focused tests. There are no API, database, event, runtime dispatch, permission, Artifact, or plugin-envelope changes.
