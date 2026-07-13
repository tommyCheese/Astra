from app.schemas.agent import RunResult, RunView


def test_run_result_normalizes_legacy_payload_and_omits_unknown_fields():
    result = RunResult.model_validate(
        {
            "summary": "legacy",
            "findings": "one finding",
            "sources": ["https://example.com", None, {}],
            "failed_sources": [None, "bad", {"url": "https://bad.example", "message": "no"}],
            "source_quality": [None, {"url": "https://example.com", "quality_score": 0.8}],
            "conflicts": "not-a-record",
            "caveats": "legacy caveat",
            "verification_notes": None,
            "memory_references": [None, {"id": "memory-1", "confidence": "invalid"}],
            "audit_refs": "invalid",
            "verification_report": {"notes": []},
            "completion_decision": {"state": "unknown"},
            "internal_debug_payload": {"secret": True},
        }
    )

    payload = result.model_dump(mode="json")
    assert payload["findings"] == [
        {"text": "one finding", "source_urls": [], "artifact_ids": []}
    ]
    assert [source["url"] for source in payload["sources"]] == ["https://example.com"]
    assert payload["failed_sources"][0]["url"] == "https://bad.example"
    assert payload["source_quality"][0]["quality_score"] == 0.8
    assert payload["conflicts"] == []
    assert payload["caveats"] == ["legacy caveat"]
    assert payload["memory_references"] == []
    assert payload["audit_refs"]["referenced_artifact_ids"] == []
    assert payload["verification_report"] is None
    assert payload["completion_decision"] is None
    assert "internal_debug_payload" not in payload


def test_run_result_preserves_structured_failure():
    result = RunResult.model_validate(
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
