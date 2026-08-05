from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.core.config import AstraRuntimeSettings, get_settings
from app.common.schemas.agent.run_policy import EXECUTABLE_SUBAGENT_COHORTS
from app.infrastructure.db.models.conversations import ToolProviderSettingRecord
from app.infrastructure.db.session import get_session
from app.infrastructure.plugins.contracts import PluginLifecycleState
from app.infrastructure.repositories.tool_settings import (
    ToolProviderSettingsRepository,
    ToolSettingsRepository,
    default_tool_states,
    persisted_tool_name,
)
from app.infrastructure.tools.base import AstraToolSpec, validate_json_schema
from app.infrastructure.tools.registry import build_plugin_inventory, sandbox_available
from app.infrastructure.tools.runtime import build_runtime_tool_registry

router = APIRouter(prefix="/api/tools", tags=["tools"])
provider_router = APIRouter(prefix="/api/tool-providers", tags=["tool-providers"])


class ToolToggle(BaseModel):
    name: str
    provider_id: str
    version: str
    label: str
    description: str
    enabled: bool
    available: bool
    health: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    unavailable_reason: str | None = None


class ToolProviderView(BaseModel):
    provider_id: str
    label: str
    version: str
    enabled: bool
    state: str
    health: str
    available: bool
    unavailable_reason: str | None = None
    configuration_schema: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)
    configuration_revision: str


class ToolSettingsResponse(BaseModel):
    tools: list[ToolToggle]
    providers: list[ToolProviderView]


class ToolSettingsUpdate(BaseModel):
    """One-version adapter for the former fixed-field settings payload."""

    web_search: bool | None = None
    web_fetch: bool | None = None
    chart_render: bool | None = None
    bash_execute: bool | None = None
    swarm: bool | None = None


class EnabledUpdate(BaseModel):
    enabled: bool


class ProviderConfigurationUpdate(BaseModel):
    configuration: dict[str, Any]


def _label(identifier: str) -> str:
    return " ".join(part.capitalize() for part in identifier.replace(".", "_").split("_"))


def _provider_defaults(settings: AstraRuntimeSettings) -> dict[str, bool]:
    return {provider_id: True for provider_id in settings.trusted_tool_provider_map}


def _tool_defaults(settings: AstraRuntimeSettings, specs: dict[str, AstraToolSpec]) -> dict[str, bool]:
    legacy = default_tool_states(settings)
    return {
        persisted_tool_name(name): legacy.get(persisted_tool_name(name), True)
        for name in specs
    }


