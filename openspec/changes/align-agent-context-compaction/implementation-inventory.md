# Context Compaction Implementation Inventory

This inventory is the implementation map for all context-owning execution paths. It deliberately separates canonical/audit persistence from model-visible projections.

| Role/path | Context construction | Observation/audit persistence | Ordinary model generation | Usage accounting | Recovery/checkpoint |
|---|---|---|---|---|---|
| Conversation | `ConversationContextManager.projection`, `render`, `status`, `prepare_for_run` in `backend/app/conversation_context.py` | `TaskRecord.context_state`; Runs remain in `ConversationRepository` | The subsequent root Run uses the selected `ModelClient`; compaction must call the same generic client | `estimate_tokens`/`estimate_messages_tokens`, model catalog window | V1 `summary`/`folded_run_ids` in `context_state`; `/compact` in `backend/app/api/conversations.py` |
| Standard root | `AgentContextBuilder.build` and the quick branch of `AgentLoop.execute` in `backend/app/runner/agent_loop.py`; standard inputs are loaded by `RunRepository.load_standard_context_inputs` | `RunRecord.agent_state.observations`, Turns, ToolCalls, Artifacts/Evidence; root mirror in `AgentExecutionRecord.checkpoint` | `decide_with_answer`, `reflect`, `finalize` on `ModelClient` | `DatabaseUsageRecorder` attached in `backend/app/runner/engine.py`; Provider usage normalized by `repositories/usage.py` | Turn phase recovery in `AgentLoop.execute`; root state synchronized by `RunRepository` |
| Trusted root | `AgentContextBuilder.build`, canonical TaskContract/Plan/AgentState and trusted branch of `AgentLoop.execute` | Versioned `RunRecord.agent_state`, Plan/NodeExecution, Turns, ToolCalls, Artifact/Evidence and root execution mirror | `contract`, `plan`, `decide_with_answer`, `reflect`, `finalize` on the same generic `ModelClient` | Same recorder plus canonical `budget_usage` in AgentState | `AgentLoop` replays committed tool results from AgentTurn; state CAS in `RunRepository.update_agent_state_if_version` |
| Quick child | `SubagentContextComposer.compose` plus `LocalAstraAgentExecutor.execute`; eligibility explicitly supports quick root delegation | `AgentExecutionRecord.context_manifest`, `checkpoint.observations`, Turns/ToolCalls/Artifacts/Evidence | `plan`, `decide_with_answer`, `reflect` on the bound ordinary `ModelClient` | Child `budget_usage` and invocation attribution to `agent_execution_id` | `SubagentExecutionRecovery`; continuation handling in `SubagentRuntime.resume_child` |
| Trusted child | Same isolated manifest/executor path, with frozen contract, attenuated tool/skill catalogs and local plan | Same child-owned canonical and audit records; fan-in promotes only validated results | Same ordinary `ModelClient` surface, never a Provider compaction endpoint | Same child budget envelope and Provider-reported invocation usage | Same V1 checkpoint reader, fencing/state-version/cancellation-epoch recovery |

## Shared integration seams

- `backend/app/runner/model_client.py` is the only model generation abstraction compaction may use. No compaction-specific Provider request fields belong there.
- `backend/app/repositories/usage.py` is the source of normalized Provider-reported usage; conservative local accounting is the fallback.
- `backend/app/repositories/runs.py` and `backend/app/repositories/agent_executions.py` already expose versioned writes suitable for conditional checkpoint installation.
- `backend/app/subagents/context.py` owns manifest/reference access and continuation validation; V2 child checkpoint validation must reuse those boundaries.
- `backend/app/subagents/recovery.py` is the compatibility gate for persisted child checkpoints.
- `backend/app/runner/node_worker.py` has an additional trusted node-level decision loop and must receive the same pre-model/post-tool pressure checks as the main loop.

## Invariants for implementation

- Full Runs, Turns, ToolCalls, Artifacts and Evidence remain authoritative and are never deleted by compaction.
- Standard paths must not synthesize trusted-only TaskContract/Plan state.
- Child compaction never receives full parent history, sibling state, credentials or hidden reasoning.
- Active model context is rebuilt as protected prefix + one Astra checkpoint + a bounded chronological raw tail.
