from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.evolution import (
    get_available_evolution_tools,
)
from app.api.evolution import (
    router as evolution_router,
)
from app.core.errors import AstraError, ErrorEnvelope
from app.db.models import (
    AgentEvolutionCandidateRecord,
    Base,
    RunRecord,
    TaskRecord,
    utc_now,
)
from app.db.session import get_session
from app.evolution import (
    EvaluationCaseResult,
    EvaluationCaseSplit,
    EvaluationManifest,
    EvaluationResultSummary,
    EvolutionCandidate,
    EvolutionCandidateType,
    EvolutionSourceReference,
    EvolutionSourceType,
    EvolutionTarget,
    SafetyMetricDirection,
    SafetyMetricResult,
)


def digest(seed: str) -> str:
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


def procedure_candidate(
    run_id: str,
    *,
    key: str,
    required_tools: tuple[str, ...] = (),
) -> EvolutionCandidate:
    return EvolutionCandidate(
        candidate_key=key,
        candidate_type=EvolutionCandidateType.procedure,
        target=EvolutionTarget.procedure,
        title="Verified procedure",
        content="Use the bounded, verified read-only workflow.",
        source_refs=(
            EvolutionSourceReference(
                source_type=EvolutionSourceType.run,
                source_id=run_id,
                digest=digest(run_id),
            ),
        ),
        required_tools=required_tools,
    )


def passing_manifest(
    candidate: EvolutionCandidate,
    *,
    safety_candidate: float = 1.0,
) -> EvaluationManifest:
    cases: list[EvaluationCaseResult] = []
    for index in range(7):
        cases.append(
            EvaluationCaseResult(
                case_id=f"representative-{index}",
                case_digest=digest(f"representative-{index}"),
                split=EvaluationCaseSplit.representative,
                baseline_score=0.8,
                candidate_score=0.9,
            )
        )
    for index in range(3):
        cases.append(
            EvaluationCaseResult(
                case_id=f"held-out-{index}",
                case_digest=digest(f"held-out-{index}"),
                split=EvaluationCaseSplit.held_out,
                baseline_score=0.8,
                candidate_score=0.9,
            )
        )
    context_digest = digest("frozen-evaluation-context")
    return EvaluationManifest(
        candidate_digest=candidate.digest,
        evaluator_id="astra.offline-eval",
        evaluator_version="1.0",
        suite_id="evolution.regression-suite",
        suite_version="2026-07",
        suite_digest=digest("suite"),
        baseline=EvaluationResultSummary(
            sample_size=len(cases),
            success_rate=0.8,
            mean_cost=1,
            mean_latency_ms=100,
            context_digest=context_digest,
        ),
        candidate=EvaluationResultSummary(
            sample_size=len(cases),
            success_rate=0.9,
            mean_cost=1.1,
            mean_latency_ms=110,
            context_digest=context_digest,
        ),
        cases=tuple(cases),
        safety_metrics=(
            SafetyMetricResult(
                name="namespace_isolation",
                direction=SafetyMetricDirection.higher_is_better,
                baseline_value=1,
                candidate_value=safety_candidate,
            ),
        ),
        thresholds={},
    )


