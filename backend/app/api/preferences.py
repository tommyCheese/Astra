from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.conversation_strategy import ConversationStrategyRepository
from app.schemas.agent import PlanningStrategy, ReasoningEffort, ReflectionTrigger

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class ConversationStrategyPreferences(BaseModel):
    reasoning_effort: ReasoningEffort = ReasoningEffort.balanced
    planning_strategy: PlanningStrategy = PlanningStrategy.adaptive
    reflection_enabled: bool = True
    reflection_trigger: ReflectionTrigger = ReflectionTrigger.adaptive


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
