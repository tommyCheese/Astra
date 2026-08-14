# Governed Agent Hooks Implementation Inventory

Baseline: 2026-08-12. No Hook implementation exists yet; this file freezes the current insertion boundaries for later review.

## Hook Types and Execution Model

| Hook family | Events | Sync/async | Core purpose | Existing coupling it can remove |
|---|---|---|---|---|
| Run/prompt admission | `run.before_start`, `prompt.before_accept` | synchronous | reject or add bounded, attributed context before acceptance | product/policy checks embedded in Run command orchestration |
| Model admission/observation | `model.before_request`; `model.responded`, `model.failed` | before is synchronous and context-only; results are asynchronous observations | contextual guardrails plus audit/metrics without response mutation | model-call telemetry and context decorators tied to decision services |
| Tool admission/observation | `tool.before_authorize`; execution/result events | before is synchronous; facts are asynchronous | restrict/ask/patch input, then force schema/effect re-analysis and authorization; observe outcomes | tool-specific policy branches and external audit callbacks inside invocation code |
| Approval observation | `approval.requested`, `approval.decided` | asynchronous | audit human decisions and frozen provenance | notification/audit logic coupled to approval persistence |
| Compaction | `context.before_compact`; compacted/failed | synchronous bounded notification/admission; outcomes asynchronous | export/guard before compaction and observe result without changing checkpoints | backup/audit behavior coupled to compaction service |
| Subagent admission/observation | `subagent.before_start`; started/stopped | before is synchronous; lifecycle facts asynchronous | further attenuate delegation, audit lineage and outcomes | tenant/deployment delegation checks embedded in Subagent orchestration |
| Completion admission/observation | `run.before_complete`; completed/failed/cancelled | before is synchronous; terminal facts asynchronous | require bounded remediation before canonical completion and notify after commit | compliance/verification completion checks scattered around finalization |

`continue` never means authorization. Only the canonical Permission Engine may authorize effects. Observation Hooks cannot mutate canonical outcomes.

## Current Insertion Boundaries

- Fixed runtime contract: `backend/app/application/agent_runtime/contracts.py`, `composition.py`, `loop.py`. Do not add an arbitrary Hook/middleware slot to the loop.
- Runtime observation: adapt through the existing trusted `LifecycleObserver` capability and canonical Runtime/Run events.
- Run/prompt lifecycle: `backend/app/application/run_management/`.
- Tool admission: after resolution/schema validation at `services/tooling/action_boundary.py` / `services/execution/tool_action.py`, before trusted effect analysis and authorization.
- Compaction: `backend/app/application/context_compaction/`.
- Subagent delegation/supervision: `backend/app/application/subagents/`.
- Completion: typed completion/finalization boundary under `application/agent_runtime/services/completion/` and Run finalization.

## Proposed New Owners

- `application/hooks/`: schemas, catalog contracts, frozen binding resolution, admission aggregation and observation orchestration.
- `infrastructure/db/models` and `infrastructure/repositories`: definitions, versions, Run snapshots, executions, outbox, leases and dead letters.
- `infrastructure/hooks/`: managed, isolated-command and restricted-HTTP handlers.
- `interfaces/api`: management, dry-run, history and replay endpoints.
- Frontend: management/timeline views using the transport-neutral projection/component boundary, without coupling to AG-UI delivery.

## Review Decisions Required Before Implementation

- Approve the v1 event catalog and sync/async classification above.
- Confirm managed-only HTTP/admission scope, interactive `blocked` versus unattended `failed` completion cap behavior, and exact-name-only compatibility matcher import.
- Confirm staged delivery: observation-only, then managed admission, then isolated command/restricted HTTP, then UI/import.
- Confirm that no implementation starts until this proposal is explicitly approved.
