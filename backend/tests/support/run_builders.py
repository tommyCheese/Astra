"""Builders that keep Run request setup explicit without dictionary fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.common.schemas.agent.api_views import CreateRunRequest
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.types import AnswerMode
from app.common.schemas.models import RunModelConfig


@dataclass(frozen=True)
class RunRequestBuilder:
    goal: str = "完成测试目标"
    conversation_id: str | None = None
    answer_mode: AnswerMode = AnswerMode.standard
    interactive: bool = True
    model: RunModelConfig | None = None
    reasoning_policy: RequestedReasoningPolicy = field(default_factory=RequestedReasoningPolicy)

    def with_values(self, **changes: Any) -> RunRequestBuilder:
        return replace(self, **changes)

    def build(self) -> CreateRunRequest:
        return CreateRunRequest(
            goal=self.goal,
            task_id=self.conversation_id,
            answer_mode=self.answer_mode,
            interactive=self.interactive,
            model=self.model,
            reasoning_policy=self.reasoning_policy,
        )
