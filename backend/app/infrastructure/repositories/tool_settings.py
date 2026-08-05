from weakref import WeakKeyDictionary

from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import (
    ToolProviderSettingRecord,
    ToolSettingRecord,
    ToolSettingsAuditRecord,
)

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


def apply_provider_states(
    settings: AstraRuntimeSettings,
    records: dict[str, ToolProviderSettingRecord],
) -> AstraRuntimeSettings:
    states = {provider_id: bool(record.enabled) for provider_id, record in records.items()}
    configurations = {
        provider_id: dict(record.configuration or {})
        for provider_id, record in records.items()
    }
    configuration_revisions = {
        provider_id: str(record.configuration_revision)
        for provider_id, record in records.items()
    }
    result = settings.model_copy(
        update={
            "tool_provider_states": states,
            "tool_provider_configurations": configurations,
            "tool_provider_configuration_revisions": configuration_revisions,
        },
        deep=True,
    )
    if not states.get("astra.builtin", True):
        result.tool_states = {**result.tool_states, "swarm": False}
    return result


def default_tool_states(settings: AstraRuntimeSettings) -> dict[str, bool]:
    from app.infrastructure.tools.registry import build_plugin_inventory
    from app.infrastructure.tools.runtime import build_runtime_tool_registry

    specs = {
        **{name: tool.spec for name, tool in build_plugin_inventory(settings).tools.items()},
        **build_runtime_tool_registry().specs(),
    }
    return {name: spec.enabled_by_default for name, spec in specs.items()}


def apply_tool_states(
    settings: AstraRuntimeSettings,
    states: dict[str, bool],
) -> AstraRuntimeSettings:
    return settings.model_copy(update={"tool_states": dict(states)}, deep=True)


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

    async def set_tool(self, tool_name: str, enabled: bool, *, default: bool) -> bool:
        key = tool_name
        before = await self.get_or_create({key: default})
        await self.set_all({key: enabled}, {key: default})
        self.session.add(
            ToolSettingsAuditRecord(
                target_kind="tool",
                target_id=tool_name,
                action="set_enabled",
                before={"enabled": before[key]},
                after={"enabled": enabled},
            )
        )
        await self.session.flush()
        return enabled

    def _stage_cache(self, states: dict[str, bool]) -> None:
        session = self.session.sync_session
        session.info[PENDING_TOOL_SETTINGS_CACHE] = (
            _session_engine(session),
            dict(states),
        )


class ToolProviderSettingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(
        self,
        defaults: dict[str, bool],
    ) -> dict[str, ToolProviderSettingRecord]:
        records = list(
            (
                await self.session.scalars(
                    select(ToolProviderSettingRecord).where(
                        ToolProviderSettingRecord.provider_id.in_(defaults)
                    )
                )
            ).all()
        )
        by_id = {record.provider_id: record for record in records}
        for provider_id, enabled in defaults.items():
            if provider_id not in by_id:
                record = ToolProviderSettingRecord(provider_id=provider_id, enabled=enabled)
                self.session.add(record)
                by_id[provider_id] = record
        await self.session.flush()
        return by_id

    async def set_enabled(self, provider_id: str, enabled: bool) -> ToolProviderSettingRecord:
        record = (await self.get_or_create({provider_id: True}))[provider_id]
        before = bool(record.enabled)
        record.enabled = enabled
        record.updated_at = utc_now()
        self.session.add(
            ToolSettingsAuditRecord(
                target_kind="provider",
                target_id=provider_id,
                action="set_enabled",
                before={"enabled": before},
                after={"enabled": enabled},
            )
        )
        await self.session.flush()
        return record

    async def set_configuration(
        self,
        provider_id: str,
        configuration: dict,
    ) -> ToolProviderSettingRecord:
        record = (await self.get_or_create({provider_id: True}))[provider_id]
        before = {
            "configuration": dict(record.configuration or {}),
            "configuration_revision": record.configuration_revision,
        }
        record.configuration = dict(configuration)
        record.configuration_revision += 1
        record.updated_at = utc_now()
        self.session.add(
            ToolSettingsAuditRecord(
                target_kind="provider",
                target_id=provider_id,
                action="set_configuration",
                before=before,
                after={
                    "configuration": dict(configuration),
                    "configuration_revision": record.configuration_revision,
                },
            )
        )
        await self.session.flush()
        return record
