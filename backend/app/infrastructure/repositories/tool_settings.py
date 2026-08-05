from weakref import WeakKeyDictionary

from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.common.core.config import Settings
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import ToolSettingRecord

TOOL_SETTING_FIELDS = {
    "web_search": "tool_web_search_enabled",
    "web_fetch": "tool_web_fetch_enabled",
    "chart_render": "tool_chart_render_enabled",
    "bash_execute": "tool_bash_execute_enabled",
    "swarm": "tool_swarm_enabled",
}
PENDING_TOOL_SETTINGS_CACHE = "astra_pending_tool_settings_cache"
_TOOL_SETTINGS_CACHE: WeakKeyDictionary[Engine, dict[str, bool]] = WeakKeyDictionary()


def _session_engine(session: Session) -> Engine:
    bind = session.get_bind()
    return bind.engine if hasattr(bind, "engine") else bind


@event.listens_for(Session, "after_commit")
def publish_tool_settings_cache(session: Session) -> None:
    pending = session.info.pop(PENDING_TOOL_SETTINGS_CACHE, None)
    if pending is not None:
        engine, states = pending
        _TOOL_SETTINGS_CACHE[engine] = states


@event.listens_for(Session, "after_rollback")
def discard_tool_settings_cache(session: Session) -> None:
    session.info.pop(PENDING_TOOL_SETTINGS_CACHE, None)


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
        pending = self.session.sync_session.info.get(PENDING_TOOL_SETTINGS_CACHE)
        cached = (
            pending[1]
            if pending is not None
            else _TOOL_SETTINGS_CACHE.get(_session_engine(self.session.sync_session))
        )
        if cached is not None and defaults.keys() <= cached.keys():
            return {name: cached[name] for name in defaults}
        records = list((await self.session.scalars(select(ToolSettingRecord))).all())
        by_name = {record.tool_name: record for record in records}
        for name, enabled in defaults.items():
            if name not in by_name:
                record = ToolSettingRecord(tool_name=name, enabled=enabled)
                self.session.add(record)
                by_name[name] = record
        await self.session.flush()
        states = {name: bool(by_name[name].enabled) for name in defaults}
        self._stage_cache(states)
        return states

    async def set_all(self, states: dict[str, bool], defaults: dict[str, bool]) -> dict[str, bool]:
        current = await self.get_or_create(defaults)
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
        updated = {record.tool_name: bool(record.enabled) for record in records}
        self._stage_cache({**current, **updated})
        return updated

    def _stage_cache(self, states: dict[str, bool]) -> None:
        session = self.session.sync_session
        session.info[PENDING_TOOL_SETTINGS_CACHE] = (
            _session_engine(session),
            dict(states),
        )
