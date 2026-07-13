## 1. Runtime Schema and Persistence

- [x] 1.1 Add enums and Pydantic schemas for requested/effective reasoning policy, policy adjustments, reflection trigger modes, execution modes, verification levels, and run budgets.
- [x] 1.2 Add schemas for TaskContract, success criteria, assumptions, prohibited actions, verification requirements, ambiguity state, and criterion status.
- [x] 1.3 Add schemas for versioned AgentState, PlanGraph/PlanStep, expected observations, Evaluation, ReflectionPatch, failure fingerprints, terminal intent, and completion decision.
- [x] 1.4 Create database migrations and repository methods for policy snapshots, task contracts, plan versions, state versions, evaluations, reflection patches, terminal reasons, and resumable waiting state.
- [x] 1.5 Extend Run and AgentTurn API views with backward-compatible defaults and audit references for the new records.
- [x] 1.6 Add repository and schema tests covering serialization, version conflicts, immutable policy snapshots, and legacy Run reads.

## 2. Reasoning Policy Compiler

- [x] 2.1 Implement Run creation request support for reasoning effort, planning strategy, reflection settings, execution mode, and verification level.
- [x] 2.2 Implement PolicyCompiler defaults for balanced reasoning, adaptive planning/reflection, request approval, and standard verification.
- [x] 2.3 Map fast/balanced/deep effort to explicit plan, candidate-strategy, model-call, reflection, replan, turn, tool, and verification budgets.
- [x] 2.4 Implement risk/complexity safety floors that can raise effective planning, approval, and verification requirements without weakening hard restrictions.
- [x] 2.5 Persist requested/effective policy and adjustment reasons at Run start and ensure later workspace setting changes do not mutate active runs.
- [x] 2.6 Add policy matrix tests for reflection disabled, direct/adaptive/plan-first, plan-only/request-approval/auto-approval, and high-risk automatic adjustments.

## 3. Task Contract and Planning

- [x] 3.1 Extend ModelClient with structured TaskContract generation and validation while preserving the original user goal.
- [x] 3.2 Implement deterministic contract checks for mandatory deliverables, stable success-criterion IDs, verification methods, prohibited actions, and material ambiguity.
- [x] 3.3 Transition materially ambiguous runs to resumable waiting_user with a focused clarification request.
- [x] 3.4 Implement PlanGraph creation for direct, adaptive, and plan-first strategies with dependency, risk, capability, criterion, and expected-outcome metadata.
- [x] 3.5 Implement plan readiness checks and versioned replan operations that preserve unaffected completed steps and evidence.
- [x] 3.6 Add tests for simple contracts, ambiguous goals, dependency blocking, partial replans, stale plan patches, and planning-strategy behavior.

## 4. Canonical Agent State and Evaluation Loop

- [x] 4.1 Implement AgentState assembly from the task contract, policy snapshot, current plan, criteria, provenance-bearing facts, open questions, observations, failures, and budgets.
- [x] 4.2 Extend AgentDecision validation with target step, criterion references, typed expected observation, risk, confidence, and fallback fields.
- [x] 4.3 Implement typed Observation normalization interfaces for tool results, tool failures, user responses, approval outcomes, and validator reports.
- [x] 4.4 Implement Evaluation generation for matched, partial, mismatch, conflict, and inconclusive outcomes using deterministic rules before optional model evaluation.
- [x] 4.5 Apply observation/evaluation state updates atomically before the next decision and reject stale state versions.
- [x] 4.6 Add tests proving that technical tool success does not satisfy a criterion when semantic expectations are missed and that conflicting facts retain provenance.
- [x] 4.7 Implement the runtime-owned node transition graph and typed NodeResult contract with transition and state-patch authority checks.
- [x] 4.8 Implement categorized node error exits for model, policy, transient/permanent tool, state, validator, budget, and internal runtime failures.
- [x] 4.9 Add transition tests proving that model output cannot skip policy, evaluation, state persistence, reflection gating, or completion gating.

## 5. Structured Reflection and Progress Control

