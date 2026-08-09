# Single-Loop Refactor Baseline

Baseline commit: `997cc0e63988c63d13862724899448a84636546b`

This inventory is the comparison point for `converge-agent-runtime-core`. It records observable owners and structures, not desired compatibility APIs. Re-run the commands in each section against the same environment before comparing a later slice.

## 1. Current production call paths

### 1.1 Shared Run entry

```text
interfaces.api.runs / schedules / conversation commands
  → run_management.lifecycle.RunApplicationService
  → run_management.lifecycle.RunCreator or RunContinuationService
  → run_management.execution.InProcessRunDispatcher
  → runner.engine.start_run_in_process
  → runner.engine.RunEngine.run
```

Current owners and overlaps:

| Concern | Current owner(s) | Target owner | Finding |
| --- | --- | --- | --- |
| Create/continue/cancel Run | `run_management` | `run_management` | Keep |
| Background task ownership | `run_management.execution.dispatcher` | `run_management` | Keep, replace import of `runner` starter |
| Provider/tool construction | `runner.engine.RunEngine` | infrastructure composition | Move |
| Mode/controller dispatch | `runner.engine.RunEngine._run_with_repo` | frozen runtime composition | Delete branch |
| Top-level error/final status | `runner.engine`, Fast/Trusted finalizers | `run_management` | Merge |
| Answer stream buffering | `runner.answer_stream`, `runner.engine` | `run_management` lifecycle adapter | Move |

### 1.2 Standard path

```text
RunEngine._run_with_repo
  → RunEngine._execute_fast_runtime
  → FastAgentExecutor.run
      → FastContextBuilder.build
      → ModelClient.fast_decide
      → FastToolStage.execute
          → PermissionAuthorizationStage
          → ApprovalRoutingStage
          → ToolInvocationStage
          → FastObservation conversion
      → FastRecovery / FastRuntimeSnapshot mutation
      → FastFinalizer.persist
  → RunEngine answer-stream completion
```

Responsibility findings:

- Fast owns a second controller, decision/action/observation/result types, snapshot mutation, recovery and finalization.
- Tool safety and side-effect execution already reuse the shared authorization, approval, invocation, Workspace, Artifact, Sandbox, Skill, Memory and plugin stages.
- Fast converts `FastAgentAction` to `AgentDecision` immediately before the shared action boundary, then converts the result back to `FastObservation`.
- `FastAgentExecutor.run` is the largest and most complex backend function: 230 lines, complexity 37.

### 1.3 Trusted root path

```text
RunEngine._run_with_repo
  → RunEngine._prepare_trusted_run
      → TaskContract/model planning
      → PlanService / PlanRepository
      → AgentState initialization or plan-confirmation wait
  → RunEngine._execute_trusted_runtime
  → RunEngine._execute_agent_loop
  → AstraAgentLoop.run
      → AgentRuntimeBuilder.build
      → RootRuntimeComposer.compose
      → execute_turns
      → RootAgentIterationStage.execute
          → turn/context preparation
          → root model decision
          → completion/control routing
          → ToolActionStage / InvocationPipeline
              → PermissionAuthorizationStage
              → ApprovalRoutingStage
              → ToolInvocationStage
              → ObservationNormalizationStage
              → progress/reflection
      → FinalizationStage.execute
  → RunEngine._finalize_agent_loop
```

Responsibility findings:

- `AstraAgentLoop` is named as the Loop but constructs concrete permissions, planning, reflection, completion, plugin and Subagent collaborators.
- `AgentRuntimeBuilder` and `RootRuntimeComposer` add two assembly layers between the Loop and iteration stage.
- `RootAgentIterationStage` owns trusted-only routing and mutates `RootRuntimeState`; `RunEngine` separately owns planning, answer streaming and public finalization.
- Shared action stages are embedded inside a trusted progress/reflection pipeline, forcing Fast to reassemble a partial parallel path.

### 1.4 Trusted parallel node path

```text
RunEngine._execute_trusted_runtime
  → RunExecutionRecovery.scan
  → RunCoordinator.run
      → planning PlanScheduler / NodeExecutionRepository
      → ReadOnlyAgentNodeExecutor
          → node-local Agent runtime/tool collaborators
      → merge NodeExecutionResult
  → AstraAgentLoop root synthesis/finalization
```

Responsibility findings:

