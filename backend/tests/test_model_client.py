from unittest.mock import AsyncMock

import pytest

from app.agent_profile import ModelOperation
from app.core.config import Settings
from app.runner.model_client import (
    MockModelClient,
    ModelConfigurationError,
    build_model_client,
    extract_partial_json_string,
    json_string_field_complete,
    normalize_contract_payload,
    normalize_final_answer_payload,
    normalize_memory_payload,
    normalize_plan_payload,
    normalize_reflection_payload,
    parse_json_object,
)
from app.schemas.agent import AgentReflection, FinalAnswer, MemoryRecord, PlanOutput, TaskContract


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
    assert not json_string_field_complete('{"summary":"still streaming', "summary")
    assert not json_string_field_complete('{"summary":"escaped \\" quote', "summary")
    assert json_string_field_complete('{"summary":"done","findings":[', "summary")


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
    assert answer.findings[0].artifact_ids == []


@pytest.mark.parametrize(
    ("artifact_ids", "expected"),
    [
        ([], []),
        (["artifact-a"], ["artifact-a"]),
        (["artifact-a", "artifact-b"], ["artifact-a", "artifact-b"]),
        ("artifact-a", []),
        (["artifact-a", 42, None], ["artifact-a"]),
    ],
)
def test_final_answer_normalizes_artifact_ids(artifact_ids, expected):
    answer = FinalAnswer.model_validate(
        normalize_final_answer_payload(
            {
                "summary": "完成",
                "findings": [{"text": "结论", "artifact_ids": artifact_ids}],
            }
        )
    )

    assert answer.findings[0].artifact_ids == expected


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


def test_reflection_normalization_accepts_common_model_shorthand():
    payload = normalize_reflection_payload(
        {
            "summary": "无需搜索即可回答。",
            "next_action": "finalize",
            "patch": {
                "fact_updates": [{"add": "可使用稳定医学常识回答。"}],
                "criterion_updates": [],
                "added_verification_requirements": ["medical_safety"],
                "terminal_intent": {"type": "inform"},
            },
        }
    )
    reflection = AgentReflection.model_validate(payload)
    assert reflection.patch.level == "local"
    assert reflection.patch.fact_updates[0].statement == "可使用稳定医学常识回答。"
    assert reflection.patch.added_verification_requirements[0].validator == "medical_safety"
    assert isinstance(reflection.patch.terminal_intent, str)


def test_memory_normalization_converts_scalar_provenance_and_drops_empty_content():
    normalized = normalize_memory_payload(
        {"content": "用户询问口腔溃疡", "provenance": "conversation", "confidence": 2}
    )
    memory = MemoryRecord.model_validate(normalized)
    assert memory.provenance == {"source": "conversation"}
    assert memory.confidence == 1
    assert normalize_memory_payload({"content": ""}) is None


async def test_real_model_operations_use_explicit_profile_composition():
    from app.runner.model_client import OpenAICompatibleModelClient

    client = OpenAICompatibleModelClient(
        Settings(model_provider="openai", model_api_key="secret", model_name="test")
    )
    captured = []
    payloads = {
        ModelOperation.CONTRACT: {},
        ModelOperation.PLAN: {"steps": []},
        ModelOperation.SYNTHESIS: {"summary": "完成"},
        ModelOperation.DECISION: {
            "decision_type": "finalize",
            "reasoning_summary": "可以回答",
        },
        ModelOperation.DECISION_WITH_ANSWER: {
            "decision_type": "finalize",
            "reasoning_summary": "可以回答",
            "final_answer": {"summary": "完成"},
        },
        ModelOperation.REFLECTION: {"summary": "继续", "next_action": "continue"},
        ModelOperation.MEMORY: {"memories": []},
    }

    async def fake_chat(messages, *, operation, **kwargs):
        captured.append((operation, messages, kwargs))
        return payloads[operation]

    client._chat_json = AsyncMock(side_effect=fake_chat)
    await client.contract("目标")
    await client.plan("目标")
    await client.synthesize("目标", [])
    await client.decide("目标", {"memory_reads": []})
    await client.decide_with_answer("目标", {"memory_reads": []})
    await client.reflect("目标", {"memory_reads": []})
    await client.extract_memory_candidates("目标", {"memory_reads": []})

    assert [item[0] for item in captured] == list(payloads)
    assert all("Trusted Agent Profile" in item[1][0]["content"] for item in captured)
    assert all("AUTODREAM.md" not in item[1][0]["content"] for item in captured)
    assert all("secret" not in item[1][0]["content"] for item in captured)
