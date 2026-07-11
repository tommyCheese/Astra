import json
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.config import Settings
from app.repositories.runs import RunRepository
from app.runner.model_client import ModelClient, ModelOutputError
from app.schemas.agent import AgentObservation, FinalAnswer, VerificationReport
from app.tools.base import ToolExecutionError, ToolRegistry


class ToolRouter:
    def __init__(self, registry: ToolRegistry, allowed_tools: Optional[set[str]] = None):
        self.registry = registry
        self.allowed_tools = allowed_tools or {"web_search", "web_fetch"}

    def resolve(self, tool_name: Optional[str], tool_input: Dict[str, Any]):
        if not tool_name:
            raise ToolExecutionError("invalid_decision", "Agent decision did not include a tool")
        if tool_name not in self.allowed_tools:
            raise ToolExecutionError("tool_not_allowed", f"Tool is not allowed in Web Agent mode: {tool_name}")
        tool = self.registry.get(tool_name)
        required = tool.spec.input_schema.get("required", [])
        missing = [key for key in required if not tool_input.get(key)]
        if missing:
            raise ToolExecutionError("invalid_input", f"Missing required tool input: {', '.join(missing)}")
        if tool.spec.permission != "network_read" or tool.spec.side_effect_level != "read_only":
            raise ToolExecutionError("permission_denied", f"Tool permission is not allowed: {tool_name}")
        return tool