- DAG scheduling concepts are split across `runner.coordinator`, `runner.node_worker`, `runner.concurrency`, `planning.scheduler` and repositories.
- Node execution has another result representation and execution assembly path.
- Target ownership is `planning` for scheduling/work selection and the shared Agent Loop for one node iteration.

### 1.5 Recovery, cancellation and finalization

| Behavior | Current path | Duplication/ownership issue |
| --- | --- | --- |
| Standard recovery | `FastRecovery` inside `FastAgentExecutor` | Fast-only result/action/observation conversions |
| Trusted execution recovery | `run_management.execution.RunExecutionRecovery`, runtime recovery stage, coordinator replay | Multiple scan/result types and owners |
| Approval continuation | `RunContinuationService` → dispatcher → mode-specific recovery | Shared entry, divergent controller resume |
| Cancellation entry | `RunApplicationService.cancel` → `InProcessRunDispatcher.cancel` | Correct outer owner |
| Cancellation cleanup | `RunEngine.run` plus mode-specific events/state | Duplicated terminal convergence |
| Standard finalization | `FastFinalizer` plus `RunEngine` stream completion | Split owner |
| Trusted finalization | runtime `FinalizationStage` plus `RunEngine._finalize_agent_loop` | Split owner |
| Parallel finalization | `RunCoordinator` barrier/result plus root Loop synthesis | Scheduling and completion ownership mixed |

## 2. Representation-chain inventory

The “target” column identifies the canonical owner. Items marked delete are field mirrors or routing wrappers unless later inspection proves a distinct invariant.

| Concept | Current representations/conversions | Canonical target | Initial deletion/retention decision |
| --- | --- | --- | --- |
| Model decision | `FastAgentAction` → `AgentDecision`; `DecisionStageResult`; `RootDecisionResult`; persisted turn decision dict | `agent_runtime.contracts.LoopDecision` | Delete Fast action and mirror result wrappers; keep one provider-to-canonical validation |
| Action | action fields inside `AgentDecision`; `FastPendingAction`; `ToolActionInput`; `AuthorizationStageInput`; tuple `AuthorizedInvocation`; `ApprovalStageInput`; `InvocationStageInput` | `LoopAction` plus one action-boundary command | Delete tuple and field-copy stage inputs as the action path migrates; persisted pending envelope remains a state-adapter concern |
| Observation | `FastObservation`; `AgentObservation`; `NormalizedObservation`; plugin processed result; observation dicts in progress/context | `LoopObservation` | Delete Fast and internal mirror forms; retain plugin raw-result validation at external boundary |
| Loop outcome | `FastExecutionResult`; domain `StageOutcome` dataclass union; tuple `ToolActionOutcome`; `CompletionRoutingResult`; `RunCoordinationResult`; `NodeExecutionResult`; `AgentRunResult` | discriminated `LoopOutcome` | Delete mode/controller result mirrors; planning may retain a bounded scheduling result that is not a Loop outcome |
| Final answer | `AgentFinalAnswer`; answer fields copied into Fast result, Loop dict and Run result | `AgentFinalAnswer` until canonical contracts land | Keep one structured answer; remove wrapper copies and dict round-trips |
| Runtime checkpoint | `FastRuntimeSnapshot`/`FastPendingAction`; AgentState/turn/tool-call records; execution recovery result dataclasses | `LoopCheckpoint` envelope at state port | Keep persisted ORM/JSON facts; delete controller-local recovery result mirrors |
| Context checkpoint | conversation/root/child V2 schemas plus older child schema | capability-owned checkpoint payload inside canonical state envelope | Retain role-specific validation and reference/identity invariants; do not flatten into a universal summary DTO |
| Runtime identity/profile | `RuntimeKind`; `RunExecutionProfile`; Run ORM fields; Fast policy/snapshot versions | frozen `RuntimeCompositionIdentity`, with persisted aliases at adapter boundary | Keep current public/persisted profile during this change; stop using it to select controllers |
| Iteration state | domain `ExecutionContext`; `RootRuntimeState`; `ExecutionProgress`; `PreparedRootTurn`; runtime assembly dataclasses | bounded `LoopState` plus capability views | Delete mirror mutable state and assembly wrappers as consumers migrate |
| Run public view | `RunRecord` ORM → `RunViewProjector.payload` dict → `RunView`; `RunQueryService` wrappers | one authorization/redaction-aware projection to `RunView` | Retain public-schema and ORM invariants; remove intermediate payload/wrapper steps that add neither |
| Node execution result | `NodeExecutionResult` Pydantic → `RunCoordinationResult` dataclass → root synthesis state | planning work result plus canonical Loop outcome/observation | Keep planning scheduling aggregation only; remove duplicated terminal fields |

