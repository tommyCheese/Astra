## Context

Astra currently has two production Agent controllers. `FastAgentExecutor` owns a compact model/action loop and a Fast-specific snapshot, while `AstraAgentLoop` and `RunCoordinator` own trusted root and DAG execution. `RunEngine` chooses between them and also constructs providers, binds profiles and Skills, prepares trusted Plans, streams answers, maps failures, and coordinates recovery. `run_management` separately owns Run creation, dispatch, continuation, cancellation, and recovery entry.

The safety boundary is already partly shared: Fast adapts its actions into the trusted authorization, approval, invocation, Workspace, Artifact, Sandbox, Skill, Memory, and plugin stages. The remaining duplication is concentrated in controller state, decision/observation representations, terminal routing, recovery, finalization, event naming, and orchestration ownership.

The repository also repeats representations across API Pydantic schemas, runtime Pydantic schemas, dataclasses, ORM records, domain objects, projection models, and dictionaries. Many are necessary boundaries, but others copy identical fields without adding validation, behavior, redaction, versioning, or ownership.

This refactor must preserve the current public and persisted behavior while making three outcomes inseparable: clean architecture, clean code, and working functionality.

## Goals / Non-Goals

**Goals:**

- Replace Fast and Trusted controllers with one minimal Agent Loop.
- Compose standard and trusted behavior from deterministic, typed capability plugins frozen by the Run Profile.
- Keep mandatory action safety, persistence, recovery, and cancellation invariants installed in every composition.
- Eliminate generic `runner` ownership and give Run lifecycle, Agent iteration, planning, and infrastructure composition one owner each.
- Delete duplicate runtime models and mapping layers as each vertical slice migrates.
- Move AutoDream, Evolution, Credential administration, and other non-serving control planes outside the core Loop without changing their current public behavior.
- Preserve HTTP/OpenAPI, SSE, database, approval, recovery, cancellation, security, Workspace/Artifact, Skill, Memory, Subagent, and answer-mode behavior.
- Make the primary dispatch-to-iteration and action-to-observation paths readable through a small number of cohesive files.

**Non-Goals:**

- Adding a new product capability or enabling a currently disabled capability.
- Implementing a general lifecycle Hook bus or loading untrusted Runtime plugins.
- Removing persisted runtime identities or rewriting historical Run data in place.
- Replacing the database, introducing a distributed queue, or redesigning the frontend.
- Flattening all layers into ORM-driven business logic; real validation, persistence, domain, and redaction boundaries remain.
- Combining this work with Graph Memory, automatic Evolution promotion, AutoDream rollout, remote Subagent execution, or enterprise identity.

## Decisions

### 1. One fixed Loop with typed outcomes

The core Loop owns only this control flow:

```text
load canonical LoopState
→ run pre-iteration policies
→ collect bounded context contributions
→ request one model decision
→ route control decision or resolve one action
→ execute through the mandatory action boundary
→ record one canonical observation
→ run observation/progress/completion policies
→ continue | wait | complete | blocked | failed | cancelled
```

The Loop does not import Planning, Memory, Skill, Subagent, Evidence, AutoDream, Evolution, Credential administration, HTTP, SQLAlchemy, model-provider transports, Tool Provider discovery, Sandbox implementations, or frontend projections.

The terminal protocol is one discriminated `LoopOutcome`. Capability code cannot directly update the public Run terminal state; `run_management` persists the selected outcome through one application port.

Alternative: keep Fast and Trusted controllers and only share the tool pipeline. Rejected because the selected target is one Runtime, and two controllers would retain duplicate recovery, event, finalization, and model-state semantics.

### 2. Capability plugins use fixed typed slots, not arbitrary hooks

Runtime composition contains ordered implementations of a small fixed contract set:

- `ContextContributor`
- `DecisionPolicy`
- `ActionProvider`
- `ObservationProcessor`
- `ProgressPolicy`
- `CompletionPolicy`
- `LifecycleObserver`

A capability may implement multiple slots, but each slot has a bounded input and output. Plugins cannot mutate `LoopState` directly, obtain a Repository or database Session, reorder mandatory stages, or subscribe to arbitrary event names. State changes are returned as typed contributions and applied by the Loop through its state port.

Runtime capability plugins are trusted platform implementations registered by the composition root. They are distinct from external Tool Provider Plugins and the proposed governed Hook system. No Task Workspace scan or runtime import participates in composition.

