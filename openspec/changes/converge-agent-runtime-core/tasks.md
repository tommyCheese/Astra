## 1. Baseline and Ownership Inventory

- [x] 1.1 Record the current standard/trusted dispatch, iteration, action, recovery, finalization, and event call paths with their responsibility owners
- [x] 1.2 Inventory Decision, Action, Observation, Outcome, Checkpoint, runtime identity, and Run projection representation chains and identify each canonical owner or deletion target
- [x] 1.3 Capture current production lines, modules, classes, functions, public symbols, import edges, largest functions, OpenAPI signature, ORM tables, and migration head as the refactor baseline
- [x] 1.4 Add paired characterization coverage for standard/trusted answer, tool success, permission denial, approval wait/resume, cancellation, idempotent recovery, result-unknown, Skill, Memory, Workspace/Artifact, and Subagent behavior
- [x] 1.5 Add architecture assertions that forbid new production imports of the future canonical Loop from `planning`, `run_management`, interfaces, or peripheral administration in the wrong direction

## 2. Canonical Loop Contracts and Composition

- [x] 2.1 Add canonical `LoopState`, model decision, action, observation, capability identity, and discriminated Loop outcome contracts with serialization/recovery tests
- [x] 2.2 Define fixed typed capability slots for context, decision, action, observation, progress, completion, and lifecycle contributions without arbitrary event subscription
- [x] 2.3 Define mandatory model, state, action, cancellation, and event ports without importing SQLAlchemy, HTTP, provider transports, or concrete platform capabilities
- [x] 2.4 Implement deterministic `RuntimeComposition` validation for identity/version/digest/order, unique mandatory owners, safety coverage, and forbidden duplicate providers
- [x] 2.5 Add standard and trusted frozen composition builders plus compatibility aliases for persisted `fast-v1`, `trusted-v1`, and `legacy-standard-v1` identities
- [x] 2.6 Add composition tests for missing mandatory stages, duplicate capability identity, ordering conflict, digest drift, untrusted registration, and resume compatibility

## 3. Minimal Agent Loop

- [x] 3.1 Implement the fixed load → context → decision → route/action → observation → policy → outcome Loop over canonical contracts and ports
- [x] 3.2 Implement canonical state-change validation so capabilities cannot mutate undeclared or protected state
- [x] 3.3 Implement bounded iteration, cancellation checks, classified failures, and terminal convergence without importing concrete optional capabilities
- [x] 3.4 Add Loop unit tests for continue, wait, complete, blocked, failed, cancelled, invalid contribution, and budget exhaustion
- [x] 3.5 Add an architecture/readability assertion for the dispatch-to-Loop and action-to-observation path and keep Loop modules within configured budgets

## 4. Standard Composition Migration

- [x] 4.1 Adapt current Fast model decisions and streamed answers to the canonical decision contract without a second controller
- [x] 4.2 Adapt Fast snapshot load/save/recovery once at the state port and remove Fast-only in-loop snapshot mutation helpers
- [x] 4.3 Route standard context, explicit/automatic Skills, bounded Memory behavior, and lightweight Subagent behavior through typed capabilities
- [x] 4.4 Route standard actions through the mandatory shared action boundary and canonical observation contract
- [x] 4.5 Route standard waiting, answer, blocked, error, cancellation, metrics, and finalization through shared Loop outcomes and Run management
- [x] 4.6 Pass standard characterization, latency, usage, approval, recovery, cancellation, Workspace/Artifact, Skill, Memory, and Subagent tests
- [x] 4.7 Delete `FastAgentExecutor`, `FastToolStage`, Fast-only decision/observation/result mirrors, and replaced mappings without a compatibility controller

## 5. Trusted Root Composition Migration

- [x] 5.1 Implement TaskContract and initial Plan preparation as the trusted Planning capability without Loop imports of concrete planning code
- [x] 5.2 Adapt trusted context and root decisions to canonical Loop contracts and remove trusted-only mirror DTOs
- [x] 5.3 Implement Reflection, Verification, Evidence, and CompletionGate as typed trusted capabilities
- [x] 5.4 Route trusted root actions through the same mandatory action and canonical observation boundary used by standard
- [x] 5.5 Route trusted waiting, clarification, approval, replan, completion, failure, cancellation, and finalization through shared Loop outcomes
- [x] 5.6 Pass trusted root characterization, Plan confirmation/revision, approval, recovery, completion, Skill, Memory, Evidence, and Subagent tests
- [x] 5.7 Delete the replaced trusted root controller stages, result mirrors, and mode-specific action/finalization mappings

