# 重构 Characterization Matrix

本矩阵把不可破坏语义映射到当前测试。迁移实现时优先保持这些公共行为，不复制旧私有调用结构。

| 不变量 | 主要测试 | 测试层次 |
| --- | --- | --- |
| Run 创建与 follow-up 复用 Conversation | `test_api.py::test_create_and_get_run`、`test_repository.py::test_follow_up_run_reuses_task` | API / Repository integration |
| standard 快速路径不创建 Plan/QA 对象 | `test_engine.py::test_standard_profile_skips_planning_and_quality_assurance_objects` | Runtime integration |
| trusted 模式创建完整 contract/plan | `test_engine.py::test_trusted_engine_always_builds_contract_and_complete_plan` | Runtime integration |
| plan confirm 只激活绑定版本一次 | `test_engine.py::test_trusted_confirmation_activates_exact_plan_once_before_execution` | Runtime integration |
| waiting、completed、warning、blocked、cancelled 终态 | `test_engine.py`、`test_agent_loop.py`、`test_repository.py::test_run_lifecycle_persistence`、`test_cancel_run_is_idempotent_and_preserves_partial_answer` | Runtime / Repository integration |
| checkpoint 恢复不重复 Tool call | `test_engine.py::test_engine_replays_recorded_checkpoint_without_duplicate_tool_call` | Runtime integration |
| Approval 冻结输入与 exactly-once | `test_approvals.py::test_request_approval_freezes_tool_before_execution`、`test_approve_once_resumes_exact_frozen_call`、`test_approval_resume_preserves_tool_budget_and_does_not_execute_twice` | Application integration |
| Approval reject、replay 和 tamper fail closed | `test_approvals.py::test_rejection_never_executes_rejected_call_and_replay_fails`、`test_approved_action_fails_closed_when_frozen_input_is_tampered` | Security integration |
| SSE committed-only、cursor、critical ordering | `test_runtime_events.py` 与 `test_api.py` 的 event-stream tests | Domain / API integration |
| 事务提交后才发布事件 | `test_runtime_events.py::test_event_aware_session_notifies_only_after_commit`、`test_event_aware_session_discards_rolled_back_notifications` | Database integration |
| 模型等待前释放 read transaction | `test_agent_loop.py::test_standard_mode_releases_read_transaction_before_model_wait` | Runtime integration |
| 权限 effect-aware deny/ask/allow | `test_effect_aware_security.py`、`test_permission_engine.py` | Domain / integration |
| root/subagent 权限衰减与 catalog 冻结 | `test_subagent_governance.py` | Domain / Repository integration |
| child fencing、取消传播与安全恢复 | `test_subagent_lifecycle.py`、`test_subagent_scheduling.py` | Concurrency integration |
| Tool audit、Workspace 与 Artifact 归属 | `test_agent_loop.py`、`test_artifacts_sandbox.py`、`test_sandboxed_tools.py` | Runtime / security integration |
| 历史 Run result JSON | `test_run_result_schema.py`、`test_repository.py::test_run_view_rejects_obsolete_persisted_result_contract` | Contract / Repository integration |
| ORM 54 表精确集合 | `test_refactoring_contracts.py::test_orm_metadata_matches_refactoring_baseline` | Persistence contract |
| OpenAPI canonical shape | `test_refactoring_contracts.py::test_openapi_contract_matches_refactoring_baseline` | HTTP contract |
| Alembic fresh/history/no-diff | `test_persistence_baseline.py` | Migration integration |

## Single-Loop paired coverage

下列行为在 standard 与 trusted composition 上共同受保护；mode-specific 事件名称仍作为持久化兼容边界保留，不再代表两套控制器。

| 成对行为 | 覆盖 |
| --- | --- |
| 直接回答、流式回答与迟到的结构化失败 | `test_engine.py::test_streamed_answer_is_not_replaced_after_late_model_validation_error`、`test_streamed_answer_is_not_resynthesized_when_answer_object_is_missing` |
| Tool 成功、拒绝与强制安全边界 | `test_fast_agent_runtime.py`、`test_agent_loop.py`、`test_effect_aware_security.py` |
| Approval wait/resume、拒绝、篡改与 exactly-once | `test_approvals.py`、`test_fast_runtime_foundations.py` |
| cancellation、幂等恢复与 result-unknown | `test_engine.py`、`test_fast_runtime_foundations.py`、`test_parallel_execution.py` |
| Skill 显式/自动激活与不兼容能力隔离 | `test_skills.py`、`test_fast_agent_runtime.py`、`test_engine.py` |
| bounded Memory、Workspace 与 Artifact | `test_memory_runtime.py`、`test_memory_tools.py`、`test_workspace_tools.py`、`test_agent_loop.py` |
| Subagent eligibility、required barrier 与安全衰减 | `test_subagent_governance.py`、`test_subagent_fan_in.py`、`test_agent_loop.py` |
| canonical lifecycle 与公开事件顺序 | `test_runtime_core_loop.py`、`test_runtime_events.py`、`test_engine.py` |

共享的 typed test doubles 位于 `backend/tests/support/`。Application service 使用 builders 和 fake ports；Agent 阶段使用 scripted model client；真实 Repository 继续使用内存 SQLite 或显式 PostgreSQL integration marker。
