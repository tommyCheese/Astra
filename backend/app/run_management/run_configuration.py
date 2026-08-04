"""Normalize immutable configuration before a Run is persisted."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from app.agent_profile import AgentProfile, load_agent_profile
from app.contracts.json_values import JsonObject
from app.runner.model_reasoning import normalize_model_thinking
from app.runner.reasoning import RunProfileResolver
from app.schemas.agent.run_policy import (
    ReasoningPolicySnapshot,
    RequestedReasoningPolicy,
    RunExecutionProfile,
)
from app.schemas.agent.types import AnswerMode, PlanExecution


@dataclass(frozen=True)
class PreparedRunConfiguration:
    answer_mode: str
    execution_profile: JsonObject
    reasoning_policy: JsonObject
    model_policy: JsonObject
    agent_profile_snapshot: JsonObject


def prepare_run_configuration(
    *,
    model_policy: JsonObject,
    reasoning_policy: JsonObject | None,
    answer_mode: str,
    execution_profile: JsonObject | None,
    agent_profile_snapshot: JsonObject | None,
) -> PreparedRunConfiguration:
    profile = _execution_profile(reasoning_policy, answer_mode, execution_profile)
    frozen_reasoning = (
        ReasoningPolicySnapshot.model_validate(reasoning_policy)
        if reasoning_policy is not None
        else profile.reasoning_policy
    )
    snapshot = agent_profile_snapshot or load_agent_profile().snapshot()
    AgentProfile.from_snapshot(snapshot)
    return PreparedRunConfiguration(
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
        reasoning_policy=frozen_reasoning.model_dump(mode="json"),
        model_policy=_model_policy(model_policy),
        agent_profile_snapshot=deepcopy(snapshot),
    )


def _execution_profile(
    reasoning_policy: JsonObject | None,
    answer_mode: str,
    execution_profile: JsonObject | None,
) -> RunExecutionProfile:
    if execution_profile is not None:
        return RunExecutionProfile.model_validate(execution_profile)
    resolved_mode = AnswerMode.trusted if reasoning_policy is not None else AnswerMode(answer_mode)
    profile = RunProfileResolver().resolve(
        resolved_mode,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto if resolved_mode == AnswerMode.trusted else None,
    )
    if reasoning_policy is None:
        return profile
    return profile.model_copy(
        update={"reasoning_policy": ReasoningPolicySnapshot.model_validate(reasoning_policy)}
    )


def _model_policy(model_policy: JsonObject) -> JsonObject:
    if isinstance(model_policy.get("thinking"), dict):
        return deepcopy(model_policy)
    thinking = normalize_model_thinking(
        provider=str(model_policy.get("provider") or "mock"),
        model=str(model_policy.get("model") or "mock"),
        selection=None,
    )
    return {**model_policy, "thinking": thinking.model_dump(mode="json")}
