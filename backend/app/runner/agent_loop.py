import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.artifacts import ArtifactService, LocalArtifactStore
from app.core.config import Settings
from app.permissions.effects import (
    DefaultEffectAnalyzer,
    effect_plan_hash,
    grant_proposals,
    workspace_mount_mode,
)
from app.permissions.engine import PermissionEngine
from app.permissions.governance import ExtensionTrustPolicy
from app.repositories.permissions import PermissionRepository
from app.repositories.plans import PlanRepository, plan_to_view
from app.repositories.runs import RunRepository
from app.repositories.workspaces import WorkspaceRepository
from app.runner.adapters import ChartTaskAdapter, ProcessorRegistry, WebTaskAdapter
from app.runner.approvals import input_hash, safe_preview, similar_matcher
from app.runner.model_client import ModelClient, ModelOutputError
from app.runner.planning import PlanScheduler, PlanService
from app.runner.reasoning import (
    CompletionGate,
    ObservationEvaluator,
    ReflectionGate,
    apply_reflection_patch,
    apply_validation_outcomes,
    failure_fingerprint,
)
from app.runner.runtime import LoopOrchestrator, NoProgressDetector
from app.sandbox.docker_provider import build_sandbox_provider
from app.sandbox.runtime import SandboxJobService, SandboxSupervisor
from app.schemas.agent import (
    AgentDecision,
    AgentObservation,
    AgentState,
    AnswerMode,
    AssuranceLevel,
    CompletionDecision,
    ContractMode,
    EvaluationOutcome,
    ExpectedObservation,
    FailureFingerprint,
    FinalAnswer,
    NodeResult,
    PlanNodeStatus,
    ReasoningPolicySnapshot,
    RunExecutionProfile,
    TerminalState,
    ValidationIssue,
    ValidationOutcome,
    VerificationReport,
)
from app.schemas.permissions import (
    ExtensionDescriptor,
    PermissionBundle,
    PermissionDecisionKind,
    PermissionPolicySet,
    PermissionSubject,
)
from app.tools.base import (
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
)
from app.tools.router import ToolRouter
from app.workspaces.runtime import WorkspaceRuntimeService

logger = logging.getLogger("astra.agent_loop")


INVALID_ARTIFACT_REFERENCE_WARNING = "已移除无效或不可访问的工具输出引用。"


def normalize_final_answer_artifact_references(
    final_answer: FinalAnswer,
    artifacts: list[Any],
) -> tuple[FinalAnswer, int, list[str]]:
    """Keep only accessible artifacts from the current run without leaking rejected IDs."""
    allowed_ids = {
        str(artifact.id)
        for artifact in artifacts
        if artifact.security_status == "verified" and artifact.storage_key
    }
    invalid_count = 0
    referenced_ids: list[str] = []
    normalized_findings = []
    for finding in final_answer.findings:
        seen: set[str] = set()
        valid_ids: list[str] = []
        for artifact_id in finding.artifact_ids:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            if artifact_id not in allowed_ids:
                invalid_count += 1
                continue
            valid_ids.append(artifact_id)
            if artifact_id not in referenced_ids:
                referenced_ids.append(artifact_id)
        normalized_findings.append(finding.model_copy(update={"artifact_ids": valid_ids}))

    verification_notes = list(final_answer.verification_notes)
    if invalid_count and INVALID_ARTIFACT_REFERENCE_WARNING not in verification_notes:
        verification_notes.append(INVALID_ARTIFACT_REFERENCE_WARNING)
    return (
        final_answer.model_copy(
            update={
                "findings": normalized_findings,
                "verification_notes": verification_notes,
            }
        ),
        invalid_count,
        referenced_ids,
    )


def _quick_workspace_change_completes_goal(
    goal: str, workspace_changes: list[dict[str, Any]]
) -> bool:
    """Finish a one-step file task once its requested target actually changed."""
    if not workspace_changes:
        return False
    normalized_goal = goal.casefold()
    multi_step_markers = (
        "图表",
        "绘图",
        "可视化",
        "渲染",
        "图片",
        "chart",
        "plot",
        "visuali",
        "render",
        "image",
    )
    if any(marker in normalized_goal for marker in multi_step_markers):
        return False
    for change in workspace_changes:
        path = str(change.get("path") or "").strip()
        if not path:
            continue
        filename = path.rsplit("/", 1)[-1].casefold()
        if filename and filename in normalized_goal:
            return True
    if any(
        marker in normalized_goal
        for marker in ("删除工作区", "清空工作区", "delete workspace", "clear workspace")
    ):
        return all(change.get("kind") == "deleted" for change in workspace_changes)
    return False


