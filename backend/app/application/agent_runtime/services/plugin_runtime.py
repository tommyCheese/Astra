"""Run-scoped access to frozen Tool Provider Plugin contribution bindings."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from app.application.permissions.effects import DefaultEffectAnalyzer
from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.agent.run_result import AgentValidationOutcome
from app.domain.grounding.ledger import GroundingEvidenceLedger
from app.domain.grounding.schemas import GroundingEvidenceFragment
from app.infrastructure.plugins.catalog import PluginCatalog
from app.infrastructure.plugins.contracts import PluginComponentContribution
from app.infrastructure.plugins.interfaces import (
    PluginApprovalPresenter,
    PluginResultAdapter,
    PluginResultProcessingOutput,
    PluginResultProcessor,
    PluginResultValidator,
    ToolEffectAnalyzer,
    RuntimeBackend,
)
from app.infrastructure.plugins.diagnostics import plugin_diagnostics
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolSpec,
    ToolExecutionContext,
    ToolExecutionError,
    ToolResultEnvelope,
    validate_tool_result,
)


@dataclass(frozen=True)
class ProcessedPluginResult:
    observation: AgentObservation
    evidence: tuple[dict[str, Any], ...] = ()
    validation_inputs: tuple[dict[str, Any], ...] = ()
    completion_signals: tuple[str, ...] = ()


class PluginRuntimeState:
    """Instantiate frozen host contributions once and aggregate their Run outputs."""

    def __init__(self, catalog: PluginCatalog | None) -> None:
        self.catalog = catalog
        entries = () if catalog is None else (
            *catalog.effect_analyzers,
            *catalog.result_processors,
            *catalog.validators,
            *catalog.approval_presenters,
        )
        self._components = {
            entry.identity.component_id: entry.factory()
            for entry in entries
        }
        self._used_specs: dict[str, AstraToolSpec] = {}
        self._evidence: list[dict[str, Any]] = []
        self._validation_inputs: list[dict[str, Any]] = []
        self._completion_signals: list[str] = []
        self.grounding = GroundingEvidenceLedger()

    async def execute(
        self,
        tool: AstraTool,
        tool_input: dict[str, Any],
        *,
        context: ToolExecutionContext | None,
    ) -> dict[str, Any]:
        started = plugin_diagnostics.timer()
        plugin_diagnostics.record(
            "invocation_started", provider_id=tool.spec.provider_id, tool_name=tool.spec.name
        )
        try:
            binding = self.catalog.tool_bindings.get(tool.spec.name) if self.catalog else None
            if binding is None or binding.executor_id == "in_process":
                result = await tool.run(tool_input, context=context)
            else:
                backend_entry = self.catalog.runtime_backends.get(binding.executor_id)
                backend = backend_entry.backend if backend_entry is not None else None
                if not isinstance(backend, RuntimeBackend):
                    raise ToolExecutionError(
                        "runtime_backend_unavailable",
                        "The frozen runtime backend is unavailable",
                    )
                result = await backend.execute(tool.spec, tool_input, context=context)
        except Exception as exc:
            plugin_diagnostics.record(
                "invocation_failed",
                duration_ms=plugin_diagnostics.elapsed_ms(started),
                provider_id=tool.spec.provider_id,
                tool_name=tool.spec.name,
                category=getattr(exc, "category", type(exc).__name__),
            )
            raise
        plugin_diagnostics.record(
            "invocation_completed",
            duration_ms=plugin_diagnostics.elapsed_ms(started),
            provider_id=tool.spec.provider_id,
            tool_name=tool.spec.name,
        )
        return result

    @classmethod
    def from_registry(cls, registry) -> "PluginRuntimeState":
        catalog = getattr(registry, "plugin_catalog", None)
        if catalog is None:
            from app.infrastructure.plugins.builtin_compat import (
                build_legacy_compatibility_catalog,
            )

            catalog = build_legacy_compatibility_catalog(registry)
        return cls(catalog)

    def effect_analyzer(self, spec: AstraToolSpec) -> ToolEffectAnalyzer:
        matches = self._matching(self.catalog.effect_analyzers if self.catalog else (), spec)
        if len(matches) > 1:
            raise ToolExecutionError(
                "effect_analyzer_ambiguous",
                "Multiple frozen effect analyzers match the invocation",
            )
        if not matches:
            return DefaultEffectAnalyzer()
        component = self._component(matches[0])
        if not isinstance(component, ToolEffectAnalyzer):
            raise ToolExecutionError(
                "effect_analyzer_unavailable",
                "The frozen effect analyzer is unavailable",
            )
        return component

    def approval_presenter(self, spec: AstraToolSpec) -> PluginApprovalPresenter | None:
        matches = self._matching(self.catalog.approval_presenters if self.catalog else (), spec)
        if len(matches) > 1:
            raise ToolExecutionError(
                "approval_presenter_ambiguous",
                "Multiple frozen approval presenters match the invocation",
            )
        if not matches:
            return None
        component = self._component(matches[0])
        if not isinstance(component, PluginApprovalPresenter):
            raise ToolExecutionError(
                "approval_presenter_unavailable",
                "The frozen approval presenter is unavailable",
            )
        return component

    def adapt_and_validate(
        self,
        spec: AstraToolSpec,
        raw: dict[str, Any],
    ) -> ToolResultEnvelope:
        binding = self.catalog.tool_bindings.get(spec.name) if self.catalog else None
        if binding is not None and binding.result_adapter_id != "envelope.v1":
            factory = binding.result_adapter_factory
            adapter = factory() if factory is not None else None
            if not isinstance(adapter, PluginResultAdapter):
                raise ToolExecutionError(
                    "result_adapter_unavailable",
                    "The frozen result adapter is unavailable",
                )
            try:
                raw = adapter.adapt(raw)
            except Exception as exc:
                raise ToolExecutionError(
                    "invalid_result",
                    "The legacy tool result could not be adapted",
                ) from exc
        envelope = validate_tool_result(raw, spec)
        if envelope.status == "failed":
            assert envelope.error is not None
            raise ToolExecutionError(envelope.error.category, envelope.error.message)
        return envelope

    def persistence_payload(
        self,
        spec: AstraToolSpec,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        binding = self.catalog.tool_bindings.get(spec.name) if self.catalog else None
        if binding is not None and binding.result_adapter_id == "legacy.raw.v0":
            return dict(result.get("data") or {})
        return result

    def process(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        result: dict[str, Any],
    ) -> ProcessedPluginResult:
        self._used_specs[spec.name] = spec
        outputs: list[PluginResultProcessingOutput] = []
        envelope = ToolResultEnvelope.model_validate(
            {
                key: result[key]
                for key in (
                    "protocol_version",
                    "status",
                    "data",
                    "warnings",
                    "metrics",
                    "artifacts",
                    "error",
                )
                if key in result
            }
        )
        media_types = {artifact.mime_type for artifact in envelope.artifacts}
        result_kind = str(envelope.data.get("kind")) if envelope.data.get("kind") else None
        for entry in self.catalog.result_processors if self.catalog else ():
            if not entry.applicability.matches(
                tool_name=spec.name,
                capabilities=set(spec.capabilities),
                result_kind=result_kind,
                media_types=media_types,
            ):
                continue
            processor = self._component(entry)
            if not isinstance(processor, PluginResultProcessor):
                raise ToolExecutionError(
                    "result_processor_unavailable",
                    "A frozen result processor is unavailable",
                )
            try:
                outputs.append(processor.process(spec, tool_input, result))
            except Exception as exc:
                raise ToolExecutionError(
                    "result_processing_failed",
                    "Tool result processing failed",
                ) from exc
        if not outputs:
            outputs.append(
                PluginResultProcessingOutput(
                    observation=AgentObservation(
                        kind="tool_result",
                        status="succeeded",
                        summary=f"{spec.name} completed",
                        data={"tool_name": spec.name, **result},
                    )
                )
            )
        self._record_outputs(outputs)
        primary = outputs[0].observation
        if len(outputs) > 1:
            primary = primary.model_copy(
                update={
                    "data": {
                        **primary.data,
                        "processor_observations": [
                            item.observation.model_dump(mode="json") for item in outputs[1:]
                        ],
                    }
                }
            )
        return ProcessedPluginResult(
            observation=primary,
            evidence=tuple(item.evidence for item in outputs if item.evidence),
            validation_inputs=tuple(
                item.validation_input for item in outputs if item.validation_input
            ),
            completion_signals=tuple(
                signal for item in outputs for signal in item.completion_signals
            ),
        )

    def record_failure(
        self,
        spec: AstraToolSpec | None,
        tool_input: dict[str, Any],
        error: dict[str, Any],
    ) -> None:
        if spec is None:
            return
        self._used_specs[spec.name] = spec
        for entry in self._matching(self.catalog.result_processors if self.catalog else (), spec):
            processor = self._component(entry)
            if not isinstance(processor, PluginResultProcessor):
                continue
            try:
                evidence = processor.process_failure(spec, tool_input, error)
            except Exception:
                continue
            if evidence:
                self._record_evidence(evidence)

    def evidence_pack(self, goal: str) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        fetched: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        warnings: list[str] = []
        for item in self._evidence:
            if item.get("domain") != "web":
                continue
            if item.get("kind") == "search":
                candidates.extend(item.get("candidates", []))
                warnings.extend(item.get("warnings", []))
            elif item.get("kind") == "fetch" and isinstance(item.get("source"), dict):
                fetched.append(item["source"])
                warnings.extend(item["source"].get("warnings", []))
            elif item.get("kind") == "failure" and isinstance(item.get("source"), dict):
                failed.append(item["source"])
        candidates = self._dedupe_candidates(candidates)
        return {
            "query": goal,
            "fragments": list(self._evidence),
            "validation_inputs": list(self._validation_inputs),
            "completion_signals": list(dict.fromkeys(self._completion_signals)),
            "candidates": candidates,
            "fetched_sources": fetched,
            "failed_sources": failed,
            "dedupe": {
                "candidate_count": sum(
                    len(item.get("candidates", [])) for item in self._evidence
                    if item.get("domain") == "web" and item.get("kind") == "search"
                ),
                "deduped_count": len(candidates),
            },
            "warnings": list(dict.fromkeys(warnings)),
            "external_evidence_attempted": any(
                item.get("domain") == "web" for item in self._evidence
            ),
            "grounding": self.grounding.model_dump(),
            "grounding_context": self.grounding.context_projection(),
        }

    def validate(
        self,
        result: dict[str, Any],
        evidence: dict[str, Any],
    ) -> list[AgentValidationOutcome]:
        outcomes: list[AgentValidationOutcome] = []
        seen: set[str] = set()
        for spec in self._used_specs.values():
            for entry in self._matching(self.catalog.validators if self.catalog else (), spec):
                component_id = entry.identity.component_id
                if component_id in seen:
                    continue
                seen.add(component_id)
                validator = self._component(entry)
                if not isinstance(validator, PluginResultValidator):
                    outcomes.append(
                        AgentValidationOutcome(
                            validator=component_id,
                            passed=False,
                            blocking=True,
                            warnings=["Frozen plugin validator is unavailable."],
                        )
                    )
                    continue
                try:
                    outcomes.append(validator.validate(result, evidence))
                except Exception:
                    outcomes.append(
                        AgentValidationOutcome(
                            validator=component_id,
                            passed=False,
                            blocking=True,
                            warnings=["Plugin validator failed safely."],
                        )
                    )
        outcomes.append(
            AgentValidationOutcome(
                validator="task_adapter",
                passed=True,
                blocking=True,
            )
        )
        return outcomes

    def has_completion_signal(self, signal: str) -> bool:
        return signal in self._completion_signals

    def component_identities_for(self, spec: AstraToolSpec) -> dict[str, list[dict[str, Any]]]:
        catalog = self.catalog
        if catalog is None:
            return {"analyzers": [], "processors": [], "validators": [], "presenters": []}
        return {
            name: [
                {
                    "identity": entry.identity.model_dump(mode="json"),
                    "applicability": entry.applicability.model_dump(mode="json"),
                }
                for entry in self._matching(entries, spec)
            ]
            for name, entries in {
                "analyzers": catalog.effect_analyzers,
                "processors": catalog.result_processors,
                "validators": catalog.validators,
                "presenters": catalog.approval_presenters,
            }.items()
        }

    def snapshot_catalog(self, registry) -> list[dict[str, Any]]:
        entries = []
        for name, spec in sorted(registry.specs().items()):
            binding = self.catalog.tool_bindings.get(name) if self.catalog else None
            provider = self.catalog.providers.get(spec.provider_id) if self.catalog else None
            entries.append(
                {
                    "tool": self._behavioral_tool_spec(spec),
                    "provider": (
                        provider.descriptor.model_dump(mode="json")
                        if provider is not None
                        else {
                            "provider_id": spec.provider_id,
                            "version": spec.version,
                            "digest": spec.provider_digest,
                            "configuration_revision": "default",
                        }
                    ),
                    "executor": {
                        "id": binding.executor_id if binding else spec.execution_backend,
                        "result_adapter_id": (
                            binding.result_adapter_id if binding else "envelope.v1"
                        ),
                        "backend_identity": (
                            self.catalog.runtime_backends[binding.executor_id]
                            .identity.model_dump(mode="json")
                            if self.catalog is not None
                            and binding is not None
                            and binding.executor_id in self.catalog.runtime_backends
                            else None
                        ),
                    },
                    "components": self.component_identities_for(spec),
                }
            )
        return entries

    def behavioral_digest(self, registry) -> str:
        return self._digest(self.snapshot_catalog(registry))

    @staticmethod
    def display_digest(registry) -> str:
        return PluginRuntimeState._digest(
            [
                {
                    "name": name,
                    "description": spec.description,
                }
                for name, spec in sorted(registry.specs().items())
            ]
        )

    @staticmethod
    def _behavioral_tool_spec(spec: AstraToolSpec) -> dict[str, Any]:
        return spec.model_dump(mode="json", exclude={"description"})

    @staticmethod
    def _digest(payload: Any) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _record_outputs(self, outputs: list[PluginResultProcessingOutput]) -> None:
        for output in outputs:
            if output.evidence:
                self._record_evidence(output.evidence)
            if output.validation_input:
                self._validation_inputs.append(dict(output.validation_input))
            self._completion_signals.extend(output.completion_signals)

    def _record_evidence(self, evidence: dict[str, Any]) -> None:
        item = dict(evidence)
        self._evidence.append(item)
        for raw in item.get("fragments", []):
            try:
                self.grounding.append(GroundingEvidenceFragment.model_validate(raw))
            except (TypeError, ValueError):
                continue

    def _component(self, entry: PluginComponentContribution) -> Any:
        return self._components.get(entry.identity.component_id)

    @staticmethod
    def _matching(entries, spec: AstraToolSpec) -> list[PluginComponentContribution]:
        return [
            entry
            for entry in entries
            if entry.applicability.matches(
                tool_name=spec.name,
                capabilities=set(spec.capabilities),
            )
        ]

    @staticmethod
    def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate.get("canonical_url") or candidate.get("url") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result
