"""Stable external and persistence contracts protected during backend refactoring."""

import ast
import hashlib
import json
from pathlib import Path

from fake_information_tools import FakeSearch
from sqlalchemy import create_mock_engine
from support import (
    DecisionStep,
    RunRequestBuilder,
    ScriptedDecisionClient,
)
from support import TrustedRuntimeHarness as AstraAgentLoop

from app.application.agent_runtime.policies.reasoning import resolve_run_profile
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.common.schemas.agent.types import AnswerMode
from app.infrastructure.db.model_base import AstraOrmRecordBase
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry
from app.main import create_app

EXPECTED_OPENAPI_SHA256 = "3be25e7c97ef29695ce295a615e59d6ac9d668cc9dd6bcbcbc0dd2ab7e80ebf2"
EXPECTED_ORM_TABLES = {
    "agent_budget_reservations",
    "agent_delegations",
    "agent_evolution_audit_events",
    "agent_evolution_candidates",
    "agent_evolution_evaluations",
    "agent_evolution_sources",
    "agent_executions",
    "agent_identities",
    "agent_joins",
    "agent_turns",
    "approval_grants",
    "approval_requests",
    "artifacts",
    "budget_reservations",
    "context_compaction_attempts",
    "conversation_shares",
    "conversation_strategy_preferences",
    "credential_grants",
    "data_flow_states",
    "evidence_records",
    "memories",
    "memory_audit_events",
    "memory_consolidation_jobs",
    "memory_links",
    "memory_recall_events",
    "memory_sources",
    "model_invocations",
    "node_executions",
    "plan_edges",
    "plan_nodes",
    "plans",
    "resource_leases",
    "run_events",
    "run_skill_snapshots",
    "runs",
    "runtime_builds",
    "runtime_profiles",
    "sandbox_jobs",
    "scheduled_job_runs",
    "scheduled_jobs",
    "skill_audit_events",
    "skill_blobs",
    "skill_drafts",
    "skill_revisions",
    "skills",
    "steps",
    "task_workspaces",
    "tasks",
    "tool_calls",
    "tool_catalog_snapshots",
    "tool_provider_settings",
    "tool_settings",
    "tool_settings_audit",
    "workspace_changes",
    "workspace_checkpoints",
    "workspace_files",
}

CORE_LOOP_MODULES = (
    "app/application/agent_runtime/contracts.py",
    "app/application/agent_runtime/composition.py",
    "app/application/agent_runtime/loop.py",
    "app/application/agent_runtime/action.py",
)


def test_runtime_dispatch_and_action_path_remain_readable():
    backend = Path(__file__).parents[1]
    engine = (backend / "app/application/run_management/execution/service.py").read_text()
    standard = (backend / "app/infrastructure/bootstrap/standard_runtime.py").read_text()
    trusted = (backend / "app/infrastructure/bootstrap/trusted_runtime.py").read_text()
    loop = (backend / "app/application/agent_runtime/loop.py").read_text()

    assert "run_standard_runtime(" in engine
    assert "run_trusted_runtime(" in engine
    assert "await run_loop(" in standard
    assert "await run_loop(" in trusted
    assert "composition.ports.action(" in loop
    assert "_process_observation(composition" in loop

    for relative_path in CORE_LOOP_MODULES:
        source = (backend / relative_path).read_text()
        assert len(source.splitlines()) <= 500, relative_path
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno is not None
                assert node.end_lineno - node.lineno + 1 <= 60, (
                    relative_path,
                    node.name,
                )


def test_openapi_contract_matches_refactoring_baseline():
    openapi_document = create_app().openapi()
    canonical_openapi = json.dumps(
        openapi_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert len(openapi_document["paths"]) == 97
    assert len(openapi_document["components"]["schemas"]) == 201
    assert hashlib.sha256(canonical_openapi).hexdigest() == EXPECTED_OPENAPI_SHA256


def test_orm_metadata_matches_refactoring_baseline():
    assert set(AstraOrmRecordBase.metadata.tables) == EXPECTED_ORM_TABLES


def test_orm_metadata_compiles_for_postgresql():
    statements: list[str] = []
    engine = create_mock_engine(
        "postgresql+psycopg://",
        lambda statement, *args, **kwargs: statements.append(str(statement.compile(dialect=engine.dialect))),
    )

    AstraOrmRecordBase.metadata.create_all(engine, checkfirst=False)

    create_table_statements = [statement for statement in statements if statement.lstrip().startswith("CREATE TABLE")]
    assert len(create_table_statements) == len(EXPECTED_ORM_TABLES)


def test_run_request_builder_uses_domain_names_and_typed_defaults():
    request = RunRequestBuilder().with_values(goal="重构后仍可运行").build()

    assert request.goal == "重构后仍可运行"
    assert request.task_id is None
    assert request.answer_mode.value == "standard"
    assert request.interactive is True


class TransactionInspectingSearch(FakeSearch):
    def __init__(self, session):
        self._session = session
        self.transaction_states: list[bool] = []

    async def run(self, tool_input, *, context=None):
        self.transaction_states.append(self._session.in_transaction())
        return await super().run(tool_input, context=context)


async def test_tool_execution_does_not_hold_a_database_transaction(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    execution_profile = resolve_run_profile(
        AnswerMode.standard,
        RequestedReasoningPolicy(execution_mode="auto_approval", reflection_enabled=False),
    )
    run_repository = RunUnitOfWork(session)
    run = await run_repository.create_task_run(
        "检索后总结",
        settings.model_policy,
        reasoning_policy=execution_profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=execution_profile.answer_mode.value,
        execution_profile=execution_profile.model_dump(mode="json"),
    )
    goal = run.task.description
    run_id = run.id
    await run_repository.commit()
    model_client = ScriptedDecisionClient(
        [
            DecisionStep(
                AgentDecision(
                    decision_type="call_tool",
                    reasoning_summary="先检索证据",
                    tool_name="catalog_search",
                    tool_input={"query": "Astra"},
                )
            ),
            DecisionStep(
                AgentDecision(decision_type="finalize", reasoning_summary="证据足够"),
                AgentFinalAnswer(summary="检索完成"),
            ),
        ]
    )
    search_tool = TransactionInspectingSearch(session)
    tool_registry = AstraToolRegistry()
    tool_registry.register(search_tool)

    await AstraAgentLoop(
        settings,
        model_client=model_client,
        tool_registry=tool_registry,
    ).run(run_repository, run_id, goal)

    assert search_tool.transaction_states == [False]
    assert model_client.remaining_step_count == 0
