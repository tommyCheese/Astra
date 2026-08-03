from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.grounding.schemas import EvidenceFragment
from app.permissions.effects import DefaultEffectAnalyzer, effect_plan_hash
from app.plugins.catalog import PluginCatalog
from app.plugins.interfaces import (
    EffectAnalyzer,
    ProcessorOutput,
    ResultAdapter,
    ResultProcessor,
    ToolExecutor,
)
from app.schemas.agent import AgentObservation
from app.schemas.permissions import ActionEffectPlan
from app.tools.base import (
    Tool,
    ToolExecutionContext,
    ToolExecutionError,
    ToolResultEnvelope,
    validate_tool_result,
)
from app.tools.router import ToolRouter


class InvocationStatus(str, Enum):
    succeeded = "succeeded"
    failed = "failed"
    waiting_approval = "waiting_approval"
    blocked = "blocked"


class AuthorizationDisposition(str, Enum):
    allow = "allow"
    ask = "ask"
    deny = "deny"


@dataclass(frozen=True)
class InvocationRequest:
    run_id: str
    task_id: str
    tool_name: str
    tool_input: dict[str, Any]
    tool_call_id: str | None = None
    step_id: str | None = None
    plan_node_id: str | None = None
    node_execution_id: str | None = None
    idempotency_key: str | None = None
    resumed: bool = False


@dataclass(frozen=True)
class SerializedToolExecutionContext:
    run_id: str
    tool_call_id: str
    step_id: str | None
    trace_id: str
    task_id: str | None
    workspace_mode: str
    effect_plan: dict[str, Any] | None
    runtime_identity_id: str | None


@dataclass(frozen=True)
class InvocationRuntimeContext:
    execution_context: ToolExecutionContext
    available_backends: frozenset[str] = frozenset({"in_process"})
    frozen_component_identities: dict[str, str] = field(default_factory=dict)

    def serialized(self) -> SerializedToolExecutionContext:
        context = self.execution_context
        return SerializedToolExecutionContext(
            run_id=context.run_id,
            tool_call_id=context.tool_call_id,
            step_id=context.step_id,
            trace_id=context.trace_id,
            task_id=context.task_id,
            workspace_mode=context.workspace_mode,
            effect_plan=context.effect_plan,
            runtime_identity_id=context.runtime_identity_id,
        )


