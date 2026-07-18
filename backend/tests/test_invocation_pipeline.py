import asyncio

import pytest

from app.permissions.effects import DefaultEffectAnalyzer, ToolEffectAnalyzer
from app.plugins.builtin_components import LegacyRawResultAdapter
from app.plugins.catalog import PluginCatalogBuilder
from app.plugins.contracts import (
    ApplicabilityBinding,
    ComponentContribution,
    ComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    ToolContribution,
)
from app.plugins.discovery import BuiltinDiscoverySource
from app.plugins.interfaces import EffectAnalyzer, ProcessorOutput, ResultProcessor
from app.runner.invocation import (
    AuthorizationDisposition,
    AuthorizationResult,
    InvocationAuthorizationGateway,
    InvocationPipeline,
    InvocationRecorder,
    InvocationRequest,
    InvocationRuntimeContext,
    InvocationStatus,
)
from app.schemas.agent import AgentObservation
from app.tools.base import Tool, ToolExecutionContext, ToolResultEnvelope, ToolSpec


class PipelineTool(Tool):
    def __init__(self, output=None, error=None):
        self.output = output or ToolResultEnvelope(data={"value": "ok"}).model_dump(mode="json")
        self.error = error
        self.spec = ToolSpec(
            name="pipeline.read",
            version="1",
            input_schema={"type": "object", "required": ["query"]},
            output_schema={
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "string"}},
            },
            permission="network_read",
            side_effect_level="read_only",
            provider_id="pipeline.provider",
            provider_digest="sha256:pipeline",
            trust_level="managed",
        )

    async def run(self, tool_input, *, context=None):
        if self.error:
            raise self.error
        return self.output


class PipelineProcessor(ResultProcessor):
    def __init__(self, error=None):
        self.error = error

    def process(self, spec, tool_input, result):
        if self.error:
            raise self.error
        return ProcessorOutput(
            observation=AgentObservation(
                kind="pipeline_result",
                status="succeeded",
                summary="processed",
                data={"tool_name": spec.name, "value": result["data"]["value"]},
            ),
            evidence={"value": result["data"]["value"]},
            validation_input={"kind": "pipeline"},
            completion_signals=("goal_satisfied",),
        )


class FailingAnalyzer(ToolEffectAnalyzer):
    def analyze(self, spec, tool_input, *, task_id):
        raise RuntimeError("private analyzer detail")


class PluginContractAnalyzer(EffectAnalyzer):
    def analyze(self, spec, tool_input, *, task_id):
        return DefaultEffectAnalyzer().analyze(spec, tool_input, task_id=task_id)


class Gateway(InvocationAuthorizationGateway):
    def __init__(self, disposition=AuthorizationDisposition.allow):
        self.disposition = disposition
        self.requests = []

    async def authorize(self, request, effect_plan, *, effect_hash):
        self.requests.append((request, effect_plan, effect_hash))
        return AuthorizationResult(
            self.disposition,
            reason_code="policy_denied",
            summary="denied by policy",
            approval_payload={"request": "approve"},
        )


class Recorder(InvocationRecorder):
    def __init__(self):
        self.prepared_requests = []
        self.outcomes = []
        self.errors = []

    async def prepared(self, request, effect_plan, *, effect_hash):
        self.prepared_requests.append((request, effect_hash))

    async def succeeded(self, request, outcome):
        self.outcomes.append((request, outcome))

    async def failed(self, request, error):
        self.errors.append((request, error))


class FailingRecorder(Recorder):
    async def failed(self, request, error):
        raise RuntimeError("private recorder detail")


def descriptor():
    return PluginDescriptor(
        plugin_id="pipeline.plugin",
        provider_id="pipeline.provider",
        version="1",
        digest="sha256:pipeline",
        source="builtin",
        trust_level="platform",
    )


