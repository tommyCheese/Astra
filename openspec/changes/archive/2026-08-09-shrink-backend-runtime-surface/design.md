## Context

The built-in Web provider was retired, but two production modules still convert its raw `candidates` and `content` payloads into canonical grounding records. Static import analysis across `backend/app` and `backend/tests` shows the Web output normalizer has no consumers and the fragment adapter is consumed only by its unit tests. Active plugin execution already ingests schema-validated `GroundingEvidenceFragment` values through the generic processor boundary.

## Goals / Non-Goals

**Goals:**

- Remove production modules that cannot be reached from an active runtime.
- Prune grounding identity helpers and concrete source/search schemas made unreachable by those removals.
- Preserve generic evidence fragments, ledger behavior, persistence, validation, projection, and trusted completion behavior.
- Prove the resulting import graph and backend suite remain valid.

**Non-Goals:**

- Retiring `legacy-standard-v1` or changing Fast/Trusted dispatch.
- Removing generic grounding, plugin result processors, or isolated provider contracts.
- Changing APIs, persisted data, migrations, or frontend behavior.

## Decisions

1. Delete provider-specific conversion rather than moving it. Keeping an unreachable adapter in a neutral package would preserve code without a runtime owner.
2. Keep `GroundingEvidenceFragment`, its kind and lineage, claims/citations, and passage validation because active ledgers, repositories, processors, projections, and validators consume them.
3. Reduce identity generation to `stable_id`, the only helper imported by active production modules after the retired adapters are removed.
4. Remove only tests whose subject is deleted. Retain end-to-end plugin evidence, ledger, repository, projection, and validation tests as the behavioral safety net.
5. Add an import-graph regression assertion so a retired `infrastructure.tools.web` source module cannot silently return.

## Risks / Trade-offs

- [External code imports an internal deleted helper] → These modules are not public API and have no repository consumers; release notes identify the removal.
- [Grounding behavior is accidentally weakened] → Preserve generic evidence contracts and run focused grounding plus full backend tests.
- [A dynamic resource looks unreachable to static analysis] → Limit deletion to Python modules, excluding bundled Skill scripts and declared plugin transport contracts.
