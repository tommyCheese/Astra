from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from app.application.fast_agent_runtime.context import FastContextBuilder
from app.application.fast_agent_runtime.catalog import FastToolCatalogBoundary
from app.application.fast_agent_runtime.finalizer import FastFinalizer
from app.application.fast_agent_runtime.recovery import FastRecovery
from app.application.fast_agent_runtime.tool_stage import FastToolStage
from app.common.schemas.agent.fast_runtime import FastAgentAction, FastExecutionResult, FastObservation
from app.common.schemas.agent.run_policy import (
    FastPendingAction,
    FastRuntimeSnapshot,
    RunExecutionProfile,
)
from app.infrastructure.model_clients.contracts import ModelClient, ModelOutputError
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.router import ToolRouter


class FastAgentExecutor:
    def __init__(self, *, settings, model_client: ModelClient, router: ToolRouter) -> None:
        self._model = model_client
        self._context = FastContextBuilder(router)
        self._tools = FastToolStage(settings, router)
        self._finalizer = FastFinalizer()
        self._recovery = FastRecovery()
        self._catalog = FastToolCatalogBoundary(router.registry)

    async def run(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        goal: str,
        *,
        active_skills: list[dict] | None = None,
        on_answer_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> FastExecutionResult:
        run = await repo.require_run_core(run_id)
        started_at = time.monotonic()
        first_token_latency_ms: int | None = None

        async def observe_answer_delta(delta: str) -> None:
            nonlocal first_token_latency_ms
            if delta and delta not in {"\0", "\1"} and first_token_latency_ms is None:
                first_token_latency_ms = int((time.monotonic() - started_at) * 1000)
            if on_answer_delta is not None:
                await on_answer_delta(delta)

        def with_metrics(result: FastExecutionResult) -> FastExecutionResult:
            return result.model_copy(
                update={
                    "first_token_latency_ms": first_token_latency_ms,
                    "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                }
            )
        profile = RunExecutionProfile.model_validate(run.execution_profile or {})
        policy = profile.fast_runtime_policy
        if policy is None:
            raise ValueError("fast-v1 Run is missing FastRuntimePolicy")
        snapshot = FastRuntimeSnapshot.model_validate(run.fast_runtime_snapshot or {})
        recovery = await self._recovery.recover(repo, run_id, snapshot)
        if recovery.snapshot.snapshot_version != snapshot.snapshot_version:
            await repo.update_fast_runtime_snapshot(
                run_id,
                expected_version=snapshot.snapshot_version,
                snapshot=recovery.snapshot,
            )
            await repo.session.commit()
        snapshot = recovery.snapshot
        observations = recovery.observations
        model_calls = 0
        tool_actions = 0
        await self._catalog.freeze(repo.session, run_id)
        await repo.update_run_status(run_id, "executing", loaded_run=run)
        await repo.add_event(run_id, "fast.started", {"runtime": "fast-v1", "version": 1})
        await repo.session.commit()
        approved_tool_call = recovery.approved_tool_call
        recovered_action = recovery.replay_action
        if recovery.waiting_for_approval:
            result = with_metrics(
                FastExecutionResult(
                    status="waiting_user",
                    answer="等待批准先前的工具调用。",
                    observations=observations,
                    model_call_count=0,
                    tool_action_count=0,
                )
            )
            await self._finalizer.persist(repo, run_id, result)
            return result
        if recovery.result_unknown:
            result = with_metrics(
                FastExecutionResult(
                    status="waiting_user",
                    answer=observations[-1].summary,
                    observations=observations,
                    model_call_count=0,
                    tool_action_count=0,
                )
            )
            await self._finalizer.persist(repo, run_id, result)
            return result
        if approved_tool_call is not None:
            await repo.add_event(
                run_id,
                "fast.recovery.resumed",
                {"pending_action": "approval", "tool_call_id": approved_tool_call.id},
            )
            await repo.session.commit()
        for turn_index in range(snapshot.turn_index + 1, snapshot.turn_index + policy.max_consecutive_tool_actions + 2):
            context = self._context.build(snapshot=snapshot, active_skills=active_skills or [])
            try:
                if approved_tool_call is not None:
                    action = FastAgentAction(
                        action="call_tool",
                        tool_name=approved_tool_call.tool_name,
                        tool_input=dict(approved_tool_call.input or {}),
                        reason="Resume an approved Fast tool action.",
                    )
                elif recovered_action is not None:
                    action = recovered_action
                    recovered_action = None
                else:
                    snapshot = await self._set_pending(
                        repo,
                        run_id,
                        snapshot,
                        FastPendingAction(action_id=str(uuid.uuid4()), kind="model"),
                    )
                    raw_action = await self._model.fast_decide(
                        goal,
                        context,
                        on_delta=observe_answer_delta if on_answer_delta is not None else None,
                    )
                    action = FastAgentAction.model_validate(raw_action)
                    snapshot = await self._set_pending(repo, run_id, snapshot, None)
            except (ModelOutputError, ValueError) as error:
                await repo.add_event(
                    run_id,
                    "fast.model.failed",
                    {"turn_index": turn_index, "category": "model_output_error"},
                )
                observation = FastObservation(
                    kind="model_error", status="failed", summary=str(error), data={"retryable": True}
                )
                observations.append(observation)
                snapshot = await self._save(
                    repo, run_id, snapshot, turn_index, observations, pending_action=None
                )
                if model_calls >= policy.max_protocol_retries:
                    result = FastExecutionResult(
                        status="blocked", answer="模型输出无法解析，快速模式已停止。",
                        observations=observations, model_call_count=model_calls + 1,
                        tool_action_count=tool_actions,
                    )
                    result = with_metrics(result)
                    await self._finalizer.persist(repo, run_id, result)
                    return result
                model_calls += 1
                continue
            if approved_tool_call is None:
                model_calls += 1
            await self._catalog.ensure_root_identity(repo.session, run)
            await repo.add_event(run_id, "fast.action.decided", {"turn_index": turn_index, "action": action.action, "tool_name": action.tool_name})
            if action.reason and action.reason.startswith("Adopted the already-streamed answer"):
                await repo.add_event(
                    run_id,
                    "answer.schema_degraded",
                    {"turn_index": turn_index, "answer_mode": "standard", "reason": action.reason},
                )
            elif action.reason and action.reason.startswith("Adopted the streamed answer"):
                await repo.add_event(
                    run_id,
                    "answer.structure_adopted",
                    {"turn_index": turn_index, "answer_mode": "standard"},
                )
            if action.action == "call_tool":
                tool_actions += 1
                if snapshot.pending_action is None:
                    snapshot = await self._set_pending(
                        repo,
                        run_id,
                        snapshot,
                        FastPendingAction(
                            action_id=str(uuid.uuid4()),
                            kind="tool",
                            phase="decided",
                            tool_name=action.tool_name,
                            tool_input=action.tool_input,
                        ),
                    )

                async def on_prepared(tool_call, idempotent, waiting):
                    nonlocal snapshot
                    snapshot = await self._set_pending(
                        repo,
                        run_id,
                        snapshot,
                        FastPendingAction(
                            action_id=tool_call.id,
                            kind="approval" if waiting else "tool",
                            phase="waiting" if waiting else "executing",
                            tool_name=action.tool_name,
                            tool_input=action.tool_input,
                            idempotent=idempotent,
                        ),
                    )

                tool_result = await self._tools.execute(
                    repo,
                    run_id,
                    turn_index,
                    action,
                    approved_tool_call=approved_tool_call,
                    on_prepared=on_prepared,
                )
                approved_tool_call = None
                observation = tool_result.observation
                observations.append(observation)
                snapshot = await self._save(
                    repo,
                    run_id,
                    snapshot,
                    turn_index,
                    observations,
                    pending_action=(
                        snapshot.pending_action if tool_result.waiting_for_approval else None
                    ),
                )
                if tool_result.waiting_for_approval:
                    result = FastExecutionResult(
                        status="waiting_user",
                        answer=observation.summary,
                        observations=observations,
                        model_call_count=model_calls,
                        tool_action_count=tool_actions,
                    )
                    result = with_metrics(result)
                    await self._finalizer.persist(repo, run_id, result)
                    return result
                continue
            answer = action.content or action.reason or "快速模式已停止。"
            status = "waiting_user" if action.action == "ask_user" else ("completed" if action.action == "answer" else "blocked")
            terminal = action.action if action.action in {"answer", "ask_user", "stop"} else None
            snapshot = snapshot.model_copy(update={"snapshot_version": snapshot.snapshot_version + 1, "turn_index": turn_index, "recent_observations": [item.model_dump(mode="json") for item in observations[-100:]], "terminal_intent": terminal})
            await repo.update_fast_runtime_snapshot(
                run_id,
                expected_version=snapshot.snapshot_version - 1,
                snapshot=snapshot,
            )
            result = FastExecutionResult(status=status, answer=answer, observations=observations, model_call_count=model_calls, tool_action_count=tool_actions)
            result = with_metrics(result)
            await self._finalizer.persist(repo, run_id, result)
            return result
        result = FastExecutionResult(status="blocked", answer="快速模式的连续工具动作已达到部署保护上限。", observations=observations, model_call_count=model_calls, tool_action_count=tool_actions)
        result = with_metrics(result)
        await self._finalizer.persist(repo, run_id, result)
        return result

    async def _save(
        self,
        repo,
        run_id,
        snapshot,
        turn_index,
        observations,
        *,
        pending_action=...,
    ):
        update = {
            "snapshot_version": snapshot.snapshot_version + 1,
            "turn_index": turn_index,
            "recent_observations": [item.model_dump(mode="json") for item in observations[-100:]],
        }
        if pending_action is not ...:
            update["pending_action"] = pending_action
        updated = snapshot.model_copy(update=update)
        await repo.update_fast_runtime_snapshot(
            run_id,
            expected_version=snapshot.snapshot_version,
            snapshot=updated,
        )
        await repo.session.commit()
        return updated

    async def _set_pending(self, repo, run_id, snapshot, pending_action):
        updated = snapshot.model_copy(
            update={
                "snapshot_version": snapshot.snapshot_version + 1,
                "pending_action": pending_action,
            }
        )
        await repo.update_fast_runtime_snapshot(
            run_id,
            expected_version=snapshot.snapshot_version,
            snapshot=updated,
        )
        await repo.session.commit()
        return updated