def component_identity(component_id):
    return ComponentIdentity(
        component_id=component_id,
        provider_id="pipeline.provider",
        version="1",
        digest=f"sha256:{component_id}",
    )


async def catalog(tool=None, analyzer=None, processor=None, *, legacy=False):
    contribution = PluginContribution(
        descriptor=descriptor(),
        tools=(
            ToolContribution(
                tool=tool or PipelineTool(),
                executor_id="in_process",
                result_adapter_id="legacy.raw.v0" if legacy else "envelope.v1",
                result_adapter_factory=LegacyRawResultAdapter if legacy else None,
            ),
        ),
        effect_analyzers=(
            ComponentContribution(
                component_identity("analyzer"),
                ApplicabilityBinding(tool_names=("pipeline.read",)),
                lambda: analyzer or DefaultEffectAnalyzer(),
            ),
        ),
        result_processors=(
            ComponentContribution(
                component_identity("processor"),
                ApplicabilityBinding(tool_names=("pipeline.read",)),
                lambda: processor or PipelineProcessor(),
            ),
        ),
    )
    return await PluginCatalogBuilder(
        [BuiltinDiscoverySource([contribution])],
        allowed_providers={"pipeline.provider": {"sha256:pipeline"}},
    ).build()


def request(**updates):
    values = {
        "run_id": "run-1",
        "task_id": "task-1",
        "tool_call_id": "call-1",
        "tool_name": "pipeline.read",
        "tool_input": {"query": "astra"},
    }
    values.update(updates)
    return InvocationRequest(**values)


def runtime():
    context = ToolExecutionContext(
        run_id="run-1",
        tool_call_id="call-1",
        step_id=None,
        trace_id="trace-1",
        artifact_service=object(),
        sandbox_service=object(),
        task_id="task-1",
    )
    return InvocationRuntimeContext(context)


async def test_pipeline_allows_executes_validates_and_processes_result():
    gateway = Gateway()
    pipeline = InvocationPipeline(await catalog(), authorization=gateway)

    outcome = await pipeline.invoke(request(), runtime())

    assert outcome.status == InvocationStatus.succeeded
    assert outcome.envelope.data == {"value": "ok"}
    assert outcome.observations[0].kind == "pipeline_result"
    assert outcome.evidence == ({"value": "ok"},)
    assert outcome.validation_inputs == ({"kind": "pipeline"},)
    assert outcome.completion_signals == ("goal_satisfied",)
    assert gateway.requests[0][2] == outcome.effect_plan_hash
    serialized = runtime().serialized()
    assert serialized.run_id == "run-1"
    assert not hasattr(serialized, "artifact_service")


async def test_pipeline_accepts_analyzer_implementing_only_plugin_contract():
    outcome = await InvocationPipeline(
        await catalog(analyzer=PluginContractAnalyzer()), authorization=Gateway()
    ).invoke(request(), runtime())

    assert outcome.status == InvocationStatus.succeeded


@pytest.mark.parametrize(
    ("disposition", "status"),
    [
        (AuthorizationDisposition.ask, InvocationStatus.waiting_approval),
        (AuthorizationDisposition.deny, InvocationStatus.blocked),
    ],
)
async def test_pipeline_stops_before_execution_for_ask_and_deny(disposition, status):
    tool = PipelineTool()
    gateway = Gateway(disposition)

    outcome = await InvocationPipeline(await catalog(tool=tool), authorization=gateway).invoke(
        request(resumed=True), runtime()
    )

    assert outcome.status == status
    assert outcome.envelope is None
    assert outcome.approval_payload == (
        {"request": "approve"} if status.value == "waiting_approval" else None
    )


async def test_pipeline_rejects_invalid_result_without_payload_leak():
    tool = PipelineTool(
        output={
            "protocol_version": "1",
            "status": "succeeded",
            "data": {"private": "do-not-leak"},
        }
    )

    outcome = await InvocationPipeline(await catalog(tool=tool), authorization=Gateway()).invoke(
        request(), runtime()
    )

    assert outcome.status == InvocationStatus.failed
    assert outcome.error["category"] == "invalid_result"
    assert "do-not-leak" not in str(outcome.error)


