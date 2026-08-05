import pytest
from pydantic import ValidationError

from app.common.schemas.agent.api_views import RunView
from app.common.schemas.agent.run_result import AgentRunResult


def test_verification_report_defaults_optional_validation_outcomes():
    result = AgentRunResult.model_validate(
        {
            "summary": "legacy",
            "verification_report": {"status": "completed", "notes": []},
        }
    )

    assert result.verification_report is not None
    assert result.verification_report.validation_outcomes == []


def test_run_result_rejects_obsolete_payload():
    with pytest.raises(ValidationError):
        AgentRunResult.model_validate(
            {
                "summary": "obsolete",
                "findings": "one finding",
                "sources": ["https://example.com", None, {}],
                "failed_sources": [
                    None,
                    "bad",
                    {"url": "https://bad.example", "message": "no"},
                ],
                "source_quality": [
                    None,
                    {"url": "https://example.com", "quality_score": 0.8},
                ],
                "conflicts": "not-a-record",
                "caveats": "obsolete caveat",
                "verification_notes": None,
                "memory_references": [
                    None,
                    {"id": "memory-1", "confidence": "invalid"},
                ],
                "audit_refs": "invalid",
                "verification_report": {"notes": []},
                "completion_decision": {"state": "unknown"},
                "internal_debug_payload": {"secret": True},
            }
        )


def test_run_result_preserves_grounding_claims_citations_and_audit_refs():
    result = AgentRunResult.model_validate(
        {
            "summary": "grounded",
            "claims": [
                {
                    "id": "claim-1",
                    "text": "grounded",
                    "evidence_refs": ["evidence-1"],
                    "support_status": "supported",
                }
            ],
            "citations": [
                {
                    "id": "citation-1",
                    "claim_id": "claim-1",
                    "evidence_ref": "evidence-1",
                    "url": "https://example.com/source",
                    "ordinal": 1,
                }
            ],
            "audit_refs": {
                "evidence_ledger_artifact_id": "artifact-1",
                "evidence_record_count": 3,
            },
        }
    )

    assert result.claims[0].evidence_refs == ["evidence-1"]
    assert result.citations[0].claim_id == "claim-1"
    assert result.audit_refs.evidence_ledger_artifact_id == "artifact-1"
    assert result.audit_refs.evidence_record_count == 3


def test_run_result_preserves_structured_failure():
    result = AgentRunResult.model_validate(
        {
            "summary": "无法完成",
            "error": {
                "type": "dependency.model_response_invalid",
                "code": "MODEL_RESPONSE_INVALID",
                "message": "模型响应无效",
                "retryable": True,
                "trace_id": "req_123",
                "details": {"reason": "invalid JSON"},
            },
        }
    )

    assert result.error is not None
    assert result.error.code == "MODEL_RESPONSE_INVALID"
    assert result.error.retryable is True
    assert result.findings == []
    assert result.sources == []


def test_run_view_keeps_null_result_for_in_progress_run():
    view = RunView.model_validate(
        {
            "id": "run-1",
            "task_id": "task-1",
            "status": "executing",
            "mode": "web_agent",
            "summary": None,
            "result": None,
            "steps": [],
            "tool_calls": [],
            "artifacts": [],
            "events": [],
        }
    )

    assert view.result is None
