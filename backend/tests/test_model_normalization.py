from unittest.mock import AsyncMock

import pytest

from app.application.agent_runtime.policies.reasoning import build_default_contract
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentReflection
from app.common.schemas.agent.planning import PlanDraft, TaskContract
from app.common.schemas.agent.run_result import AgentFinalAnswer, AgentRunMemoryCandidate
from app.domain.agent_profile import ModelOperation
from app.infrastructure.model_clients.factory import build_model_client
from app.infrastructure.model_clients.normalization import (
    StreamingJsonFieldExtractor,
    extract_partial_json_string,
    json_string_field_complete,
    normalize_contract_payload,
    normalize_final_answer_payload,
    normalize_memory_payload,
    normalize_plan_payload,
    normalize_reflection_payload,
    parse_json_object,
)
from app.infrastructure.model_clients.openai_compatible import OpenAICompatibleModelClient


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


def test_partial_json_stream_separates_reasoning_summary_from_final_answer():
    content = '{"decision_type":"finalize","reasoning_summary":"先检查已有信息。","final_answer":{"summary":"这是最终回答。"}}'

    assert extract_partial_json_string(content, "reasoning_summary") == "先检查已有信息。"
    assert extract_partial_json_string(content, "summary") == "这是最终回答。"
    assert json_string_field_complete(content, "reasoning_summary")
    assert json_string_field_complete(content, "summary")


