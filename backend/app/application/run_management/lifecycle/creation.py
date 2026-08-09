"""Create and freeze a governed Run before background execution begins."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent_runtime.policies.reasoning import (
    compile_subagent_policy,
    resolve_run_profile,
)
from app.application.permissions.governance import verify_permission_bundle
from app.application.run_management.conversations.context import ConversationContextManager
from app.application.run_management.lifecycle.contracts import PreparedRunExecution, run_response
from app.application.run_management.lifecycle.settings import RunSettingsResolver
from app.application.skills.activation import SkillActivationService
from app.application.skills.catalog import SkillCatalogBuilder
from app.application.subagents.eligibility import subagent_execution_eligibility
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import (
    AstraConfigurationError,
    AstraInputValidationError,
    AstraResourceNotFoundError,
)
from app.common.schemas.agent.api_views import CreateRunRequest
from app.common.schemas.agent.run_policy import RunExecutionProfile
from app.common.schemas.permissions import PermissionBundle
from app.domain.agent_profile import AgentProfileConfigurationError, load_agent_profile
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.context_windows import resolve_context_window
from app.infrastructure.model_clients.reasoning import normalize_model_thinking
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

logger = logging.getLogger("astra.run_creation")


@dataclass
class RunCreator:
    _session: AsyncSession
    _settings: AstraRuntimeSettings
    _settings_resolver: RunSettingsResolver

    async def prepare(
        self,
        request: CreateRunRequest,
        *,
        commit: bool = True,
    ) -> PreparedRunExecution:
        run_goal = request.goal.strip()
        if not run_goal:
            raise AstraInputValidationError("GOAL_REQUIRED", "请输入你想完成的目标。", {"field": "goal"})
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
                    "permission_bundle": (permission_bundle.model_dump(mode="json") if permission_bundle else None),
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
            raise AstraConfigurationError(
                "AGENT_PROFILE_INVALID",
                "Astra 身份配置无效，暂时无法创建任务。",
            ) from error
        except ValueError as error:
            if str(error).startswith("Task not found"):
                raise AstraResourceNotFoundError("TASK_NOT_FOUND", "找不到指定任务。") from error
            raise AstraInputValidationError("RUN_REQUEST_INVALID", "无法创建任务。") from error
        response = run_response(run)
        logger.info(
            "run.create.accepted run_id=%s conversation_id=%s status=%s",
            run.id,
            run.task_id,
            run.status,
        )
        return PreparedRunExecution(response=response, settings=run_settings)

    async def _prepare_conversation(
        self,
        request: CreateRunRequest,
        run_goal: str,
        run_settings: AstraRuntimeSettings,
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
        run_settings: AstraRuntimeSettings,
    ) -> RunExecutionProfile:
        execution_profile = resolve_run_profile(
            request.answer_mode,
            request.reasoning_policy,
            plan_execution=request.plan_execution,
            subagent_policy=compile_subagent_policy(run_settings),
            subagent_mode=request.subagent_mode,
        )
        if request.subagent_mode == "required":
            eligibility = subagent_execution_eligibility(
                execution_profile.reasoning_policy.effective.subagents,
                live_swarm_enabled=run_settings.tool_enabled("swarm"),
            )
            if not eligibility.executable:
                raise AstraInputValidationError(
                    "SUBAGENT_COMMAND_UNAVAILABLE",
                    "当前策略不允许创建必需子 Agent 运行。",
                )
        if not request.interactive and request.permission_bundle is None:
            raise AstraInputValidationError(
                "PERMISSION_BUNDLE_REQUIRED",
                "无人值守、定时或后台运行必须提供显式权限包。",
            )
        return execution_profile

    async def _create_run_record(
        self,
        request: CreateRunRequest,
        run_goal: str,
        run_settings: AstraRuntimeSettings,
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
            raise AstraInputValidationError("PERMISSION_BUNDLE_INVALID", "权限包格式无效。") from error
        if not verify_permission_bundle(
            permission_bundle,
            self._settings.permission_bundle_signing_secret,
        ):
            raise AstraInputValidationError(
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
        run_settings: AstraRuntimeSettings,
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
                raise AstraInputValidationError(
                    "SKILL_SELECTION_INVALID",
                    f"无法激活 Skill：{skill_identity}",
                    {"qualified_identity": skill_identity, "reason": str(error)},
                ) from error

    @staticmethod
    def _configured_skill_capabilities(settings: AstraRuntimeSettings) -> set[str]:
        capabilities = {name for name, enabled in settings.tool_states.items() if enabled}
        if settings.sandbox_enabled:
            capabilities.add("sandbox")
        return capabilities
