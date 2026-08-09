## Why

The governed subagent runtime currently stops at a disabled-by-default, single-active-child slice and is not connected to the root Agent's production decision loop. This contradicts Astra's intended use of subagents for independent parallel work: bounded concurrency must be a first-class invariant, not a future relaxation of a serial design.

## What Changes

- Replace the phase-one "one active child per parent" rule with policy-bounded concurrent fan-out enforced by cumulative, active, budget, provider, and deployment limits.
- Add an Astra `swarm` runtime built-in that lets the root Agent atomically create a bounded group of child executions and its durable join through the existing Tool-selection surface without becoming a third-party or sandbox Tool.
- List `swarm` in the persisted Tool settings UI as the only product enablement switch; runtime eligibility also remains subject to server-enforced cohort policy and the emergency kill switch.
- Add `/subagent <task>` to the registered slash-command system as a Run-creation command that starts a trusted Run with required subagent execution while keeping the command text out of conversation messages.
- Add a Run-scoped subagent supervisor that dispatches queued children concurrently through independent database sessions and model-client execution contexts while the root continues unrelated work.
- Automatically validate, join, merge, and explicitly promote verified child results into parent observations exactly once.
- Make Plan scheduling wait only at the node consuming a child join, and make root completion require all mandatory descendant and join barriers to be consumed.
- Keep the initial production scope trusted-mode, read-only, root-only delegation with `max_depth = 1`; recursive delegation and write-capable children remain out of scope.
- Preserve kill-switch, recovery, cancellation, approval, fencing, quota, telemetry, and sanitized UI behavior for concurrent children.

## Capabilities

### New Capabilities

- `concurrent-subagent-supervision`: Bounded concurrent fan-out, durable supervision, independent child execution contexts, join reconciliation, and exactly-once parent consumption.

### Modified Capabilities

- `general-agent-reasoning`: Add first-class delegation decisions and make validated merged child results available as parent observations.
- `plan-execution-runtime`: Add dependency-scoped child-join barriers so unrelated parent work can continue during child execution.
- `completion-gate`: Prevent successful root completion until mandatory concurrent child results are terminal, joined, consumed, and conflict-safe.
- `task-runner`: Own the concurrent subagent supervisor lifecycle, dispatch, recovery, cancellation, and shutdown behavior.
- `agent-chat-ui`: Present simultaneous child execution, join, wait, budget, and cancellation state without implying serial execution.
- `slash-system-commands`: Register the parameterized `/subagent <task>` Run-creation command and preserve command/Skill coexistence and submission recovery.

## Impact

- Backend runtime built-in registration/dispatch, `AgentLoop`, `RunEngine`, Run creation, Plan scheduling, completion evaluation, subagent runtime/coordinator/fan-in services, policy compilation, slash-command catalog, events, recovery, and telemetry.
- A new supervisor/orchestration boundary and per-child model-client/runtime factory; the shared HTTP transport and immutable Tool Registry may still be reused.
- Run API and frontend Agent-tree projections gain join-consumption and concurrent-progress metadata while remaining backward compatible for root-only Runs.
- Existing governed-subagent tables remain usable; schema changes may be needed for explicit join merge/consumption state and atomic fan-out group identity.
- Supersedes the single-active-child rollout constraint documented by `add-governed-subagent-runtime`; it does not enable recursive or write-capable children.