@dataclass(frozen=True)
class AuthorizationResult:
    disposition: AuthorizationDisposition
    reason_code: str = "allowed"
    summary: str = "Invocation is allowed"
    approval_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class InvocationOutcome:
    status: InvocationStatus
    tool_name: str
    effect_plan: ActionEffectPlan | None = None
    effect_plan_hash: str | None = None
    envelope: ToolResultEnvelope | None = None
    observations: tuple[AgentObservation, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    validation_inputs: tuple[dict[str, Any], ...] = ()
    completion_signals: tuple[str, ...] = ()
    approval_payload: dict[str, Any] | None = None
    error: dict[str, str] | None = None


class InvocationAuthorizationGateway(ABC):
    @abstractmethod
    async def authorize(
        self,
        request: InvocationRequest,
        effect_plan: ActionEffectPlan,
        *,
        effect_hash: str,
    ) -> AuthorizationResult: ...


class InvocationRecorder:
    async def prepared(
        self, request: InvocationRequest, effect_plan: ActionEffectPlan, *, effect_hash: str
    ) -> None:
        return None

    async def succeeded(self, request: InvocationRequest, outcome: InvocationOutcome) -> None:
        return None

    async def enrich_envelope(
        self,
        request: InvocationRequest,
        effect_plan: ActionEffectPlan,
        envelope: ToolResultEnvelope,
    ) -> ToolResultEnvelope:
        return envelope

    async def failed(self, request: InvocationRequest, error: ToolExecutionError) -> None:
        return None


class InProcessToolExecutor(ToolExecutor):
    def __init__(self, tool: Tool):
        self.tool = tool

    async def execute(self, spec, tool_input, *, context):
        return await self.tool.run(tool_input, context=context)


class InvocationPipeline:
    def __init__(
        self,
        catalog: PluginCatalog,
        *,
        authorization: InvocationAuthorizationGateway,
        recorder: InvocationRecorder | None = None,
        fallback_analyzer: EffectAnalyzer | None = None,
        evidence_writer: Any | None = None,
    ):
        self.catalog = catalog
        self.authorization = authorization
        self.recorder = recorder or InvocationRecorder()
        self.fallback_analyzer = fallback_analyzer or DefaultEffectAnalyzer()
        self.evidence_writer = evidence_writer
        self._components = {
            entry.identity.component_id: entry.create()
            for entry in (
                *catalog.effect_analyzers,
                *catalog.result_processors,
                *catalog.validators,
                *catalog.approval_presenters,
            )
        }

    async def invoke(
        self,
        request: InvocationRequest,
        runtime: InvocationRuntimeContext,
    ) -> InvocationOutcome:
        phase = "context_validation"
        try:
            self._validate_runtime_context(request, runtime)
            router = ToolRouter(
                self.catalog.tool_registry(),
                available_backends=set(runtime.available_backends),
            )
            phase = "resolution"
            tool = router.resolve(request.tool_name, request.tool_input)
            phase = "effect_analysis"
            effect_plan = self._analyze(tool, request)
            effect_hash = effect_plan_hash(effect_plan)
            phase = "recording"
            await self.recorder.prepared(request, effect_plan, effect_hash=effect_hash)
            phase = "authorization"
            authorization = await self.authorization.authorize(
                request, effect_plan, effect_hash=effect_hash
            )
            if authorization.disposition == AuthorizationDisposition.ask:
                return InvocationOutcome(
                    status=InvocationStatus.waiting_approval,
                    tool_name=tool.spec.name,
                    effect_plan=effect_plan,
                    effect_plan_hash=effect_hash,
                    approval_payload=authorization.approval_payload,
                )
            if authorization.disposition == AuthorizationDisposition.deny:
                return InvocationOutcome(
                    status=InvocationStatus.blocked,
                    tool_name=tool.spec.name,
                    effect_plan=effect_plan,
                    effect_plan_hash=effect_hash,
                    error={
                        "category": authorization.reason_code,
                        "message": authorization.summary,
                    },
                )
            phase = "execution"
            executor = self._executor(tool)
            raw = await executor.execute(
                tool.spec,
                request.tool_input,
                context=runtime.execution_context,
            )
            phase = "result_adaptation"
            raw = self._adapt_result(tool, raw)
            phase = "result_validation"
            envelope = validate_tool_result(raw, tool.spec)
            if envelope.status == "failed":
                raise ToolExecutionError("tool_failed", "Tool reported a failed result")
            phase = "result_persistence"
            envelope = await self.recorder.enrich_envelope(request, effect_plan, envelope)
            phase = "result_processing"
            processed = self._process(tool, request, envelope)
            if self.evidence_writer is not None:
                phase = "evidence_persistence"
                fragments = [
                    EvidenceFragment.model_validate(fragment)
                    for item in processed
                    for fragment in item.evidence.get("fragments", [])
                ]
                if fragments:
                    await self.evidence_writer.write(
                        request.run_id,
                        fragments,
                        plan_node_id=request.plan_node_id,
                        node_execution_id=request.node_execution_id,
                        tool_call_id=request.tool_call_id,
                        artifact_ids=[artifact.id for artifact in envelope.artifacts],
                    )
            outcome = InvocationOutcome(
                status=InvocationStatus.succeeded,
                tool_name=tool.spec.name,
                effect_plan=effect_plan,
                effect_plan_hash=effect_hash,
                envelope=envelope,
                observations=tuple(item.observation for item in processed),
                evidence=tuple(item.evidence for item in processed if item.evidence),
                validation_inputs=tuple(
                    item.validation_input for item in processed if item.validation_input
                ),
                completion_signals=tuple(
                    signal for item in processed for signal in item.completion_signals
                ),
            )
            phase = "recording"
            await self.recorder.succeeded(request, outcome)
            return outcome
        except asyncio.CancelledError:
            raise
        except ToolExecutionError as exc:
            error = await self._record_failure(request, exc)
            return InvocationOutcome(
                status=InvocationStatus.failed,
                tool_name=request.tool_name,
                error=error.to_payload(),
            )
        except TimeoutError:
            error = ToolExecutionError("tool_timeout", "Tool execution timed out")
            error = await self._record_failure(request, error)
            return InvocationOutcome(
                status=InvocationStatus.failed,
                tool_name=request.tool_name,
                error=error.to_payload(),
            )
        except Exception:
            category = {
                "authorization": "authorization_failed",
                "recording": "recording_failed",
                "execution": "tool_failed",
                "result_adaptation": "invalid_result",
                "result_validation": "invalid_result",
                "result_persistence": "result_persistence_failed",
                "result_processing": "result_processing_failed",
                "evidence_persistence": "evidence_persistence_failed",
            }.get(phase, "invocation_failed")
            error = ToolExecutionError(category, f"Invocation failed during {phase}")
            error = await self._record_failure(request, error)
            return InvocationOutcome(
                status=InvocationStatus.failed,
                tool_name=request.tool_name,
                error=error.to_payload(),
            )

    @staticmethod
    def _validate_runtime_context(
        request: InvocationRequest, runtime: InvocationRuntimeContext
    ) -> None:
        context = runtime.execution_context
        if request.run_id != context.run_id or request.task_id != context.task_id:
            raise ToolExecutionError(
                "execution_context_mismatch", "Invocation context does not match the request"
            )
        if request.tool_call_id is not None and request.tool_call_id != context.tool_call_id:
            raise ToolExecutionError(
                "execution_context_mismatch", "Invocation ToolCall does not match the context"
            )
        if request.step_id is not None and request.step_id != context.step_id:
            raise ToolExecutionError(
                "execution_context_mismatch", "Invocation step does not match the context"
            )

    async def _record_failure(
        self, request: InvocationRequest, error: ToolExecutionError
    ) -> ToolExecutionError:
        try:
            await self.recorder.failed(request, error)
        except Exception:
            return ToolExecutionError(
                "recording_failed", "Invocation failure could not be recorded safely"
            )
        return error

    def _analyze(self, tool: Tool, request: InvocationRequest) -> ActionEffectPlan:
        matches = [
            entry
            for entry in self.catalog.effect_analyzers
            if entry.applicability.matches(
                tool_name=tool.spec.name, capabilities=set(tool.spec.capabilities)
            )
        ]
        if len(matches) > 1:
            raise ToolExecutionError(
                "effect_analyzer_ambiguous", "Multiple effect analyzers match the invocation"
            )
        analyzer = (
            self._components[matches[0].identity.component_id]
            if matches
            else self.fallback_analyzer
        )
        if not isinstance(analyzer, EffectAnalyzer):
            raise ToolExecutionError(
                "effect_analyzer_unavailable", "No trusted effect analyzer is available"
            )
        try:
            return analyzer.analyze(tool.spec, request.tool_input, task_id=request.task_id)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                "effect_analysis_failed", "Trusted effect analysis failed"
            ) from exc

    def _executor(self, tool: Tool) -> ToolExecutor:
        binding = self.catalog.tool_bindings[tool.spec.name]
        if binding.executor_id == "in_process":
            return InProcessToolExecutor(tool)
        contribution = self.catalog.runtime_backends.get(binding.executor_id)
        if contribution is None or not isinstance(contribution.backend, ToolExecutor):
            raise ToolExecutionError(
                "sandbox_unavailable", "The bound tool executor is unavailable"
            )
        return contribution.backend

    def _adapt_result(self, tool: Tool, result: dict[str, Any]) -> dict[str, Any]:
        binding = self.catalog.tool_bindings[tool.spec.name]
        if binding.result_adapter_id == "envelope.v1":
            return result
        if binding.result_adapter_factory is None:
            raise ToolExecutionError(
                "result_adapter_unavailable", "The bound result adapter is unavailable"
            )
        adapter = binding.result_adapter_factory()
        if not isinstance(adapter, ResultAdapter):
            raise ToolExecutionError(
                "result_adapter_unavailable", "The bound result adapter is unavailable"
            )
        try:
            return adapter.adapt(result)
        except Exception as exc:
            raise ToolExecutionError(
                "invalid_result", "The legacy tool result could not be adapted"
            ) from exc

    def _process(
        self,
        tool: Tool,
        request: InvocationRequest,
        envelope: ToolResultEnvelope,
    ) -> list[ProcessorOutput]:
        outputs = []
        media_types = {artifact.mime_type for artifact in envelope.artifacts}
        result_kind = str(envelope.data.get("kind")) if envelope.data.get("kind") else None
        for entry in self.catalog.result_processors:
            if not entry.applicability.matches(
                tool_name=tool.spec.name,
                capabilities=set(tool.spec.capabilities),
                result_kind=result_kind,
                media_types=media_types,
            ):
                continue
            component = self._components[entry.identity.component_id]
            if not isinstance(component, ResultProcessor):
                raise ToolExecutionError(
                    "result_processor_unavailable", "A bound result processor is unavailable"
                )
            try:
                outputs.append(
                    component.process(
                        tool.spec,
                        request.tool_input,
                        envelope.model_dump(mode="json", exclude_none=True),
                    )
                )
            except Exception as exc:
                raise ToolExecutionError(
                    "result_processing_failed", "Tool result processing failed"
                ) from exc
        if not outputs:
            outputs.append(
                ProcessorOutput(
                    observation=AgentObservation(
                        kind="tool_result",
                        status="succeeded",
                        summary=f"{tool.spec.name} completed",
                        data={
                            "tool_name": tool.spec.name,
                            **envelope.model_dump(mode="json", exclude_none=True),
                        },
                    )
                )
            )
        return outputs