## 6. Trusted DAG and Node Migration

- [x] 6.1 Move deterministic ready-node selection, coordinator ownership, resource/budget leases, and node lifecycle into `application.planning`
- [x] 6.2 Represent a ready NodeExecution as bounded trusted capability input to the same Agent Loop
- [x] 6.3 Route node decisions, actions, observations, progress, reflection, and terminal results through canonical contracts
- [x] 6.4 Preserve branch approval, fan-out/fan-in, conflict, retry, cancellation, replan drain, heartbeat, and result-unknown semantics
- [x] 6.5 Pass parallel DAG, recovery, event replay, CompletionGate barrier, graph snapshot, and frontend reducer/component tests
- [x] 6.6 Delete `application.runner` and migrate every production/test import directly to its concept owner without re-exports

## 7. Run Lifecycle, Recovery, and Infrastructure Ownership

- [x] 7.1 Move provider/model/tool registry construction and shared client lifecycle out of Run execution into typed infrastructure composition
- [x] 7.2 Make `run_management` the sole owner of dispatch, continuation, cancellation entry, recovery entry, terminal persistence, and public Run event publication
- [x] 7.3 Collapse standard/trusted recovery into one canonical recovery decision with persisted-shape readers only at the state adapter boundary
- [x] 7.4 Collapse finalization, answer streaming, metrics, and classified error mapping into shared Run lifecycle services
- [x] 7.5 Verify API-process cancellation, SQLite transaction release, schedule dispatch, approval resume, and restart behavior
- [x] 7.6 Remove overlapping `RunEngine`, dispatcher, recovery, answer-stream, and finalizer wrappers after their owners migrate

## 8. Representation and Mapping Reduction

- [x] 8.1 For every inventory entry, document the distinct invariant of each surviving API schema, ORM record, domain object, public projection, Runtime contract, or dataclass
- [x] 8.2 Delete field-for-field Schema/dataclass/domain/projection mirrors for runtime decisions, actions, observations, outcomes, checkpoints, and capability identities
- [x] 8.3 Replace dict round-trips between typed runtime values with direct canonical values except at JSON/database/event boundaries
- [x] 8.4 Reduce ORM-to-runtime and runtime/ORM-to-public conversion to one tested boundary conversion per path
- [x] 8.5 Delete single-consumer projector/mapper/adapter classes and colocate any remaining ephemeral grouping with its sole consumer
- [x] 8.6 Add architecture checks preventing mirror suffix chains, generic projector/mapper wrappers, and canonical Runtime types from returning to `common.schemas`
- [x] 8.7 Prove net reduction in production lines, modules, classes, functions, mappings, and public symbols against the recorded baseline

## 9. Peripheral Capability Isolation

- [x] 9.1 Remove AutoDream/consolidation administration and background scheduling references from core Runtime construction and expose only its independent application service
- [x] 9.2 Remove Evolution candidate lifecycle/promotion administration references from core Runtime construction and preserve its independent API behavior
- [x] 9.3 Keep Credential Grant administration outside Loop state and expose credential resolution only through a narrow mandatory action port
- [x] 9.4 Separate Memory and Skill serving contributions from their authoring, management, consolidation, and publication control planes
- [x] 9.5 Verify disabled/default-isolated capability behavior, management APIs, deletion propagation, audit, and frontend settings remain unchanged

## 10. Legacy Removal and Full Verification

- [x] 10.1 Audit persisted runtime identities and resumable records, define removal evidence for `legacy-standard-v1`, and retain only necessary boundary readers
- [x] 10.2 Remove obsolete Runtime feature flags, compatibility branches, re-exports, events, schemas, and tests with no real persisted or external consumer
- [x] 10.3 Run Ruff, architecture checks, compileall, full pytest, migration/Alembic checks, and strict OpenSpec validation
- [x] 10.4 Run frontend lint, typecheck, tests, production build, and browser verification for both answer modes, approvals, cancellation, graphs, history, and settings
- [x] 10.5 Run standard/trusted latency, Token, cost, model-call, tool-call, recovery, and success-rate comparisons and investigate material regressions
- [x] 10.6 Update architecture, runtime, persistence, operator, and contributor documentation with the final single-Loop plugin model
- [x] 10.7 Record final before/after metrics and prove all three acceptance gates: architecture cleanliness, code cleanliness, and functional correctness
