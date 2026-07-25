from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.conversation_strategy import ConversationStrategyRepository
from app.schemas.agent import (
    TOOL_CALL_LIMIT_DEFAULTS,
    AnswerMode,
    ReasoningEffort,
    ReflectionTrigger,
    validate_tool_call_limit,
)

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class ConversationStrategyPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_answer_mode: AnswerMode = AnswerMode.standard
    reasoning_effort: ReasoningEffort = ReasoningEffort.balanced
    max_tool_calls: int | None = 8
    reflection_enabled: bool = True
    reflection_trigger: ReflectionTrigger = ReflectionTrigger.adaptive

    @model_validator(mode="after")
    def validate_tool_budget(self) -> "ConversationStrategyPreferences":
        if "max_tool_calls" not in self.model_fields_set:
            self.max_tool_calls = TOOL_CALL_LIMIT_DEFAULTS[self.reasoning_effort]
        if self.reasoning_effort == ReasoningEffort.deep:
            if self.max_tool_calls is not None:
                validate_tool_call_limit(self.reasoning_effort, self.max_tool_calls)
        elif self.max_tool_calls is None:
            raise ValueError("max_tool_calls is required for non-deep reasoning")
        else:
            validate_tool_call_limit(self.reasoning_effort, self.max_tool_calls)
        return self


@router.get(
    "/conversation-strategy", response_model=ConversationStrategyPreferences
)
async def get_conversation_strategy(
    session: AsyncSession = Depends(get_session),
) -> ConversationStrategyPreferences:
    strategy = await ConversationStrategyRepository(session).get_or_create()
    await session.commit()
    return ConversationStrategyPreferences.model_validate(strategy)


@router.put(
    "/conversation-strategy", response_model=ConversationStrategyPreferences
)
async def update_conversation_strategy(
    update: ConversationStrategyPreferences,
    session: AsyncSession = Depends(get_session),
) -> ConversationStrategyPreferences:
    strategy = await ConversationStrategyRepository(session).set(
        update.model_dump(mode="json")
    )
    await session.commit()
    return ConversationStrategyPreferences.model_validate(strategy)
