"""Resolve frozen model and tool settings for new or resumed Runs."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.contracts.json_values import JsonObject
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import AstraInputValidationError
from app.common.schemas.model_providers import RunModelConfig
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.providers import (
    API_KEY_OPTIONAL_MODEL_PROVIDERS,
    SUPPORTED_MODEL_PROVIDERS,
)
from app.infrastructure.repositories.tool_settings import (
    ToolProviderSettingsRepository,
    ToolSettingsRepository,
    apply_provider_states,
    apply_tool_states,
    default_tool_states,
)


@dataclass
class RunSettingsResolver:
    _session: AsyncSession
    _base_settings: AstraRuntimeSettings

    async def for_new_run(self, model: RunModelConfig | None) -> AstraRuntimeSettings:
        tool_states = await ToolSettingsRepository(self._session).get_or_create(default_tool_states(self._base_settings))
        provider_defaults = dict.fromkeys(
            self._base_settings.trusted_tool_provider_map,
            True,
        )
        provider_states = await ToolProviderSettingsRepository(self._session).get_or_create(provider_defaults)
        return self.apply_model_config(
            apply_provider_states(
                apply_tool_states(self._base_settings, tool_states),
                provider_states,
            ),
            model,
        )

    async def for_existing_run(
        self,
        run: RunRecord,
        model: RunModelConfig | None,
    ) -> AstraRuntimeSettings:
        run_settings = await self.for_new_run(model)
        return self._restore_frozen_model(run_settings, run.model_policy)

    @staticmethod
    def apply_model_config(
        settings: AstraRuntimeSettings,
        model: RunModelConfig | dict | None,
    ) -> AstraRuntimeSettings:
        if not model:
            return settings
        model_config = model if isinstance(model, RunModelConfig) else RunModelConfig.model_validate(model)
        if model_config.provider not in SUPPORTED_MODEL_PROVIDERS:
            raise AstraInputValidationError(
                "MODEL_PROVIDER_UNSUPPORTED",
                "当前模型供应商尚未接入通用运行时。",
            )
        configured_settings = settings.model_copy(
            update={
                "model_provider": model_config.provider,
                "model_name": model_config.name,
                "model_api_key": model_config.api_key,
                "model_base_url": model_config.base_url,
            }
        )
        missing_api_key = (
            model_config.provider not in API_KEY_OPTIONAL_MODEL_PROVIDERS and not configured_settings.model_api_key
        )
        if not configured_settings.model_name or not configured_settings.model_base_url or missing_api_key:
            raise AstraInputValidationError(
                "MODEL_CONFIGURATION_REQUIRED",
                "请先配置模型名称、API 地址和 API Key。",
            )
        return configured_settings

    @staticmethod
    def _restore_frozen_model(settings: AstraRuntimeSettings, model_policy: JsonObject) -> AstraRuntimeSettings:
        provider = str(model_policy.get("provider") or "")
        model_name = str(model_policy.get("model") or "")
        base_url = str(model_policy.get("base_url") or "")
        if not provider or not model_name:
            return settings
        if settings.model_provider != provider or settings.model_name != model_name:
            raise AstraInputValidationError(
                "RUN_MODEL_MISMATCH",
                "继续运行时必须使用该任务开始时选择的模型。",
                {"provider": provider, "model": model_name},
            )
        return settings.model_copy(
            update={
                "model_provider": provider,
                "model_name": model_name,
                "model_base_url": base_url or settings.model_base_url,
            }
        )
