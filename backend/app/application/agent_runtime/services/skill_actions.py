"""Skill activation and resource-read decisions."""

from __future__ import annotations

from app.application.agent_runtime.services.progress import ExecutionProgress
from app.application.skills.activation import SkillActivationService
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


class SkillActionStage:
    def __init__(
        self,
        repository: RunUnitOfWork,
        activation_service: SkillActivationService,
        model_client: ModelClient,
        progress: ExecutionProgress,
    ) -> None:
        self._repository = repository
        self._activation_service = activation_service
        self._model_client = model_client
        self._progress = progress

    async def execute(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        *,
        quick_mode: bool,
    ) -> bool:
        if decision.decision_type == "activate_skill":
            observation = await self._activate(run_id, decision, quick_mode=quick_mode)
        elif decision.decision_type == "read_skill_resource":
            observation = await self._read_resource(run_id, decision)
        else:
            return False
        self._progress.observations.append(observation.model_dump(mode="json"))
        await self._repository.update_agent_turn(
            turn.id,
            status="completed" if observation.status == "completed" else "failed",
            observation=observation.model_dump(mode="json"),
        )
        await self._repository.session.commit()
        return True

    async def _activate(
        self,
        run_id: str,
        decision: AgentDecision,
        *,
        quick_mode: bool,
    ) -> AgentObservation:
        identity = decision.skill_identity or ""
        run = await self._repository.require_run_core(run_id)
        contract_skills = {
            item.get("qualified_identity")
            for item in (run.task_contract or {}).get("skill_revisions", [])
            if isinstance(item, dict)
        }
        if not quick_mode and identity not in contract_skills:
            observation = AgentObservation(
                kind="skill_replan_required",
                status="failed",
                summary="可信模式需要通过 PlanPatch 绑定此前未选择的 Skill。",
                data={"qualified_identity": identity},
            )
            await self._repository.add_event(
                run_id,
                "skill.replan_required",
                observation.model_dump(mode="json"),
            )
            return observation
        try:
            activated = await self._activation_service.activate(
                run_id,
                identity,
                initiator="model",
                reason=decision.reasoning_summary,
            )
            self._model_client.bind_skills(await self._activation_service.prompt_blocks(run_id))
            return AgentObservation(
                kind="skill_activation",
                status="completed",
                summary=f"已激活 {identity}",
                data={
                    "activation": activated["activation"],
                    "resources": activated["resources"],
                    "mode_recommendation": activated.get("mode_recommendation"),
                },
            )
        except ValueError as error:
            return AgentObservation(
                kind="skill_activation",
                status="failed",
                summary="Skill 激活被拒绝。",
                data={"qualified_identity": identity},
                error={"category": "skill_activation", "message": str(error)},
            )

    async def _read_resource(
        self,
        run_id: str,
        decision: AgentDecision,
    ) -> AgentObservation:
        identity = decision.skill_identity or ""
        path = decision.skill_resource_path or ""
        try:
            content = await self._activation_service.read_resource(run_id, identity, path)
            return AgentObservation(
                kind="skill_resource",
                status="completed",
                summary=f"已读取 {identity} 的 {path}",
                data={
                    "qualified_identity": identity,
                    "path": path,
                    "content": content.decode("utf-8"),
                },
            )
        except (UnicodeDecodeError, ValueError) as error:
            return AgentObservation(
                kind="skill_resource",
                status="failed",
                summary="Skill 资源读取被拒绝。",
                data={"qualified_identity": identity, "path": path},
                error={"category": "skill_resource", "message": str(error)},
            )