def test_streaming_json_field_extractor_handles_chunked_keys_and_escapes():
    extractor = StreamingJsonFieldExtractor({"reasoning_summary", "summary"})
    content = (
        '{"decision_type":"finalize","reasoning_summary":"先\\n检查\\u4fe1息。",'
        '"final_answer":{"summary":"回答包含 \\"引号\\"。"}}'
    )
    events = []
    for index in range(0, len(content), 3):
        events.extend(extractor.feed(content[index : index + 3]))

    reasoning = "".join(value for field, value in events if field == "reasoning_summary" and value != "\1")
    summary = "".join(value for field, value in events if field == "summary" and value != "\1")
    assert reasoning == "先\n检查信息。"
    assert summary == '回答包含 "引号"。'
    assert events.count(("reasoning_summary", "\1")) == 1
    assert events.count(("summary", "\1")) == 1
    assert events.index(("reasoning_summary", "\1")) < events.index(("summary", "\1"))


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
    plan = PlanDraft.model_validate(
        normalize_plan_payload(
            {
                "nodes": [
                    {
                        "node_key": "answer",
                        "title": "生成回复",
                        "intent": "问候",
                        "success_criteria_refs": "criterion-1",
                    }
                ]
            },
            contract=contract,
        )
    )

    assert contract.assumptions[0].statement == "用户希望得到问候"
    assert contract.success_criteria[0].verification_method == "task_adapter"
    assert plan.nodes[0].intent == "问候"
    assert plan.nodes[0].success_criteria_refs == ["criterion-1"]
    assert plan.nodes[0].expected_outcome.kind == "step_result"


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
    answer = AgentFinalAnswer.model_validate(
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


@pytest.mark.parametrize("model_status", ["inferred", "self_known", "VERIFIED", "unknown"])
def test_final_answer_normalizes_non_contract_support_status(model_status):
    answer = AgentFinalAnswer.model_validate(
        normalize_final_answer_payload(
            {
                "summary": "我是 AI，没有人类意义上的年龄。",
                "claims": [
                    {
                        "id": "identity",
                        "text": "我是 AI",
                        "evidence_refs": [],
                        "material": True,
                        "support_status": model_status,
                    }
                ],
            }
        )
    )

    assert answer.claims[0].support_status == "unverified"


async def test_standard_combined_answer_accepts_non_contract_support_status_once():
    client = build_model_client(AstraRuntimeSettings(model_provider="openai", model_api_key="secret"))
    client._chat_json = AsyncMock(
        return_value={
            "reasoning_summary": "这是稳定的身份信息。",
            "summary": "我是 AI，没有人类意义上的年龄。",
            "claims": [
                {
                    "id": "identity",
                    "text": "我是 AI",
                    "evidence_refs": [],
                    "material": True,
                    "support_status": "self_known",
                }
            ],
            "decision_type": "finalize",
        }
    )

    decision, answer = await client.decide_with_answer(
        "你多大了",
        {"answer_mode": "standard", "memory_reads": []},
    )

    assert decision.decision_type == "finalize"
    assert answer is not None
    assert answer.claims[0].support_status == "unverified"
    client._chat_json.assert_awaited_once()


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
    answer = AgentFinalAnswer.model_validate(
        normalize_final_answer_payload(
            {
                "summary": "完成",
                "findings": [{"text": "结论", "artifact_ids": artifact_ids}],
            }
        )
    )

    assert answer.findings[0].artifact_ids == expected


def test_final_answer_normalization_drops_scalar_record_placeholders():
    answer = AgentFinalAnswer.model_validate(
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


def test_memory_normalization_is_bounded_typed_and_drops_empty_content():
    normalized = normalize_memory_payload(
        {
            "content": " 用户询问口腔溃疡 ",
            "scope": "unknown",
            "kind": "semantic_fact",
            "provenance": "conversation",
            "confidence": 2,
            "importance": -1,
        }
    )
    memory = AgentRunMemoryCandidate.model_validate(normalized)
    assert memory.content == "用户询问口腔溃疡"
    assert memory.scope == "run"
    assert memory.kind == "semantic_fact"
    assert memory.status == "candidate"
    assert memory.memory_key.startswith("memory:")
    assert memory.provenance == {}
    assert memory.confidence == 1
    assert memory.importance == 0
    assert memory.utility_score == 0
    assert normalize_memory_payload({"content": "x", "kind": "unknown"}) is None
    assert normalize_memory_payload({"content": ""}) is None


async def test_real_model_operations_use_explicit_profile_composition():

    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(model_provider="openai", model_api_key="secret", model_name="test")
    )
    captured = []
    payloads = {
        ModelOperation.CONTRACT: {},
        ModelOperation.PLAN: {
            "nodes": [
                {
                    "node_key": "answer",
                    "title": "回答",
                    "intent": "完成目标",
                    "depends_on": [],
                    "required_capabilities": [],
                    "success_criteria_refs": ["criterion-result"],
                    "expected_outcome": {
                        "kind": "final_answer",
                        "success_condition": "产生回答",
                    },
                }
            ]
        },
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
    await client.plan(
        "目标",
        contract=build_default_contract("目标"),
    )
    await client.synthesize("目标", [])
    await client.decide("目标", {"memory_reads": []})
    await client.decide_with_answer("目标", {"memory_reads": []})
    await client.reflect("目标", {"memory_reads": []})
    await client.extract_memory_candidates("目标", {"memory_reads": []})

    assert [item[0] for item in captured] == list(payloads)
    assert all("Trusted Agent Profile" in item[1][0]["content"] for item in captured)
    assert all("AUTODREAM.md" not in item[1][0]["content"] for item in captured)
    assert all("secret" not in item[1][0]["content"] for item in captured)


async def test_standard_combined_answer_prompt_streams_reasoning_before_summary():
    client = build_model_client(AstraRuntimeSettings(model_provider="openai", model_api_key="secret"))
    captured = []

    async def fake_chat(messages, *, operation, **kwargs):
        captured.append((messages, operation, kwargs))
        return {
            "reasoning_summary": "可以回答",
            "summary": "完成",
            "decision_type": "finalize",
        }

    client._chat_json = AsyncMock(side_effect=fake_chat)
    decision, answer = await client.decide_with_answer(
        "目标",
        {"answer_mode": "standard", "memory_reads": []},
    )

    system_prompt = captured[0][0][0]["content"]
    assert "Emit reasoning_summary as the very first key" in system_prompt
    assert "emit summary immediately after reasoning_summary" in system_prompt
    assert "Do not wrap these fields in final_answer" in system_prompt
    assert "without hidden chain-of-thought" in system_prompt
    assert decision.decision_type == "finalize"
    assert answer is not None and answer.summary == "完成"


async def test_trusted_combined_answer_accepts_flat_streamed_final_answer():
    client = build_model_client(AstraRuntimeSettings(model_provider="openai", model_api_key="secret"))
    client._chat_json = AsyncMock(
        return_value={
            "reasoning_summary": "直接完成创作任务。",
            "summary": "这是已经流式展示的完整故事。",
            "decision_type": "finalize",
        }
    )

    decision, answer = await client.decide_with_answer(
        "写一个故事",
        {"answer_mode": "trusted", "memory_reads": []},
    )

    assert decision.decision_type == "finalize"
    assert answer is not None
    assert answer.summary == "这是已经流式展示的完整故事。"
    client._chat_json.assert_awaited_once()
