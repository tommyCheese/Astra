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

共享的 typed test doubles 位于 `backend/tests/support/`。Application service 使用 builders 和 fake ports；Agent 阶段使用 scripted model client；真实 Repository 继续使用内存 SQLite 或显式 PostgreSQL integration marker。
