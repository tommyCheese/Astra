from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationStrategyPreferenceRecord, utc_now

DEFAULT_CONVERSATION_STRATEGY = {
    "reasoning_effort": "balanced",
    "planning_strategy": "adaptive",
    "reflection_enabled": True,
    "reflection_trigger": "adaptive",
}


class ConversationStrategyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self) -> dict[str, str | bool]:
        record = await self.session.get(ConversationStrategyPreferenceRecord, "default")
        if record is None:
            record = ConversationStrategyPreferenceRecord(
                id="default", **DEFAULT_CONVERSATION_STRATEGY
            )
            self.session.add(record)
            await self.session.flush()
        return self._serialize(record)

    async def set(self, strategy: dict[str, str | bool]) -> dict[str, str | bool]:
        await self.get_or_create()
        record = await self.session.get(ConversationStrategyPreferenceRecord, "default")
        assert record is not None
        record.reasoning_effort = str(strategy["reasoning_effort"])
        record.planning_strategy = str(strategy["planning_strategy"])
        record.reflection_enabled = bool(strategy["reflection_enabled"])
        record.reflection_trigger = str(strategy["reflection_trigger"])
        record.updated_at = utc_now()
        await self.session.flush()
        return self._serialize(record)

    @staticmethod
    def _serialize(record: ConversationStrategyPreferenceRecord) -> dict[str, str | bool]:
        return {
            "reasoning_effort": record.reasoning_effort,
            "planning_strategy": record.planning_strategy,
            "reflection_enabled": record.reflection_enabled,
            "reflection_trigger": record.reflection_trigger,
        }