- [x] 5.1 Implement ReflectionGate trigger evaluation for failure-only, adaptive, and every-turn modes with effective reflection budgets.
- [x] 5.2 Preserve non-optional runtime recovery for schema errors, bounded transient retries, permission rejection, duplicate prevention, and budget exhaustion when model reflection is disabled.
- [x] 5.3 Extend ModelClient reflection output with local/plan/goal level, diagnosis, invalidated assumptions, violated criteria, and a typed ReflectionPatch.
- [x] 5.4 Implement ReflectionPatch validation and atomic application for tool input, fact/assumption, criterion, plan, verification, waiting_user, and blocked state changes.
- [x] 5.5 Generate normalized failure fingerprints and reject equivalent strategies after their retry budget is exhausted.
- [x] 5.6 Implement no-progress detection from information gain, criterion changes, completed steps, plan changes, and repeated state transitions.
- [x] 5.7 Add tests for actionable and non-actionable reflections, prohibited patches, local correction, plan rework, goal clarification, retry deduplication, no-progress stopping, and reflection budget exhaustion.

## 6. Completion Gate and Terminal Semantics

- [x] 6.1 Implement CompletionGate evaluation of mandatory/noncritical criteria, accepted evidence, validator results, approvals, unresolved failures, and budget termination reasons.
- [x] 6.2 Prevent Agent Controller and Finalizer from directly setting successful terminal states; treat finalize as terminal intent only.
- [x] 6.3 Implement strict completed, completed_with_warnings, waiting_user, blocked, and failed transitions with structured terminal reasons and unmet criteria.
- [x] 6.4 Implement state-specific finalization schemas so blocked and waiting_user responses cannot be represented as successful answers.
- [x] 6.5 Implement same-Run resume from waiting_user while preserving plan/state versions and recording user input or approval as an Observation.
- [x] 6.6 Add terminal-state tests for success, allowable partial result, missing evidence, pending approval, exhausted safe strategies, unexpected runtime failure, and budget exhaustion.

## 7. Task Adapter Architecture and Web Migration

- [x] 7.1 Define the authorized TaskAdapter interface for tool manifests, default criteria, observation normalization, validators, evidence references, and final response schemas.
- [x] 7.2 Implement WebTaskAdapter around web_search, web_fetch, Evidence Pack, source quality, conflict, retrieval-failure, and citation verification behavior.
- [x] 7.3 Remove Web-specific candidate, fetch, and source-count branching from the core reasoning, reflection, and completion modules.
- [x] 7.4 Route Web runs through the general runtime behind a feature flag while retaining the existing Web loop as a rollback path.
- [x] 7.5 Add deterministic mock Web integration tests covering successful completion, low-quality warning, failed retrieval reflection, missing evidence blocking, and policy variations.

## 8. Frontend Policy and Audit Experience

- [x] 8.1 Send reasoning effort, planning strategy, reflection enabled/trigger, execution mode, and verification level in Run creation requests.
- [x] 8.2 Display effective policy, policy adjustments, and budget summaries without exposing hidden chain-of-thought.
- [x] 8.3 Display criterion progress, plan versions, expected-versus-actual evaluation, reflection trigger/patch summary, and terminal reason in the audit view.
- [x] 8.4 Implement waiting_user clarification and approval responses that resume the existing Run.
- [x] 8.5 Add frontend tests for policy request payloads, automatic policy adjustments, reflection-disabled behavior, waiting/resume, and distinct terminal-state messaging.

## 9. Migration, Observability, and Verification

- [x] 9.1 Add feature flags for policy shadow mode and the general reasoning runtime, with documented legacy rollback behavior.
- [x] 9.2 Add structured events and metrics for policy adjustments, decision validation, evaluation outcomes, reflection triggers/patch rejection, no-progress detection, and completion decisions.
- [x] 9.3 Backfill or expose legacy runs with a legacy policy/adapter marker without rewriting historical decisions.
- [x] 9.4 Run backend migrations, type/schema checks, unit tests, API tests, frontend tests, and full mock Web end-to-end tests.
- [x] 9.5 Update README and architecture documentation with the reasoning lifecycle, policy semantics, reflection boundaries, TaskAdapter contract, terminal states, and operator rollback procedure.
- [x] 9.6 Add turn phases, stable idempotency keys, tool idempotency declarations, continuation tokens, and paused-node metadata to persistence schemas.
- [x] 9.7 Implement prepare/execute/commit checkpoints and recovery for prepared actions, recorded idempotent results, stale commits, and unknown non-idempotent outcomes.
- [x] 9.8 Add crash-point and resume tests proving that external actions are not duplicated and waiting_user resumes from the persisted continuation.
