# Agent Runtime operations

## One Loop, two compositions

Every standard and trusted Run executes the same fixed Loop:

`load → context capabilities → model decision → action boundary → observation → progress/completion policies → typed outcome`.

The Loop and its contracts live in `application/agent_runtime/{loop,contracts,composition}.py`. It has no imports from planning, Run management, interfaces, SQLAlchemy, providers, or optional administration. Runtime composition is frozen before execution and validates mandatory port ownership, capability identity/order/digest, and safety coverage.

- `standard-v1` contributes the compact tool/Skill context and lightweight completion policy. Persisted `fast-v1` is translated to this composition at the state boundary.
- `trusted-v1` contributes Planning input, reflection, verification, evidence, CompletionGate, and governed Subagent behavior.
- a ready Plan node is bounded input to the trusted composition; DAG scheduling, leases, heartbeat, fan-in, retry, and result merge belong to `application.planning`.

Model calls, state/recovery, action execution, cancellation, and event publication are mandatory ports. Optional capabilities cannot replace authorization, approval integrity, persistence, cancellation, schema validation, effect analysis, or result-unknown recovery.

## Recovery and persisted identities

Current persisted identities are `fast-v1` and `trusted-v1`. There is no evidence that `legacy-standard-v1` was ever persisted, so no speculative reader is retained. New internal code must use `standard-v1`/`trusted-v1` composition identities and treat persisted names only as adapter input.

The standard state adapter is the sole reader/writer of the historical `fast_runtime_snapshot` column. Interrupted model calls may retry; recorded tool results become observations; approval resumes the frozen call; a non-idempotent action without a recorded result becomes `result_unknown` and is never replayed automatically. Trusted Run and node records are classified through the same canonical `StatePort.recover → LoopOutcome` contract.

## Rollout, rollback, and operations

A rollout changes the frozen composition version/digest for newly created Runs. Existing resumable Runs require the exact persisted identity and digest. Rollback stops assigning a new composition but never rewrites an existing checkpoint.

Public `fast.*` events and `runtime_kind=fast-v1` remain compatibility data boundaries, not evidence of a second controller. Diagnose incidents by canonical outcome plus boundary category: model protocol, authorization/approval, tool/Sandbox, persistence/recovery, cancellation, or completion verification. Never bypass the mandatory action boundary as mitigation.

AutoDream, Memory consolidation/authoring, Evolution promotion, Credential Grant administration, Skill publication, and other control planes are independent application services. Runtime receives only narrow serving/resolution capabilities.