def _safe_configuration(configuration: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    safe: dict[str, Any] = {}
    for key, value in configuration.items():
        field_schema = properties.get(key, {}) if isinstance(properties, dict) else {}
        if isinstance(field_schema, dict) and field_schema.get("x-secret") is True:
            safe[key] = {"configured": bool(value)}
        else:
            safe[key] = value
    return safe


def _validate_secret_references(configuration: dict[str, Any], schema: dict[str, Any]) -> None:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    for key, field_schema in properties.items():
        if key not in configuration or not isinstance(field_schema, dict):
            continue
        if field_schema.get("x-secret") is not True:
            continue
        value = configuration[key]
        if not (
            isinstance(value, dict)
            and set(value) == {"credential_ref"}
            and isinstance(value["credential_ref"], str)
            and value["credential_ref"].strip()
        ):
            raise HTTPException(
                status_code=422,
                detail=f"{key} must contain only a credential_ref",
            )


def _retain_omitted_secrets(
    submitted: dict[str, Any],
    stored: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(submitted)
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    for key, field_schema in properties.items():
        if (
            key not in merged
            and key in stored
            and isinstance(field_schema, dict)
            and field_schema.get("x-secret") is True
        ):
            merged[key] = stored[key]
    return merged


async def _catalog_view(
    settings: AstraRuntimeSettings,
    session: AsyncSession,
) -> ToolSettingsResponse:
    catalog = build_plugin_inventory(settings)
    runtime_specs = build_runtime_tool_registry().specs()
    plugin_specs = {name: tool.spec for name, tool in catalog.tools.items()}
    specs = {**plugin_specs, **runtime_specs}
    provider_repository = ToolProviderSettingsRepository(session)
    provider_records = await provider_repository.get_or_create(_provider_defaults(settings))
    tool_states = await ToolSettingsRepository(session).get_or_create(
        _tool_defaults(settings, specs)
    )
    sandbox_ready = settings.sandbox_enabled and sandbox_available(settings)

    providers: list[ToolProviderView] = []
    for provider_id in sorted(provider_records):
        record = provider_records[provider_id]
        status = catalog.providers.get(provider_id)
        descriptor = status.descriptor if status else None
        requires_sandbox = any(
            spec.provider_id == provider_id and spec.execution_backend == "sandbox.remote"
            for spec in specs.values()
        )
        available = not requires_sandbox or sandbox_ready
        enabled = bool(record.enabled)
        state = (
            PluginLifecycleState.disabled.value
            if not enabled
            else PluginLifecycleState.unhealthy.value
            if not available
            else (status.state.value if status else PluginLifecycleState.enabled.value)
        )
        reason = None if available else "安全运行环境当前不可用。"
        schema = descriptor.configuration_schema if descriptor else {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        providers.append(
            ToolProviderView(
                provider_id=provider_id,
                label=_label(provider_id.removeprefix("astra.")),
                version=descriptor.version if descriptor else "1",
                enabled=enabled,
                state=state,
                health="healthy" if available else "unhealthy",
                available=available,
                unavailable_reason=reason,
                configuration_schema=schema,
                configuration=_safe_configuration(dict(record.configuration or {}), schema),
                configuration_revision=str(record.configuration_revision),
            )
        )

    provider_views = {provider.provider_id: provider for provider in providers}
    tools: list[ToolToggle] = []
    for name, spec in sorted(specs.items()):
        provider = provider_views[spec.provider_id]
        available = provider.available
        reason = provider.unavailable_reason
        if spec.execution_backend == "astra.runtime":
            available = bool(
                not settings.agent_subagent_kill_switch
                and settings.agent_subagent_rollout_cohort in EXECUTABLE_SUBAGENT_COHORTS
            )
            if settings.agent_subagent_kill_switch:
                reason = "子 Agent 已被紧急停止开关禁用。"
            elif not available:
                reason = "当前发布批次不允许执行子 Agent。"
            else:
                reason = None
        tools.append(
            ToolToggle(
                name=name,
                provider_id=spec.provider_id,
                version=spec.version,
                label=_label(name),
                description=spec.description,
                enabled=bool(tool_states[persisted_tool_name(name)]),
                available=available,
                health="healthy" if available else "unavailable",
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
                unavailable_reason=reason,
            )
        )
    return ToolSettingsResponse(tools=tools, providers=providers)


@router.get("", response_model=ToolSettingsResponse)
async def get_tool_settings(
    settings: AstraRuntimeSettings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ToolSettingsResponse:
    result = await _catalog_view(settings, session)
    await session.commit()
    return result


@provider_router.get("", response_model=list[ToolProviderView])
async def get_tool_providers(
    settings: AstraRuntimeSettings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[ToolProviderView]:
    result = await _catalog_view(settings, session)
    await session.commit()
    return result.providers


@router.put("/{tool_name}/state", response_model=ToolSettingsResponse)
async def update_tool_state(
    tool_name: str,
    update: EnabledUpdate,
    settings: AstraRuntimeSettings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ToolSettingsResponse:
    current = await _catalog_view(settings, session)
    known = {tool.name for tool in current.tools}
    if tool_name not in known:
        raise HTTPException(status_code=404, detail="Unknown tool")
    defaults = _tool_defaults(
        settings,
        {
            **{name: tool.spec for name, tool in build_plugin_inventory(settings).tools.items()},
            **build_runtime_tool_registry().specs(),
        },
    )
    await ToolSettingsRepository(session).set_tool(
        persisted_tool_name(tool_name),
        update.enabled,
        default=defaults[persisted_tool_name(tool_name)],
    )
    result = await _catalog_view(settings, session)
    await session.commit()
    return result


@provider_router.put("/{provider_id}/state", response_model=ToolSettingsResponse)
async def update_provider_state(
    provider_id: str,
    update: EnabledUpdate,
    settings: AstraRuntimeSettings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ToolSettingsResponse:
    current = await _catalog_view(settings, session)
    known = {provider.provider_id for provider in current.providers}
    if provider_id not in known:
        raise HTTPException(status_code=404, detail="Unknown tool provider")
    await ToolProviderSettingsRepository(session).set_enabled(provider_id, update.enabled)
    result = await _catalog_view(settings, session)
    await session.commit()
    return result


@provider_router.put("/{provider_id}/config", response_model=ToolSettingsResponse)
async def update_provider_configuration(
    provider_id: str,
    update: ProviderConfigurationUpdate,
    settings: AstraRuntimeSettings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ToolSettingsResponse:
    current = await _catalog_view(settings, session)
    provider = next(
        (item for item in current.providers if item.provider_id == provider_id),
        None,
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown tool provider")
    records = await ToolProviderSettingsRepository(session).get_or_create({provider_id: True})
    configuration = _retain_omitted_secrets(
        update.configuration,
        dict(records[provider_id].configuration or {}),
        provider.configuration_schema,
    )
    try:
        validate_json_schema(configuration, provider.configuration_schema, path="configuration")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_secret_references(configuration, provider.configuration_schema)
    await ToolProviderSettingsRepository(session).set_configuration(
        provider_id,
        configuration,
    )
    result = await _catalog_view(settings, session)
    await session.commit()
    return result


@router.put("", response_model=ToolSettingsResponse, deprecated=True)
async def update_tool_settings(
    update: ToolSettingsUpdate,
    response: Response,
    settings: AstraRuntimeSettings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ToolSettingsResponse:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Thu, 06 Aug 2027 00:00:00 GMT"
    response.headers["Link"] = '</api/tools/{tool_name}/state>; rel="successor-version"'
    repository = ToolSettingsRepository(session)
    await repository.set_all(
        update.model_dump(exclude_none=True), default_tool_states(settings)
    )
    result = await _catalog_view(settings, session)
    await session.commit()
    return result
