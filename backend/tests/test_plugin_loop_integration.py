from app.application.agent_runtime.policies.reasoning import (
    AgentReasoningPolicyCompiler,
)
from app.application.agent_runtime.services.loop import AstraAgentLoop
from app.application.permissions.effects import DefaultEffectAnalyzer
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.run_result import (
    AgentFinalAnswer,
    AgentValidationOutcome,
)
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.plugins.builtin_components import (
    ChartArtifactValidator,
    ChartResultProcessor,
)
from app.infrastructure.plugins.catalog import PluginCatalogBuilder
from app.infrastructure.plugins.contracts import (
    PluginApplicabilityBinding,
    PluginComponentContribution,
    PluginComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    PluginToolContribution,
)
from app.infrastructure.plugins.discovery import BuiltinDiscoverySource
from app.infrastructure.plugins.interfaces import (
    PluginResultProcessingOutput,
    PluginResultProcessor,
    PluginResultValidator,
)
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolSpec,
    ToolResultEnvelope,
)


class StaticTool(AstraTool):
    def __init__(self, spec: AstraToolSpec, output: dict):
        self.spec = spec
        self.output = output

    async def run(self, tool_input, *, context=None):
        return self.output


class SyntheticProcessor(PluginResultProcessor):
    def process(self, spec, tool_input, result):
        return PluginResultProcessingOutput(
            observation=AgentObservation(
                kind="synthetic_result",
                status="succeeded",
                summary="synthetic provider processed the result",
                data={"value": result["data"]["value"]},
            ),
            evidence={"domain": "synthetic", "value": result["data"]["value"]},
            validation_input={"domain": "synthetic"},
            completion_signals=("goal_satisfied",),
        )


class SyntheticValidator(PluginResultValidator):
    def validate(self, result, evidence):
        passed = any(item.get("domain") == "synthetic" for item in evidence["fragments"])
        return AgentValidationOutcome(
            validator="synthetic_validation",
            passed=passed,
            blocking=True,
        )


class ToolSequenceClient(MockModelClient):
    def __init__(self, calls, answer):
        self.calls = list(calls)
        self.answer = answer

    async def decide_with_answer(
        self,
        goal,
        context,
        *,
        on_delta=None,
        on_reasoning_delta=None,
    ):
        if self.calls:
            name, tool_input = self.calls.pop(0)
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary=f"call {name}",
                tool_name=name,
                tool_input=tool_input,
            ), None
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="all provider results are ready",
        ), self.answer


def _policy():
    return AgentReasoningPolicyCompiler().compile(
        RequestedReasoningPolicy(execution_mode="auto_approval")
    ).model_dump(mode="json")


def _spec(name, provider, permission="network_read"):
    return AstraToolSpec(
        name=name,
        version="1",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permission=permission,
        permissions=[permission],
        capabilities=[permission],
        side_effect_level="read_only",
        provider_id=provider,
        provider_digest="builtin",
        trust_level="platform",
    )


def _descriptor(provider):
    return PluginDescriptor(
        plugin_id=f"{provider}.plugin",
        provider_id=provider,
        version="1",
        digest="builtin",
        source="builtin",
        trust_level="platform",
    )


def _component(provider, component_id, tool_names, factory):
    return PluginComponentContribution(
        identity=PluginComponentIdentity(
            component_id=f"{provider}.{component_id}",
            provider_id=provider,
            version="1",
            digest="builtin",
        ),
        applicability=PluginApplicabilityBinding(tool_names=tuple(tool_names)),
        factory=factory,
    )


def _catalog(*contributions):
    return PluginCatalogBuilder(
        [BuiltinDiscoverySource(contributions)],
        allowed_providers={
            item.descriptor.provider_id: {item.descriptor.digest}
            for item in contributions
        },
    ).build_static()