class ContextAssembler:
    def __init__(self, repo: RunRepository):
        self.repo = repo

    async def assemble(
        self,
        *,
        run_id: str,
        goal: str,
        tool_registry: ToolRegistry,
        sandbox_provider=None,
        tool_router: ToolRouter | None = None,
        observations: list[dict[str, Any]],
        evidence_pack: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        memories = await self.repo.list_memories(run_id=run_id, min_confidence=0.0, limit=8)
        run = await self.repo.require_run(run_id)
        plan = await PlanRepository(self.repo.session).active_for_run(run_id)
        plan_view = plan_to_view(plan).model_dump(mode="json") if plan else run.plan_graph or {}
        active_node_id = (run.agent_state or {}).get("active_node_id")
        active_node = next(
            (item for item in plan_view.get("nodes", []) if item.get("id") == active_node_id),
            None,
        )
        specs, unavailable = tool_registry.specs(), {}
        if tool_router is not None:
            specs, unavailable = tool_router.eligible_specs()
        if active_node and active_node.get("required_capabilities"):
            required = set(active_node["required_capabilities"])
            specs = {
                name: spec
                for name, spec in specs.items()
                if name in required
                or bool(
                    required
                    & {
                        *spec.capabilities,
                        *spec.permissions,
                        spec.permission,
                    }
                )
            }
        return {
            "run_id": run_id,
            "goal": goal,
            "tool_manifests": {name: spec.model_dump() for name, spec in specs.items()},
            "unavailable_capabilities": unavailable,
            "observations": observations,
            "evidence_pack": evidence_pack or {},
            "memory_reads": [
                {
                    "id": memory.id,
                    "scope": memory.scope,
                    "kind": memory.kind,
                    "content": memory.content,
                    "confidence": memory.confidence,
                    "provenance": memory.provenance,
                }
                for memory in memories
            ],
            "reasoning_policy": run.reasoning_policy or {},
            "answer_mode": run.answer_mode,
            "execution_profile": run.execution_profile or {},
            "task_contract": run.task_contract or {},
            "plan_graph": plan_view,
            "active_node": active_node,
            "agent_state": run.agent_state or {},
        }


class MemoryManager:
    def __init__(self, settings: Settings, repo: RunRepository, model_client: ModelClient):
        self.settings = settings
        self.repo = repo
        self.model_client = model_client

    async def write_candidates(
        self,
        *,
        run_id: str,
        goal: str,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self.settings.agent_memory_write_enabled:
            return []
        try:
            candidates = await self.model_client.extract_memory_candidates(goal, context)
        except ModelOutputError as exc:
            logger.warning("memory.extraction.skipped run_id=%s reason=%s", run_id, str(exc))
            await self.repo.add_event(
                run_id, "memory.extraction_skipped", {"reason": "invalid_model_output"}
            )
            await self.repo.session.commit()
            return []
        writes = []
        for candidate in candidates:
            memory = await self.repo.create_memory(
                run_id=run_id,
                scope=candidate.scope,
                kind=candidate.kind,
                content=candidate.content,
                structured_data=candidate.structured_data,
                provenance=candidate.provenance,
                confidence=candidate.confidence,
                expires_at=candidate.expires_at,
            )
            writes.append(
                {
                    "id": memory.id,
                    "scope": memory.scope,
                    "kind": memory.kind,
                    "content": memory.content,
                    "confidence": memory.confidence,
                    "provenance": memory.provenance,
                }
            )
        return writes


class VerificationEngine:
    def verify(
        self,
        final_answer: FinalAnswer,
        evidence_pack: dict[str, Any],
        *,
        validation_outcomes: list[ValidationOutcome] | None = None,
        invalid_artifact_references: int = 0,
        assurance_level: AssuranceLevel = AssuranceLevel.full,
    ) -> VerificationReport:
        fetched_sources = evidence_pack.get("fetched_sources", [])
        low_quality = [
            source for source in fetched_sources if float(source.get("quality_score") or 0) < 0.5
        ]
        notes = list(final_answer.verification_notes)
        outcomes = list(validation_outcomes or [])
        artifact_warnings: list[str] = []
        artifact_issues: list[ValidationIssue] = []
        if invalid_artifact_references:
            artifact_warnings.append(INVALID_ARTIFACT_REFERENCE_WARNING)
            artifact_issues.append(
                ValidationIssue(
                    code="artifact_reference_invalid",
                    message=INVALID_ARTIFACT_REFERENCE_WARNING,
                    severity="warning",
                    details={"invalid_count": invalid_artifact_references},
                )
            )
        outcomes.append(
            ValidationOutcome(
                validator="artifact_reference",
                passed=True,
                blocking=False,
                issues=artifact_issues,
                warnings=artifact_warnings,
            )
        )
        for outcome in outcomes:
            notes.extend(outcome.warnings)
            notes.extend(issue.message for issue in outcome.issues)
        if (
            fetched_sources
            and final_answer.sources
            and not any(not outcome.passed and outcome.blocking for outcome in outcomes)
        ):
            notes.append("至少一个抓取来源支撑了最终答案。")
        has_blocking_failure = any(not outcome.passed and outcome.blocking for outcome in outcomes)
        has_warnings = any(
            outcome.warnings or any(issue.severity == "warning" for issue in outcome.issues)
            for outcome in outcomes
        )
        status = (
            "failed"
            if has_blocking_failure
            else "completed_with_warnings"
            if has_warnings
            else "completed"
        )
        return VerificationReport(
            status=status,
            assurance_level=assurance_level,
            source_count=len(final_answer.sources),
            caveat_count=len(final_answer.caveats),
            low_quality_sources=low_quality,
            failed_sources=evidence_pack.get("failed_sources", []),
            memory_references=final_answer.memory_references,
            invalid_artifact_references=invalid_artifact_references,
            notes=list(dict.fromkeys(notes)),
            validation_outcomes=outcomes,
        )


class AgentLoop:
    def __init__(
        self,
        settings: Settings,
        *,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        sandbox_provider=None,
    ):
        self.settings = settings
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.sandbox_provider = sandbox_provider
        backends = {"in_process"}
        if settings.sandbox_enabled:
            backends.add("sandbox.remote")
        self.router = ToolRouter(tool_registry, available_backends=backends)
        self.adapter = WebTaskAdapter()
        self.chart_adapter = ChartTaskAdapter()
        self.processors = ProcessorRegistry([self.adapter, self.chart_adapter])
        self.evaluator = ObservationEvaluator()
        self.reflection_gate = ReflectionGate()
        self.completion_gate = CompletionGate()

    async def run(
        self,
        repo: RunRepository,
        run_id: str,
        goal: str,
        on_answer_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        assembler = ContextAssembler(repo)
        memory_manager = MemoryManager(self.settings, repo, self.model_client)
        verifier = VerificationEngine()
        artifact_service = ArtifactService(
            repo,
            LocalArtifactStore(self.settings.artifact_store_path),
            max_files=self.settings.artifact_max_files,
            max_bytes=self.settings.artifact_max_bytes,
        )
        provider = self.sandbox_provider or build_sandbox_provider(self.settings)
        initial_run = await repo.require_run(run_id)
        permission_repository = PermissionRepository(repo.session)
        main_identity = await permission_repository.get_or_create_identity(
            identity_type="main_agent",
            principal="astra.agent",
            task_id=initial_run.task_id,
            run_id=run_id,
            trust_level="platform",
            attributes={"permission_scope": {"actions": ["*"], "resources": ["*"]}},
        )
        provider_identities: dict[str, Any] = {}
        catalog = [
            spec.model_dump(mode="json") for _, spec in sorted(self.tool_registry.specs().items())
        ]
        extension_policy = ExtensionTrustPolicy()
        try:
            extension_policy.inventory(
                [
                    ExtensionDescriptor(
                        extension_type="tool",
                        id=entry["name"],
                        version=entry["version"],
                        provider_id=entry["provider_id"],
                        digest=entry["provider_digest"],
                        trust_level=entry["trust_level"],
                        schema_digest=hashlib.sha256(
                            json.dumps(
                                entry["input_schema"],
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                        annotations={"description": entry.get("description", "")},
                    )
                    for entry in catalog
                ],
                allowed_providers=self.settings.trusted_tool_provider_map,
            )
        except ValueError as exc:
            raise ToolExecutionError("extension_trust_denied", str(exc)) from exc
        catalog_digest = hashlib.sha256(
            json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        await permission_repository.freeze_tool_catalog(
            run_id,
            catalog=catalog,
            digest=catalog_digest,
        )
        workspace_repository = WorkspaceRepository(repo.session)
        workspace_service = WorkspaceRuntimeService(
            workspace_repository,
            self.settings.task_workspace_store_path,
            max_files=self.settings.task_workspace_max_files,
            max_bytes=self.settings.task_workspace_max_bytes,
            max_file_bytes=self.settings.task_workspace_max_file_bytes,
        )
        workspace_path = await workspace_service.prepare(initial_run.task_id)
        sandbox_service = SandboxJobService(
            repo,
            SandboxSupervisor(provider),
            artifact_service,
            workspace_service,
        )
        plan_repository = PlanRepository(repo.session)
        scheduler = PlanScheduler(plan_repository)
        canonical_plan = await plan_repository.active_for_run(run_id)
        orchestrator = LoopOrchestrator()
        no_progress = NoProgressDetector()
        policy_snapshot = ReasoningPolicySnapshot.model_validate(initial_run.reasoning_policy or {})
        profile = (
            RunExecutionProfile.model_validate(initial_run.execution_profile)
            if initial_run.execution_profile
            else RunExecutionProfile(
                answer_mode=AnswerMode.trusted,
                contract_mode=ContractMode.model,
                assurance_level=AssuranceLevel.full,
                reasoning_policy=policy_snapshot,
                validators=["task_adapter", "artifact_reference"],
            )
        )
        quick_mode = profile.answer_mode == AnswerMode.standard
        policy = policy_snapshot.effective
        max_turns = (
            self.settings.agent_max_turns
            if policy.budgets.max_turns is None
            else min(policy.budgets.max_turns, self.settings.agent_max_turns)
        )
        max_tool_calls = (
            self.settings.agent_max_tool_calls
            if policy.budgets.max_tool_calls is None
            else min(policy.budgets.max_tool_calls, self.settings.agent_max_tool_calls)
        )
        max_reflections = min(policy.budgets.max_reflections, self.settings.agent_max_reflections)
        max_replans = min(policy.budgets.max_replans, self.settings.agent_max_replans)
        observations: list[dict[str, Any]] = list(
            (initial_run.agent_state or {}).get("observations", [])
        )
        tool_outputs: list[dict[str, Any]] = []
        tool_call_count = sum(
            1
            for call in initial_run.tool_calls
            if call.status in {"running", "succeeded", "failed"}
        )
        retry_counts: dict[str, int] = {}
        failed_action_counts: dict[str, int] = {}
        final_turn_id: str | None = None
        terminal_override: str | None = None
        terminal_summary: str | None = None
        streamed_final_answer: FinalAnswer | None = None
        reflection_count = 0
        replan_count = 0
        active_node = None
        approved_call = await repo.get_approved_tool_call(run_id)
        approved_request_snapshot = (
            {
                "effect_plan_hash": approved_call.approval_request.effect_plan_hash,
                "frozen_effect_plan": dict(approved_call.approval_request.frozen_effect_plan or {}),
                "analyzer_version": approved_call.approval_request.analyzer_version,
                "analyzer_digest": approved_call.approval_request.analyzer_digest,
            }
            if approved_call is not None and approved_call.approval_request is not None
            else None
        )
        if approved_call is not None and (
            approved_call.approval_request is None
            or approved_call.approval_request.status != "approved"
            or approved_call.approval_request.input_hash != input_hash(approved_call.input)
            or approved_call.approval_request.frozen_input != approved_call.input
        ):
            await repo.finish_tool_call(
                approved_call.id,
                error={
                    "category": "approval_integrity_error",
                    "message": "Approved tool input no longer matches the frozen action",
                },
            )
            raise ToolExecutionError(
                "approval_integrity_error", "Approved tool input failed integrity validation"
            )
        approved_turn = (
            next(
                (turn for turn in initial_run.turns if turn.tool_call_id == approved_call.id),
                None,
            )
            if approved_call is not None
            else None
        )
        if initial_run.turns:
            checkpoint = sorted(initial_run.turns, key=lambda item: item.turn_index)[-1]
            call = next(
                (item for item in initial_run.tool_calls if item.id == checkpoint.tool_call_id),
                None,
            )
            if checkpoint.phase == "result_recorded" and call and call.output is not None:
                recovered_output = self._normalize_tool_output(call.tool_name, call.output)
                recovered_output["tool_call_id"] = call.id
                processor = self.processors.for_tool(call.tool_name)
                if processor:
                    recovered_observation, _ = processor.process(call.tool_name, recovered_output)
                else:
                    recovered_observation = AgentObservation(
                        kind="tool_result",
                        status="succeeded",
                        summary=f"{call.tool_name} recovered from checkpoint",
                        data=recovered_output,
                    )
                observations.append(recovered_observation.model_dump(mode="json"))
                await repo.update_agent_turn(
                    checkpoint.id,
                    status="completed",
                    observation=recovered_observation.model_dump(mode="json"),
                    phase="committed",
                )
                await repo.add_event(
                    run_id,
                    "reasoning.checkpoint_recovered",
                    {"turn_id": checkpoint.id, "action": "replay_result"},
                )
            elif checkpoint.phase == "executing" and call and call.status == "running":
                if call.side_effect_level != "read_only":
                    terminal_override = "waiting_user"
                    terminal_summary = "上一次非幂等行动的执行结果未知，需要用户确认后继续。"
                    await repo.set_waiting_state(
                        run_id,
                        {
                            "paused_node": "execute",
                            "state_version": initial_run.state_version,
                            "plan_version": (initial_run.agent_state or {}).get(
                                "active_plan_version", 1
                            ),
                            "request": terminal_summary,
                        },
                    )
                else:
                    await repo.finish_tool_call(
                        call.id,
                        error={
                            "category": "interrupted",
                            "message": "Recovered after interruption",
                        },
                    )
                    await repo.update_agent_turn(checkpoint.id, status="failed", phase="failed")
                    await repo.add_event(
                        run_id,
                        "reasoning.checkpoint_recovered",
                        {
                            "turn_id": checkpoint.id,
                            "action": "retry_same_idempotency_key",
                            "idempotency_key": checkpoint.idempotency_key,
                        },
                    )

        async def maybe_reflect(signal: str, reflection_context: dict[str, Any]):
            nonlocal reflection_count, canonical_plan
            if reflection_count >= max_reflections or not self.reflection_gate.should_reflect(
                policy, signal, reflection_count
            ):
                await repo.add_event(
                    run_id,
                    "reflection.skipped",
                    {
                        "signal": signal,
                        "enabled": policy.reflection_enabled,
                        "trigger": policy.reflection_trigger.value,
                        "used": reflection_count,
                        "limit": max_reflections,
                    },
                )
                await repo.session.commit()
                return None
            try:
                reflection = await self.model_client.reflect(goal, reflection_context)
            except ModelOutputError as exc:
                logger.warning(
                    "reflection.invalid_output_skipped run_id=%s signal=%s reason=%s",
                    run_id,
                    signal,
                    str(exc),
                )
                await repo.add_event(
                    run_id,
                    "reflection.skipped",
                    {"signal": signal, "reason": "invalid_model_output"},
                )
                await repo.session.commit()
                return None
            reflection_count += 1
            reflection_observation = {
                "kind": "reflection",
                "status": "completed",
                "summary": reflection.summary,
                "data": {
                    "signal": signal,
                    "next_action": reflection.next_action,
                    "retry": reflection.retry,
                    "revised_tool_input": reflection.revised_tool_input,
                },
            }
            observations.append(reflection_observation)
            state_version = None
            current = await repo.require_run(run_id)
            if current.agent_state:
                state = AgentState.model_validate(current.agent_state)
                state.observations = list(observations)
                state.budget_usage.update(
                    {
                        "turns": len(current.turns),
                        "tool_calls": tool_call_count,
                        "reflections": reflection_count,
                        "replans": replan_count,
                    }
                )
                patch = reflection.patch
                if patch and patch.actionable():
                    try:
                        if patch.plan_patch and canonical_plan is not None:
                            capabilities = set(self.tool_registry.specs())
                            for spec in self.tool_registry.specs().values():
                                capabilities.update(spec.capabilities)
                            canonical_plan = await PlanService(plan_repository).apply_patch(
                                run_id,
                                patch.plan_patch,
                                contract=state.task_contract,
                                capabilities=capabilities,
                                budgets=policy.budgets,
                            )
                            state.active_plan_id = canonical_plan.id
                            state.active_plan_version = canonical_plan.version
                            state.active_node_id = None
                        state = apply_reflection_patch(
                            state, patch, expected_version=current.state_version
                        )
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            "reflection.patch_rejected run_id=%s signal=%s reason=%s",
                            run_id,
                            signal,
                            str(exc),
                        )
                        await repo.add_event(
                            run_id,
                            "reflection.patch_rejected",
                            {
                                "signal": signal,
                                "reason": str(exc),
                            },
                        )
                        state.version = current.state_version + 1
                else:
                    state.version = current.state_version + 1
                updated = await repo.update_reasoning_state(
                    run_id,
                    expected_version=current.state_version,
                    agent_state=state.model_dump(mode="json"),
                    plan_graph=plan_to_view(canonical_plan).model_dump(mode="json")
                    if canonical_plan is not None
                    else state.plan.model_dump(mode="json")
                    if state.plan
                    else {},
                    waiting_state=current.waiting_state,
                )
                state_version = updated.state_version
            await repo.add_event(
                run_id,
                "reflection.created",
                {
                    **reflection.model_dump(mode="json"),
                    "state_version": state_version,
                },
            )
            await repo.session.commit()
            return reflection

        async def persist_progress(evaluation=None) -> None:
            current = await repo.require_run(run_id)
            if not current.agent_state:
                return
            state = AgentState.model_validate(current.agent_state)
            state.observations = list(observations)
            known_fingerprints = {item.fingerprint for item in state.failures}
            for item in observations:
                fingerprint = item.get("data", {}).get("failure_fingerprint")
                if fingerprint and fingerprint not in known_fingerprints:
                    state.failures.append(
                        FailureFingerprint(
                            fingerprint=fingerprint,
                            tool_name=item.get("data", {}).get("tool_name"),
                            error_category=(item.get("error") or {}).get("category", "unknown"),
                            attempt_count=int(item.get("data", {}).get("retry_count", 1)),
                        )
                    )
                    known_fingerprints.add(fingerprint)
            if evaluation is not None:
                state.evaluations.append(evaluation.model_dump(mode="json"))
                for criterion in state.task_contract.success_criteria:
                    if criterion.id in evaluation.criterion_updates:
                        criterion.status = evaluation.criterion_updates[criterion.id]
            state.budget_usage.update(
                {
                    "turns": len(current.turns),
                    "tool_calls": tool_call_count,
                    "reflections": reflection_count,
                    "replans": replan_count,
                }
            )
            if canonical_plan is not None:
                active = await plan_repository.active_for_run(run_id)
                if active:
                    state.active_plan_id = active.id
                    state.active_plan_version = active.version
                    state.active_node_id = (current.agent_state or {}).get("active_node_id")
            state.version = current.state_version + 1
            await repo.update_reasoning_state(
                run_id,
                expected_version=current.state_version,
                agent_state=state.model_dump(mode="json"),
                plan_graph=plan_to_view(active).model_dump(mode="json")
                if canonical_plan is not None and active
                else current.plan_graph,
                waiting_state=current.waiting_state,
            )

        async def evaluate_node_completion(node, decision, candidate_answer=None):
            current = await repo.require_run(run_id)
            prior_match = next(
                (
                    turn.evaluation
                    for turn in sorted(
                        current.turns, key=lambda item: item.turn_index, reverse=True
                    )
                    if turn.plan_node_id == node.id
                    and isinstance(turn.evaluation, dict)
                    and turn.evaluation.get("outcome") == EvaluationOutcome.matched.value
                ),
                None,
            )
            if prior_match:
                return None, None, True
            expected = (
                ExpectedObservation.model_validate(node.expected_outcome)
                if node.expected_outcome
                else ExpectedObservation(
                    kind="step_result",
                    success_condition="node result is available",
                )
            )
            data = dict(decision.node_result or {})
            if candidate_answer is not None:
                data = {**candidate_answer.model_dump(mode="json"), **data}
            observation = AgentObservation(
                kind=expected.kind,
                status="succeeded",
                summary=f"Plan node {node.node_key} proposed completion",
                data=data,
            )
            evaluation = self.evaluator.evaluate(
                observation, expected, node.success_criteria_refs or []
            )
            return observation, evaluation, evaluation.outcome == EvaluationOutcome.matched

        logger.info(
            "agent.policy run_id=%s effort=%s planning=%s reflection=%s/%s limits=turns:%s tools:%s reflections:%s replans:%s",
            run_id,
            policy.reasoning_effort.value,
            policy.planning_strategy.value,
            policy.reflection_enabled,
            policy.reflection_trigger.value,
            max_turns,
            max_tool_calls,
            max_reflections,
            max_replans,
        )
        if not quick_mode:
            await repo.add_event(
                run_id,
                "reasoning.runtime_limits",
                {
                    "reasoning_effort": policy.reasoning_effort.value,
                    "planning_strategy": policy.planning_strategy.value,
                    "max_turns": max_turns,
                    "max_tool_calls": max_tool_calls,
                    "max_reflections": max_reflections,
                    "max_replans": max_replans,
                },
            )
            await repo.session.commit()

        start_turn_index = (
            approved_turn.turn_index if approved_turn is not None else len(initial_run.turns) + 1
        )
        for turn_index in range(start_turn_index, max_turns + 1):
            if terminal_override == "waiting_user":
                break
            if canonical_plan is not None:
                canonical_plan = await plan_repository.active_for_run(run_id)
                current = await repo.require_run(run_id)
                active_node_id = (current.agent_state or {}).get("active_node_id")
                active_node = next(
                    (
                        node
                        for node in canonical_plan.nodes
                        if node.id == active_node_id and node.status == PlanNodeStatus.running.value
                    ),
                    None,
                )
                if active_node is None and any(
                    node.status == PlanNodeStatus.pending.value for node in canonical_plan.nodes
                ):
                    active_node = await scheduler.select_next(run_id)
                    await repo.session.commit()
                    canonical_plan = await plan_repository.active_for_run(run_id)
                if active_node is None and any(
                    node.status
                    in {
                        PlanNodeStatus.failed.value,
                        PlanNodeStatus.blocked.value,
                    }
                    and not node.optional
                    for node in canonical_plan.nodes
                ):
                    terminal_override = "blocked"
                    terminal_summary = "活动计划存在失败或阻塞的必需节点。"
                    break
            context = await assembler.assemble(
                run_id=run_id,
                goal=goal,
                tool_registry=self.tool_registry,
                tool_router=self.router,
                observations=observations,
            )
            if not quick_mode:
                await repo.add_event(
                    run_id,
                    "reasoning.phase.started",
                    {
                        "phase": "selecting_action",
                        "label": "正在分析下一步",
                        "turn_index": turn_index,
                    },
                )
                await repo.session.commit()
            reasoning_buffer = ""
            reasoning_summary = ""
            reasoning_last_flush = 0.0
            reasoning_completed = False
            current_turn_index = turn_index

            async def on_reasoning_delta(
                delta: str, *, event_turn_index: int = current_turn_index
            ) -> None:
                nonlocal reasoning_buffer, reasoning_summary
                nonlocal reasoning_last_flush, reasoning_completed
                if delta == "\1":
                    if reasoning_buffer:
                        await repo.add_event(
                            run_id,
                            "reasoning.summary.delta",
                            {"turn_index": event_turn_index, "delta": reasoning_buffer},
                        )
                        reasoning_buffer = ""
                    await repo.add_event(
                        run_id,
                        "reasoning.summary.completed",
                        {
                            "turn_index": event_turn_index,
                            "summary": reasoning_summary[:4000],
                        },
                    )
                    reasoning_completed = True
                    await repo.session.commit()
                    return
                if not delta or len(reasoning_summary) >= 4000:
                    return
                safe_delta = delta[: 4000 - len(reasoning_summary)]
                reasoning_summary += safe_delta
                reasoning_buffer += safe_delta
                now = time.monotonic()
                if (
                    reasoning_last_flush == 0.0
                    or now - reasoning_last_flush >= 0.02
                    or len(reasoning_buffer) >= 96
                ):
                    await repo.add_event(
                        run_id,
                        "reasoning.summary.delta",
                        {"turn_index": event_turn_index, "delta": reasoning_buffer},
                    )
                    reasoning_buffer = ""
                    reasoning_last_flush = now
                    await repo.session.commit()

            forced_action = approved_call is not None and approved_turn is not None
            try:
                if forced_action:
                    decision = AgentDecision.model_validate(approved_turn.decision).model_copy(
                        update={
                            "tool_name": approved_call.tool_name,
                            "tool_input": dict(approved_call.input),
                        }
                    )
                    candidate_answer = None
                else:
                    decision, candidate_answer = await self.model_client.decide_with_answer(
                        goal,
                        context,
                        # A node-level answer is provisional. Only stream after the
                        # canonical plan has no active node left, otherwise a model
                        # could expose an intermediate node result as the task answer.
                        on_delta=on_answer_delta
                        if canonical_plan is None or active_node is None
                        else None,
                        on_reasoning_delta=None if quick_mode else on_reasoning_delta,
                    )
            except ModelOutputError as exc:
                logger.exception("agent.decision.invalid run_id=%s turn=%s", run_id, turn_index)
                if on_answer_delta:
                    await on_answer_delta("\0")
                decision = None
                observation = AgentObservation(
                    kind="model_error",
                    status="failed",
                    summary="模型决策输出无法解析。",
                    error={"category": "model_output_error", "message": str(exc)},
                )
                observations.append(observation.model_dump())
                reflection = await maybe_reflect(
                    "model_output_failed",
                    {"last_observation": observation.model_dump(), "retry_count": 0},
                )
                turn = await repo.create_agent_turn(
                    run_id,
                    turn_index,
                    "reflect" if reflection else "model_error",
                    reflection.summary if reflection else observation.summary,
                    decision={"decision_type": "reflect" if reflection else "model_error"},
                    memory_reads=context["memory_reads"],
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="completed",
                    observation=observation.model_dump(),
                    reflection=reflection.model_dump() if reflection else None,
                )
                await repo.session.commit()
                continue

            if not quick_mode and not reasoning_completed:
                if reasoning_buffer:
                    await repo.add_event(
                        run_id,
                        "reasoning.summary.delta",
                        {"turn_index": turn_index, "delta": reasoning_buffer},
                    )
                await repo.add_event(
                    run_id,
                    "reasoning.summary.completed",
                    {"turn_index": turn_index, "summary": decision.reasoning_summary[:4000]},
                )
                await repo.session.commit()

            logger.info(
                "agent.decision run_id=%s turn=%s type=%s tool=%s confidence=%.2f",
                run_id,
                turn_index,
                decision.decision_type,
                decision.tool_name,
                decision.confidence,
            )

            if canonical_plan is not None:
                if active_node is not None and decision.target_step_id not in {
                    None,
                    active_node.id,
                    active_node.node_key,
                }:
                    observation = AgentObservation(
                        kind="decision_error",
                        status="failed",
                        summary="模型选择了非活动计划节点。",
                        data={
                            "active_node_id": active_node.id,
                            "proposed_node_id": decision.target_step_id,
                        },
                    )
                    observations.append(observation.model_dump(mode="json"))
                    await repo.add_event(
                        run_id,
                        "reasoning.decision_rejected",
                        observation.model_dump(mode="json"),
                    )
                    await repo.session.commit()
                    continue
                if decision.decision_type == "call_tool" and active_node is None:
                    terminal_override = "blocked"
                    terminal_summary = "计划没有可执行节点，工具决策已被拒绝。"
                    break

            idempotency_key = None
            disallowed_tool_observation = None
            if decision.decision_type == "call_tool":
                if active_node is not None and active_node.required_capabilities:
                    proposed_spec = self.tool_registry.specs().get(decision.tool_name or "")
                    required = set(active_node.required_capabilities)
                    provided = (
                        {
                            decision.tool_name,
                            *proposed_spec.capabilities,
                            *proposed_spec.permissions,
                            proposed_spec.permission,
                        }
                        if proposed_spec is not None
                        else {decision.tool_name}
                    )
                    if not required & provided:
                        disallowed_tool_observation = AgentObservation(
                            kind="decision_error",
                            status="failed",
                            summary="模型选择了活动节点未声明的工具。",
                            data={
                                "plan_node_id": active_node.id,
                                "tool_name": decision.tool_name,
                                "required_capabilities": sorted(required),
                            },
                        )
                encoded = json.dumps(
                    {
                        "run_id": run_id,
                        "turn_index": turn_index,
                        "tool": decision.tool_name,
                        "input": decision.tool_input,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                )
                idempotency_key = hashlib.sha256(encoded.encode()).hexdigest()
            if forced_action:
                turn = approved_turn
            else:
                turn = await repo.create_agent_turn(
                    run_id,
                    turn_index,
                    decision.decision_type,
                    decision.reasoning_summary,
                    selected_tool=decision.tool_name,
                    decision=decision.model_dump(),
                    memory_reads=context["memory_reads"],
                    state_version_before=(await repo.require_run(run_id)).state_version,
                    plan_version=((await repo.require_run(run_id)).plan_graph or {}).get(
                        "version", 1
                    ),
                    phase="prepared" if decision.decision_type == "call_tool" else "created",
                    idempotency_key=idempotency_key,
                    plan_node_id=active_node.id if active_node is not None else None,
                )
            if disallowed_tool_observation is not None:
                observations.append(disallowed_tool_observation.model_dump(mode="json"))
                await repo.update_agent_turn(
                    turn.id,
                    status="failed",
                    observation=disallowed_tool_observation.model_dump(mode="json"),
                )
                await repo.add_event(
                    run_id,
                    "reasoning.decision_rejected",
                    disallowed_tool_observation.model_dump(mode="json"),
                )
                await repo.session.commit()
                continue
            if not quick_mode:
                await repo.add_event(
                    run_id,
                    "reasoning.decision_validated",
                    {
                        "turn_index": turn_index,
                        "decision_type": decision.decision_type,
                        "target_step_id": decision.target_step_id,
                    },
                )
                await repo.session.commit()

            if decision.decision_type == "finalize":
                if not quick_mode:
                    orchestrator.validate_result(
                        "select_action", NodeResult(next_node="completion_gate")
                    )
                final_turn_id = turn.id
                if canonical_plan is not None and active_node is not None:
                    (
                        completion_observation,
                        completion_evaluation,
                        matched,
                    ) = await evaluate_node_completion(active_node, decision, candidate_answer)
                    if not matched:
                        if completion_observation is not None:
                            observations.append(completion_observation.model_dump(mode="json"))
                        if completion_evaluation is not None:
                            await persist_progress(completion_evaluation)
                        await repo.update_agent_turn(
                            turn.id,
                            status="failed",
                            observation=completion_observation.model_dump(mode="json")
                            if completion_observation
                            else None,
                            evaluation=completion_evaluation.model_dump(mode="json")
                            if completion_evaluation
                            else None,
                            phase="failed",
                        )
                        await maybe_reflect(
                            "expectation_mismatch",
                            {
                                "last_observation": completion_observation.model_dump(mode="json")
                                if completion_observation
                                else {},
                                "runtime_context": context,
                                "retry_count": 0,
                            },
                        )
                        continue
                    if completion_observation is not None:
                        observations.append(completion_observation.model_dump(mode="json"))
                    await PlanService(plan_repository).complete_node(
                        run_id,
                        active_node.id,
                        evaluation=completion_evaluation,
                        evidence_refs=[
                            str(item.get("data", {}).get("tool_call_id"))
                            for item in observations
                            if item.get("data", {}).get("tool_call_id")
                        ],
                    )
                    await repo.update_agent_turn(turn.id, status="completed", phase="committed")
                    await repo.session.commit()
                    canonical_plan = await plan_repository.active_for_run(run_id)
                    active_node = None
                    # The answer generated while an active node was selected was deliberately
                    # not streamed because it was still provisional. Even when that was the
                    # final plan node, start one canonical answer turn with no active node so
                    # the user-facing response can be emitted incrementally instead of being
                    # revealed only by answer.completed.
                    final_turn_id = None
                    streamed_final_answer = None
                    continue
                streamed_final_answer = candidate_answer
                await repo.update_agent_turn(turn.id, status="completed")
                break

            if decision.decision_type == "complete_node":
                if canonical_plan is None or active_node is None:
                    await repo.update_agent_turn(turn.id, status="failed", phase="failed")
                    await repo.add_event(
                        run_id,
                        "reasoning.decision_rejected",
                        {"reason": "complete_node requires an active canonical plan node"},
                    )
                    await repo.session.commit()
                    continue
                (
                    completion_observation,
                    completion_evaluation,
                    matched,
                ) = await evaluate_node_completion(active_node, decision)
                if not matched:
                    if completion_observation is not None:
                        observations.append(completion_observation.model_dump(mode="json"))
                    if completion_evaluation is not None:
                        await persist_progress(completion_evaluation)
                    await repo.update_agent_turn(
                        turn.id,
                        status="failed",
                        observation=completion_observation.model_dump(mode="json")
                        if completion_observation
                        else None,
                        evaluation=completion_evaluation.model_dump(mode="json")
                        if completion_evaluation
                        else None,
                        phase="failed",
                    )
                    await maybe_reflect(
                        "expectation_mismatch",
                        {
                            "last_observation": completion_observation.model_dump(mode="json")
                            if completion_observation
                            else {},
                            "runtime_context": context,
                            "retry_count": 0,
                        },
                    )
                    continue
                if completion_observation is not None:
                    observations.append(completion_observation.model_dump(mode="json"))
                await PlanService(plan_repository).complete_node(
                    run_id,
                    active_node.id,
                    evaluation=completion_evaluation,
                    evidence_refs=active_node.evidence_refs or [],
                )
                await repo.update_agent_turn(turn.id, status="completed", phase="committed")
                await repo.session.commit()
                active_node = None
                continue

            if decision.decision_type in {"blocked", "ask_user"}:
                observation = AgentObservation(
                    kind="agent_state",
                    status=decision.decision_type,
                    summary=decision.reasoning_summary,
                    data={"required_action": decision.expected_observation},
                )
                observations.append(observation.model_dump())
                await repo.update_agent_turn(
                    turn.id,
                    status=decision.decision_type,
                    observation=observation.model_dump(),
                )
                terminal_override = (
                    "waiting_user" if decision.decision_type == "ask_user" else "blocked"
                )
                terminal_summary = decision.reasoning_summary
                if terminal_override == "waiting_user":
                    await repo.set_waiting_state(
                        run_id,
                        {
                            "paused_node": "select_action",
                            "state_version": (await repo.require_run(run_id)).state_version,
                            "plan_version": ((await repo.require_run(run_id)).plan_graph or {}).get(
                                "version", 1
                            ),
                            "request": decision.expected_observation or decision.reasoning_summary,
                        },
                    )
                break

            if decision.decision_type == "replan":
                replan_count += 1
                if replan_count > max_replans:
                    terminal_override = "blocked"
                    terminal_summary = "已达到用户策略允许的最大重新规划次数。"
                    await repo.update_agent_turn(turn.id, status="blocked")
                    break
                reflection = await maybe_reflect(
                    "dependency_broken",
                    {
                        "reason": decision.reasoning_summary,
                        "last_observation": observations[-1] if observations else {},
                        "runtime_context": context,
                        "retry_count": 0,
                    },
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="completed" if reflection else "failed",
                    reflection=reflection.model_dump(mode="json") if reflection else None,
                    reflection_patch=reflection.patch.model_dump(mode="json")
                    if reflection and reflection.patch
                    else None,
                )
                await repo.session.commit()
                continue

            if decision.decision_type == "reflect":
                reflection = await maybe_reflect(
                    "model_requested",
                    {
                        "last_observation": observations[-1] if observations else {},
                        "retry_count": 0,
                    },
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="completed",
                    reflection=reflection.model_dump() if reflection else None,
                )
                continue

            if decision.decision_type != "call_tool":
                observation = AgentObservation(
                    kind="agent_state",
                    status=decision.decision_type,
                    summary=decision.reasoning_summary,
                )
                observations.append(observation.model_dump())
                if no_progress.record(
                    evidence_refs=[],
                    criterion_changes={},
                    completed_steps=[],
                    plan_version=canonical_plan.version if canonical_plan is not None else 1,
                ):
                    await maybe_reflect(
                        "no_progress",
                        {
                            "last_observation": observation.model_dump(),
                            "runtime_context": context,
                            "retry_count": 0,
                        },
                    )
                turn_reflection = await maybe_reflect(
                    "turn_completed",
                    {"last_observation": observation.model_dump(), "retry_count": 0},
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="completed",
                    observation=observation.model_dump(),
                    reflection=turn_reflection.model_dump() if turn_reflection else None,
                )
                continue

            if tool_call_count >= max_tool_calls:
                observation = AgentObservation(
                    kind="limit",
                    status="blocked",
                    summary="已达到最大工具调用次数。",
                    data={"max_tool_calls": max_tool_calls},
                )
                observations.append(observation.model_dump())
                await repo.update_agent_turn(
                    turn.id, status="blocked", observation=observation.model_dump()
                )
                terminal_override = "blocked"
                terminal_summary = "已达到用户策略允许的最大工具调用次数。"
                if canonical_plan is not None and active_node is not None:
                    await plan_repository.transition_node(
                        active_node.id,
                        PlanNodeStatus.blocked,
                        failure={"category": "budget_exhausted"},
                    )
                    await scheduler.clear_active_node(run_id, active_node.id)
                break

            try:
                if not quick_mode:
                    orchestrator.validate_result(
                        "select_action", NodeResult(next_node="policy_gate")
                    )
                    orchestrator.validate_result("policy_gate", NodeResult(next_node="execute"))
                action_signature = json.dumps(
                    {"tool": decision.tool_name, "input": decision.tool_input},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                tool = self.router.resolve(decision.tool_name, decision.tool_input)
                provider_identity = provider_identities.get(tool.spec.provider_id)
                if provider_identity is None:
                    provider_identity = await permission_repository.get_or_create_identity(
                        identity_type="external_provider"
                        if tool.spec.provider_id != "astra.builtin"
                        else "tool_provider",
                        principal=tool.spec.provider_id,
                        task_id=initial_run.task_id,
                        run_id=run_id,
                        parent_identity_id=main_identity.id,
                        trust_level=tool.spec.trust_level,
                        attributes={"provider_digest": tool.spec.provider_digest},
                    )
                    provider_identities[tool.spec.provider_id] = provider_identity
                schema_digest = hashlib.sha256(
                    json.dumps(
                        tool.spec.input_schema,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                runtime_identity = await permission_repository.get_or_create_identity(
                    identity_type="tool_runtime",
                    principal=f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}",
                    task_id=initial_run.task_id,
                    run_id=run_id,
                    parent_identity_id=provider_identity.id,
                    trust_level=tool.spec.trust_level,
                    attributes={
                        "provider_digest": tool.spec.provider_digest,
                        "schema_digest": schema_digest,
                        "permission_scope": {
                            "actions": tool.spec.permissions,
                            "resources": ["*"],
                        },
                    },
                )
                effect_plan = DefaultEffectAnalyzer().analyze(
                    tool.spec,
                    decision.tool_input,
                    task_id=initial_run.task_id,
                )
                effect_hash = effect_plan_hash(effect_plan)
                data_flow = await permission_repository.get_data_flow_state(run_id)
                raw_profile = initial_run.execution_profile or {}
                unattended = not bool(raw_profile.get("interactive", True))
                raw_bundle = raw_profile.get("permission_bundle")
                bundle = PermissionBundle.model_validate(raw_bundle) if raw_bundle else None
                raw_permission_policies = raw_profile.get("permission_policy_set")
                permission_policies = (
                    PermissionPolicySet.model_validate(raw_permission_policies)
                    if raw_permission_policies
                    else None
                )
                step = (
                    active_node
                    if canonical_plan is not None
                    else None
                    if quick_mode
                    else await self._step_for_tool(repo, run_id, tool.spec.name)
                )
                if canonical_plan is None and step is not None:
                    await repo.update_step(step.id, "running")
                if forced_action:
                    call = approved_call
                    approved_request = approved_request_snapshot
                    if approved_request is None or (
                        approved_request["effect_plan_hash"] is not None
                        and (
                            approved_request["effect_plan_hash"] != effect_hash
                            or approved_request["frozen_effect_plan"]
                            != effect_plan.model_dump(mode="json")
                            or approved_request["analyzer_version"] != effect_plan.analyzer_version
                            or approved_request["analyzer_digest"] != effect_plan.analyzer_digest
                        )
                    ):
                        await repo.finish_tool_call(
                            call.id,
                            error={
                                "category": "approval_integrity_error",
                                "message": "Approved effect plan no longer matches the invocation",
                            },
                        )
                        raise ToolExecutionError(
                            "approval_integrity_error",
                            "Approved effect plan failed integrity validation",
                        )
                grants = await repo.list_approval_grants(run_id, tool.spec.name, tool.spec.version)
                authorization = PermissionEngine().authorize_invocation(
                    subject=PermissionSubject(
                        agent_id=runtime_identity.id,
                        identity_type="tool_runtime",
                        task_id=initial_run.task_id,
                        run_id=run_id,
                        parent_agent_id=provider_identity.id,
                        delegation_chain=[
                            main_identity.id,
                            provider_identity.id,
                            runtime_identity.id,
                        ],
                    ),
                    effect_plan=effect_plan,
                    effect_plan_hash=effect_hash,
                    tool_input=decision.tool_input,
                    declared_permissions=tool.spec.permissions,
                    execution_mode=policy.execution_mode,
                    policies=permission_policies,
                    grants=grants,
                    provider_id=tool.spec.provider_id,
                    schema_digest=schema_digest,
                    once_approved=forced_action,
                    data_flow=data_flow,
                    permission_bundle=bundle,
                    permission_bundle_signing_secret=(
                        self.settings.permission_bundle_signing_secret
                    ),
                    unattended=unattended,
                    tool_identity=(
                        f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}:"
                        f"{tool.spec.provider_digest}"
                    ),
                    tool_call_count=tool_call_count,
                    run_started_at=initial_run.started_at or initial_run.created_at,
                )
                await repo.add_event(
                    run_id,
                    "permission.decided",
                    {
                        "tool_name": tool.spec.name,
                        "effect_plan_hash": effect_hash,
                        "decision": authorization.decision.decision.value,
                        "reason_code": authorization.decision.explanation.reason_code,
                        "requests": [
                            {
                                "action": request.action,
                                "resource": request.resource,
                                "subject_id": request.subject.agent_id,
                            }
                            for request in authorization.requests
                        ],
                    },
                )
                if authorization.blocked_by_mode:
                    if forced_action:
                        await repo.finish_tool_call(
                            call.id,
                            error={
                                "category": "effect_blocked_by_mode",
                                "message": authorization.decision.explanation.summary,
                            },
                        )
                    observation = AgentObservation(
                        kind="effect_blocked_by_mode",
                        status="blocked",
                        summary=f"仅规划模式未执行：{effect_plan.summary}",
                        data={
                            "tool_name": tool.spec.name,
                            "effect_plan": effect_plan.model_dump(mode="json"),
                        },
                    )
                    observations.append(observation.model_dump(mode="json"))
                    await repo.update_agent_turn(
                        turn.id,
                        status="completed",
                        phase="committed",
                        observation=observation.model_dump(mode="json"),
                    )
                    await repo.add_event(
                        run_id,
                        "tool_call.effect_blocked_by_mode",
                        observation.model_dump(mode="json"),
                    )
                    await repo.session.commit()
                    continue
                if authorization.decision.decision == PermissionDecisionKind.deny:
                    if forced_action:
                        await repo.finish_tool_call(
                            call.id,
                            error={
                                "category": authorization.decision.explanation.reason_code,
                                "message": authorization.decision.explanation.summary,
                            },
                        )
                    raise ToolExecutionError(
                        authorization.decision.explanation.reason_code,
                        authorization.decision.explanation.summary,
                    )
                if authorization.decision.decision == PermissionDecisionKind.ask:
                    if forced_action:
                        await repo.finish_tool_call(
                            call.id,
                            error={
                                "category": "approval_revalidation_required",
                                "message": authorization.decision.explanation.summary,
                            },
                        )
                        raise ToolExecutionError(
                            "approval_revalidation_required",
                            authorization.decision.explanation.summary,
                        )
                    call = await repo.start_tool_call(
                        run_id,
                        step.id if canonical_plan is None and step is not None else None,
                        tool.spec.name,
                        tool.spec.version,
                        decision.tool_input,
                        tool.spec.permission,
                        tool.spec.side_effect_level,
                        plan_node_id=step.id if canonical_plan is not None else None,
                        status="awaiting_approval",
                    )
                    request = await repo.create_approval_request(
                        run_id=run_id,
                        turn_id=turn.id,
                        tool_call_id=call.id,
                        tool_name=tool.spec.name,
                        tool_version=tool.spec.version,
                        frozen_input=decision.tool_input,
                        input_hash=input_hash(decision.tool_input),
                        preview=safe_preview(tool.spec.name, decision.tool_input),
                        permission=", ".join(effect_plan.required_permissions),
                        impact=max(
                            (effect.risk for effect in effect_plan.effects),
                            default=tool.spec.side_effect_level,
                        ),
                        similar_matcher=(
                            grant_proposals(effect_plan)[0]
                            if grant_proposals(effect_plan)
                            else similar_matcher(tool.spec.name, decision.tool_input)
                        ),
                        frozen_effect_plan=effect_plan.model_dump(mode="json"),
                        effect_plan_hash=effect_hash,
                        analyzer_version=effect_plan.analyzer_version,
                        analyzer_digest=effect_plan.analyzer_digest,
                    )
                    await repo.update_agent_turn(
                        turn.id,
                        status="waiting_user",
                        phase="awaiting_approval",
                        paused_node="policy_gate",
                        tool_call_id=call.id,
                    )
                    terminal_override = "waiting_user"
                    terminal_summary = f"等待批准工具调用：{tool.spec.name}"
                    await repo.set_waiting_state(
                        run_id,
                        {
                            "kind": "tool_approval",
                            "approval_id": request.id,
                            "tool_call_id": call.id,
                            "paused_node": "policy_gate",
                            "request": terminal_summary,
                        },
                    )
                    break
                if authorization.grant_ids:
                    await repo.consume_approval_grants(authorization.grant_ids)
                if forced_action:
                    await repo.transition_tool_call(call.id, "running")
                    approved_call = None
                    approved_turn = None
                    approved_request_snapshot = None
                    tool_call_count += 1
                else:
                    call = await repo.start_tool_call(
                        run_id,
                        step.id if canonical_plan is None and step is not None else None,
                        tool.spec.name,
                        tool.spec.version,
                        decision.tool_input,
                        tool.spec.permission,
                        tool.spec.side_effect_level,
                        plan_node_id=step.id if canonical_plan is not None else None,
                    )
                    tool_call_count += 1
                await repo.update_agent_turn(turn.id, phase="executing", tool_call_id=call.id)
                try:
                    execution_context = ToolExecutionContext(
                        run_id=run_id,
                        tool_call_id=call.id,
                        step_id=step.id if step is not None else None,
                        trace_id=f"{run_id}:{call.id}",
                        artifact_service=artifact_service,
                        sandbox_service=sandbox_service,
                        task_id=initial_run.task_id,
                        workspace_path=workspace_path,
                        workspace_mode=workspace_mount_mode(effect_plan),
                        effect_plan=effect_plan.model_dump(mode="json"),
                        runtime_identity_id=runtime_identity.id,
                    )
                    output = await tool.run(decision.tool_input, context=execution_context)
                except ToolExecutionError as exc:
                    await repo.finish_tool_call(call.id, error=exc.to_payload())
                    raise
                workspace_changes = await workspace_repository.list_changes_for_tool_call(call.id)
                if workspace_changes:
                    output = {
                        **output,
                        "data": {
                            **dict(output.get("data") or {}),
                            "workspace_changes": [
                                {
                                    "kind": change.change_kind,
                                    "path": change.relative_path,
                                    "size_bytes": change.size_bytes,
                                    "mime_type": change.mime_type,
                                }
                                for change in workspace_changes
                            ],
                        },
                    }
                await repo.finish_tool_call(call.id, output=output)
                observed_kinds = {item.kind.value for item in effect_plan.effects}
                if observed_kinds & {"workspace_read", "network_read", "sensitive_data_read"}:
                    current_flow = await permission_repository.get_data_flow_state(run_id)
                    trust_sources = list(current_flow.trust_sources if current_flow else [])
                    data_labels = list(current_flow.data_labels if current_flow else [])
                    if "workspace_read" in observed_kinds:
                        trust_sources.append(f"workspace:{initial_run.task_id}")
                        data_labels.append("untrusted")
                    if "network_read" in observed_kinds:
                        trust_sources.append("web:public")
                        data_labels.append("untrusted")
                    for effect in effect_plan.effects:
                        data_labels.extend(effect.data_labels)
                    if "sensitive_data_read" in observed_kinds:
                        data_labels.append("sensitive")
                    await permission_repository.update_data_flow_state(
                        run_id,
                        expected_version=current_flow.state_version if current_flow else 0,
                        trust_sources=list(dict.fromkeys(trust_sources)),
                        data_labels=list(dict.fromkeys(data_labels)),
                        allowed_destinations=(
                            current_flow.allowed_destinations if current_flow else []
                        ),
                        prohibited_destinations=(
                            current_flow.prohibited_destinations if current_flow else []
                        ),
                    )
                await repo.update_agent_turn(turn.id, phase="result_recorded", tool_call_id=call.id)
                logger.info(
                    "tool.complete run_id=%s turn=%s tool=%s call_id=%s",
                    run_id,
                    turn_index,
                    tool.spec.name,
                    call.id,
                )
                output = self._normalize_tool_output(tool.spec.name, output)
                output["tool_call_id"] = call.id
                tool_outputs.append(output)
                processor = self.processors.for_tool(tool.spec.name)
                if processor:
                    observation, step_evidence = processor.process(tool.spec.name, output)
                else:
                    observation = AgentObservation(
                        kind="tool_result",
                        status="succeeded",
                        summary=f"{tool.spec.name} completed",
                        data={"tool_name": tool.spec.name, **output},
                    )
                    step_evidence = {}
                if canonical_plan is not None and active_node is not None:
                    observation.plan_node_id = active_node.id
                if canonical_plan is None and step is not None:
                    await repo.update_step(step.id, "completed", evidence=step_evidence)
                observations.append(observation.model_dump())
                if quick_mode:
                    completed_by_workspace_change = (
                        tool.spec.name == "bash_execute"
                        and _quick_workspace_change_completes_goal(
                            goal,
                            list(output.get("data", {}).get("workspace_changes", [])),
                        )
                    )
                    await repo.update_agent_turn(
                        turn.id,
                        status="completed",
                        observation=observation.model_dump(),
                        tool_call_id=call.id,
                        phase="committed",
                    )
                    if completed_by_workspace_change:
                        await repo.add_event(
                            run_id,
                            "reasoning.quick_completion_detected",
                            {
                                "tool_call_id": call.id,
                                "reason": "requested_workspace_target_changed",
                                "workspace_changes": output["data"]["workspace_changes"],
                            },
                        )
                    await repo.session.commit()
                    if completed_by_workspace_change:
                        break
                    continue
                orchestrator.validate_result(
                    "execute", NodeResult(next_node="normalize_observation")
                )
                orchestrator.validate_result(
                    "normalize_observation", NodeResult(next_node="evaluate")
                )
                expected = decision.expected
                criterion_refs = decision.success_criteria_refs
                if canonical_plan is not None and active_node is not None:
                    expected = (
                        ExpectedObservation.model_validate(active_node.expected_outcome)
                        if active_node.expected_outcome
                        else expected
                    )
                    criterion_refs = active_node.success_criteria_refs or criterion_refs
                    active_node.evidence_refs = list(
                        dict.fromkeys([*(active_node.evidence_refs or []), call.id])
                    )
                evaluation = self.evaluator.evaluate(observation, expected, criterion_refs)
                orchestrator.validate_result("evaluate", NodeResult(next_node="update_state"))
                await repo.add_event(
                    run_id,
                    "reasoning.evaluation_created",
                    {"turn_index": turn_index, **evaluation.model_dump(mode="json")},
                )
                await persist_progress(evaluation)
                orchestrator.validate_result(
                    "update_state", NodeResult(next_node="reflection_gate")
                )
                if no_progress.record(
                    evidence_refs=active_node.evidence_refs
                    if canonical_plan is not None and active_node is not None
                    else [call.id],
                    criterion_changes=evaluation.criterion_updates,
                    completed_steps=[],
                    plan_version=canonical_plan.version if canonical_plan is not None else 1,
                ):
                    await maybe_reflect(
                        "no_progress",
                        {
                            "last_observation": observation.model_dump(),
                            "runtime_context": context,
                            "retry_count": 0,
                        },
                    )
                writes = await memory_manager.write_candidates(
                    run_id=run_id,
                    goal=goal,
                    context={
                        "run_id": run_id,
                        "last_observation": observation.model_dump(),
                        "evidence_pack": {},
                    },
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="completed",
                    observation=observation.model_dump(),
                    tool_call_id=call.id,
                    memory_writes=writes,
                    evaluation=evaluation.model_dump(mode="json"),
                    phase="committed",
                )
                turn_reflection = await maybe_reflect(
                    "turn_completed",
                    {"last_observation": observation.model_dump(), "retry_count": 0},
                )
                if turn_reflection:
                    await repo.update_agent_turn(turn.id, reflection=turn_reflection.model_dump())
            except ToolExecutionError as exc:
                logger.warning(
                    "tool.failed run_id=%s turn=%s tool=%s category=%s",
                    run_id,
                    turn_index,
                    decision.tool_name,
                    exc.category,
                )
                action_signature = json.dumps(
                    {"tool": decision.tool_name, "input": decision.tool_input},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                failed_action_counts[action_signature] = (
                    failed_action_counts.get(action_signature, 0) + 1
                )
                fingerprint = failure_fingerprint(
                    decision.tool_name,
                    decision.tool_input,
                    exc.category,
                    decision.reasoning_summary,
                )
                retry_counts[decision.tool_name or "unknown"] = (
                    retry_counts.get(decision.tool_name or "unknown", 0) + 1
                )
                observation = AgentObservation(
                    kind="tool_error",
                    status="failed",
                    summary=f"{decision.tool_name} failed",
                    error=exc.to_payload(),
                    data={
                        "tool_name": decision.tool_name,
                        "retry_count": retry_counts[decision.tool_name or "unknown"],
                    },
                )
                observation.data["failure_fingerprint"] = fingerprint
                observations.append(observation.model_dump())
                await persist_progress()
                processor = self.processors.for_tool(decision.tool_name or "")
                if processor:
                    processor.record_failure(
                        decision.tool_name or "", decision.tool_input, exc.to_payload()
                    )
                reflection = (
                    await maybe_reflect(
                        "tool_failed",
                        {
                            "last_observation": observation.model_dump(),
                            "retry_count": retry_counts[decision.tool_name or "unknown"],
                        },
                    )
                    if not quick_mode
                    else None
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="failed",
                    observation=observation.model_dump(),
                    reflection=reflection.model_dump() if reflection else None,
                    reflection_patch=reflection.patch.model_dump(mode="json")
                    if reflection and reflection.patch
                    else None,
                    phase="failed",
                )
                if not quick_mode:
                    await repo.add_event(
                        run_id,
                        "reasoning.failure_fingerprinted",
                        {
                            "fingerprint": fingerprint,
                            "attempt_count": failed_action_counts[action_signature],
                        },
                    )
                await repo.session.commit()

        evidence_pack = self.adapter.build_evidence(goal, self.adapter.attempted)
        artifact = None
        if not quick_mode:
            artifact = await repo.create_artifact(
                run_id,
                "evidence_pack",
                content_ref=json.dumps(evidence_pack, ensure_ascii=False),
                metadata={
                    "format": "json",
                    "audited_sources": len(evidence_pack["fetched_sources"]),
                    "failed_sources": len(evidence_pack["failed_sources"]),
                },
            )
            evidence_pack["artifact_id"] = artifact.id
        final_context = {
            "run_id": run_id,
            "observations": observations,
            "tool_outputs": tool_outputs,
            "evidence_pack": evidence_pack,
        }
        if terminal_override:
            final_answer = FinalAnswer(
                summary=terminal_summary or "任务未能完成。",
                caveats=["运行在满足全部成功条件前停止。"],
                verification_notes=["该响应表示运行状态，不表示任务成功完成。"],
            )
        elif streamed_final_answer is not None:
            final_answer = streamed_final_answer
        else:
            final_answer = await self.model_client.finalize(
                goal, final_context, on_delta=on_answer_delta
            )
        current_artifacts = await repo.list_artifacts(run_id)
        final_answer, invalid_artifact_references, referenced_artifact_ids = (
            normalize_final_answer_artifact_references(final_answer, current_artifacts)
        )
        if quick_mode:
            final_status = terminal_override or TerminalState.completed.value
            result = final_answer.model_dump()
            result["answer_mode"] = profile.answer_mode.value
            result["assurance_level"] = profile.assurance_level.value
            result["verification_report"] = None
            result["completion_decision"] = None
            result["audit_refs"] = {
                "agent_turn_count": len((await repo.require_run(run_id)).turns),
                "referenced_artifact_ids": referenced_artifact_ids,
            }
            if final_turn_id:
                await repo.update_agent_turn(
                    final_turn_id,
                    status="completed",
                    observation={
                        "kind": "final_answer",
                        "status": final_status,
                        "summary": final_answer.summary,
                    },
                )
            if final_status not in {"waiting_user", "executing"}:
                checkpoint = await workspace_service.create_checkpoint(
                    run_id=run_id, workspace_dir=workspace_path
                )
                await repo.add_event(run_id, "workspace.checkpoint_created", checkpoint)
            await repo.session.commit()
            return {"answer": final_answer, "result": result, "status": final_status}
        memory_writes = await memory_manager.write_candidates(
            run_id=run_id,
            goal=goal,
            context=final_context,
        )
        adapter_outcomes = []
        if profile.assurance_level == AssuranceLevel.full:
            adapter_outcomes = [
                self.chart_adapter.validate(final_answer.model_dump(), {})
                if self.chart_adapter.attempted and not self.adapter.attempted
                else self.adapter.validate(final_answer.model_dump(), evidence_pack)
            ]
        report = verifier.verify(
            final_answer,
            evidence_pack,
            validation_outcomes=adapter_outcomes,
            invalid_artifact_references=invalid_artifact_references,
            assurance_level=profile.assurance_level,
        )
        run_record = await repo.require_run(run_id)
        if run_record.agent_state:
            state = AgentState.model_validate(run_record.agent_state)
            state.observations = list(observations)
            state.budget_usage.update(
                {
                    "turns": len(run_record.turns),
                    "tool_calls": tool_call_count,
                    "reflections": reflection_count,
                    "replans": replan_count,
                }
            )
            state = apply_validation_outcomes(state, report.validation_outcomes)
            state.version = run_record.state_version + 1
            run_record = await repo.update_reasoning_state(
                run_id,
                expected_version=run_record.state_version,
                agent_state=state.model_dump(mode="json"),
                plan_graph=plan_to_view(await plan_repository.active_for_run(run_id)).model_dump(
                    mode="json"
                )
                if canonical_plan is not None
                else state.plan.model_dump(mode="json")
                if state.plan
                else {},
                waiting_state=run_record.waiting_state,
            )
            required_user_action = (
                (run_record.waiting_state or {}).get("request")
                if terminal_override == "waiting_user"
                else None
            )
            gate_decision = (
                self.completion_gate.evaluate(
                    state,
                    validation_outcomes=report.validation_outcomes,
                    plan=plan_to_view(await plan_repository.active_for_run(run_id))
                    if canonical_plan is not None
                    else None,
                    required_user_action=required_user_action,
                )
                if profile.assurance_level == AssuranceLevel.full
                else self.completion_gate.evaluate_basic(
                    validation_outcomes=report.validation_outcomes,
                    required_user_action=required_user_action,
                )
            )
        else:
            blocking = [
                outcome
                for outcome in report.validation_outcomes
                if not outcome.passed and outcome.blocking
            ]
            outcome_warnings = list(
                dict.fromkeys(
                    warning
                    for outcome in report.validation_outcomes
                    for warning in outcome.warnings
                )
            )
            gate_decision = CompletionDecision(
                state=TerminalState.blocked
                if blocking
                else TerminalState.completed_with_warnings
                if outcome_warnings
                else TerminalState.completed,
                reason="验证存在阻塞问题。" if blocking else "验证要求已满足。",
                unmet_criteria=[f"validator:{item.validator}" for item in blocking],
                warnings=outcome_warnings,
            )
        if terminal_override == "waiting_user":
            gate_decision = gate_decision.model_copy(
                update={
                    "state": TerminalState.waiting_user,
                    "reason": terminal_summary or gate_decision.reason,
                    "required_user_action": terminal_summary,
                }
            )
        elif terminal_override == "blocked":
            gate_decision = gate_decision.model_copy(
                update={
                    "state": TerminalState.blocked,
                    "reason": terminal_summary or gate_decision.reason,
                }
            )
        if gate_decision.state == TerminalState.continue_run:
            gate_decision = gate_decision.model_copy(
                update={
                    "state": TerminalState.blocked,
                    "reason": "执行循环结束时活动计划仍有未完成节点。",
                }
            )
        completion_reflection = None
        if gate_decision.state == TerminalState.blocked and not terminal_override:
            completion_reflection = await maybe_reflect(
                "completion_gate_failed",
                {
                    "last_observation": {
                        "kind": "completion_gate",
                        "status": "failed",
                        "summary": gate_decision.reason,
                        "data": gate_decision.model_dump(mode="json"),
                    },
                    "retry_count": 0,
                },
            )
        final_status = gate_decision.state.value
        result = final_answer.model_dump()
        result["answer_mode"] = profile.answer_mode.value
        result["assurance_level"] = profile.assurance_level.value
        result["verification_report"] = report.model_dump()
        result["audit_refs"] = {
            "evidence_pack_artifact_id": artifact.id if artifact is not None else None,
            "agent_turn_count": len(observations) + (1 if final_turn_id else 0),
            "referenced_artifact_ids": referenced_artifact_ids,
        }
        result["completion_decision"] = gate_decision.model_dump(mode="json")
        await repo.add_event(
            run_id, "reasoning.completion_decided", gate_decision.model_dump(mode="json")
        )
        if final_turn_id:
            await repo.update_agent_turn(
                final_turn_id,
                status="completed",
                observation={
                    "kind": "final_answer",
                    "status": final_status,
                    "summary": final_answer.summary,
                },
                artifact_id=artifact.id if artifact is not None else None,
                memory_writes=memory_writes,
                reflection=completion_reflection.model_dump() if completion_reflection else None,
            )
        await repo.add_event(run_id, "verification.created", report.model_dump())
        if final_status not in {"waiting_user", "executing"}:
            checkpoint = await workspace_service.create_checkpoint(
                run_id=run_id, workspace_dir=workspace_path
            )
            await repo.add_event(run_id, "workspace.checkpoint_created", checkpoint)
        await repo.session.commit()
        return {"answer": final_answer, "result": result, "status": final_status}

    async def _step_for_tool(self, repo: RunRepository, run_id: str, tool_name: str):
        run = await repo.require_run(run_id)
        spec = self.tool_registry.get(tool_name).spec
        keywords = [tool_name, *spec.capabilities]
        for step in sorted(run.steps, key=lambda item: item.index):
            if tool_name in step.intent or tool_name in step.title:
                return step
            if any(keyword in step.title or keyword in step.intent for keyword in keywords):
                return step
        return await repo.create_step(run_id, len(run.steps) + 1, tool_name, f"调用 {tool_name}")

    def _normalize_tool_output(self, tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(output)
        normalized["tool_name"] = tool_name
        return normalized
