import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_profile import AgentProfileConfigurationError, load_agent_profile
from app.conversation_context import ConversationContextManager, resolve_context_window
from app.core.config import Settings
from app.core.errors import ConfigurationError, ResourceError, ValidationError
from app.model_providers import API_KEY_OPTIONAL_MODEL_PROVIDERS, SUPPORTED_MODEL_PROVIDERS
from app.permissions.governance import verify_permission_bundle
from app.repositories.runs import RunRepository
from app.repositories.tool_settings import (
    ToolSettingsRepository,
    apply_tool_states,
    default_tool_states,
)
from app.runner.model_reasoning import normalize_model_thinking
from app.runner.reasoning import RunProfileResolver, compile_subagent_policy
from app.schemas.agent import CreateRunRequest, CreateRunResponse
from app.schemas.models import RunModelConfig
from app.schemas.permissions import PermissionBundle
from app.skills.catalog import SkillActivationService, SkillCatalogBuilder
from app.subagents.eligibility import subagent_execution_eligibility

logger = logging.getLogger("astra.run_creation")


class RunCreationService:
    """Create a governed Run for HTTP, schedules, commands, and future workers."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def create(
        self,
        payload: CreateRunRequest,
        *,
        commit: bool = True,
    ) -> tuple[CreateRunResponse, Settings]:
        goal = payload.goal.strip()
        if not goal:
            raise ValidationError("GOAL_REQUIRED", "请输入你想完成的目标。", {"field": "goal"})
        repo = RunRepository(self.session)
        logger.info(
            "run.create.start task_id=%s provider=%s model=%s goal_chars=%s",
            payload.task_id,
            payload.model.provider if payload.model else self.settings.model_provider,
            payload.model.name if payload.model else self.settings.model_name,
            len(goal),
        )
        try:
            tool_states = await ToolSettingsRepository(self.session).get_or_create(
                default_tool_states(self.settings)
            )
            run_settings = apply_tool_states(self.settings, tool_states)
            run_settings = self.apply_model_config(run_settings, payload.model)
            context_window = resolve_context_window(
                run_settings.model_provider,
                run_settings.model_name,
                fallback_tokens=run_settings.context_window_fallback_tokens,
            )
            if payload.task_id:
                context_manager = ConversationContextManager(self.session, run_settings)
                context_task = await context_manager.require_task(payload.task_id)
                await context_manager.prepare_for_run(
                    context_task,
                    provider=run_settings.model_provider,
                    model=run_settings.model_name,
                    draft=goal,
                )
            profile = RunProfileResolver().resolve(
                payload.answer_mode,
                payload.reasoning_policy,
                plan_execution=payload.plan_execution,
                subagent_policy=compile_subagent_policy(run_settings),
                subagent_mode=payload.subagent_mode,
            )
            if payload.subagent_mode == "required":
                eligibility = subagent_execution_eligibility(
                    profile.reasoning_policy.effective.subagents,
                    live_swarm_enabled=bool(run_settings.tool_swarm_enabled),
                )
                if not eligibility.executable:
                    raise ValidationError(
                        "SUBAGENT_COMMAND_UNAVAILABLE",
                        "当前策略不允许创建必需子 Agent 运行。",
                    )
            if not payload.interactive and payload.permission_bundle is None:
                raise ValidationError(
                    "PERMISSION_BUNDLE_REQUIRED",
                    "无人值守、定时或后台运行必须提供显式权限包。",
                )
            permission_bundle = self._validated_permission_bundle(payload.permission_bundle)
            policy = profile.reasoning_policy
            thinking = normalize_model_thinking(
                provider=run_settings.model_provider,
                model=run_settings.model_name,
                selection=payload.model.thinking if payload.model else None,
            )
            profile = profile.model_copy(
                update={
                    "interactive": payload.interactive,
                    "permission_bundle": (
                        permission_bundle.model_dump(mode="json")
                        if permission_bundle
                        else None
                    ),
                }
            )
            run = await repo.create_task_run(
                goal,
                {
                    **run_settings.model_policy,
                    "thinking": thinking.model_dump(mode="json"),
                    "context": {
                        "window_tokens": context_window.tokens,
                        "max_output_tokens": context_window.max_output_tokens,
                        "source": context_window.source,
                        "verified": context_window.verified,
                        "documentation_url": context_window.documentation_url,
                        "capability_version": 2,
                    },
                },
                payload.task_id,
                reasoning_policy=policy.model_dump(mode="json"),
                answer_mode=profile.answer_mode.value,
                execution_profile=profile.model_dump(mode="json"),
                agent_profile_snapshot=load_agent_profile().snapshot(),
                session_id=payload.session_id,
                commit=False,
            )
            await self._freeze_skills(run.id, goal, payload, profile.answer_mode.value, run_settings)
            for adjustment in policy.adjustments:
                await repo.add_event(
                    run.id, "reasoning.policy_adjusted", adjustment.model_dump(mode="json")
                )
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
        except AgentProfileConfigurationError as exc:
            raise ConfigurationError(
                "AGENT_PROFILE_INVALID", "Astra 身份配置无效，暂时无法创建任务。"
            ) from exc
        except ValueError as exc:
            if str(exc).startswith("Task not found"):
                raise ResourceError("TASK_NOT_FOUND", "找不到指定任务。") from exc
            raise ValidationError("RUN_REQUEST_INVALID", "无法创建任务。") from exc
        logger.info(
            "run.create.accepted run_id=%s task_id=%s status=%s",
            run.id,
            run.task_id,
            run.status,
        )
        return (
            CreateRunResponse(
                task_id=run.task_id,
                run_id=run.id,
                status=run.status,
                answer_mode=profile.answer_mode,
            ),
            run_settings,
        )

    def _validated_permission_bundle(self, raw: dict | None) -> PermissionBundle | None:
        if raw is None:
            return None
        try:
            bundle = PermissionBundle.model_validate(raw)
        except ValueError as exc:
            raise ValidationError("PERMISSION_BUNDLE_INVALID", "权限包格式无效。") from exc
        if not verify_permission_bundle(
            bundle, self.settings.permission_bundle_signing_secret
        ):
            raise ValidationError(
                "PERMISSION_BUNDLE_INVALID", "权限包签名无效或签名密钥未配置。"
            )
        return bundle

    async def _freeze_skills(
        self,
        run_id: str,
        goal: str,
        payload: CreateRunRequest,
        answer_mode: str,
        settings: Settings,
    ) -> None:
        if not settings.skills_enabled:
            return
        builder = SkillCatalogBuilder(
            self.session, metadata_chars=settings.skills_catalog_metadata_chars
        )
        catalog = await builder.build(
            goal=goal,
            explicit_identities=payload.skill_ids,
            runtime_capabilities=self._configured_skill_capabilities(settings),
        )
        await builder.freeze(run_id, answer_mode, catalog, new_run=True)
        activator = SkillActivationService(
            self.session,
            max_active=settings.skills_max_active,
            max_resource_bytes=settings.skills_max_resource_bytes_per_run,
        )
        try:
            for identity in payload.skill_ids:
                catalog.require(identity)
        except ValueError as exc:
            raise ValidationError(
                "SKILL_SELECTION_INVALID",
                f"无法激活 Skill：{identity}",
                {"qualified_identity": identity, "reason": str(exc)},
            ) from exc
        for identity in payload.skill_ids:
            try:
                await activator.activate(
                    run_id,
                    identity,
                    initiator="explicit",
                    reason="explicit run selection",
                )
            except ValueError as exc:
                raise ValidationError(
                    "SKILL_SELECTION_INVALID",
                    f"无法激活 Skill：{identity}",
                    {"qualified_identity": identity, "reason": str(exc)},
                ) from exc

    @staticmethod
    def apply_model_config(settings: Settings, model: RunModelConfig | dict | None) -> Settings:
        if not model:
            return settings
        if not isinstance(model, RunModelConfig):
            model = RunModelConfig.model_validate(model)
        if model.provider not in SUPPORTED_MODEL_PROVIDERS:
            raise ValidationError(
                "MODEL_PROVIDER_UNSUPPORTED", "当前模型供应商尚未接入通用运行时。"
            )
        configured = settings.model_copy(
            update={
                "model_provider": model.provider,
                "model_name": model.name,
                "model_api_key": model.api_key,
                "model_base_url": model.base_url,
            }
        )
        if (
            not configured.model_name
            or not configured.model_base_url
            or (
                model.provider not in API_KEY_OPTIONAL_MODEL_PROVIDERS
                and not configured.model_api_key
            )
        ):
            raise ValidationError(
                "MODEL_CONFIGURATION_REQUIRED", "请先配置模型名称、API 地址和 API Key。"
            )
        return configured

    @staticmethod
    def _configured_skill_capabilities(settings: Settings) -> set[str]:
        return {
            name
            for name, enabled in {
                "web_search": settings.tool_web_search_enabled,
                "web_fetch": settings.tool_web_fetch_enabled,
                "chart_render": settings.tool_chart_render_enabled,
                "bash_execute": settings.tool_bash_execute_enabled,
                "sandbox": settings.sandbox_enabled,
            }.items()
            if enabled
        }
