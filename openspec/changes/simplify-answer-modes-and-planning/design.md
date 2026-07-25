## Context

Astra currently combines three independent controls:

- `AnswerMode.standard | trusted`
- `RequestedPlanningStrategy.adaptive | plan_first`
- `ExecutionMode.plan_only | request_approval | auto_approval`

The combinations do not form a coherent product model. Standard Runs already bypass TaskContract and canonical Plan creation unless `plan_only` is selected, while trusted Runs can either start from a coarse adaptive node or generate a full DAG. The composer exposes answer mode, planning strategy, and execution behavior in different menus, so users must understand implementation-level distinctions before they can predict whether a Run will create or execute a plan.

This change is intentionally a full upgrade. Removed values are not retained as aliases and old clients are not supported after deployment. The deployment must migrate stored data and frontend/backend versions together.

## Goals / Non-Goals

**Goals:**

- Present exactly two deterministic product behaviors: quick response and trusted execution.
- Guarantee that quick response never creates a TaskContract or canonical Plan DAG.
- Guarantee that trusted execution persists a complete, validated DAG before its first external action.
- Remove plan-only execution, standalone plan activation, and user-selectable planning strategy across UI, API, persistence, and runtime.
- Let trusted users choose between automatic execution and an explicit checkpoint after the complete Plan is visible.
- Retain bounded DAG repair during trusted execution without exposing it as an adaptive-planning mode.
- Remove compatibility parsing and fallback branches for deleted values.
- Make the upgrade testable through behavioral assertions rather than only serialized fields.

**Non-Goals:**

- Removing `request_approval` or `auto_approval`; they remain permission behaviors.
- Removing reflection triggers, including the separately named adaptive reflection trigger.
- Giving quick response a synthetic single-node Plan for visual uniformity.
- Adding parallel DAG execution or changing PlanScheduler ordering.
- Preserving rollback compatibility with application versions that still send or store removed policy fields.

## Decisions

### 1. Answer mode becomes the only product-level planning choice

`standard` compiles to a fixed quick profile: fast reasoning, system-minimal context, no TaskContract, no Plan records, no Plan nodes or edges, no AgentState plan lifecycle, and no full VerificationEngine/CompletionGate objects. It continues to use the shared model client, ToolRouter, permission gate, tool schemas, artifact sanitation, cancellation, and error boundaries.

`trusted` compiles to a fixed trusted profile: model TaskContract, complete initial PlanDraft, canonical DAG validation and persistence, ready-node scheduling, Observation/Evaluation, bounded reflection and replan, full verification, and CompletionGate.

This is preferred over keeping a one-value planning enum because a one-value user policy still suggests configurability and preserves unnecessary serialization, validation, and migration surface.

### 2. Trusted execution always plans first but may repair the unfinished DAG

Before the first external action, trusted execution MUST create the complete initial DAG and persist its dependencies, risks, expected outcomes, and success-criterion references. `PlanScheduler` remains the only source of executable nodes.

PlanPatch and replan remain available after observations, failures, conflicts, or changed assumptions. They are recovery mechanisms governed by reflection and replan budgets, not a planning mode. Completed nodes and accepted evidence remain immutable across revisions.

Alternative considered: disable all replanning after the initial DAG. This was rejected because a static plan cannot safely recover from tool failures, unavailable capabilities, or invalidated dependencies.

### 3. Planning strategy is removed end to end

Remove requested/effective planning strategy fields from:

- conversation strategy preference API and persistence;
- CreateRun reasoning-policy requests;
- immutable Run execution profiles and policy snapshots;
- PlanDraft, PlanRecord, PlanGraph/View, and audit presentation where the value only distinguishes initial planning modes;
- frontend state, menus, labels, and help content.

Remove `RequestedPlanningStrategy` and `PlanningStrategy` rather than retaining only `plan_first`. Trusted planning is selected by `answer_mode=trusted`; standard planning is absent.

Strict request models reject obsolete `planning_strategy` input instead of ignoring it.

### 4. Plan-only is replaced by an integrated trusted Plan confirmation checkpoint

`ExecutionMode` contains only `request_approval` and `auto_approval`. Remove:

- the `plan_only` enum member and policy compiler branch;
- no-side-effect plan-only permission rules;
- RunEngine plan-only completion and caveat generation;
- `POST /runs/{run_id}/activate-plan`;
- activation repository/service behavior;
- frontend “仅规划” option and its forced planning preference;
- plan-only tests, translations, documentation, and OpenAPI shapes.

Trusted mode means plan first, then execute. It does not mean produce a plan without execution.

Trusted Run creation accepts a separate strict value, `plan_execution=auto | confirm`, that is not part of `ExecutionMode`:

- `auto`: create, validate, persist, and activate the complete DAG, then execute the first ready node.
- `confirm`: create, validate, and persist the complete DAG without activating a node; persist a version-bound continuation request and enter `waiting_user`.

The frontend exposes this as a trusted-only control labelled “计划生成后直接执行”. The default is `confirm`, so a new trusted workflow shows the generated Plan and requires an explicit “执行计划” action. Choosing “暂不执行” leaves the Run waiting and side-effect free; ordinary Run cancellation remains available.

