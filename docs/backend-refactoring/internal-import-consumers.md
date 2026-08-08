# 后端内部导入消费者登记

基线日期：2026-08-04。生产包以外的 Python 消费者包含测试和 Alembic 入口。迁移内部模块路径时必须搜索并更新这些消费者；以下登记使用受控集合而非承诺兼容私有路径。

## 基础设施消费者

- `backend/alembic/env.py`

## 测试消费者

- `backend/tests/conftest.py`
- `backend/tests/fake_information_tools.py`
- `backend/tests/test_agent_evolution_api.py`
- `backend/tests/test_agent_evolution_domain.py`
- `backend/tests/test_agent_executions.py`
- `backend/tests/test_agent_loop.py`
- `backend/tests/test_agent_profile.py`
- `backend/tests/test_api.py`
- `backend/tests/test_approvals.py`
- `backend/tests/test_artifacts_sandbox.py`
- `backend/tests/test_autodream_worker.py`
- `backend/tests/test_automation_commands.py`
- `backend/tests/test_bash_tool.py`
- `backend/tests/test_capability_tool_selection.py`
- `backend/tests/test_chart.py`
- `backend/tests/test_context_compaction_policy.py`
- `backend/tests/test_context_compaction_service.py`
- `backend/tests/test_context_tool_output_governance.py`
- `backend/tests/test_conversation_context.py`
- `backend/tests/test_conversation_retention.py`
- `backend/tests/test_db_session.py`
- `backend/tests/test_docker_integration.py`
- `backend/tests/test_docker_provider.py`
- `backend/tests/test_effect_aware_security.py`
- `backend/tests/test_engine.py`
- `backend/tests/test_errors.py`
- `backend/tests/test_grounding.py`
- `backend/tests/test_invocation_pipeline.py`
- `backend/tests/test_memory_consolidation_api.py`
- `backend/tests/test_memory_consolidation_domain.py`
- `backend/tests/test_memory_consolidation_repository.py`
- `backend/tests/test_memory_domain.py`
- `backend/tests/test_memory_evaluation.py`
- `backend/tests/test_memory_repository.py`
- `backend/tests/test_memory_retrieval.py`
- `backend/tests/test_memory_runtime.py`
- `backend/tests/test_model_client.py`
- `backend/tests/test_model_connection.py`
- `backend/tests/test_model_reasoning.py`
- `backend/tests/test_model_reasoning_transport.py`
- `backend/tests/test_model_thinking.py`
- `backend/tests/test_node_worker_tool_selection.py`
- `backend/tests/test_parallel_execution.py`
- `backend/tests/test_permission_engine.py`
- `backend/tests/test_permission_foundations.py`
- `backend/tests/test_persistence_baseline.py`
- `backend/tests/test_plan_runtime.py`
- `backend/tests/test_plugin_catalog.py`
- `backend/tests/test_plugin_characterization.py`
- `backend/tests/test_plugin_contracts.py`
- `backend/tests/test_qa_latency_benchmark.py`
- `backend/tests/test_reasoning.py`
- `backend/tests/test_repository.py`
- `backend/tests/test_root_context_compaction.py`
- `backend/tests/test_run_result_schema.py`
- `backend/tests/test_runtime_events.py`
- `backend/tests/test_runtime_profile_repository.py`
- `backend/tests/test_runtime_profiles.py`
- `backend/tests/test_sandboxed_tools.py`
- `backend/tests/test_schedule_calculations.py`
- `backend/tests/test_schedule_repository.py`
- `backend/tests/test_scheduled_deliverables.py`
- `backend/tests/test_scheduled_dispatcher.py`
- `backend/tests/test_scheduler_service.py`
- `backend/tests/test_skills.py`
- `backend/tests/test_subagent_context.py`
- `backend/tests/test_subagent_contracts.py`
- `backend/tests/test_subagent_executor.py`
- `backend/tests/test_subagent_fan_in.py`
- `backend/tests/test_subagent_governance.py`
- `backend/tests/test_subagent_lifecycle.py`
- `backend/tests/test_subagent_observability.py`
- `backend/tests/test_subagent_scheduling.py`
- `backend/tests/test_system_command_parsing.py`
- `backend/tests/test_tools.py`
- `backend/tests/test_usage.py`

## 重新生成

```bash
rg -l '^(from|import) app([.]|\b)' backend/tests backend/alembic runtimes scripts -g '*.py' | sort
```

任何新消费者必须登记其依赖的公开能力入口；禁止新增对带下划线私有符号的跨包导入。
