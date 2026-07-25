## Why

Astra currently exposes answer mode, planning strategy, and execution mode as overlapping choices, allowing combinations such as trusted + adaptive, trusted + plan-first, and either answer mode + plan-only. These combinations create unclear product promises and inconsistent DAG behavior; the product should instead offer two deterministic choices: a low-latency quick response and a trusted execution that always plans before acting.

## What Changes

- Make `standard` the fixed quick-response mode: it enters the shared Agent Loop without creating a TaskContract, canonical Plan DAG, Plan nodes, or trusted verification objects.
- Make `trusted` the fixed trusted-execution mode: it creates and persists a complete canonical Plan DAG before the first external action, executes ready nodes through the scheduler, and applies full verification and completion gates.
- Add a trusted-only “execute after planning” control: the user can allow immediate execution or require the Run to pause after the complete DAG is generated and explicitly confirm that exact Plan version before execution.
- Preserve bounded PlanPatch/replan recovery inside trusted execution so observations and failures can revise unfinished portions of the DAG without exposing an adaptive-planning choice.
- **BREAKING** Remove `plan_only` from execution modes and remove the old completed-plan/activation lifecycle; plan review becomes a resumable trusted execution checkpoint rather than a separate mode.
- **BREAKING** Remove `adaptive` as a requested or effective planning strategy and remove planning strategy from user preferences, Run creation requests, and user-facing controls.
- **BREAKING** Apply a one-way database/data migration that upgrades stored preferences and Run policy/profile snapshots to the new fixed semantics; do not retain aliases, fallback parsing, legacy enum members, or compatibility execution branches.
- Keep `request_approval` and `auto_approval` as permission behaviors independent of answer mode; neither may bypass platform prohibitions, tool restrictions, sandboxing, or other hard safety limits.
- Replace the current mode/planning controls and explanatory copy with one clear product choice: quick response versus trusted execution.

## Capabilities

### New Capabilities

- `answer-mode-selection`: Defines the final two-mode product contract, immutable Run mode snapshots, and the absence of legacy mode compatibility.

### Modified Capabilities

- `reasoning-policy`: Removes adaptive planning and plan-only execution from supported policy inputs and fixes planning behavior by answer mode.
- `runtime-reasoning-policy-enforcement`: Replaces selectable planning paths with deterministic quick and trusted runtime paths while retaining bounded trusted replan recovery.
- `general-agent-reasoning`: Scopes TaskContract, canonical Plan DAG, scheduler, evaluation, and full AgentState lifecycle to trusted execution while keeping quick response on the shared safe Agent Loop.
- `agent-chat-ui`: Removes the plan-only and planning-strategy controls and presents only the quick-response/trusted-execution choice.
- `task-runner`: Scopes canonical plan nodes and DAG-backed step audit data to trusted Runs and removes plan-only activation.

## Impact

- Frontend composer, trusted plan-execution control, plan-confirmation card, execution-mode menu, trusted strategy menu, preference types, API payloads, translations, tests, and explanatory UI.
- Backend answer/profile resolution, policy schemas and compiler, Run engine planning branches, permission policy handling, continuation protocol, repositories, serializers, and behavioral tests.
- Database schema and data migration for conversation preferences and persisted Run policy/profile JSON.
- OpenAPI/request contracts become intentionally breaking: removed values are rejected rather than normalized.
- Existing deployments require the one-way migration before starting the upgraded application; rollback to the previous mode model is not supported.
