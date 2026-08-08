## 1. Runtime Identity And Persistence

- [x] 1.1 Add versioned `runtime_kind` and `runtime_version` fields to immutable Run execution profiles while preserving `answer_mode` API compatibility
- [x] 1.2 Define and validate the versioned `FastRuntimePolicy` and `FastRuntimeSnapshot` schemas without trusted planning, reflection, verification, or completion fields
- [x] 1.3 Persist and query Fast Runtime snapshots, pending action references, protocol versions, and terminal intent
- [x] 1.4 Add migration and compatibility readers for historical standard Runs that have no explicit runtime kind
- [x] 1.5 Add repository tests proving runtime identity is immutable across continuation, restart, and preference changes

## 2. Independent Fast Runtime Package

- [x] 2.1 Create the `application/fast_agent_runtime` package with executor, context builder, decision loop, tool stage, finalizer, and recovery boundaries
- [x] 2.2 Define a versioned Fast model action protocol containing only `answer`, `call_tool`, `ask_user`, and `stop`
- [x] 2.3 Implement the Fast context builder using conversation context, active Skills allowed for Fast Runs, current tool manifests, and recent normalized observations
- [x] 2.4 Implement the model-driven observe-decide-act loop without TaskContract, AgentState, Plan, Evaluation, Reflection, VerificationEngine, or CompletionGate dependencies
- [x] 2.5 Implement direct answer streaming and one-pass Fast finalization with accessible Artifact-reference cleaning
- [x] 2.6 Implement lightweight Fast failure handling that returns model/tool errors as observations and permits model-directed retry or fallback

## 3. Runtime Dispatch And Trusted Isolation

- [x] 3.1 Route new standard Runs to `fast-v1` and trusted Runs to `trusted-v1` from the Run application service
- [x] 3.2 Split runtime construction so trusted services no longer receive or branch on `quick_mode`
- [x] 3.3 Move legacy standard execution behind an explicit compatibility executor used only for eligible historical Runs
- [x] 3.4 Add dependency-boundary tests proving Fast Runtime cannot import trusted planning, reflection, verification, or completion packages
- [x] 3.5 Add regression tests proving trusted contract generation, DAG scheduling, reflection, verification, and CompletionGate behavior are unchanged

## 4. Shared Platform Boundaries

- [x] 4.1 Extract or retain a single shared tool-catalog, ToolRouter, input-Schema, effect-analysis, permission, approval, and Sandbox execution boundary for both runtimes
- [x] 4.2 Adapt shared tool results and failures into the minimal Fast Observation contract without creating trusted Evaluation records
- [x] 4.3 Preserve shared cancellation, sensitive-data, Artifact access, error-envelope, idempotency, and non-idempotent recovery behavior in Fast Runs
- [x] 4.4 Add cross-runtime contract tests for allowed tools, denied tools, malformed inputs, approval pauses, Sandbox failures, cancellation, and Artifact cleaning
- [x] 4.5 Verify Fast model output cannot grant tools, bypass approval, disable platform boundaries, or forge accessible Artifact IDs

## 5. Fast Recovery And Operational Protection

- [x] 5.1 Implement Fast restart recovery for pending model calls, prepared tool actions, recorded idempotent results, approvals, and unknown non-idempotent outcomes
- [x] 5.2 Add deployment-level Fast action-loop protection and observability without exposing trusted budgets or CompletionGate semantics
- [x] 5.3 Record Fast Runtime version, model-call count, tool-action count, first-token latency, completion, cancellation, error, and recovery metrics without conversation content
- [x] 5.4 Add a Fast Runtime rollout switch and rollback routing that affects only newly created standard Runs
- [x] 5.5 Define the compatibility-window and retirement checks for the legacy standard executor

## 6. Events And API Views

- [x] 6.1 Define `fast.*` lifecycle, action, tool, waiting, recovery, and completion events with stable cursor/replay semantics
- [x] 6.2 Expose runtime kind/version and Fast snapshot status in Run views without fabricating plan, reflection, verification, or completion objects
- [x] 6.3 Ensure Fast results always return null `verification_report` and `completion_decision` and never emit a trusted verification badge state
- [x] 6.4 Update conversation history, sharing, scheduling, cancellation, and continuation APIs to dispatch and project the frozen runtime correctly
- [x] 6.5 Add API and SSE tests for first-token streaming, event replay, terminal snapshots, waiting approvals, resume, cancellation, and historical compatibility

## 7. Frontend Runtime Projection

- [x] 7.1 Update frontend Run types and reducers to select Fast or Trusted projections by explicit runtime kind rather than missing events
- [x] 7.2 Build a compact Fast process timeline for model activity, tool calls, approvals, waiting, errors, and streamed answers
- [x] 7.3 Keep Plan DAG, Reflection, Evidence Pack, VerificationReport, CompletionDecision, and trusted-result badges exclusive to Trusted Runs
- [x] 7.4 Remove trusted reasoning, planning, reflection, verification, Subagent, and DAG settings from Fast Run requests and menus
- [x] 7.5 Update mode copy to explain that quick mode is model-driven and unverified while trusted mode provides planned, audited execution
- [x] 7.6 Add component tests for Fast streaming, tools, approvals, errors, cancellation, history, dark mode, narrow layouts, and absence of trusted placeholders

## 8. Capability And Workflow Migration

- [x] 8.1 Decide and encode Fast Skill compatibility so activated Skills cannot implicitly request trusted-only planning, verification, Subagent, or memory-write capabilities
- [x] 8.2 Remove lightweight Fast Subagent exposure and route explicit Subagent workflows to Trusted Runtime until an independent Fast extension is specified
- [x] 8.3 Keep memory recall as untrusted context only if supported by the Fast contract and disable Fast memory-candidate writes in the initial release
- [x] 8.4 Update scheduled jobs, Draft Skill tests, slash commands, and conversation preferences to preserve the selected runtime semantics
- [x] 8.5 Add migration guidance for integrations that previously assumed standard and trusted shared one Agent Loop

## 9. Verification And Rollout

- [x] 9.1 Add deterministic backend tests proving a Fast Run creates no TaskContract, AgentState, Plan, Evaluation, Reflection, VerificationReport, CompletionDecision, Evidence Pack Artifact, or memory candidate
- [x] 9.2 Add end-to-end tests for direct answers, multi-tool loops, tool failure recovery, user questions, approval resume, cancellation, restart, and terminal convergence
- [x] 9.3 Benchmark `fast-v1` against legacy standard for first-token latency, total latency, model calls, tool calls, error rate, and task success on representative cases
- [x] 9.4 Run shadow comparisons before changing the standard default and document acceptable latency, quality, cost, and recovery thresholds
- [x] 9.5 Run backend unit/integration suites, architecture checks, migrations, frontend tests/build, and browser verification for both runtime kinds
- [x] 9.6 Update in-app documentation and operational runbooks with runtime ownership, rollout, rollback, observability, and compatibility behavior