Alternative: a general before/after event bus. Rejected because it makes ordering and mutation authority implicit and recreates a second orchestration system.

### 3. Standard and trusted are frozen compositions

Every Run freezes a `RuntimeComposition` containing capability identity, version, configuration digest, ordering, and mandatory-slot coverage.

Standard installs:

- model decision and answer handling;
- mandatory action resolution, schema validation, effect analysis, permission, approval, invocation, observation persistence, cancellation, and recovery;
- explicitly selected/eligible lightweight Skill, Memory, and Subagent contributors only where current standard behavior already supports them.

Trusted installs the same base plus:

- TaskContract/Planning capability;
- DAG work-selection and node-progress capability;
- Reflection capability;
- Verification and CompletionGate capabilities;
- trusted Evidence and Subagent completion barriers.

The answer mode selects a composition, not a controller. Restore uses the frozen composition identity. Existing `fast-v1`, `trusted-v1`, and `legacy-standard-v1` fields remain readable during this change, but they map to composition builders rather than separate Loop implementations.

### 4. Mandatory invariants are adapters but never optional

The Loop depends on ports for model calls, state/checkpoint persistence, action execution, cancellation, and event publication. Implementations are adapters, but the composition validator refuses to start without all mandatory ports and safety policies.

Permission, schema validation, effect analysis, approval integrity, Sandbox/Workspace enforcement, cancellation fencing, and result-unknown recovery cannot be removed or reordered by answer mode or an optional capability.

Alternative: model every safety stage as a normal optional plugin. Rejected because a malformed composition could silently weaken enforcement.

### 5. Target ownership removes `runner` and the Fast controller package

Target application ownership:

```text
run_management
  Run creation, dispatch request, continuation, cancellation,
  recovery entry, terminal persistence, public Run events

agent_runtime
  contracts.py      canonical Loop types and capability slots
  loop.py           minimal fixed control flow
  composition.py    validation and frozen capability assembly
  action.py         mandatory action-to-observation boundary

planning
  trusted contract/Plan generation, DAG state, scheduling,
  node work selection and planning capability implementation
```

Infrastructure composition constructs model, repository, tool, sandbox, plugin, workspace, and artifact adapters. Interface modules only map HTTP/SSE requests and responses.

`app.application.runner` and the independent `app.application.fast_agent_runtime` are removed after their consumers migrate. Compatibility re-export modules are prohibited.

### 6. One canonical representation per concept

Canonical persisted or plugin-crossing Loop values live in `agent_runtime/contracts.py` and use Pydantic because they require validation, discriminated unions, serialization, and recovery. Ephemeral local groupings may use a colocated dataclass only when they are not field-for-field mirrors of a canonical value.

Representation retention rules:

| Representation | Required invariant |
| --- | --- |
| API/request schema | validates untrusted protocol input or versions a public response |
| ORM record | maps a real table, relation, transaction, or query identity |
| Domain object | owns behavior/invariants that do not belong to API, Runtime, or persistence |
| Public projection | performs authorization, redaction, aggregation, or stable version conversion |
| Runtime contract | canonical value exchanged by Loop, capability, persistence, or recovery |
| Dataclass | ephemeral non-serialized grouping with no canonical mirror |

Each migrated concept records its existing chain and selected canonical owner. A surviving intermediate representation must name its unique invariant. Otherwise it and its mapper are deleted.

Repository adapters perform at most one ORM-to-canonical conversion. Interface projection performs at most one canonical/ORM-to-public-schema conversion. Dict round-trips between typed values are prohibited except at JSON/database/event boundaries.

### 7. Direct target evolution uses verified vertical slices

The code moves directly toward the single Loop; no new long-lived dual-controller abstraction is introduced. Implementation remains continuously verifiable through vertical slices:

1. freeze behavior and architecture baselines;
2. introduce canonical contracts and composition validation;
3. migrate one complete standard iteration through the single Loop;
4. migrate trusted root and node iterations through the same Loop;
5. move planning ownership and remove `runner`;
6. remove the Fast controller package and duplicate contracts;
7. isolate peripheral control planes and finish projection/model cleanup.

Temporary old paths may exist inside an unfinished slice, but a task cannot be marked complete until its old path, mirror models, and compatibility facade are removed.

