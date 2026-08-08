from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.application.agent_runtime.policies.reasoning import (
    build_default_contract,
    normalize_contract,
    validate_contract,
)
from app.application.run_management.conversations.context import ConversationContextManager
from app.common.schemas.agent.planning import (
    ExpectedObservation,
    PlanDraft,
    PlanNodeDraft,
    SuccessCriterion,
    TaskContract,
)
from app.common.schemas.agent.run_policy import ReasoningPolicySnapshot, RunExecutionProfile
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.contracts import ModelOutputError
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

logger = logging.getLogger("astra.engine")


def _mandatory_skill_criteria(blocks):
    criteria = []
    known_checks = set()
    for block in blocks:
        metadata = block.get("metadata", {})
        checks = metadata.get("mandatory_checks", []) if isinstance(metadata, dict) else []
        if not isinstance(checks, list):
            continue
        for raw_check in checks:
            check = str(raw_check).strip()
            if not check or check in known_checks:
                continue
            known_checks.add(check)
            identity = block["qualified_identity"]
            stable_id = hashlib.sha256(f"{identity}\0{check}".encode()).hexdigest()[:12]
            criteria.append(
                SuccessCriterion(
                    id=f"skill-check-{stable_id}",
                    description=check,
                    verification_method="task_adapter",
                    provenance={
                        "kind": "skill_mandatory_check",
                        "qualified_identity": identity,
                        "revision_id": block["revision_id"],
                        "digest": block["digest"],
                    },
                )
            )
    return criteria


class PlanPreparationMixin:
    async def _prepare_plan(
        self,
        run_id: str,
        goal: str,
        reasoning_policy: dict[str, Any],
        execution_profile: dict[str, Any] | None = None,
    ) -> tuple[TaskContract, PlanDraft]:
        ReasoningPolicySnapshot.model_validate(reasoning_policy)
        RunExecutionProfile.model_validate(execution_profile or {})
        public_goal = self._public_plan_text(goal)
        try:
            contract_result = await self.model_client.contract(public_goal)
        except ModelOutputError as exc:
            contract_result = exc
        contract = self._resolve_contract(run_id, public_goal, contract_result)
        contract, skill_revisions = self._enrich_contract_with_skills(contract)
        try:
            plan_result = await self.model_client.plan(
                goal,
                contract=contract,
            )
        except ModelOutputError as exc:
            plan_result = exc
        plan = self._resolve_plan(
            run_id,
            plan_result,
            contract=contract,
        )
        active_identities = [item["qualified_identity"] for item in skill_revisions]
        if active_identities:
            plan = plan.model_copy(
                update={
                    "nodes": [
                        node
                        if node.required_skill_ids
                        else node.model_copy(update={"required_skill_ids": active_identities})
                        for node in plan.nodes
                    ]
                }
            )
        return contract, plan

    def _enrich_contract_with_skills(self, contract):
        blocks = getattr(self, "_active_skill_blocks", [])
        revisions = [
            {key: item[key] for key in ("qualified_identity", "revision_id", "digest")}
            for item in blocks
        ]
        if not revisions:
            return contract, revisions
        criteria = [
            item.model_copy(
                update={
                    "provenance": {
                        **item.provenance,
                        "skill_revisions": revisions,
                    }
                }
            )
            for item in contract.success_criteria
        ]
        criteria.extend(_mandatory_skill_criteria(blocks))
        return contract.model_copy(
            update={
                "skill_revisions": revisions,
                "success_criteria": criteria,
            }
        ), revisions

    def _resolve_contract(
        self, run_id: str, goal: str, result: TaskContract | Exception | None
    ) -> TaskContract:
        contract = result
        if isinstance(result, Exception):
            if not isinstance(result, ModelOutputError):
                raise result
            logger.warning("run.contract.fallback run_id=%s reason=%s", run_id, str(result))
            contract = build_default_contract(goal)
        if contract:
            contract = normalize_contract(contract, goal)
            try:
                validate_contract(contract)
            except ValueError as exc:
                raise ModelOutputError(f"Invalid task contract: {exc}") from exc
        return contract or build_default_contract(goal)

    def _resolve_plan(
        self,
        run_id: str,
        result: PlanDraft | Exception,
        *,
        contract: TaskContract,
    ) -> PlanDraft:
        if not isinstance(result, Exception):
            if result.nodes:
                return result
            logger.warning("run.plan.fallback run_id=%s reason=empty plan nodes", run_id)
            return self._default_plan(
                "生成回复",
                "直接回应用户当前请求",
                contract=contract,
            )
        if not isinstance(result, ModelOutputError):
            raise result
        logger.warning("run.plan.fallback run_id=%s reason=%s", run_id, str(result))
        return self._default_plan(
            "生成回复",
            "直接回应用户当前请求",
            contract=contract,
        )

    @staticmethod
    def _default_plan(
        title: str,
        intent: str,
        *,
        contract: TaskContract,
    ) -> PlanDraft:
        return PlanDraft(
            nodes=[
                PlanNodeDraft(
                    node_key="step-1",
                    title=title,
                    intent=intent,
                    success_criteria_refs=[item.id for item in contract.success_criteria],
                    expected_outcome=ExpectedObservation(
                        kind="step_result",
                        success_condition="step completed with accepted evidence",
                    ),
                    risk_level=contract.risk_level,
                )
            ],
        )

    @staticmethod
    def _public_plan_text(text: str) -> str:
        context_marker = "Conversation context:\n"
        request_marker = "\nCurrent user request: "
        if context_marker not in text or request_marker not in text:
            return text
        prefix, contextual = text.split(context_marker, 1)
        _, current_request = contextual.rsplit(request_marker, 1)
        return prefix + current_request

    async def _conversation_goal(self, repo: RunUnitOfWork, run: RunRecord) -> str:
        current_goal = run.model_policy.get("conversation_goal")
        if not current_goal:
            current_goal = (await repo.require_run(run.id)).task.description
        if run.model_policy.get("conversation_context_required") is False:
            return current_goal
        manager = ConversationContextManager(repo.session, self.settings)
        task = await manager.require_task(run.task_id)
        return await manager.render_goal(task, current_goal)