async def test_pipeline_applies_frozen_legacy_result_adapter_without_tool_name_branch():
    tool = PipelineTool(output={"value": "legacy"})

    outcome = await InvocationPipeline(
        await catalog(tool=tool, legacy=True), authorization=Gateway()
    ).invoke(request(), runtime())

    assert outcome.status == InvocationStatus.succeeded
    assert outcome.envelope.data == {"value": "legacy"}


async def test_pipeline_fails_closed_for_analyzer_and_processor_failures():
    analyzer_failure = await InvocationPipeline(
        await catalog(analyzer=FailingAnalyzer()), authorization=Gateway()
    ).invoke(request(), runtime())
    processor_failure = await InvocationPipeline(
        await catalog(processor=PipelineProcessor(RuntimeError("private processor detail"))),
        authorization=Gateway(),
    ).invoke(request(), runtime())

    assert analyzer_failure.error["category"] == "effect_analysis_failed"
    assert "private" not in str(analyzer_failure.error)
    assert processor_failure.error["category"] == "result_processing_failed"
    assert "private" not in str(processor_failure.error)


async def test_pipeline_normalizes_timeout_and_propagates_cancellation():
    timeout = await InvocationPipeline(
        await catalog(tool=PipelineTool(error=TimeoutError())), authorization=Gateway()
    ).invoke(request(), runtime())

    assert timeout.error["category"] == "tool_timeout"

    with pytest.raises(asyncio.CancelledError):
        await InvocationPipeline(
            await catalog(tool=PipelineTool(error=asyncio.CancelledError())),
            authorization=Gateway(),
        ).invoke(request(), runtime())


async def test_pipeline_rejects_context_mismatch_and_normalizes_arbitrary_tool_failure():
    mismatch = await InvocationPipeline(await catalog(), authorization=Gateway()).invoke(
        request(run_id="other-run"), runtime()
    )
    crashed = await InvocationPipeline(
        await catalog(tool=PipelineTool(error=RuntimeError("private executor detail"))),
        authorization=Gateway(),
    ).invoke(request(), runtime())

    assert mismatch.error["category"] == "execution_context_mismatch"
    assert crashed.error["category"] == "tool_failed"
    assert "private" not in str(crashed.error)


async def test_pipeline_normalizes_failed_envelope_and_recorder_failure():
    failed_result = ToolResultEnvelope(
        status="failed",
        error={"category": "provider_failed", "message": "safe provider failure"},
    ).model_dump(mode="json", exclude_none=True)
    failed = await InvocationPipeline(
        await catalog(tool=PipelineTool(output=failed_result)), authorization=Gateway()
    ).invoke(request(), runtime())
    unrecorded = await InvocationPipeline(
        await catalog(tool=PipelineTool(error=RuntimeError("private"))),
        authorization=Gateway(),
        recorder=FailingRecorder(),
    ).invoke(request(), runtime())

    assert failed.error["category"] == "tool_failed"
    assert unrecorded.error["category"] == "recording_failed"
    assert "private" not in str(unrecorded.error)


async def test_pipeline_preserves_recovery_identity_for_authorizer_and_recorder():
    gateway = Gateway()
    recorder = Recorder()
    recovered = request(resumed=True, idempotency_key="stable-key")

    outcome = await InvocationPipeline(
        await catalog(), authorization=gateway, recorder=recorder
    ).invoke(recovered, runtime())

    assert outcome.status == InvocationStatus.succeeded
    assert gateway.requests[0][0].resumed is True
    assert gateway.requests[0][0].idempotency_key == "stable-key"
    assert recorder.prepared_requests[0][0] == recovered
    assert recorder.outcomes[0][0] == recovered
