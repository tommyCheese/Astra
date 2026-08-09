# Fast Agent Runtime operations

## Ownership

New `standard` Runs freeze `runtime_kind=fast-v1`; new `trusted` Runs freeze
`runtime_kind=trusted-v1`. The former is owned by
`application/fast_agent_runtime`, while trusted planning, reflection,
verification, DAG scheduling, evidence and CompletionGate remain owned by
`application/agent_runtime`.

Model transport, ToolRouter, input Schema validation, effect analysis,
permissions, approvals, Sandbox, Workspace, Artifact access, cancellation and
safe error envelopes are platform boundaries shared by both runtimes. They are
not optional Fast policy features.

Every Run must carry an explicit runtime identity. Integrations must:

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

## Version rollout and rollback

New Fast protocol versions must use a new explicit `runtime_kind` or
`runtime_version`. Existing Runs continue under their frozen version. A rollback
may stop assigning a new version to subsequently created Runs, but it must not
reinterpret or rewrite existing runtime state.

## Observability and incident response

Watch `fast.started`, `fast.action.decided`, `fast.tool.*`,
`fast.approval.waiting`, `fast.recovery.resumed`, `fast.completed`,
`fast.waiting`, `fast.blocked` and `fast.cancelled`. Terminal Fast events report
runtime version, first-token and elapsed latency, model-call count and tool-action
count without conversation content.

For rising errors, first separate model protocol failures, authorization denials,
Sandbox/tool failures and recovery unknown outcomes. Do not bypass permissions,
approvals or Sandbox as a mitigation. Preserve Run rows and event cursors for
replay diagnostics.
