"""Stable external and persistence contracts protected during backend refactoring."""

import hashlib
import json

from fake_web_tools import FakeSearch
from sqlalchemy import create_mock_engine
from support import DecisionStep, RunRequestBuilder, ScriptedDecisionClient

from app.agent_runtime.reasoning import RunProfileResolver
from app.agent_runtime.service import AgentLoop
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.execution_state import AgentDecision
from app.schemas.agent.run_policy import RequestedReasoningPolicy
from app.schemas.agent.run_result import FinalAnswer
from app.schemas.agent.types import AnswerMode
from app.tools.base import ToolRegistry

EXPECTED_OPENAPI_SHA256 = "904475c47d2d55691ee1e78df214d29579ba4b8461297213fa4e85aaeace8465"
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
    "tool_settings",
    "workspace_changes",
    "workspace_checkpoints",
    "workspace_files",
}


def test_openapi_contract_matches_refactoring_baseline():
    openapi_document = create_app().openapi()
    canonical_openapi = json.dumps(
        openapi_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert len(openapi_document["paths"]) == 93
    assert len(openapi_document["components"]["schemas"]) == 198
    assert hashlib.sha256(canonical_openapi).hexdigest() == EXPECTED_OPENAPI_SHA256


def test_orm_metadata_matches_refactoring_baseline():
    assert set(Base.metadata.tables) == EXPECTED_ORM_TABLES


def test_orm_metadata_compiles_for_postgresql():
    statements: list[str] = []
    engine = create_mock_engine(
        "postgresql+psycopg://",
        lambda statement, *args, **kwargs: statements.append(
            str(statement.compile(dialect=engine.dialect))
        ),
    )

    Base.metadata.create_all(engine, checkfirst=False)

    create_table_statements = [
        statement for statement in statements if statement.lstrip().startswith("CREATE TABLE")
    ]
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
    settings = Settings(model_provider="mock", web_search_provider="mock")
    execution_profile = RunProfileResolver().resolve(
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
                    tool_name="web_search",
                    tool_input={"query": "Astra"},
                )
            ),
            DecisionStep(
                AgentDecision(decision_type="finalize", reasoning_summary="证据足够"),
                FinalAnswer(summary="检索完成"),
            ),
        ]
    )
    search_tool = TransactionInspectingSearch(session)
    tool_registry = ToolRegistry()
    tool_registry.register(search_tool)

    await AgentLoop(
        settings,
        model_client=model_client,
        tool_registry=tool_registry,
    ).run(run_repository, run_id, goal)

    assert search_tool.transaction_states == [False]
    assert model_client.remaining_step_count == 0
