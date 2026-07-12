import pytest

from app.core.config import Settings
from app.runner.model_client import (
    MockModelClient,
    ModelConfigurationError,
    build_model_client,
    extract_partial_json_string,
    normalize_contract_payload,
    normalize_final_answer_payload,
    normalize_plan_payload,
    parse_json_object,
)
from app.schemas.agent import FinalAnswer, PlanOutput, TaskContract


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


def test_mock_model_requires_no_credentials():
    client = build_model_client(Settings(model_provider="mock", model_api_key=""))

    assert isinstance(client, MockModelClient)


def test_model_json_parser_accepts_fences_and_leading_text():
    assert parse_json_object('```json\n{"answer": "ok"}\n```') == {"answer": "ok"}
    assert parse_json_object('Here is the JSON: {"answer": "ok"}') == {"answer": "ok"}


def test_partial_json_string_streams_only_complete_characters():
    assert extract_partial_json_string('{"summary":"你好，世', "summary") == "你好，世"
    assert extract_partial_json_string('{"summary":"line\\nnext', "summary") == "line\nnext"
    assert extract_partial_json_string('{"summary":"A\\u4f6', "summary") == "A"
    assert extract_partial_json_string('{"summary":"A\\u4f60', "summary") == "A你"


def test_model_payload_normalization_accepts_shorthand_contract_and_plan():
    contract = TaskContract.model_validate(
        normalize_contract_payload(
            {
                "original_goal": "你好",
                "assumptions": ["用户希望得到问候"],
                "success_criteria": ["自然回应"],
                "verification_requirements": ["task_adapter"],
            },
            "你好",
        )
    )
    plan = PlanOutput.model_validate(
        normalize_plan_payload(
            {
                "steps": [
                    {"title": "生成回复", "intent": "问候", "success_criteria": "用户收到回复"}
                ],
                "success_criteria": "用户感到被回应",
            }
        )
    )

    assert contract.assumptions[0].statement == "用户希望得到问候"
    assert contract.success_criteria[0].verification_method == "task_adapter"
    assert plan.steps[0].intent == "问候"
    assert plan.steps[0].success_criteria == ["用户收到回复"]
    assert plan.success_criteria == ["用户感到被回应"]


def test_contract_goal_mismatch_falls_back_to_user_request():
    contract = TaskContract.model_validate(
        normalize_contract_payload(
            {
                "original_goal": "开发一个用户登录功能",
                "deliverables": ["登录 API"],
                "ambiguity_status": "低",
                "clarification_question": "是否支持 OAuth？",
            },
            "你好",
        )
    )

    assert contract.original_goal == "你好"
    assert contract.deliverables == ["回复用户请求：你好"]
    assert contract.ambiguity_status == "clear"
    assert contract.clarification_question is None


def test_final_answer_normalization_accepts_nullable_and_scalar_fields():
    answer = FinalAnswer.model_validate(
        normalize_final_answer_payload(
            {
                "summary": "递归是自我调用。",
                "findings": "递归会调用自身",
                "sources": None,
                "source_quality": None,
                "caveats": "这是简化解释",
                "verification_notes": "无需外部来源",
            }
        )
    )

    assert answer.findings[0].text == "递归会调用自身"
    assert answer.caveats == ["这是简化解释"]
    assert answer.source_quality == []


def test_final_answer_normalization_drops_scalar_record_placeholders():
    answer = FinalAnswer.model_validate(
        normalize_final_answer_payload(
            {
                "summary": "完成",
                "source_quality": ["N/A"],
                "failed_sources": ["none"],
                "conflicts": "none",
                "memory_references": "none",
            }
        )
    )

    assert answer.source_quality == []
    assert answer.failed_sources == []
    assert answer.conflicts == []
    assert answer.memory_references == []
