## 1. Baseline and unused surface removal

- [x] 1.1 Record production structure metrics and verify the pre-change architecture suite
- [x] 1.2 Remove test-only subagent rollout, benchmark, exchange, checkpoint, and retry surfaces with their dedicated tests
- [x] 1.3 Remove unused artifact pruning, recovery policy, approval matcher, and memory model-output normalization surfaces with their dedicated tests

## 2. Canonical package ownership

- [x] 2.1 Move the concrete action boundary from the Agent Runtime root into the tooling capability and migrate imports
- [x] 2.2 Move Standard, Trusted, and Node runtime adapters and runtime dependency modules from bootstrap into `infrastructure.runtime`
- [x] 2.3 Move subagent telemetry persistence into infrastructure repository ownership and retain application-level observation operations only
- [x] 2.4 Move run and conversation read projections out of repository ownership into a canonical projections package
- [x] 2.5 Repackage flat skill API modules under an owned `interfaces.api.skills` package
- [x] 2.6 Rename generic runtime, model-provider, execution-service, and memory-consolidation module owners where names do not describe their responsibility

## 3. Typed execution boundaries

- [x] 3.1 Define a public typed Node execution contract owned by planning/application code
- [x] 3.2 Replace planning-to-bootstrap imports and infrastructure calls to executor-private methods with the typed Node boundary
- [x] 3.3 Replace `PlanPreparationMixin` inheritance with explicit planning composition
- [x] 3.4 Replace Trusted generic collaborator/infrastructure dictionaries with typed dependencies
- [x] 3.5 Compose Trusted fixed capability slots directly and remove obsolete Builder, Composer, Assembly, and superseded Stage abstractions

## 4. Duplicate and representation reduction

- [x] 4.1 Consolidate runtime port identity, outcome, and canonical observation behavior
- [x] 4.2 Consolidate lifecycle response construction and default model finalization behavior
- [x] 4.3 Consolidate repeated UTC normalization and aggregate lookup behavior without introducing a generic utilities package
- [x] 4.4 Remove redundant schema, dataclass, domain, and projection transfer models that cross no trust or persistence boundary

## 5. Enforcement and verification

- [x] 5.1 Update architecture checks to enforce the Runtime root, infrastructure runtime ownership, projection ownership, and Node dependency direction
- [x] 5.2 Update runtime/package documentation and migration maps to the canonical paths
- [x] 5.3 Run formatting, static checks, focused runtime/planning/API tests, active-change verification suites, and the complete backend suite
- [x] 5.4 Record final structure metrics and confirm net reduction with no compatibility forwarding modules
