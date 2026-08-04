"""Create and freeze a governed Run before background execution begins."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_profile import AgentProfileConfigurationError, load_agent_profile
from app.context_windows import resolve_context_window
from app.conversation_context import ConversationContextManager
from app.core.config import Settings
from app.core.errors import ConfigurationError, ResourceError, ValidationError
from app.db.models.runs import RunRecord
from app.permissions.governance import verify_permission_bundle
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.run_management.contracts import PreparedRun
from app.run_management.settings import RunSettingsResolver
from app.runner.model_reasoning import normalize_model_thinking
from app.runner.reasoning import RunProfileResolver, compile_subagent_policy
from app.schemas.agent.api_views import CreateRunRequest, CreateRunResponse
from app.schemas.agent.run_policy import RunExecutionProfile
from app.schemas.permissions import PermissionBundle
from app.skills.activation import SkillActivationService
from app.skills.catalog import SkillCatalogBuilder
from app.subagents.eligibility import subagent_execution_eligibility

logger = logging.getLogger("astra.run_creation")


class RunCreator:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        settings_resolver: RunSettingsResolver,
    ) -> None:
        self._session = session
        self._settings = settings
        self._settings_resolver = settings_resolver

    async def prepare(
        self,
        request: CreateRunRequest,
        *,
        commit: bool = True,
    ) -> PreparedRun:
        run_goal = request.goal.strip()
        if not run_goal:
            raise ValidationError("GOAL_REQUIRED", "请输入你想完成的目标。", {"field": "goal"})
        logger.info(
            "run.create.start conversation_id=%s provider=%s model=%s goal_chars=%s",
            request.task_id,
            request.model.provider if request.model else self._settings.model_provider,
            request.model.name if request.model else self._settings.model_name,
            len(run_goal),
        )
        try:
            run_settings = await self._settings_resolver.for_new_run(request.model)
            await self._prepare_conversation(request, run_goal, run_settings)
            execution_profile = self._compile_execution_profile(request, run_settings)
            permission_bundle = self._validated_permission_bundle(request.permission_bundle)
            execution_profile = execution_profile.model_copy(
                update={
                    "interactive": request.interactive,
                    "permission_bundle": (
                        permission_bundle.model_dump(mode="json") if permission_bundle else None
                    ),
                }
            )
            run = await self._create_run_record(
                request,
                run_goal,
                run_settings,
                execution_profile,
            )
            if commit:
                await self._session.commit()
            else:
                await self._session.flush()
        except AgentProfileConfigurationError as error:
            raise ConfigurationError(
                "AGENT_PROFILE_INVALID",
                "Astra 身份配置无效，暂时无法创建任务。",
            ) from error
        except ValueError as error:
            if str(error).startswith("Task not found"):
                raise ResourceError("TASK_NOT_FOUND", "找不到指定任务。") from error
            raise ValidationError("RUN_REQUEST_INVALID", "无法创建任务。") from error
        response = self._response_for_run(run)
        logger.info(
            "run.create.accepted run_id=%s conversation_id=%s status=%s",
            run.id,
            run.task_id,
            run.status,
        )
        return PreparedRun(response=response, settings=run_settings)

    async def _prepare_conversation(
        self,
        request: CreateRunRequest,
        run_goal: str,
        run_settings: Settings,
    ) -> None:
        if request.task_id is None:
            return
        context_manager = ConversationContextManager(self._session, run_settings)
        conversation = await context_manager.require_task(request.task_id)
        await context_manager.prepare_for_run(
            conversation,
            provider=run_settings.model_provider,
            model=run_settings.model_name,
            draft=run_goal,
        )

    @staticmethod
    def _compile_execution_profile(
        request: CreateRunRequest,
        run_settings: Settings,
    ) -> RunExecutionProfile:
        execution_profile = RunProfileResolver().resolve(
            request.answer_mode,
            request.reasoning_policy,
            plan_execution=request.plan_execution,
            subagent_policy=compile_subagent_policy(run_settings),
            subagent_mode=request.subagent_mode,
        )
        if request.subagent_mode == "required":
            eligibility = subagent_execution_eligibility(
                execution_profile.reasoning_policy.effective.subagents,
                live_swarm_enabled=bool(run_settings.tool_swarm_enabled),
            )
            if not eligibility.executable:
                raise ValidationError(
                    "SUBAGENT_COMMAND_UNAVAILABLE",
                    "当前策略不允许创建必需子 Agent 运行。",
                )
        if not request.interactive and request.permission_bundle is None:
            raise ValidationError(
                "PERMISSION_BUNDLE_REQUIRED",
                "无人值守、定时或后台运行必须提供显式权限包。",
            )
        return execution_profile

    async def _create_run_record(
        self,
        request: CreateRunRequest,
        run_goal: str,
        run_settings: Settings,
        execution_profile: RunExecutionProfile,
    ) -> RunRecord:
        context_window = resolve_context_window(
            run_settings.model_provider,
            run_settings.model_name,
            fallback_tokens=run_settings.context_window_fallback_tokens,
        )
        thinking = normalize_model_thinking(
            provider=run_settings.model_provider,
            model=run_settings.model_name,
            selection=request.model.thinking if request.model else None,
        )
        repository = RunUnitOfWork(self._session)
        run = await repository.create_task_run(
            run_goal,
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
            request.task_id,
            reasoning_policy=execution_profile.reasoning_policy.model_dump(mode="json"),
            answer_mode=execution_profile.answer_mode.value,
            execution_profile=execution_profile.model_dump(mode="json"),
            agent_profile_snapshot=load_agent_profile().snapshot(),
            session_id=request.session_id,
            commit=False,
        )
        await self._freeze_skills(
            run.id,
            run_goal,
            request,
            execution_profile.answer_mode.value,
            run_settings,
        )
        for adjustment in execution_profile.reasoning_policy.adjustments:
            await repository.add_event(
                run.id,
                "reasoning.policy_adjusted",
                adjustment.model_dump(mode="json"),
            )
        return run

    def _validated_permission_bundle(self, raw_bundle: dict | None) -> PermissionBundle | None:
        if raw_bundle is None:
            return None
        try:
            permission_bundle = PermissionBundle.model_validate(raw_bundle)
        except ValueError as error:
            raise ValidationError("PERMISSION_BUNDLE_INVALID", "权限包格式无效。") from error
        if not verify_permission_bundle(
            permission_bundle,
            self._settings.permission_bundle_signing_secret,
        ):
            raise ValidationError(
                "PERMISSION_BUNDLE_INVALID",
                "权限包签名无效或签名密钥未配置。",
            )
        return permission_bundle

    async def _freeze_skills(
        self,
        run_id: str,
        run_goal: str,
        request: CreateRunRequest,
        answer_mode: str,
        run_settings: Settings,
    ) -> None:
        if not run_settings.skills_enabled:
            return
        catalog_builder = SkillCatalogBuilder(
            self._session,
            metadata_chars=run_settings.skills_catalog_metadata_chars,
        )
        skill_catalog = await catalog_builder.build(
            goal=run_goal,
            explicit_identities=request.skill_ids,
            runtime_capabilities=self._configured_skill_capabilities(run_settings),
        )
        await catalog_builder.freeze(run_id, answer_mode, skill_catalog, new_run=True)
        skill_activator = SkillActivationService(
            self._session,
            max_active=run_settings.skills_max_active,
            max_resource_bytes=run_settings.skills_max_resource_bytes_per_run,
        )
        for skill_identity in request.skill_ids:
            try:
                skill_catalog.require(skill_identity)
                await skill_activator.activate(
                    run_id,
                    skill_identity,
                    initiator="explicit",
                    reason="explicit run selection",
                )
            except ValueError as error:
                raise ValidationError(
                    "SKILL_SELECTION_INVALID",
                    f"无法激活 Skill：{skill_identity}",
                    {"qualified_identity": skill_identity, "reason": str(error)},
                ) from error

    @staticmethod
    def _configured_skill_capabilities(settings: Settings) -> set[str]:
        return {
            capability
            for capability, is_enabled in {
                "web_search": settings.tool_web_search_enabled,
                "web_fetch": settings.tool_web_fetch_enabled,
                "chart_render": settings.tool_chart_render_enabled,
                "bash_execute": settings.tool_bash_execute_enabled,
                "sandbox": settings.sandbox_enabled,
            }.items()
            if is_enabled
        }

    @staticmethod
    def _response_for_run(run: RunRecord) -> CreateRunResponse:
        return CreateRunResponse(
            task_id=run.task_id,
            run_id=run.id,
            status=run.status,
            answer_mode=run.answer_mode,
        )
