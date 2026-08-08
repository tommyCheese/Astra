## Why

Several application packages have accumulated 13–25 peer modules with mixed responsibilities, making ownership and dependency direction difficult to see. Repackaging the two worst areas by functional cohesion will make navigation and future changes safer without changing runtime behavior.

## What Changes

- Repackage `application/agent_runtime/services` into explicit context, decision, execution, tooling, and completion subpackages.
- Repackage `application/run_management` into run lifecycle, conversation, and projection subpackages.
- Update all production, test, benchmark, and architecture references to the canonical new module paths.
- Remove old module paths instead of retaining compatibility re-export shims.
- Add architecture checks that cap root-level module counts and protect the new package boundaries.
- Preserve all public HTTP, persistence, event, Fast/Trusted runtime, continuation, scheduling, and recovery behavior.

## Capabilities

### New Capabilities

- `application-package-cohesion`: Application modules are grouped by owned capability with enforceable package boundaries and bounded root-level file counts.

### Modified Capabilities

None.

## Impact

This is an internal Python import-path refactor affecting backend application packages and their consumers. It introduces no API, schema, migration, event, configuration, or frontend changes. Internal imports of the old paths are intentionally breaking and must migrate atomically.
