from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationStrategyPreferenceRecord, utc_now

DEFAULT_CONVERSATION_STRATEGY = {
    "preferred_answer_mode": "standard",
    "reasoning_effort": "balanced",
    "max_tool_calls": 8,
    "reflection_enabled": True,
    "reflection_trigger": "adaptive",
}


class ConversationStrategyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self) -> dict[str, str | bool | int | None]:
        record = await self.session.get(ConversationStrategyPreferenceRecord, "default")
        if record is None:
            record = ConversationStrategyPreferenceRecord(
                id="default", **DEFAULT_CONVERSATION_STRATEGY
            )
            self.session.add(record)
            await self.session.flush()
        return self._serialize(record)

    async def set(self, strategy: dict[str, str | bool | int | None]) -> dict[str, str | bool | int | None]:
        await self.get_or_create()
        record = await self.session.get(ConversationStrategyPreferenceRecord, "default")
        assert record is not None
        record.preferred_answer_mode = str(strategy["preferred_answer_mode"])
        record.reasoning_effort = str(strategy["reasoning_effort"])
        max_tool_calls = strategy["max_tool_calls"]
        record.max_tool_calls = int(max_tool_calls) if max_tool_calls is not None else None
        record.reflection_enabled = bool(strategy["reflection_enabled"])
        record.reflection_trigger = str(strategy["reflection_trigger"])
        record.updated_at = utc_now()
        await self.session.flush()
        return self._serialize(record)

    @staticmethod
    def _serialize(record: ConversationStrategyPreferenceRecord) -> dict[str, str | bool | int | None]:
        return {
            "preferred_answer_mode": record.preferred_answer_mode,
            "reasoning_effort": record.reasoning_effort,
            "max_tool_calls": record.max_tool_calls,
            "reflection_enabled": record.reflection_enabled,
            "reflection_trigger": record.reflection_trigger,
        }
