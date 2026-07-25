## 1. Strict Mode and Policy Contracts

- [x] 1.1 Add the versioned `PlanExecution.auto | confirm` contract to trusted Run creation and immutable execution profiles, defaulting trusted Runs to `confirm`
- [x] 1.2 Remove `RequestedPlanningStrategy`, `PlanningStrategy`, all `planning_strategy` request/profile fields, and their normalization validators
- [x] 1.3 Remove `ExecutionMode.plan_only` and constrain execution approval behavior to `request_approval | auto_approval`
- [x] 1.4 Make Run and preference request models reject removed fields and enum values instead of ignoring or normalizing them
- [x] 1.5 Update serialized Run, waiting-state, continuation, and frontend API types for Plan execution confirmation and strict Profile versioning

## 2. One-Way Database Upgrade

- [x] 2.1 Add an Alembic migration that blocks new work, cancels incompatible non-terminal legacy Runs, and records a mode-upgrade terminal reason
- [x] 2.2 Rewrite persisted standard and trusted policy/profile JSON to the new fixed shapes and remove planning strategy keys and deleted enum values
- [x] 2.3 Remove planning strategy from conversation preference persistence and remove the Plan strategy column/field when no invariant depends on it
- [x] 2.4 Add startup validation that refuses to run workers when live records contain an old Profile version, deleted field, or deleted enum value
- [x] 2.5 Add migration tests for old standard, trusted-adaptive, plan-first, plan-only, completed, and non-terminal records
- [x] 2.6 Document the required backup, coordinated deployment, unsupported down-migration, and backup-restore recovery procedure

## 3. Fixed Quick and Trusted Runtime Paths

- [x] 3.1 Simplify `RunProfileResolver` so standard always resolves to the no-Plan quick Profile and trusted always resolves to the complete-plan-first Profile
- [x] 3.2 Remove planning-strategy dispatch and fallback branches from RunEngine, policy compilation, model planning calls, and audit events
- [x] 3.3 Ensure standard Runs create no TaskContract, AgentState, Plan, PlanNode, PlanEdge, Evaluation, VerificationReport, or CompletionDecision
- [x] 3.4 Ensure trusted Runs generate, validate, and persist the complete initial DAG before any external action
- [x] 3.5 Preserve validated PlanPatch/replan recovery for unfinished trusted nodes without a planning-strategy enum or user-facing adaptive mode
- [x] 3.6 Remove plan-only completion, caveats, side-effect suppression branches, policy rules, and special Agent Loop outcomes

## 4. Trusted Plan Confirmation Continuation

- [x] 4.1 Persist trusted `confirm` Plans without activating a node, create a version-bound continuation request, and transition the Run to `waiting_user`
- [x] 4.2 Bind the Plan confirmation token to Run ID, Plan ID, Plan version, expected state version, and one-time use
- [x] 4.3 Extend the shared continuation/resume API to validate and consume “execute plan” confirmation and activate the exact persisted Plan without rebuilding it
- [x] 4.4 Reject stale, mismatched, replayed, or already-consumed Plan confirmations without executing a node
- [x] 4.5 Keep “暂不执行” Runs resumable in `waiting_user` and preserve ordinary cancellation as the explicit terminal action
- [x] 4.6 Verify Plan confirmation never grants tool effects and that later tools still use request approval or auto approval independently
- [x] 4.7 Delete `POST /runs/{run_id}/activate-plan` and its repository, client, documentation, and test paths

## 5. Frontend Product Simplification

- [x] 5.1 Remove the “仅规划” approval option, planning-strategy state, adaptive/plan-first selector, persistence payloads, help content, and translations
- [x] 5.2 Rename and clarify the binary Composer choice as “快速响应” versus “可信执行”
- [x] 5.3 Add the trusted-only “计划生成后直接执行” control and send `plan_execution=auto | confirm` with new trusted Runs
- [x] 5.4 Render the complete waiting Plan DAG and a version-bound “执行计划” button when trusted execution requires confirmation
- [x] 5.5 Submit the continuation token and expected Plan version on confirmation and surface stale/rejected confirmation errors without optimistic execution
- [x] 5.6 Keep the Plan execution control hidden in quick mode and visually distinct from request/auto tool approval controls
- [x] 5.7 Suppress fake Plan version and empty DAG audit rows for standard Runs while preserving real trusted node/dependency audit data
- [x] 5.8 Update responsive and accessible states for the trusted control, Plan confirmation card, keyboard operation, loading, waiting, and cancellation
- [x] 5.9 Move the trusted “计划生成后直接执行” control into the model/strategy menu, remove the flat Composer control, and cover trusted/quick visibility
- [x] 5.10 Group trusted strategy controls by function and add separators between plan execution, reasoning resources, and reflection strategy
- [x] 5.11 Replace the single trusted-strategy help button with contextual help buttons beside each corresponding control and focused help content

## 6. API, Documentation, and Dead-Code Removal

- [x] 6.1 Update OpenAPI and README examples to contain no planning strategy, plan-only value, or legacy activation endpoint
- [x] 6.2 Remove obsolete enums, DTO fields, repository methods, UI components, translations, CSS selectors, events, and tests found by repository-wide reference scans
- [x] 6.3 Update Agent Profile/model prompts so standard decisions never target Plan nodes and trusted planning always returns a complete DAG
- [x] 6.4 Update audit and usage presentation to describe Plan confirmation separately from effect approval

## 7. Behavioral and Contract Verification

- [x] 7.1 Add backend tests proving standard completes with no Plan objects while retaining ToolRouter, permission, artifact, cancellation, and error safeguards
- [x] 7.2 Add backend tests proving trusted auto execution creates the complete DAG before the first external action and enforces scheduler/completion boundaries
- [x] 7.3 Add backend tests proving trusted confirmation waits without external action, activates the exact Plan once, and rejects stale or replayed tokens
- [x] 7.4 Add backend tests proving trusted PlanPatch recovery remains bounded and preserves completed nodes and evidence
- [x] 7.5 Add API/schema tests proving removed fields, values, and routes are rejected or absent with no compatibility fallback
- [x] 7.6 Add frontend tests for the two-mode UI, trusted execution control, waiting Plan card, confirmation, stale errors, audit visibility, and accessibility
- [x] 7.7 Run backend Ruff and full pytest, frontend typecheck/tests/production build, migration upgrade checks, repository-wide deleted-symbol scans, and strict OpenSpec validation