The confirmation submits the Run continuation token and expected Plan version through the shared continuation protocol. The backend activates only that exact Plan version and rejects stale, reused, or mismatched confirmation. This checkpoint does not approve any tool effect: subsequent tools still follow `request_approval` or `auto_approval`.

Trusted mode therefore always represents an executable Run, but execution may wait for the user's Plan confirmation. It never completes successfully with only a plan.

Alternative considered: map plan-only to trusted. This was rejected because it would silently change “do not execute” into “execute after planning,” which is unsafe at request time. The one-way migration handles stored state explicitly instead.

### 5. Permission behavior remains orthogonal but is not a planning mode

Both answer modes continue to use `request_approval` or `auto_approval`. `auto_approval` only skips interactions for approvable actions; platform prohibitions, permission bundles, tool restrictions, effect analysis, sandbox boundaries, and data-flow restrictions remain mandatory.

The composer approval menu removes the plan item. Product copy describes this control as approval behavior, not execution or planning strategy. The trusted Plan confirmation control is displayed separately because it governs when DAG execution begins, not whether individual effects require approval.

### 6. The upgrade uses a one-way destructive semantic migration

The migration is executed before the upgraded backend starts:

1. Cancel every non-terminal Run whose persisted profile contains `plan_only`, recording a migration terminal reason.
2. Rewrite stored trusted Run policy/profile JSON to the fixed trusted profile and remove planning strategy keys.
3. Rewrite stored standard Run policy/profile JSON to the fixed quick profile and remove planning strategy keys.
4. Rewrite `plan_only` in completed historical snapshots to `request_approval`; the old selection is intentionally not preserved as a supported semantic field.
5. Remove `planning_strategy` from conversation preference storage and set all remaining preferences to the two-mode schema.
6. Remove the Plan strategy column/DTO field if no other runtime invariant uses it after implementation.

The application does not contain legacy aliases, `direct`/`adaptive` normalization, missing-field fallbacks, or activation compatibility routes. Existing clients must be upgraded at the same time. Down-migration and mixed-version rollback are unsupported; recovery uses a database backup taken before migration.

### 7. Run snapshots use a new strict profile version

The immutable execution profile receives a new schema version. Startup validation fails with a clear migration error if any live row still contains deleted fields or enum values. This prevents partially migrated databases from silently selecting an unintended path.

Completed Run data remains readable only after migration to the new profile shape. Waiting, planning, executing, or approval-paused legacy Runs are not resumed; they are cancelled during migration.

### 8. UI exposes one binary decision

The composer continues to show the trusted toggle:

- Off: “快速响应” — low latency, no plan graph, basic safeguards.
- On: “可信执行” — plan first, execute the DAG, and fully verify.

When trusted mode is on, the composer also exposes “计划生成后直接执行”. The trusted menu may still expose reasoning effort, tool budget, and reflection policy. It MUST NOT expose planning strategy. The approval menu contains only request approval and auto approval.

Audit UI renders canonical Plan nodes and dependencies only when a trusted Run actually has them. Quick Runs do not show a fake Plan version or empty DAG placeholder.

## Risks / Trade-offs

- [Risk] Removing plan-only eliminates the old “finish successfully with only a plan” workflow. → Mitigation: trusted confirmation still allows inspection without side effects, but the Run remains waiting until execution or cancellation rather than reporting task completion.
- [Risk] Full initial planning increases trusted-mode latency. → Mitigation: communicate the mode promise clearly, stream the planning phase, and keep quick response as the low-latency choice.
- [Risk] Plan confirmation could be mistaken for tool-effect approval. → Mitigation: use distinct copy and state; Plan confirmation only starts scheduling, while every later effect still passes its configured approval behavior.
- [Risk] A stale browser confirms a superseded Plan. → Mitigation: bind the continuation token to Run ID, Plan ID, Plan version, and one-time use.
- [Risk] A complete initial DAG may become stale during execution. → Mitigation: retain budgeted PlanPatch/replan for unfinished nodes while protecting completed evidence.
- [Risk] One-way migration changes historical policy metadata. → Mitigation: require a pre-migration backup and record a migration event/terminal reason; semantic preservation of removed choices is explicitly out of scope.
- [Risk] Mixed frontend/backend deployment causes request failures. → Mitigation: ship database migration, backend, and frontend as one coordinated release and reject startup against an unmigrated database.
- [Risk] Removing strategy fields touches many contracts and tests. → Mitigation: use repository-wide symbol scans and contract tests to prove no deleted term remains in production schemas, UI, or OpenAPI.

## Migration Plan

1. Take and verify a database backup; stop all Run workers and reject new Run creation.
2. Apply the one-way migration, cancel incompatible non-terminal Runs, rewrite profile JSON, and remove obsolete columns/fields.
3. Deploy the strict backend schemas and fixed profile resolver together with the new version-bound Plan confirmation continuation and removed legacy activation endpoint.
4. Deploy the frontend that sends no planning strategy, offers no plan-only choice, and renders the trusted Plan confirmation control/card.
5. Run migration assertions, backend behavior tests, frontend tests, production build, and strict OpenSpec validation.
6. Re-enable Run creation only after startup confirms that no deleted values remain.

Rollback is not supported through a down-migration. Operational recovery restores the pre-migration backup and the previous complete application release.

## Open Questions

- Whether `auto_approval` should remain in the composer or move to advanced settings is deliberately deferred; this change only separates it from planning.
