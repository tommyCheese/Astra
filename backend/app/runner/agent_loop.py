import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.artifacts import ArtifactService, LocalArtifactStore
from app.core.config import Settings
from app.repositories.runs import RunRepository
from app.runner.adapters import ChartTaskAdapter, ProcessorRegistry, WebTaskAdapter
from app.runner.model_client import ModelClient, ModelOutputError
from app.runner.reasoning import (
    CompletionGate,
    ObservationEvaluator,
    ReflectionGate,
    apply_reflection_patch,
    failure_fingerprint,
)
from app.sandbox.docker_provider import build_sandbox_provider
from app.sandbox.runtime import SandboxJobService, SandboxSupervisor
from app.schemas.agent import (
    AgentObservation,
    AgentState,
    CriterionStatus,
    FinalAnswer,
    ReasoningPolicySnapshot,
    TerminalState,
    VerificationReport,
)
from app.tools.base import (
    CapabilityAvailability,
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
)

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


class ToolRouter:
    def __init__(
        self,
        registry: ToolRegistry,
        allowed_tools: set[str] | None = None,
        *,
        allowed_capabilities: set[str] | None = None,
        allowed_permissions: set[str] | None = None,
        allowed_risks: set[str] | None = None,
        available_backends: set[str] | None = None,
    ):
        self.registry = registry
        self.allowed_tools = allowed_tools
        self.allowed_capabilities = allowed_capabilities or {
            "network_read",
            "sandboxed_compute",
            "artifact_write",
        }
        self.allowed_permissions = allowed_permissions or {
            "network_read",
            "sandboxed_compute",
            "artifact_write",
        }
        self.allowed_risks = allowed_risks or {"low", "sandboxed"}
        self.available_backends = available_backends or {"in_process"}

    def resolve(self, tool_name: str | None, tool_input: dict[str, Any]):
        if not tool_name:
            raise ToolExecutionError("invalid_decision", "Agent decision did not include a tool")
        tool = self.registry.get(tool_name)
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            raise ToolExecutionError("tool_not_allowed", f"Tool is not allowed: {tool_name}")
        required = tool.spec.input_schema.get("required", [])
        missing = [key for key in required if not tool_input.get(key)]
        if missing:
            raise ToolExecutionError(
                "invalid_input", f"Missing required tool input: {', '.join(missing)}"
            )
        if not set(tool.spec.capabilities) <= self.allowed_capabilities:
            raise ToolExecutionError(
                "tool_not_allowed", f"Tool capability is not allowed: {tool_name}"
            )
        if not set(tool.spec.permissions) <= self.allowed_permissions:
            raise ToolExecutionError(
                "permission_denied", f"Tool permission is not allowed: {tool_name}"
            )
        if tool.spec.risk not in self.allowed_risks:
            raise ToolExecutionError("permission_denied", f"Tool risk is not allowed: {tool_name}")
        if tool.spec.execution_backend not in self.available_backends:
            raise ToolExecutionError(
                "sandbox_unavailable", f"Tool backend is unavailable: {tool.spec.execution_backend}"
            )
        return tool

    def availability(self, tool_name: str) -> CapabilityAvailability:
        try:
            spec = self.registry.get(tool_name).spec
            probe = dict.fromkeys(spec.input_schema.get("required", []), "__manifest_probe__")
            self.resolve(tool_name, probe)
            return CapabilityAvailability(capability=tool_name, available=True)
        except ToolExecutionError as exc:
            return CapabilityAvailability(
                capability=tool_name, available=False, reason=exc.category
            )

    def eligible_specs(self):
        eligible, unavailable = {}, {}
        for name, spec in self.registry.specs().items():
            status = self.availability(name)
            if status.available:
                eligible[name] = spec
            else:
                unavailable[name] = status.model_dump()
        return eligible, unavailable


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
        specs, unavailable = tool_registry.specs(), {}
        if tool_router is not None:
            specs, unavailable = tool_router.eligible_specs()
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
            "task_contract": run.task_contract or {},
            "plan_graph": run.plan_graph or {},
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
        self, final_answer: FinalAnswer, evidence_pack: dict[str, Any]
    ) -> VerificationReport:
        fetched_sources = evidence_pack.get("fetched_sources", [])
        low_quality = [
            source for source in fetched_sources if float(source.get("quality_score") or 0) < 0.5
        ]
        notes = list(final_answer.verification_notes)
        status = "completed"
        external_evidence_attempted = bool(
            evidence_pack.get("external_evidence_attempted")
            or evidence_pack.get("candidates")
            or fetched_sources
            or evidence_pack.get("failed_sources")
        )
        if not external_evidence_attempted:
            return VerificationReport(
                status=status,
                source_count=0,
                caveat_count=len(final_answer.caveats),
                memory_references=final_answer.memory_references,
                notes=list(dict.fromkeys(notes)),
            )
        if not fetched_sources:
            status = "completed_with_warnings"
            notes.append("没有成功抓取到可用来源。")
        if low_quality:
            status = "completed_with_warnings"
            notes.append("部分来源质量较低，已在 source_quality 中标记。")
        if evidence_pack.get("failed_sources"):
            status = "completed_with_warnings"
            notes.append("部分来源抓取失败，已在 failed_sources 中记录。")
        if not final_answer.sources:
            status = "completed_with_warnings"
            notes.append("最终答案缺少来源引用。")
        if fetched_sources and final_answer.sources:
            notes.append("至少一个抓取来源支撑了最终答案。")
        return VerificationReport(
            status=status,
            source_count=len(final_answer.sources),
            caveat_count=len(final_answer.caveats),
            low_quality_sources=low_quality,
            failed_sources=evidence_pack.get("failed_sources", []),
            memory_references=final_answer.memory_references,
            notes=list(dict.fromkeys(notes)),
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
        sandbox_service = SandboxJobService(repo, SandboxSupervisor(provider), artifact_service)
        initial_run = await repo.require_run(run_id)
        policy_snapshot = ReasoningPolicySnapshot.model_validate(initial_run.reasoning_policy or {})
        policy = policy_snapshot.effective
        max_turns = min(policy.budgets.max_turns, self.settings.agent_max_turns)
        max_tool_calls = min(policy.budgets.max_tool_calls, self.settings.agent_max_tool_calls)
        max_reflections = min(policy.budgets.max_reflections, self.settings.agent_max_reflections)
        max_replans = min(policy.budgets.max_replans, self.settings.agent_max_replans)
        observations: list[dict[str, Any]] = list(
            (initial_run.agent_state or {}).get("observations", [])
        )
        tool_outputs: list[dict[str, Any]] = []
        tool_call_count = 0
        retry_counts: dict[str, int] = {}
        failed_action_counts: dict[str, int] = {}
        final_turn_id: str | None = None
        terminal_override: str | None = None
        terminal_summary: str | None = None
        streamed_final_answer: FinalAnswer | None = None
        reflection_count = 0
        replan_count = 0

        async def maybe_reflect(signal: str, reflection_context: dict[str, Any]):
            nonlocal reflection_count
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
                    plan_graph=state.plan.model_dump(mode="json"),
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

        for turn_index in range(1, max_turns + 1):
            context = await assembler.assemble(
                run_id=run_id,
                goal=goal,
                tool_registry=self.tool_registry,
                tool_router=self.router,
                observations=observations,
            )
            try:
                decision, candidate_answer = await self.model_client.decide_with_answer(
                    goal,
                    context,
                    on_delta=on_answer_delta,
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

            logger.info(
                "agent.decision run_id=%s turn=%s type=%s tool=%s confidence=%.2f",
                run_id,
                turn_index,
                decision.decision_type,
                decision.tool_name,
                decision.confidence,
            )

            idempotency_key = None
            if decision.decision_type == "call_tool":
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
            turn = await repo.create_agent_turn(
                run_id,
                turn_index,
                decision.decision_type,
                decision.reasoning_summary,
                selected_tool=decision.tool_name,
                decision=decision.model_dump(),
                memory_reads=context["memory_reads"],
                state_version_before=(await repo.require_run(run_id)).state_version,
                plan_version=((await repo.require_run(run_id)).plan_graph or {}).get("version", 1),
                phase="prepared" if decision.decision_type == "call_tool" else "created",
                idempotency_key=idempotency_key,
            )
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
                final_turn_id = turn.id
                streamed_final_answer = candidate_answer
                await repo.update_agent_turn(turn.id, status="completed")
                break

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
                break

            try:
                action_signature = json.dumps(
                    {"tool": decision.tool_name, "input": decision.tool_input},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                if (
                    failed_action_counts.get(action_signature, 0)
                    >= self.settings.agent_per_tool_retry_limit
                ):
                    raise ToolExecutionError(
                        "retry_exhausted", "Equivalent failed strategy exhausted its retry budget"
                    )
                tool = self.router.resolve(decision.tool_name, decision.tool_input)
                step = await self._step_for_tool(repo, run_id, tool.spec.name)
                await repo.update_step(step.id, "running")
                call = await repo.start_tool_call(
                    run_id,
                    step.id,
                    tool.spec.name,
                    tool.spec.version,
                    decision.tool_input,
                    tool.spec.permission,
                    tool.spec.side_effect_level,
                )
                tool_call_count += 1
                await repo.update_agent_turn(turn.id, phase="executing", tool_call_id=call.id)
                try:
                    execution_context = ToolExecutionContext(
                        run_id=run_id,
                        tool_call_id=call.id,
                        step_id=step.id,
                        trace_id=f"{run_id}:{call.id}",
                        artifact_service=artifact_service,
                        sandbox_service=sandbox_service,
                    )
                    output = await tool.run(decision.tool_input, context=execution_context)
                except ToolExecutionError as exc:
                    await repo.finish_tool_call(call.id, error=exc.to_payload())
                    raise
                await repo.finish_tool_call(call.id, output=output)
                logger.info(
                    "tool.complete run_id=%s turn=%s tool=%s call_id=%s",
                    run_id,
                    turn_index,
                    tool.spec.name,
                    call.id,
                )
                output = self._normalize_tool_output(tool.spec.name, output)
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
                await repo.update_step(step.id, "completed", evidence=step_evidence)
                observations.append(observation.model_dump())
                evaluation = self.evaluator.evaluate(
                    observation, decision.expected, decision.success_criteria_refs
                )
                await repo.add_event(
                    run_id,
                    "reasoning.evaluation_created",
                    {"turn_index": turn_index, **evaluation.model_dump(mode="json")},
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
                processor = self.processors.for_tool(decision.tool_name or "")
                if processor:
                    processor.record_failure(
                        decision.tool_name or "", decision.tool_input, exc.to_payload()
                    )
                reflection = await maybe_reflect(
                    "tool_failed",
                    {
                        "last_observation": observation.model_dump(),
                        "retry_count": retry_counts[decision.tool_name or "unknown"],
                    },
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
                await repo.add_event(
                    run_id,
                    "reasoning.failure_fingerprinted",
                    {
                        "fingerprint": fingerprint,
                        "attempt_count": failed_action_counts[action_signature],
                        "exhausted": failed_action_counts[action_signature]
                        >= self.settings.agent_per_tool_retry_limit,
                    },
                )
                await repo.session.commit()
                if (
                    retry_counts[decision.tool_name or "unknown"]
                    >= self.settings.agent_per_tool_retry_limit
                ):
                    terminal_override = "blocked"
                    terminal_summary = f"{decision.tool_name} 已达到重试上限。"
                    break

        evidence_pack = self.adapter.build_evidence(goal, self.adapter.attempted)
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
        memory_writes = await memory_manager.write_candidates(
            run_id=run_id,
            goal=goal,
            context=final_context,
        )
        report = verifier.verify(final_answer, evidence_pack)
        report.invalid_artifact_references = invalid_artifact_references
        adapter_decision = (
            self.chart_adapter.validate(final_answer.model_dump(), {})
            if self.chart_adapter.attempted and not self.adapter.attempted
            else self.adapter.validate(final_answer.model_dump(), evidence_pack)
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
            if adapter_decision.state in {
                TerminalState.completed,
                TerminalState.completed_with_warnings,
            }:
                for criterion in state.task_contract.success_criteria:
                    if criterion.mandatory:
                        criterion.status = CriterionStatus.satisfied
            state.version = run_record.state_version + 1
            run_record = await repo.update_reasoning_state(
                run_id,
                expected_version=run_record.state_version,
                agent_state=state.model_dump(mode="json"),
                plan_graph=state.plan.model_dump(mode="json"),
                waiting_state=run_record.waiting_state,
            )
            gate_decision = self.completion_gate.evaluate(
                state,
                validator_passed=adapter_decision.state
                in {TerminalState.completed, TerminalState.completed_with_warnings},
                warnings=adapter_decision.warnings,
                required_user_action=(run_record.waiting_state or {}).get("request")
                if terminal_override == "waiting_user"
                else None,
            )
        else:
            gate_decision = adapter_decision
        if terminal_override == "blocked":
            gate_decision = gate_decision.model_copy(
                update={
                    "state": TerminalState.blocked,
                    "reason": terminal_summary or gate_decision.reason,
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
        report.status = final_status
        result = final_answer.model_dump()
        result["verification_report"] = report.model_dump()
        result["audit_refs"] = {
            "evidence_pack_artifact_id": artifact.id,
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
                    "status": report.status,
                    "summary": final_answer.summary,
                },
                artifact_id=artifact.id,
                memory_writes=memory_writes,
                reflection=completion_reflection.model_dump() if completion_reflection else None,
            )
        await repo.add_event(run_id, "verification.created", report.model_dump())
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
