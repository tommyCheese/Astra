"""Synthetic information tools used to exercise the generic plugin runtime."""

from app.application.permissions.effects import DefaultEffectAnalyzer
from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.agent.run_result import AgentValidationIssue, AgentValidationOutcome
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
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolResultEnvelope,
)


class FakeSearch(AstraTool):
    spec = AstraToolSpec(
        name="catalog_search",
        version="test",
        input_schema={"required": ["query"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.search", "source.discover"],
        provider_id="astra.builtin",
        provider_digest="builtin",
    )

    async def run(self, tool_input, *, context=None):
        self.last_context = context
        return ToolResultEnvelope(
            data={
                "query": tool_input["query"],
                "provider": "test",
                "candidate_count": 1,
                "warnings": [],
                "candidates": [
                    {
                        "url": "https://test.invalid/source",
                        "title": "Test",
                        "snippet": "Evidence",
                        "provider": "test",
                        "retrieved_at": "now",
                    }
                ],
            }
        ).model_dump(mode="json")


class FakeFetch(AstraTool):
    spec = AstraToolSpec(
        name="catalog_read",
        version="test",
        input_schema={"required": ["url"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.read", "source.retrieve", "evidence.extract"],
        provider_id="astra.builtin",
        provider_digest="builtin",
    )

    async def run(self, tool_input, *, context=None):
        return ToolResultEnvelope(
            data={
                "url": tool_input["url"],
                "status_code": 200,
                "title": "Test",
                "content": "Deterministic test evidence",
                "quality_score": 0.9,
                "extraction_strategy": "test",
                "warnings": [],
                "retrieved_at": "now",
            }
        ).model_dump(mode="json")


def fake_information_registry():
    tools = (FakeSearch(), FakeFetch())
    descriptor = PluginDescriptor(
        plugin_id="test.web.plugin",
        provider_id="astra.builtin",
        version="1",
        digest="builtin",
        source="builtin",
        trust_level="platform",
    )

    def component(component_id, factory):
        return PluginComponentContribution(
            identity=PluginComponentIdentity(
                component_id=f"test.web.{component_id}",
                provider_id="astra.builtin",
                version="1",
                digest="builtin",
            ),
            applicability=PluginApplicabilityBinding(tool_names=("catalog_search", "catalog_read")),
            factory=factory,
        )

    contribution = PluginContribution(
        descriptor=descriptor,
        tools=tuple(PluginToolContribution(tool, "in_process") for tool in tools),
        effect_analyzers=(component("effect", DefaultEffectAnalyzer),),
        result_processors=(component("result", SourceResultProcessor),),
        validators=(component("validator", SourceEvidenceValidator),),
    )
    catalog = PluginCatalogBuilder(
        [BuiltinDiscoverySource([contribution])],
        allowed_providers={"astra.builtin": {"builtin"}},
    ).build_static()
    return AstraToolRegistry(plugin_catalog=catalog).extend(tools)


class SourceResultProcessor(PluginResultProcessor):
    def process(self, spec, tool_input, result):
        data = dict(result.get("data") or {})
        evidence = {
            "domain": "source",
            "kind": "candidate" if "candidates" in data else "document",
            "source": data,
        }
        return PluginResultProcessingOutput(
            observation=AgentObservation(
                kind="tool_result",
                status="succeeded",
                summary=f"{spec.name} completed",
                data={"tool_name": spec.name, **data},
            ),
            evidence=evidence,
            validation_input={"domain": "source"},
        )

    def process_failure(self, spec, tool_input, error):
        return {"domain": "source", "kind": "failure", "source": dict(error)}


class SourceEvidenceValidator(PluginResultValidator):
    def validate(self, result, evidence):
        fragments = list(evidence.get("fragments", []))
        attempted = any(item.get("domain") == "source" for item in fragments)
        documents = [
            item.get("source", {}) for item in fragments if item.get("domain") == "source" and item.get("kind") == "document"
        ]
        issues = []
        if attempted and not documents:
            issues.append(
                AgentValidationIssue(
                    code="source_documents_missing",
                    message="没有成功读取到可用来源。",
                )
            )
        return AgentValidationOutcome(
            validator="source_evidence",
            passed=not issues,
            blocking=True,
            issues=issues,
            evidence_refs=[str(item.get("url")) for item in documents if item.get("url")],
        )