@pytest.fixture
async def evolution_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(evolution_router)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_available_evolution_tools] = lambda: frozenset(
        {"web_search"}
    )

    @app.exception_handler(AstraError)
    async def astra_error_handler(_: Request, exc: AstraError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorEnvelope(error=exc.payload).model_dump(mode="json"),
        )

    now = utc_now()
    async with session_factory() as session:
        task = TaskRecord(
            title="Evolution source",
            description="Frozen source trajectory",
            status="completed",
            workspace_id="workspace-a",
            created_by="user-a",
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.flush()
        run = RunRecord(
            task_id=task.id,
            status="completed",
            mode="web_agent",
            answer_mode="standard",
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        client._astra_session = session_factory
        client._astra_run_id = run_id
        yield client
    await engine.dispose()


async def create_candidate(
    client: AsyncClient,
    *,
    key: str,
    required_tools: tuple[str, ...] = (),
) -> tuple[dict, EvolutionCandidate]:
    candidate = procedure_candidate(
        client._astra_run_id,
        key=key,
        required_tools=required_tools,
    )
    response = await client.post(
        "/api/agent-evolution/candidates",
        json={
            "namespace_type": "run",
            "namespace_id": client._astra_run_id,
            "actor": "operator-a",
            "candidate": candidate.model_dump(mode="json"),
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), candidate


async def attach_manifest(
    client: AsyncClient,
    candidate_id: str,
    manifest: EvaluationManifest,
    *,
    expected_state_version: int = 1,
):
    return await client.post(
        f"/api/agent-evolution/candidates/{candidate_id}/evaluations",
        json={
            "expected_state_version": expected_state_version,
            "actor": "evaluator-a",
            "reason": "offline replay",
            "manifest": manifest.model_dump(mode="json"),
        },
    )


async def test_candidate_api_approves_only_passing_non_executable_revision(
    evolution_client,
):
    created, candidate = await create_candidate(
        evolution_client,
        key="procedure.safe-research",
    )
    assert created["status"] == "draft"
    assert created["candidate_digest"] == candidate.digest
    assert created["candidate"]["candidate_key"] == "procedure.safe-research"
    assert created["executable"] is False
    assert created["production_promotion_enabled"] is False
    assert created["sources"][0]["source_id"] == evolution_client._astra_run_id

    attached = await attach_manifest(
        evolution_client,
        created["id"],
        passing_manifest(candidate),
    )
    assert attached.status_code == 200, attached.text
    attached_payload = attached.json()
    assert attached_payload["status"] == "evaluating"
    assert attached_payload["state_version"] == 2
    assert attached_payload["current_evaluation_verdict"] == "passed"
    assert attached_payload["evaluations"][0]["verdict"] == "passed"

    approved = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/approve",
        json={
            "expected_state_version": 2,
            "actor": "reviewer-a",
            "reason": "all governed gates passed",
        },
    )
    assert approved.status_code == 200, approved.text
    payload = approved.json()
    assert payload["status"] == "approved"
    assert payload["state_version"] == 3
    assert payload["executable"] is False
    assert payload["production_promotion_enabled"] is False
    assert [item["event_type"] for item in payload["audit_events"]] == [
        "candidate_created",
        "evaluation_attached",
        "candidate_approved",
    ]

    listed = await evolution_client.get(
        "/api/agent-evolution/candidates",
        params={
            "namespace_type": "run",
            "namespace_id": evolution_client._astra_run_id,
            "status": "approved",
        },
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]


async def test_stale_review_and_production_promotion_are_denied(
    evolution_client,
):
    created, candidate = await create_candidate(
        evolution_client,
        key="procedure.stale-review",
    )
    attached = await attach_manifest(
        evolution_client,
        created["id"],
        passing_manifest(candidate),
    )
    assert attached.status_code == 200
    approved = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/approve",
        json={
            "expected_state_version": 2,
            "actor": "reviewer-a",
            "reason": "passing replay",
        },
    )
    assert approved.status_code == 200

    stale = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/reject",
        json={
            "expected_state_version": 2,
            "actor": "reviewer-b",
            "reason": "stale request",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "EVOLUTION_STATE_STALE"

    promotion = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/promotion",
        json={
            "expected_state_version": 3,
            "actor": "operator-a",
            "reason": "attempt serving rollout",
            "target": "shadow",
        },
    )
    assert promotion.status_code == 409
    assert promotion.json()["error"]["code"] == "EVOLUTION_PROMOTION_DISABLED"

    unchanged = await evolution_client.get(
        f"/api/agent-evolution/candidates/{created['id']}"
    )
    assert unchanged.json()["status"] == "approved"
    assert unchanged.json()["state_version"] == 3
    assert unchanged.json()["executable"] is False


async def test_missing_baseline_is_rejected_before_persistence(evolution_client):
    created, candidate = await create_candidate(
        evolution_client,
        key="procedure.missing-baseline",
    )
    manifest_payload = passing_manifest(candidate).model_dump(mode="json")
    manifest_payload.pop("baseline")

    response = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/evaluations",
        json={
            "expected_state_version": 1,
            "actor": "evaluator-a",
            "manifest": manifest_payload,
        },
    )
    assert response.status_code == 422

    unchanged = await evolution_client.get(
        f"/api/agent-evolution/candidates/{created['id']}"
    )
    assert unchanged.json()["status"] == "draft"
    assert unchanged.json()["state_version"] == 1
    assert unchanged.json()["evaluations"] == []

    rejected = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/reject",
        json={
            "expected_state_version": 1,
            "actor": "reviewer-a",
            "reason": "baseline evidence is incomplete",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["state_version"] == 2


async def test_safety_regression_is_immutable_and_cannot_be_approved(
    evolution_client,
):
    created, candidate = await create_candidate(
        evolution_client,
        key="procedure.safety-regression",
    )
    attached = await attach_manifest(
        evolution_client,
        created["id"],
        passing_manifest(candidate, safety_candidate=0.9),
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["current_evaluation_verdict"] == "failed"
    assert attached.json()["evaluations"][0]["verdict"] == "failed"
    assert attached.json()["audit_events"][-1]["payload"]["issue_codes"] == [
        "evaluation.safety_regression"
    ]

    denied = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/approve",
        json={
            "expected_state_version": 2,
            "actor": "reviewer-a",
            "reason": "must not bypass safety",
        },
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "EVOLUTION_EVALUATION_FAILED"

    unchanged = await evolution_client.get(
        f"/api/agent-evolution/candidates/{created['id']}"
    )
    assert unchanged.json()["status"] == "evaluating"
    assert unchanged.json()["state_version"] == 2
    assert unchanged.json()["evaluations"][0]["verdict"] == "failed"


async def test_disabled_tool_reference_cannot_enter_evaluation(evolution_client):
    created, candidate = await create_candidate(
        evolution_client,
        key="procedure.disabled-tool",
        required_tools=("bash_execute",),
    )
    response = await attach_manifest(
        evolution_client,
        created["id"],
        passing_manifest(candidate),
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "EVOLUTION_AUTHORITY_VIOLATION"
    assert error["details"]["issues"][0]["code"] == "evolution.tool_unavailable"

    unchanged = await evolution_client.get(
        f"/api/agent-evolution/candidates/{created['id']}"
    )
    assert unchanged.json()["status"] == "draft"
    assert unchanged.json()["evaluations"] == []


async def test_existing_rollout_can_record_audited_rollback_metadata(
    evolution_client,
):
    created, candidate = await create_candidate(
        evolution_client,
        key="procedure.rollback",
    )
    attached = await attach_manifest(
        evolution_client,
        created["id"],
        passing_manifest(candidate),
    )
    assert attached.status_code == 200
    approved = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/approve",
        json={
            "expected_state_version": 2,
            "actor": "reviewer-a",
            "reason": "passing replay",
        },
    )
    assert approved.status_code == 200

    # Rollout creation is deliberately unavailable through this API. This
    # simulates a frozen rollout imported from a future/external controller so
    # the initial release can still stop and audit it.
    async with evolution_client._astra_session() as session:
        record = await session.get(AgentEvolutionCandidateRecord, created["id"])
        record.status = "canary"
        record.state_version = 4
        await session.commit()

    rolled_back = await evolution_client.post(
        f"/api/agent-evolution/candidates/{created['id']}/rollback",
        json={
            "expected_state_version": 4,
            "actor": "operator-a",
            "reason": "latency threshold exceeded",
            "audience": {"workspace_ids": ["workspace-a"], "traffic_percent": 5},
            "observed_metrics": {"p95_latency_ms": 900},
            "rollback_criteria": {"p95_latency_ms": {"maximum": 500}},
        },
    )
    assert rolled_back.status_code == 200, rolled_back.text
    payload = rolled_back.json()
    assert payload["status"] == "rolled_back"
    assert payload["state_version"] == 5
    assert payload["rollback_metadata"]["audience"]["traffic_percent"] == 5
    assert payload["rollback_metadata"]["observed_metrics"]["p95_latency_ms"] == 900
    assert payload["audit_events"][-1]["event_type"] == "candidate_rolled_back"
    assert payload["executable"] is False