### 2.1 Required representation evidence

Each surviving type must eventually be recorded in this form:

| Type category | Distinct invariant required to survive |
| --- | --- |
| API schema | untrusted request validation or stable public response version |
| ORM | table/relation/transaction/query identity |
| Domain object | domain behavior not owned by protocol, runtime or persistence |
| Public projection | authorization, redaction, aggregation or version stability |
| Runtime contract | canonical Loop/capability/persistence/recovery exchange |
| Dataclass | ephemeral, non-serialized grouping with no canonical mirror |

“Lives in another package” and “makes tests easier to mock” are not sufficient invariants by themselves.

## 3. Structural and contract baseline

### 3.1 Architecture inventory

Generated from `backend/` with:

```bash
.venv/bin/python scripts/analyze_backend_architecture.py --format markdown --limit 20
```

| Metric | Baseline |
| --- | ---: |
| Production Python lines | 62,739 |
| Modules | 317 |
| Classes | 783 |
| Functions/methods | 2,462 |
| Public symbols | 1,191 |
| Internal dependency edges | 1,373 |
| Cyclic module pairs | 0 |

The configured structural budget is already exceeded for production lines (`62,739 > 61,478`), modules (`317 > 302`) and classes (`783 > 764`). This change must reduce actual counts; it must not raise the configured budget to hide the overage.

Largest target-area modules:

| Lines | Module |
| ---: | --- |
| 729 | `app.application.runner.coordinator` |
| 725 | `app.application.runner.engine` |
| 608 | `app.application.runner.node_worker` |

Largest target-area functions:

| Lines | Complexity | Function |
| ---: | ---: | --- |
| 230 | 37 | `FastAgentExecutor.run` |
| 166 | 13 | `FastToolStage.execute` |
| 98 | 12 | `RunEngine._prepare_trusted_run` |
| 95 | 20 | `FastRecovery.recover` |
| 94 | 7 | `RunCoordinator._merge_result` |

Target-area package sizes:

| Lines | Modules | Package |
| ---: | ---: | --- |
| 7,789 | 39 | `application/agent_runtime` |
| 2,815 | 9 | `application/runner` |
| 1,955 | 18 | `application/run_management` |
| 1,301 | 4 | `application/planning` |
| 931 | 7 | `application/fast_agent_runtime` |

### 3.2 Public and persistence contracts

| Contract | Baseline |
| --- | --- |
| OpenAPI paths / operations / schemas | 97 / 113 / 201 |
| OpenAPI canonical SHA-256 | `3be25e7c97ef29695ce295a615e59d6ac9d668cc9dd6bcbcbc0dd2ab7e80ebf2` |
| ORM tables | 56 |
| ORM table-name SHA-256 | `03e70258d1bf4f079bb37647c1587dd3c10d4ef02658c55c7e6675bbee9c3d2f` |
| Alembic head/current | `0012_fast_runtime_only` |

OpenAPI and ORM behavior is already guarded by `backend/tests/test_refactoring_contracts.py`. Migration behavior is guarded by `backend/tests/test_persistence_baseline.py`.

### 3.3 Existing behavior characterization

The existing matrix in `docs/backend-refactoring/characterization-matrix.md` protects Run lifecycle, standard no-Plan behavior, trusted Plan behavior, plan confirmation, terminal states, recovery, approval integrity, SSE ordering, transaction boundaries, permission effects, Subagent governance, Workspace/Artifact ownership, Run result JSON, OpenAPI, ORM and Alembic.

Missing paired coverage to add in task 1.4:

- the same answer-only case through standard and trusted compositions;
- the same tool success and permission-denial case through both modes;
- explicit standard result-unknown recovery parity with trusted recovery classification;
- standard/trusted Skill, bounded Memory and Workspace/Artifact projection parity;
- standard/trusted Subagent eligibility and mandatory safety-boundary parity;
- event equivalence at the canonical lifecycle level while retaining documented public mode-specific events.

## 4. Baseline acceptance rule