### 8. Every slice passes three gates

Architecture gate:

- import graph and forbidden dependency checks pass;
- responsibility owner and primary call path are simpler;
- module/class/public-symbol counts do not increase without an explicitly removed counterpart;
- no new compatibility facade, generic manager, or arbitrary hook.

Code gate:

- duplicate types, mappings, branches, and dead tests for the migrated slice are deleted;
- default module/function/complexity budgets are met or the pre-existing hotspot is smaller;
- focused code-reading path is documented by a characterization test or architecture assertion.

Functional gate:

- paired standard/trusted characterization cases pass;
- approval wait/resume, cancellation, idempotent recovery, non-idempotent result-unknown, Skills, Memory, Subagent, Workspace/Artifact, SSE, and errors remain stable where applicable;
- OpenAPI, ORM/Alembic, event, and frontend contracts remain stable;
- full suites pass at phase boundaries.

### 9. Peripheral capabilities cannot route the Loop

AutoDream and Evolution remain separate application use cases. Credential administration remains in permissions/infrastructure. They may expose a bounded contributor or observer only after a later change proves a serving use case. They cannot add branches to `loop.py`, access mutable Loop internals, or be instantiated merely because their database tables exist.

Memory recall/writeback, Skill context, Subagent delegation, and Evidence are serving capabilities and may remain in a Runtime composition, but their background administration and lifecycle management stay outside the Loop.

## Risks / Trade-offs

- [A direct single-Loop migration can create a large behavioral diff] → Move vertical slices, keep characterization tests green, and delete old paths within each completed slice.
- [A plugin architecture can create more abstractions than it removes] → Use only fixed slots with multiple real compositions; enforce net module/class/public-symbol reduction.
- [One canonical model can become a giant universal DTO] → Split only by stable concept, not layer; capabilities receive bounded views rather than the entire state.
- [Pydantic Runtime contracts can leak into HTTP contracts] → Interfaces own public schemas and explicit redaction/version projection; Runtime contracts are not automatically exported.
- [Removing projections can leak internal fields] → Retain projections whenever authorization, redaction, aggregation, or public-version stability is real, and test field allowlists.
- [Historical Fast snapshots need their old controller] → Add a compatibility reader that converts persisted snapshots once at the state-port boundary; do not keep a compatibility controller.
- [Capability ordering can change results] → Freeze ordered identities/digests and validate mandatory-slot uniqueness and declared ordering constraints.
- [Moving incomplete capabilities can accidentally remove UI/API behavior] → Treat relocation as behavior preserving; product removal or enablement requires a separate change.

## Migration Plan

1. Record current architecture inventory, import graph, public contracts, runtime/model chains, and paired behavior fixtures.
2. Add canonical Loop contracts, mandatory ports, capability slots, composition validation, and standard/trusted composition fixtures without switching production dispatch.
3. Route new standard Runs through the single Loop using adapters to existing model, action, state, event, and finalization behavior; delete `FastAgentExecutor` and Fast-only mirror types once parity passes.
4. Route trusted root iterations through the same Loop, supplying planning/reflection/verification/completion capabilities; preserve Plan and AgentState persistence.
5. Route trusted node work through the same iteration contract, move coordinator/node scheduling to `planning`, and delete `runner`.
6. Collapse recovery/finalization/event mapping into Run lifecycle and canonical state adapters; retain only persisted-shape readers with explicit removal criteria.
7. Inventory and delete redundant models/mappers across migrated paths, then isolate peripheral administration from Runtime composition.
8. Run full architecture, backend, migration, OpenSpec, frontend, benchmark, and behavior-parity verification; update the architecture map and main specs.

Rollback during migration selects the last complete production dispatch path and retains all new canonical checkpoint data. A rollback must not delete Run, ToolCall, Approval, Artifact, Evidence, Memory, or AgentExecution history. After both modes have migrated and old controllers are deleted, rollback is a code-version rollback against the same public/persisted contracts, not a runtime feature flag that permanently preserves two controllers.

## Open Questions

- Whether canonical Loop contracts should remain in one `contracts.py` or split only after the file exceeds the architecture budget.
- Whether existing `runtime_kind` values remain indefinitely as persisted composition aliases or are removed in a later breaking persistence change.
- Which current event names are public compatibility contracts and which Fast-specific names can be normalized after a deprecation audit.
