# Fast Agent Runtime operations and migration

## Ownership and compatibility

New `standard` Runs freeze `runtime_kind=fast-v1`; new `trusted` Runs freeze
`runtime_kind=trusted-v1`. The former is owned by
`application/fast_agent_runtime`, while trusted planning, reflection,
verification, DAG scheduling, evidence and CompletionGate remain owned by
`application/agent_runtime`.

Model transport, ToolRouter, input Schema validation, effect analysis,
permissions, approvals, Sandbox, Workspace, Artifact access, cancellation and
safe error envelopes are platform boundaries shared by both runtimes. They are
not optional Fast policy features.

Historical standard Runs without an explicit runtime identity are read as
`legacy-standard-v1`; they are never rewritten as Fast. Integrations that used
`answer_mode=standard` to infer missing plans or quick-mode branches must now:

1. dispatch and render by explicit `runtime_kind` and `runtime_version`;
2. treat plan, reflection, verification and completion objects as absent by
   contract for `fast-v1`, not as delayed data;
3. preserve the frozen runtime on continuation, scheduling and approval resume;
4. consume `fast.*` events for the compact Fast timeline;
5. route explicit Subagent workflows to `trusted-v1`.

## Recovery contract

Fast snapshots record the protocol version, turn, recent normalized
observations, terminal intent and pending model/tool/approval reference. On
restart:

- interrupted model calls are retried;
- prepared but not executed actions and interrupted idempotent tools are safe to
  retry;
- recorded tool results become observations without re-execution;
- pending/approved approvals remain waiting or resume the frozen tool request;
- an interrupted non-idempotent action becomes `result_unknown` and waits for
  user direction; it is never replayed automatically.

## Rollout, shadow gates and rollback

Run paired shadow traffic against two deployments with identical model/tool
configuration:

```bash
cd backend
python -m benchmarks.fast_runtime_performance \
  --fast-base-url http://127.0.0.1:8000 \
  --legacy-base-url http://127.0.0.1:8001 \
  --runs-per-case 3
```

Promotion requires all of the following over a representative sample:

- p50 first-token ratio `fast / legacy <= 0.75`;
- p95 total-latency ratio `<= 0.90`;
- model-call and cost mean no higher than legacy;
- error-rate delta `<= +1 percentage point`;
- task-success delta `>= -2 percentage points`;
- approval, cancellation, restart and non-idempotent unknown-outcome recovery
  deterministic suites at 100%.

Set `AGENT_FAST_RUNTIME_ENABLED=false` to route only newly created standard Runs
to the compatibility executor. Existing Fast Runs continue under their frozen
runtime. Keep `AGENT_LEGACY_STANDARD_RUNTIME_ENABLED=true` until no resumable
legacy Runs remain and the longest approval/schedule continuation window has
expired. Rollback does not rewrite data.

## Observability and incident response

Watch `fast.started`, `fast.action.decided`, `fast.tool.*`,
`fast.approval.waiting`, `fast.recovery.resumed`, `fast.completed`,
`fast.waiting`, `fast.blocked` and `fast.cancelled`. Terminal Fast events report
runtime version, first-token and elapsed latency, model-call count and tool-action
count without conversation content.

For rising errors, first separate model protocol failures, authorization denials,
Sandbox/tool failures and recovery unknown outcomes. Disable new Fast routing if
the rollout gates fail; do not bypass permissions, approvals or Sandbox as a
mitigation. Preserve Run rows and event cursors for replay diagnostics.
