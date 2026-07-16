from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import ToolSettingRecord, utc_now

TOOL_SETTING_FIELDS = {
    "web_search": "tool_web_search_enabled",
    "web_fetch": "tool_web_fetch_enabled",
    "chart_render": "tool_chart_render_enabled",
    "bash_execute": "tool_bash_execute_enabled",
}


def default_tool_states(settings: Settings) -> dict[str, bool]:
    return {
        name: bool(getattr(settings, field))
        for name, field in TOOL_SETTING_FIELDS.items()
    }


def apply_tool_states(settings: Settings, states: dict[str, bool]) -> Settings:
    result = settings.model_copy(deep=True)
    for name, field in TOOL_SETTING_FIELDS.items():
        if name in states:
            setattr(result, field, states[name])
    return result


class ToolSettingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, defaults: dict[str, bool]) -> dict[str, bool]:
        records = list((await self.session.scalars(select(ToolSettingRecord))).all())
        by_name = {record.tool_name: record for record in records}
        for name, enabled in defaults.items():
            if name not in by_name:
                record = ToolSettingRecord(tool_name=name, enabled=enabled)
                self.session.add(record)
                by_name[name] = record
        await self.session.flush()
        return {name: bool(by_name[name].enabled) for name in defaults}

    async def set_all(self, states: dict[str, bool], defaults: dict[str, bool]) -> dict[str, bool]:
        await self.get_or_create(defaults)
        records = list(
            (await self.session.scalars(
                select(ToolSettingRecord).where(ToolSettingRecord.tool_name.in_(states))
            )).all()
        )
        now = utc_now()
        for record in records:
            record.enabled = states[record.tool_name]
            record.updated_at = now
        await self.session.flush()
        return {record.tool_name: bool(record.enabled) for record in records}