For every completed migration slice:

1. applicable public/persistence hashes and behavior tests remain unchanged;
2. no dependency cycle is introduced;
3. target-path production lines, modules, classes, mappings and public symbols decrease or have a documented same-slice deletion offset;
4. the replaced controller/mirror/mapping path is removed before the slice task is marked complete.

## 5. Persisted identity audit

- Alembic `0011_fast_agent_runtime` introduced only `fast-v1` and `trusted-v1`; no migration, ORM default, API schema, or repository writer ever persisted `legacy-standard-v1`.
- `legacy-standard-v1` therefore had no resumable-record evidence and its composition alias was removed rather than retained as speculative compatibility.
- `fast-v1` remains a persisted/public boundary value for existing standard Runs. It is translated once to internal `standard-v1` composition identity and does not select a separate controller.
- `trusted-v1` remains the persisted/public trusted identity. Both identities execute `agent_runtime.loop.run_loop`; historical `fast_runtime_snapshot` storage is read only by the standard state adapter.

## 6. Final surviving representation invariants

| Survivor | Category | Distinct invariant | Conversion owner |
| --- | --- | --- | --- |
| `CreateRunRequest`, `ContinueRunRequest`, `RunView` | API schema | Validates untrusted requests and freezes the public response contract | `interfaces.api.runs` / `run_view()` |
| `RunRecord`, `AgentTurnRecord`, `ToolCallRecord`, `PlanRecord`, execution records | ORM | Table identity, relations, transaction ownership, fencing and resumable facts | narrow repositories / `RunUnitOfWork` |
| `LoopState`, `ModelDecision`, `LoopAction`, `LoopObservation`, `LoopOutcome` | Runtime contract | The only values exchanged by the fixed Loop and its typed ports | `application.agent_runtime.contracts` |
| `CompositionIdentity`, `CapabilityIdentity`, `PortIdentity` | Runtime contract | Frozen provider identity, order, version, digest and safety coverage | `application.agent_runtime.composition` |
| `AgentDecision` / provider response models | provider boundary | Validates provider-specific structured output once before canonical conversion | model client / trusted decision adapter |
| `RunExecutionProfile` | API/persistence boundary | Stable persisted mode, budgets and policy snapshot; selects composition, never a second controller | profile resolver / composition builder |
| `run_view`, `run_detail`, `recent_runs` | public projection functions | Authorization-safe aggregation and redaction from ORM directly to typed public views | infrastructure query/projection boundary |
| `ToolActionInput` | ephemeral dataclass | Groups the single persisted action context shared by authorization, approval and invocation; it is never serialized | mandatory action boundary |
| `RootRuntimeState`, `RootRuntimeAssembly`, `PreparedRootTurn`, `ExecutionProgress` | ephemeral dataclass | Mutable or bounded trusted capability state with no API/ORM identity | trusted composition only |
| `AgentMemoryContext` | ephemeral dataclass | Keeps audited identifiers separate from untrusted model-visible Memory content | `MemoryContextReader` |
| `NodeExecutionResult`, `RunCoordinationResult` | Planning result | Scheduling ownership, lease outcome and fan-in aggregation; never a Loop terminal outcome | `application.planning` |
| context checkpoint schemas | persisted runtime schema | Role, reference, lineage, continuation and migration validation | root/child/conversation state adapters |

Deleted chains include Fast decision/action/observation/result types, trusted decision/control/completion result wrappers,
stage-local authorization and invocation mirrors, `RunViewProjector`, `RunQueryService`, and controller-local recovery/finalizer wrappers.
Typed values now cross directly between adjacent stages; dictionaries remain only at JSON, database, provider and public-event boundaries.

## 7. Final structural comparison

| Metric | Before | After | Delta |
| --- | ---: | ---: | ---: |
| Production Python lines | 62,739 | 61,167 | -1,572 |
| Modules | 317 | 302 | -15 |
| Classes | 783 | 764 | -19 |
| Functions/methods | 2,462 | 2,461 | -1 |
| Public symbols | 1,191 | 1,190 | -1 |

The final graph has no dependency cycle or forbidden edge. Hard limits remain 800 lines per module, 100 lines per function,
and complexity 15. A representation guard rejects new generic `Mapper`/`Projector` classes, Runtime mirror suffix chains,
and canonical Loop types under `common.schemas`.
