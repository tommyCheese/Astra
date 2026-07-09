from typing import Any, Dict, List, Optional

from app.core.config import Settings
from app.db.session import SessionLocal
from app.repositories.runs import RunRepository
from app.runner.model_client import ModelConfigurationError, ModelOutputError, build_model_client
from app.schemas.agent import FinalAnswer, PlanOutput
from app.tools.base import ToolExecutionError, ToolRegistry
from app.tools.web import build_web_registry


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
        async with SessionLocal() as session:
            repo = RunRepository(session)
            try:
                await self._run_with_repo(repo, run_id)
            except (ModelConfigurationError, ModelOutputError) as exc:
                await repo.update_run_status(run_id, "blocked", summary=str(exc))
            except Exception as exc:
                await repo.update_run_status(
                    run_id,
                    "failed",
                    summary=f"Run failed: {exc}",
                    result={
                        "summary": "运行失败。",
                        "findings": [],
                        "sources": [],
                        "caveats": [str(exc)],
                        "verification_notes": ["运行未能完成。"],
                    },
                )

    async def _run_with_repo(self, repo: RunRepository, run_id: str) -> None:
        run = await repo.require_run(run_id)
        goal = run.task.description

        await repo.update_run_status(run_id, "planning")
        plan = await self.model_client.plan(goal)
        await self._persist_plan(repo, run_id, plan)

        await repo.update_run_status(run_id, "executing")
        tool_outputs = await self._execute_web_query(repo, run_id, goal)

        await repo.update_run_status(run_id, "synthesizing")
        synth_step = await self._mark_named_step_running(repo, run_id, "综合")
        final_answer = await self.model_client.synthesize(goal, tool_outputs)
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
            raise ModelOutputError("Plan contains no steps")
        for index, step in enumerate(plan.steps, start=1):
            await repo.create_step(
                run_id,
                index,
                step.title,
                step.intent,
                depends_on=[],
            )

    async def _execute_web_query(
        self,
        repo: RunRepository,
        run_id: str,
        goal: str,
    ) -> List[Dict[str, Any]]:
        run = await repo.require_run(run_id)
        steps = sorted(run.steps, key=lambda item: item.index)
        search_step = self._find_step(steps, "web_search") or steps[0]
        fetch_step = self._find_step(steps, "web_fetch") or (steps[1] if len(steps) > 1 else search_step)

        await repo.update_step(search_step.id, "running")
        search_output = await self._execute_tool(repo, run_id, search_step.id, "web_search", {"query": goal})
        candidates = search_output.get("candidates", [])
        await repo.update_step(
            search_step.id,
            "completed" if candidates else "failed",
            evidence={"candidate_count": len(candidates)},
        )

        tool_outputs: List[Dict[str, Any]] = [search_output]
        await repo.update_step(fetch_step.id, "running")
        fetch_failures = 0
        for candidate in candidates[:3]:
            url = candidate.get("url")
            if not url:
                continue
            try:
                output = await self._execute_tool(repo, run_id, fetch_step.id, "web_fetch", {"url": url})
                tool_outputs.append(output)
            except ToolExecutionError:
                fetch_failures += 1

        fetched_count = len([output for output in tool_outputs if "content" in output])
        await repo.update_step(
            fetch_step.id,
            "completed" if fetched_count else "failed",
            evidence={"fetched_count": fetched_count, "failed_count": fetch_failures},
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
        fetched_sources = [output for output in tool_outputs if output.get("content")]
        result = final_answer.model_dump()
        if not fetched_sources:
            result["verification_notes"].append("没有成功抓取到可用来源。")
            result["caveats"].append("证据不足，无法完整回答查询。")
            return result, "completed_with_warnings"
        if not final_answer.sources:
            result["verification_notes"].append("最终答案缺少来源引用。")
            return result, "completed_with_warnings"
        result["verification_notes"].append("至少一个抓取来源支撑了最终答案。")
        return result, "completed"

    def _find_step(self, steps, tool_name: str):
        for step in steps:
            if tool_name in step.intent or tool_name in step.title:
                return step
        return None

    async def _mark_named_step_running(self, repo: RunRepository, run_id: str, name_part: str):
        run = await repo.require_run(run_id)
        for step in sorted(run.steps, key=lambda item: item.index):
            if name_part in step.title or name_part in step.intent:
                await repo.update_step(step.id, "running")
                return step
        return None


async def start_run_in_process(run_id: str, settings: Settings) -> None:
    engine = RunEngine(settings)
    await engine.run(run_id)
