import pytest

from app.core.config import Settings
from app.runner.model_client import (
    ModelConfigurationError,
    MockModelClient,
    build_model_client,
    normalize_contract_payload,
    normalize_plan_payload,
    parse_json_object,
)
from app.schemas.agent import PlanOutput, TaskContract


async def test_mock_model_client_returns_structured_outputs():
    client = MockModelClient()
    plan = await client.plan("查询 Astra")
    answer = await client.synthesize(
        "查询 Astra",
        [{"url": "https://example.com/a", "content": "示例内容", "retrieved_at": "now"}],
    )

    assert plan.steps
    assert "web_search" in plan.required_tools
    assert answer.sources[0].url == "https://example.com/a"


async def test_mock_model_client_agent_decisions():
    client = MockModelClient()
    first = await client.decide("查询 Astra", {"observations": []})
    second = await client.decide(
        "查询 Astra",
        {
            "observations": [
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {
                        "tool_name": "web_search",
                        "candidates": [{"url": "https://example.com/a", "snippet": "A"}],
                    },
                }
            ]
        },
    )
    final = await client.decide(
        "查询 Astra",
        {
            "observations": [
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {
                        "tool_name": "web_search",
                        "candidates": [{"url": "https://example.com/a", "snippet": "A"}],
                    },
                },
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {"tool_name": "web_fetch", "url": "https://example.com/a"},
                },
            ]
        },
    )

    assert first.tool_name == "web_search"
    assert second.tool_name == "web_fetch"
    assert final.decision_type == "finalize"


async def test_mock_model_reflection_and_memory_candidates():
    client = MockModelClient()
    reflection = await client.reflect("查询 Astra", {"last_observation": {"status": "failed"}})
    memories = await client.extract_memory_candidates(
        "查询 Astra",
        {
            "run_id": "run-1",
            "evidence_pack": {
                "artifact_id": "artifact-1",
                "fetched_sources": [{"url": "https://example.com"}],
            },
        },
    )

    assert reflection.next_action
    assert memories[0].provenance["artifact_id"] == "artifact-1"


def test_real_model_requires_credentials():
    settings = Settings(model_provider="openai", model_api_key="")

    with pytest.raises(ModelConfigurationError):
        build_model_client(settings)


def test_model_json_parser_accepts_fences_and_leading_text():
    assert parse_json_object('```json\n{"answer": "ok"}\n```') == {"answer": "ok"}
    assert parse_json_object('Here is the JSON: {"answer": "ok"}') == {"answer": "ok"}


def test_model_payload_normalization_accepts_shorthand_contract_and_plan():
    contract = TaskContract.model_validate(normalize_contract_payload({
        "original_goal": "你好",
        "assumptions": ["用户希望得到问候"],
        "success_criteria": ["自然回应"],
        "verification_requirements": ["task_adapter"],
    }, "你好"))
    plan = PlanOutput.model_validate(normalize_plan_payload({
        "steps": [{"title": "生成回复", "intent": "问候", "success_criteria": "用户收到回复"}],
        "success_criteria": "用户感到被回应",
    }))

    assert contract.assumptions[0].statement == "用户希望得到问候"
    assert contract.success_criteria[0].verification_method == "task_adapter"
    assert plan.steps[0].intent == "问候"
    assert plan.steps[0].success_criteria == ["用户收到回复"]
    assert plan.success_criteria == ["用户感到被回应"]