class ContextAssembler:
    def __init__(self, repo: RunRepository):
        self.repo = repo

    async def assemble(
        self,
        *,
        run_id: str,
        goal: str,
        tool_registry: ToolRegistry,
        observations: List[Dict[str, Any]],
        evidence_pack: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        memories = await self.repo.list_memories(run_id=run_id, min_confidence=0.0, limit=8)
        return {
            "run_id": run_id,
            "goal": goal,
            "tool_manifests": {
                name: spec.model_dump() for name, spec in tool_registry.specs().items()
            },
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
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not self.settings.agent_memory_write_enabled:
            return []
        candidates = await self.model_client.extract_memory_candidates(goal, context)
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
    def verify(self, final_answer: FinalAnswer, evidence_pack: Dict[str, Any]) -> VerificationReport:
        fetched_sources = evidence_pack.get("fetched_sources", [])
        low_quality = [
            source for source in fetched_sources if float(source.get("quality_score") or 0) < 0.5
        ]
        notes = list(final_answer.verification_notes)
        status = "completed"
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
            notes=notes,
        )


class AgentLoop:
    def __init__(
        self,
        settings: Settings,
        *,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
    ):
        self.settings = settings
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.router = ToolRouter(tool_registry)

    async def run(
        self,
        repo: RunRepository,
        run_id: str,
        goal: str,
    ) -> Dict[str, Any]:
        assembler = ContextAssembler(repo)
        memory_manager = MemoryManager(self.settings, repo, self.model_client)
        verifier = VerificationEngine()
        observations: List[Dict[str, Any]] = []
        tool_outputs: List[Dict[str, Any]] = []
        filtered_candidates: List[Dict[str, Any]] = []
        fetched_sources: List[Dict[str, Any]] = []
        failed_sources: List[Dict[str, Any]] = []
        search_warnings: List[str] = []
        dedupe: Dict[str, Any] = {}
        tool_call_count = 0
        retry_counts: Dict[str, int] = {}
        final_turn_id: Optional[str] = None

        for turn_index in range(1, self.settings.agent_max_turns + 1):
            context = await assembler.assemble(
                run_id=run_id,
                goal=goal,
                tool_registry=self.tool_registry,
                observations=observations,
            )
            try:
                decision = await self.model_client.decide(goal, context)
            except ModelOutputError as exc:
                decision = None
                observation = AgentObservation(
                    kind="model_error",
                    status="failed",
                    summary="模型决策输出无法解析。",
                    error={"category": "model_output_error", "message": str(exc)},
                )
                observations.append(observation.model_dump())
                reflection = await self.model_client.reflect(
                    goal,
                    {"last_observation": observation.model_dump(), "retry_count": 0},
                )
                turn = await repo.create_agent_turn(
                    run_id,
                    turn_index,
                    "reflect",
                    reflection.summary,
                    decision={"decision_type": "reflect"},
                    memory_reads=context["memory_reads"],
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="completed",
                    observation=observation.model_dump(),
                    reflection=reflection.model_dump(),
                )
                await repo.add_event(run_id, "reflection.created", reflection.model_dump())
                await repo.session.commit()
                continue

            turn = await repo.create_agent_turn(
                run_id,
                turn_index,
                decision.decision_type,
                decision.reasoning_summary,
                selected_tool=decision.tool_name,
                decision=decision.model_dump(),
                memory_reads=context["memory_reads"],
            )

            if decision.decision_type == "finalize":
                final_turn_id = turn.id
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
                break

            if decision.decision_type != "call_tool":
                observation = AgentObservation(
                    kind="agent_state",
                    status=decision.decision_type,
                    summary=decision.reasoning_summary,
                )
                observations.append(observation.model_dump())
                await repo.update_agent_turn(turn.id, status="completed", observation=observation.model_dump())
                continue

            if tool_call_count >= self.settings.agent_max_tool_calls:
                observation = AgentObservation(
                    kind="limit",
                    status="blocked",
                    summary="已达到最大工具调用次数。",
                    data={"max_tool_calls": self.settings.agent_max_tool_calls},
                )
                observations.append(observation.model_dump())
                await repo.update_agent_turn(turn.id, status="blocked", observation=observation.model_dump())
                break

            try:
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
                try:
                    output = await tool.run(decision.tool_input)
                except ToolExecutionError as exc:
                    await repo.finish_tool_call(call.id, error=exc.to_payload())
                    raise
                await repo.finish_tool_call(call.id, output=output)
                tool_call_count += 1
                output = self._normalize_tool_output(tool.spec.name, output)
                tool_outputs.append(output)
                if tool.spec.name == "web_search":
                    filtered_candidates, dedupe = filter_candidates(output.get("candidates", []))
                    output["candidates"] = filtered_candidates
                    output["dedupe"] = dedupe
                    search_warnings = output.get("warnings", [])
                    await repo.update_step(
                        step.id,
                        "completed" if filtered_candidates else "failed",
                        evidence={
                            "candidate_count": output.get("candidate_count", len(filtered_candidates)),
                            "deduped_count": len(filtered_candidates),
                            "warnings": search_warnings,
                        },
                    )
                elif tool.spec.name == "web_fetch":
                    fetched_sources.append(output)
                    await repo.update_step(
                        step.id,
                        "completed",
                        evidence={"fetched_count": len(fetched_sources), "last_quality": output.get("quality_score")},
                    )
                observation = AgentObservation(
                    kind="tool_result",
                    status="succeeded",
                    summary=f"{tool.spec.name} completed",
                    data={"tool_name": tool.spec.name, **output},
                )
                observations.append(observation.model_dump())
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
                )
            except ToolExecutionError as exc:
                retry_counts[decision.tool_name or "unknown"] = retry_counts.get(decision.tool_name or "unknown", 0) + 1
                observation = AgentObservation(
                    kind="tool_error",
                    status="failed",
                    summary=f"{decision.tool_name} failed",
                    error=exc.to_payload(),
                    data={"tool_name": decision.tool_name, "retry_count": retry_counts[decision.tool_name or "unknown"]},
                )
                observations.append(observation.model_dump())
                if decision.tool_name == "web_fetch":
                    failed_sources.append(
                        {
                            "url": decision.tool_input.get("url"),
                            "category": exc.category,
                            "message": exc.message,
                        }
                    )
                reflection = await self.model_client.reflect(
                    goal,
                    {
                        "last_observation": observation.model_dump(),
                        "retry_count": retry_counts[decision.tool_name or "unknown"],
                    },
                )
                await repo.update_agent_turn(
                    turn.id,
                    status="failed",
                    observation=observation.model_dump(),
                    reflection=reflection.model_dump(),
                )
                await repo.add_event(run_id, "reflection.created", reflection.model_dump())
                await repo.session.commit()
                if retry_counts[decision.tool_name or "unknown"] >= self.settings.agent_per_tool_retry_limit:
                    break

        evidence_pack = build_evidence_pack(
            goal,
            filtered_candidates,
            fetched_sources,
            failed_sources,
            dedupe,
            search_warnings,
        )
        artifact = await repo.create_artifact(
            run_id,
            "evidence_pack",
            content_ref=json.dumps(evidence_pack, ensure_ascii=False),
            metadata={
                "format": "json",
                "audited_sources": len(evidence_pack["fetched_sources"]),
                "failed_sources": len(failed_sources),
            },
        )
        evidence_pack["artifact_id"] = artifact.id
        final_context = {
            "run_id": run_id,
            "observations": observations,
            "tool_outputs": tool_outputs,
            "evidence_pack": evidence_pack,
        }
        final_answer = await self.model_client.finalize(goal, final_context)
        memory_writes = await memory_manager.write_candidates(
            run_id=run_id,
            goal=goal,
            context=final_context,
        )
        report = verifier.verify(final_answer, evidence_pack)
        result = final_answer.model_dump()
        result["verification_report"] = report.model_dump()
        result["audit_refs"] = {
            "evidence_pack_artifact_id": artifact.id,
            "agent_turn_count": len(observations) + (1 if final_turn_id else 0),
        }
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
            )
        await repo.add_event(run_id, "verification.created", report.model_dump())
        await repo.session.commit()
        return {"answer": final_answer, "result": result, "status": report.status}

    async def _step_for_tool(self, repo: RunRepository, run_id: str, tool_name: str):
        run = await repo.require_run(run_id)
        keywords = ["搜索"] if tool_name == "web_search" else ["抓取"]
        for step in sorted(run.steps, key=lambda item: item.index):
            if tool_name in step.intent or tool_name in step.title:
                return step
            if any(keyword in step.title or keyword in step.intent for keyword in keywords):
                return step
        return await repo.create_step(run_id, len(run.steps) + 1, tool_name, f"调用 {tool_name}")

    def _normalize_tool_output(self, tool_name: str, output: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(output)
        normalized["tool_name"] = tool_name
        return normalized


def filter_candidates(candidates: List[Dict[str, Any]]):
    filtered: List[Dict[str, Any]] = []
    seen: set[str] = set()
    skipped: List[Dict[str, Any]] = []
    for candidate in candidates:
        url = candidate.get("url", "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            skipped.append({"url": url, "reason": "unsupported_url"})
            continue
        if parsed.path.lower().endswith((".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov")):
            skipped.append({"url": url, "reason": "unsupported_content_type"})
            continue
        canonical = canonical_url(url)
        if canonical in seen:
            skipped.append({"url": url, "reason": "duplicate"})
            continue
        seen.add(canonical)
        enriched = dict(candidate)
        enriched["canonical_url"] = canonical
        filtered.append(enriched)
    return filtered, {
        "candidate_count": len(candidates),
        "deduped_count": len(filtered),
        "skipped_count": len(skipped),
        "skipped": skipped[:8],
    }


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), normalized_path, "", urlencode(query), ""))


def build_evidence_pack(
    goal: str,
    candidates: List[Dict[str, Any]],
    fetched_sources: List[Dict[str, Any]],
    failed_sources: List[Dict[str, Any]],
    dedupe: Dict[str, Any],
    search_warnings: List[str],
) -> Dict[str, Any]:
    warnings = list(search_warnings)
    for source in fetched_sources:
        warnings.extend(source.get("warnings", []))
    if not fetched_sources:
        warnings.append("没有可用于总结的成功抓取来源。")
    return {
        "query": goal,
        "candidates": candidates,
        "fetched_sources": fetched_sources,
        "failed_sources": failed_sources,
        "dedupe": dedupe,
        "warnings": warnings,
    }
