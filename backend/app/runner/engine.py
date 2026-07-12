import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from app.core.config import Settings
from app.db.session import SessionLocal
from app.repositories.runs import RunRepository
from app.runner.agent_loop import AgentLoop
from app.runner.model_client import ModelConfigurationError, ModelOutputError, build_model_client
from app.schemas.agent import FinalAnswer, PlanOutput, PlanStep
from app.schemas.agent import AgentState, ReasoningPolicySnapshot
from app.runner.reasoning import build_default_contract, build_plan_graph, normalize_contract, validate_contract
from app.tools.base import ToolExecutionError, ToolRegistry
from app.tools.web import build_web_registry
from app.core.errors import run_error_from_exception

logger = logging.getLogger("astra.engine")


class RunEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        model_client=None,
        tool_registry: Optional[ToolRegistry] = None,
    ):
        self.settings = settings
        self.model_client = model_client or build_model_client(settings)
        self.tool_registry = tool_registry or build_web_registry(settings)

    async def run(self, run_id: str) -> None:
        logger.info("run.engine.start run_id=%s provider=%s model=%s", run_id, self.settings.model_provider, self.settings.model_name)
        async with SessionLocal() as session:
            repo = RunRepository(session)
            try:
                await self._run_with_repo(repo, run_id)
            except (ModelConfigurationError, ModelOutputError, httpx.RequestError) as exc:
                logger.exception("run.engine.model_error run_id=%s cause=%s", run_id, str(exc))
                error = run_error_from_exception(exc)
                await repo.add_event(run_id, "run.error", error)
                await repo.update_run_status(run_id, "blocked", summary=error["message"], result=error_result(error))
            except Exception as exc:
                logger.exception("run.engine.failed run_id=%s cause=%s", run_id, type(exc).__name__)
                error = run_error_from_exception(exc)
                await repo.add_event(run_id, "run.error", error)
                await repo.update_run_status(
                    run_id,
                    "failed",
                    summary=error["message"],
                    result={
                        "summary": error["message"],
                        "findings": [],
                        "sources": [],
                        "caveats": [],
                        "verification_notes": ["运行未能完成。"],
                        "error": error,
                    },
                )

    async def _run_with_repo(self, repo: RunRepository, run_id: str) -> None:
        run = await repo.require_run(run_id)
        current_goal = run.model_policy.get("conversation_goal", run.task.description)
        conversation_runs = await repo.list_task_runs(run.task_id)
        previous_runs = [item for item in conversation_runs if item.id != run.id][-6:]
        if previous_runs:
            context_lines = []
            for item in previous_runs:
                previous_goal = item.model_policy.get("conversation_goal", "")
                context_lines.extend([f"User: {previous_goal}", f"Assistant: {item.summary or ''}"])
            goal = "Conversation context:\n" + "\n".join(context_lines) + f"\nCurrent user request: {current_goal}"
        else:
            goal = current_goal

        if run.state_version and run.agent_state:
            await repo.update_run_status(run_id, "executing")
            if self.settings.agent_use_loop and self.settings.agent_use_general_runtime:
                agent_loop = AgentLoop(self.settings, model_client=self.model_client, tool_registry=self.tool_registry)
                await self._start_answer_stream(repo, run_id)
                loop_result = await agent_loop.run(repo, run_id, goal, on_answer_delta=lambda delta: self._handle_answer_delta(repo, run_id, delta))
                await self._complete_answer_stream(repo, run_id, loop_result["answer"].summary)
                await repo.update_run_status(run_id, loop_result["status"], summary=loop_result["answer"].summary, result=loop_result["result"])
                return

        logger.info("run.phase run_id=%s phase=planning", run_id)
        await repo.update_run_status(run_id, "planning")
        initial_snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        planning_strategy = initial_snapshot.effective.planning_strategy.value
        plan_only = initial_snapshot.effective.execution_mode.value == "plan_only"
        if planning_strategy == "direct" and not plan_only:
            contract_result = build_default_contract(goal)
            plan_result = PlanOutput(
                steps=[PlanStep(title="处理请求", intent="根据任务需要直接回答或选择工具")],
                success_criteria=["正确回应用户当前请求"],
                risk_level="low",
            )
            logger.info("run.plan.direct_start run_id=%s", run_id)
        elif planning_strategy == "adaptive" and not plan_only:
            if self.settings.agent_use_general_runtime:
                try:
                    contract_result = await self.model_client.contract(goal)
                except ModelOutputError as exc:
                    contract_result = exc
            else:
                contract_result = build_default_contract(goal)
            plan_result = PlanOutput(
                steps=[PlanStep(title="自适应处理", intent="根据观察决定直接回答、调用工具、反思或重新规划")],
                success_criteria=["正确回应用户当前请求"],
                risk_level="low",
            )
            logger.info("run.plan.adaptive_start run_id=%s", run_id)
        elif self.settings.agent_use_general_runtime:
            contract_result, plan_result = await asyncio.gather(
                self.model_client.contract(goal),
                self.model_client.plan(goal),
                return_exceptions=True,
            )
        else:
            contract_result, plan_result = None, await self.model_client.plan(goal)
        if isinstance(contract_result, Exception):
            if not isinstance(contract_result, ModelOutputError):
                raise contract_result
            logger.warning("run.contract.fallback run_id=%s reason=%s", run_id, str(contract_result))
            contract = build_default_contract(goal)
        else:
            contract = contract_result
        if contract:
            contract = normalize_contract(contract, goal)
            try:
                validate_contract(contract)
            except ValueError as exc:
                raise ModelOutputError(f"Invalid task contract: {exc}") from exc
        if isinstance(plan_result, Exception):
            if not isinstance(plan_result, ModelOutputError):
                raise plan_result
            logger.warning("run.plan.fallback run_id=%s reason=%s", run_id, str(plan_result))
            plan = PlanOutput(
                steps=[PlanStep(title="生成回复", intent="直接回应用户当前请求")],
                success_criteria=["正确回应用户当前请求"],
                risk_level="low",
            )
        else:
            plan = plan_result
        logger.info("run.plan.ready run_id=%s steps=%s tools=%s", run_id, len(plan.steps), len(plan.required_tools))
        await self._persist_plan(repo, run_id, plan)
        run = await repo.require_run(run_id)
        snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        if snapshot.effective.execution_mode.value == "plan_only":
            summary = "规划已生成，未执行工具或外部操作。"
            result = {
                "summary": summary,
                "findings": [{"text": f"{step.title}：{step.intent}", "source_urls": []} for step in plan.steps],
                "sources": [],
                "failed_sources": [],
                "source_quality": [],
                "conflicts": [],
                "caveats": ["当前运行使用仅规划模式。"],
                "verification_notes": ["已在执行前停止。"],
            }
            await self._complete_pending_steps(repo, run_id)
            await self._emit_answer_stream(repo, run_id, summary)
            await repo.update_run_status(run_id, "completed", summary=summary, result=result)
            logger.info("run.complete run_id=%s status=completed mode=plan_only steps=%s", run_id, len(plan.steps))
            return
        if contract:
            graph = build_plan_graph(contract, snapshot.effective.planning_strategy, [step.model_dump() for step in plan.steps])
            state = AgentState(task_contract=contract, policy_version=snapshot.version, plan=graph)
        if contract and not run.state_version:
            await repo.initialize_reasoning_state(
                run_id,
                task_contract=contract.model_dump(mode="json"),
                plan_graph=graph.model_dump(mode="json"),
                agent_state=state.model_dump(mode="json"),
            )
        if contract and contract.ambiguity_status != "clear":
            await repo.set_waiting_state(run_id, {
                "paused_node": "build_contract",
                "state_version": state.version,
                "plan_version": graph.version,
                "request": contract.clarification_question,
            })
            return

        logger.info("run.phase run_id=%s phase=executing", run_id)
        await repo.update_run_status(run_id, "executing")
        if self.settings.agent_use_loop and self.settings.agent_use_general_runtime:
            agent_loop = AgentLoop(
                self.settings,
                model_client=self.model_client,
                tool_registry=self.tool_registry,
            )
            await self._start_answer_stream(repo, run_id)
            loop_result = await agent_loop.run(repo, run_id, goal, on_answer_delta=lambda delta: self._handle_answer_delta(repo, run_id, delta))
            await self._complete_answer_stream(repo, run_id, loop_result["answer"].summary)
            final_answer = loop_result["answer"]
            result = loop_result["result"]
            status = loop_result["status"]

            await repo.update_run_status(run_id, "synthesizing")
            synth_step = await self._mark_named_step_running(repo, run_id, "综合")
            await repo.create_artifact(
                run_id,
                "final_answer",
                content_ref=final_answer.model_dump_json(),
                metadata={"format": "json"},
            )
            if synth_step is not None:
                await repo.update_step(
                    synth_step.id,
                    "completed",
                    evidence={"finding_count": len(final_answer.findings), "handled_by": "agent_loop"},
                )

            await repo.update_run_status(run_id, "verifying")
            verify_step = await self._mark_named_step_running(repo, run_id, "验证")
            if verify_step is not None:
                report = result.get("verification_report", {})
                await repo.update_step(
                    verify_step.id,
                    "completed",
                    evidence={
                        "status": report.get("status", status),
                        "source_count": report.get("source_count", len(result.get("sources", []))),
                        "caveat_count": report.get("caveat_count", len(result.get("caveats", []))),
                    },
                )
            await self._complete_pending_steps(repo, run_id)
            await repo.update_run_status(
                run_id,
                status,
                summary=final_answer.summary,
                result=result,
            )
            logger.info("run.complete run_id=%s status=%s findings=%s sources=%s", run_id, status, len(final_answer.findings), len(final_answer.sources))
            return

        tool_outputs = await self._execute_web_query(repo, run_id, goal)

        await repo.update_run_status(run_id, "synthesizing")
        synth_step = await self._mark_named_step_running(repo, run_id, "综合")
        await self._start_answer_stream(repo, run_id)
        final_answer = await self.model_client.synthesize(goal, tool_outputs, on_delta=lambda delta: self._answer_delta(repo, run_id, delta))
        await self._complete_answer_stream(repo, run_id, final_answer.summary)
        await repo.create_artifact(
            run_id,
            "final_answer",
            content_ref=final_answer.model_dump_json(),
            metadata={"format": "json"},
        )
        if synth_step is not None:
            await repo.update_step(
                synth_step.id,
                "completed",
                evidence={"finding_count": len(final_answer.findings)},
            )

        await repo.update_run_status(run_id, "verifying")
        verify_step = await self._mark_named_step_running(repo, run_id, "验证")
        result, status = self._verify(final_answer, tool_outputs)
        if verify_step is not None:
            await repo.update_step(
                verify_step.id,
                "completed",
                evidence={
                    "status": status,
                    "source_count": len(result.get("sources", [])),
                    "caveat_count": len(result.get("caveats", [])),
                },
            )
        await repo.update_run_status(
            run_id,
            status,
            summary=final_answer.summary,
            result=result,
        )

    async def _persist_plan(self, repo: RunRepository, run_id: str, plan: PlanOutput) -> None:
        if not plan.steps:
            plan = plan.model_copy(update={"steps": [PlanStep(title="生成回复", intent="直接回应用户请求")]})
        for index, step in enumerate(plan.steps, start=1):
            await repo.create_step(
                run_id,
                index,
                step.title,
                step.intent,
                depends_on=[],
            )

    async def _emit_answer_stream(self, repo: RunRepository, run_id: str, content: str) -> None:
        await self._start_answer_stream(repo, run_id)
        await self._answer_delta(repo, run_id, content)
        await self._complete_answer_stream(repo, run_id, content)

    async def _start_answer_stream(self, repo: RunRepository, run_id: str) -> None:
        await repo.add_event(run_id, "answer.started", {"role": "assistant", "mode": "native"})
        await repo.session.commit()

    async def _answer_delta(self, repo: RunRepository, run_id: str, delta: str) -> None:
        if not delta:
            return
        await repo.add_event(run_id, "answer.delta", {"delta": delta})
        await repo.session.commit()

    async def _handle_answer_delta(self, repo: RunRepository, run_id: str, delta: str) -> None:
        if delta == "\0":
            await self._start_answer_stream(repo, run_id)
            return
        await self._answer_delta(repo, run_id, delta)

    async def _complete_answer_stream(self, repo: RunRepository, run_id: str, content: str) -> None:
        await repo.add_event(run_id, "answer.completed", {"content": content})
        await repo.session.commit()

    async def _execute_web_query(
        self,
        repo: RunRepository,
        run_id: str,
        goal: str,
    ) -> List[Dict[str, Any]]:
        run = await repo.require_run(run_id)
        steps = sorted(run.steps, key=lambda item: item.index)
        search_step = self._find_step(steps, "web_search") or steps[0]
        filter_step = self._find_step(steps, "筛选") or self._find_step(steps, "去重")
        fetch_step = self._find_step(steps, "web_fetch") or self._find_step(steps, "抓取")
        evidence_step = self._find_step(steps, "证据包")
        fetch_step = fetch_step or (steps[2] if len(steps) > 2 else search_step)

        await repo.update_step(search_step.id, "running")
        search_output = await self._execute_tool(repo, run_id, search_step.id, "web_search", {"query": goal})
        candidates = search_output.get("candidates", [])
        await repo.update_step(
            search_step.id,
            "completed" if candidates else "failed",
            evidence={
                "provider": search_output.get("provider"),
                "candidate_count": len(candidates),
                "warnings": search_output.get("warnings", []),
            },
        )

        tool_outputs: List[Dict[str, Any]] = [search_output]
        if filter_step is not None:
            await repo.update_step(filter_step.id, "running")
        filtered_candidates, dedupe = self._filter_candidates(candidates)
        if filter_step is not None:
            await repo.update_step(
                filter_step.id,
                "completed" if filtered_candidates else "failed",
                evidence=dedupe,
            )

        await repo.update_step(fetch_step.id, "running")
        failed_sources: List[Dict[str, Any]] = []
        for candidate in filtered_candidates[: self.settings.google_search_result_count]:
            url = candidate.get("url")
            if not url:
                continue
            fetch_input = {
                "url": url,
                "query": goal,
                "snippet": candidate.get("snippet", ""),
                "crawler_plan": self._crawler_plan_for(candidate),
            }
            try:
                output = await self._execute_tool(repo, run_id, fetch_step.id, "web_fetch", fetch_input)
                tool_outputs.append(output)
            except ToolExecutionError as exc:
                failed_sources.append(
                    {
                        "url": url,
                        "title": candidate.get("title"),
                        "category": exc.category,
                        "message": exc.message,
                    }
                )

        fetched_count = len([output for output in tool_outputs if "content" in output])
        await repo.update_step(
            fetch_step.id,
            "completed" if fetched_count else "failed",
            evidence={"fetched_count": fetched_count, "failed_count": len(failed_sources)},
        )
        if evidence_step is not None:
            await repo.update_step(evidence_step.id, "running")
        evidence_pack = self._build_evidence_pack(
            goal,
            filtered_candidates,
            [output for output in tool_outputs if output.get("content")],
            failed_sources,
            dedupe,
            search_output.get("warnings", []),
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
        tool_outputs.append({"evidence_pack": evidence_pack})
        if evidence_step is not None:
            await repo.update_step(
                evidence_step.id,
                "completed" if evidence_pack["fetched_sources"] else "failed",
                evidence={
                    "artifact_id": artifact.id,
                    "source_count": len(evidence_pack["fetched_sources"]),
                    "warning_count": len(evidence_pack["warnings"]),
                },
            )
        return tool_outputs

    async def _execute_tool(
        self,
        repo: RunRepository,
        run_id: str,
        step_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        tool = self.tool_registry.get(tool_name)
        call = await repo.start_tool_call(
            run_id,
            step_id,
            tool.spec.name,
            tool.spec.version,
            tool_input,
            tool.spec.permission,
            tool.spec.side_effect_level,
        )
        try:
            output = await tool.run(tool_input)
        except ToolExecutionError as exc:
            await repo.finish_tool_call(call.id, error=exc.to_payload())
            raise
        except Exception as exc:
            await repo.finish_tool_call(
                call.id,
                error={"category": "unexpected_error", "message": str(exc)},
            )
            raise ToolExecutionError("unexpected_error", str(exc)) from exc
        await repo.finish_tool_call(call.id, output=output)
        return output

    def _verify(self, final_answer: FinalAnswer, tool_outputs: List[Dict[str, Any]]):
        evidence_pack = next(
            (output.get("evidence_pack") for output in tool_outputs if output.get("evidence_pack")),
            {},
        )
        fetched_sources = evidence_pack.get("fetched_sources") or [
            output for output in tool_outputs if output.get("content")
        ]
        result = final_answer.model_dump()
        if not fetched_sources:
            result["verification_notes"].append("没有成功抓取到可用来源。")
            result["caveats"].append("证据不足，无法完整回答查询。")
            return result, "completed_with_warnings"
        low_quality = [
            source for source in fetched_sources if float(source.get("quality_score") or 0) < 0.5
        ]
        if low_quality:
            result["verification_notes"].append("部分来源质量较低，已在 source_quality 中标记。")
        if not final_answer.sources:
            result["verification_notes"].append("最终答案缺少来源引用。")
            return result, "completed_with_warnings"
        if evidence_pack.get("failed_sources"):
            result["verification_notes"].append("部分来源抓取失败，已在 failed_sources 中记录。")
        result["verification_notes"].append("至少一个抓取来源支撑了最终答案。")
        status = "completed_with_warnings" if low_quality or evidence_pack.get("failed_sources") else "completed"
        return result, status

    def _find_step(self, steps, tool_name: str):
        for step in steps:
            if tool_name in step.intent or tool_name in step.title:
                return step
        return None

    def _filter_candidates(self, candidates: List[Dict[str, Any]]):
        filtered: List[Dict[str, Any]] = []
        seen: set[str] = set()
        skipped: List[Dict[str, Any]] = []
        for candidate in candidates:
            url = candidate.get("url", "")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                skipped.append({"url": url, "reason": "unsupported_url"})
                continue
            if self._looks_like_binary(parsed.path):
                skipped.append({"url": url, "reason": "unsupported_content_type"})
                continue
            canonical = self._canonical_url(url)
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

    def _canonical_url(self, url: str) -> str:
        parsed = urlparse(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
        ]
        normalized_path = parsed.path.rstrip("/") or "/"
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                normalized_path,
                "",
                urlencode(query),
                "",
            )
        )

    def _looks_like_binary(self, path: str) -> bool:
        return path.lower().endswith((".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov"))

    def _crawler_plan_for(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        metadata = candidate.get("metadata") or {}
        mime = str(metadata.get("mime") or "")
        if "pdf" in mime:
            return {"strategy": "plain_text", "selectors": [], "exclude_selectors": [], "target": "document"}
        if metadata.get("kind") == "article":
            return {"strategy": "readability", "selectors": ["article", "main"], "exclude_selectors": [], "target": "article"}
        return {"strategy": "readability", "selectors": ["main", "article"], "exclude_selectors": [], "target": "main_content"}

    def _build_evidence_pack(
        self,
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

    async def _mark_named_step_running(self, repo: RunRepository, run_id: str, name_part: str):
        run = await repo.require_run(run_id)
        for step in sorted(run.steps, key=lambda item: item.index):
            if name_part in step.title or name_part in step.intent:
                await repo.update_step(step.id, "running")
                return step
        return None

    async def _complete_pending_steps(self, repo: RunRepository, run_id: str) -> None:
        run = await repo.require_run(run_id)
        for step in sorted(run.steps, key=lambda item: item.index):
            if step.status in {"pending", "running"}:
                await repo.update_step(
                    step.id,
                    "completed",
                    evidence={"handled_by": "agent_loop"},
                )


async def start_run_in_process(run_id: str, settings: Settings) -> None:
    try:
        engine = RunEngine(settings)
    except ModelConfigurationError as exc:
        logger.exception("run.engine.configuration_error run_id=%s", run_id)
        async with SessionLocal() as session:
            repo = RunRepository(session)
            error = run_error_from_exception(exc)
            await repo.add_event(run_id, "run.error", error)
            await repo.update_run_status(run_id, "blocked", summary=error["message"], result=error_result(error))
        return
    await engine.run(run_id)


def error_result(error: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "summary": error["message"],
        "findings": [],
        "sources": [],
        "failed_sources": [],
        "source_quality": [],
        "conflicts": [],
        "caveats": [],
        "verification_notes": ["运行未能完成。"],
        "error": error,
    }