async def test_synthetic_provider_executes_processes_validates_and_completes_without_loop_change(
    session,
):
    provider = "example.synthetic"
    spec = _spec("example.synthetic.read", provider)
    tool = StaticTool(spec, ToolResultEnvelope(data={"value": "ok"}).model_dump(mode="json"))
    contribution = PluginContribution(
        descriptor=_descriptor(provider),
        tools=(PluginToolContribution(tool=tool, executor_id="in_process"),),
        effect_analyzers=(
            _component(provider, "effect", (spec.name,), DefaultEffectAnalyzer),
        ),
        result_processors=(
            _component(provider, "processor", (spec.name,), SyntheticProcessor),
        ),
        validators=(
            _component(provider, "validator", (spec.name,), SyntheticValidator),
        ),
    )
    settings = AstraRuntimeSettings(
        model_provider="mock",
        trusted_tool_providers="example.synthetic=builtin",
    )
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run(
        "use the synthetic provider",
        settings.model_policy,
        reasoning_policy=_policy(),
    )

    output = await AstraAgentLoop(
        settings,
        model_client=ToolSequenceClient(
            [(spec.name, {})],
            AgentFinalAnswer(summary="synthetic provider completed"),
        ),
        tool_registry=_catalog(contribution).tool_registry(),
    ).run(repository, run.id, run.task.description)

    validators = {
        item["validator"]
        for item in output["result"]["verification_report"]["validation_outcomes"]
    }
    loaded = await repository.require_run(run.id)
    assert output["status"] == "completed"
    assert "synthetic_validation" in validators
    assert loaded.turns[0].observation["kind"] == "synthetic_result"


async def test_mixed_source_and_chart_run_aggregates_both_plugin_validators(session):
    source_provider = "example.sources"
    chart_provider = "example.chart"
    search_spec = _spec("source_lookup", source_provider)
    fetch_spec = _spec("source_read", source_provider)
    chart_spec = _spec("chart.render", chart_provider, "artifact_write")
    search = StaticTool(
        search_spec,
        ToolResultEnvelope(
            data={"value": "candidate"}
        ).model_dump(mode="json"),
    )
    fetch = StaticTool(
        fetch_spec,
        ToolResultEnvelope(
            data={"value": "document"}
        ).model_dump(mode="json"),
    )
    chart = StaticTool(
        chart_spec,
        ToolResultEnvelope(
            data={"backend": "test"},
            artifacts=[
                {
                    "id": "chart-1",
                    "type": "chart_image",
                    "mime_type": "image/png",
                    "size_bytes": 8,
                    "checksum": "sha256:test",
                }
            ],
        ).model_dump(mode="json"),
    )
    source_contribution = PluginContribution(
        descriptor=_descriptor(source_provider),
        tools=tuple(
            PluginToolContribution(tool=tool, executor_id="in_process")
            for tool in (search, fetch)
        ),
        effect_analyzers=(
            _component(source_provider, "effect", (search_spec.name, fetch_spec.name), DefaultEffectAnalyzer),
        ),
        result_processors=(
            _component(source_provider, "processor", (search_spec.name, fetch_spec.name), SyntheticProcessor),
        ),
        validators=(
            _component(source_provider, "validator", (search_spec.name, fetch_spec.name), SyntheticValidator),
        ),
    )
    chart_contribution = PluginContribution(
        descriptor=_descriptor(chart_provider),
        tools=(PluginToolContribution(tool=chart, executor_id="in_process"),),
        effect_analyzers=(
            _component(chart_provider, "effect", (chart_spec.name,), DefaultEffectAnalyzer),
        ),
        result_processors=(
            _component(chart_provider, "processor", (chart_spec.name,), ChartResultProcessor),
        ),
        validators=(
            _component(chart_provider, "validator", (chart_spec.name,), ChartArtifactValidator),
        ),
    )
    settings = AstraRuntimeSettings(
        model_provider="mock",
        trusted_tool_providers="example.sources=builtin,example.chart=builtin",
    )
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run(
        "research and chart Astra",
        settings.model_policy,
        reasoning_policy=_policy(),
    )

    output = await AstraAgentLoop(
        settings,
        model_client=ToolSequenceClient(
            [
                (search_spec.name, {"query": "astra"}),
                (fetch_spec.name, {"url": "https://example.test/source"}),
                (chart_spec.name, {}),
            ],
            AgentFinalAnswer(
                summary="research and chart complete",
                findings=[{"text": "Synthetic data was processed and charted."}],
            ),
        ),
        tool_registry=_catalog(source_contribution, chart_contribution).tool_registry(),
    ).run(repository, run.id, run.task.description)

    outcomes = output["result"]["verification_report"]["validation_outcomes"]
    by_validator = {item["validator"]: item for item in outcomes}
    assert output["status"] == "completed"
    assert by_validator["synthetic_validation"]["passed"] is True
    assert by_validator["chart_artifact"]["passed"] is True
