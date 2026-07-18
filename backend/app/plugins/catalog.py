from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from app.plugins.contracts import (
    ComponentContribution,
    PluginContractError,
    PluginContribution,
    PluginDescriptor,
    PluginLifecycleState,
    RuntimeBackendContribution,
    ToolContribution,
)
from app.plugins.discovery import PluginCandidate, PluginDiscoverySource, discover_candidates
from app.plugins.interfaces import HealthProbe, ToolProviderPlugin
from app.tools.base import Tool, ToolExecutionError, ToolRegistry, ToolSpec


class PluginCatalogError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.safe_message = message


class CatalogTool(Tool):
    """Read-only manifest view that delegates execution to the provider tool."""

    def __init__(self, tool: Tool):
        self._tool = tool
        self._spec = tool.spec.model_copy(deep=True)

    @property
    def spec(self) -> ToolSpec:
        return self._spec.model_copy(deep=True)

    async def run(self, tool_input, *, context=None):
        if self._tool.spec.model_dump(mode="json") != self._spec.model_dump(mode="json"):
            raise ToolExecutionError(
                "provider_identity_changed", "Tool manifest changed after catalog assembly"
            )
        return await self._tool.run(tool_input, context=context)


@dataclass(frozen=True)
class ProviderStatus:
    descriptor: PluginDescriptor
    state: PluginLifecycleState
    reason: str | None = None


@dataclass(frozen=True)
class PluginCatalog:
    digest: str
    providers: Mapping[str, ProviderStatus]
    tools: Mapping[str, Tool]
    tool_bindings: Mapping[str, ToolContribution]
    effect_analyzers: tuple[ComponentContribution, ...]
    result_processors: tuple[ComponentContribution, ...]
    validators: tuple[ComponentContribution, ...]
    approval_presenters: tuple[ComponentContribution, ...]
    runtime_backends: Mapping[str, RuntimeBackendContribution]

    def tool_registry(self) -> ToolRegistry:
        return ToolRegistry().extend(self.tools.values())


class PluginCatalogBuilder:
    def __init__(
        self,
        sources: Iterable[PluginDiscoverySource],
        *,
        allowed_providers: Mapping[str, set[str]],
    ):
        self.sources = tuple(sources)
        self.allowed_providers = MappingProxyType(
            {provider_id: frozenset(digests) for provider_id, digests in allowed_providers.items()}
        )

    async def build(self) -> PluginCatalog:
        candidates = discover_candidates(self.sources)
        providers: dict[str, ProviderStatus] = {}
        contributions: list[PluginContribution] = []
        for candidate in candidates:
            descriptor = candidate.descriptor
            self._reject_duplicate_provider(providers, descriptor)
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.discovered
            )
            self._verify(candidate)
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.verified
            )
            contribution, health_target = self._load(candidate)
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.loaded
            )
            healthy, reason = await self._health(health_target)
            if not healthy:
                providers[descriptor.provider_id] = ProviderStatus(
                    descriptor, PluginLifecycleState.unhealthy, reason
                )
                continue
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.healthy
            )
            if not descriptor.enabled:
                providers[descriptor.provider_id] = ProviderStatus(
                    descriptor, PluginLifecycleState.disabled
                )
                continue
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.enabled
            )
            contributions.append(contribution)
        return self._assemble(providers, contributions)

    def build_static(self) -> PluginCatalog:
        """Assemble configured built-ins during synchronous application construction."""
        candidates = discover_candidates(self.sources)
        providers: dict[str, ProviderStatus] = {}
        contributions: list[PluginContribution] = []
        for candidate in candidates:
            descriptor = candidate.descriptor
            self._reject_duplicate_provider(providers, descriptor)
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.discovered
            )
            self._verify(candidate)
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.verified
            )
            contribution, health_target = self._load(candidate)
            if isinstance(health_target, HealthProbe):
                raise PluginCatalogError(
                    "health_check_required", "Provider requires asynchronous health verification"
                )
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.loaded
            )
            if not descriptor.enabled:
                providers[descriptor.provider_id] = ProviderStatus(
                    descriptor, PluginLifecycleState.disabled
                )
                continue
            providers[descriptor.provider_id] = ProviderStatus(
                descriptor, PluginLifecycleState.enabled
            )
            contributions.append(contribution)
        return self._assemble(providers, contributions)

    def _verify(self, candidate: PluginCandidate) -> None:
        descriptor = candidate.descriptor
        allowed = self.allowed_providers.get(descriptor.provider_id)
        if allowed is None:
            raise PluginCatalogError("provider_not_allowlisted", "Plugin provider is not allowed")
        if descriptor.digest not in allowed:
            raise PluginCatalogError("provider_digest_changed", "Plugin provider digest changed")
        if candidate.observed_digest != descriptor.digest:
            raise PluginCatalogError(
                "provider_digest_changed", "Observed plugin provider digest does not match"
            )
        if descriptor.source != "isolated_descriptor" and descriptor.trust_level not in {
            "platform",
            "managed",
            "trusted",
        }:
            raise PluginCatalogError(
                "provider_trust_too_low", "In-process plugin provider is not trusted"
            )

    @staticmethod
    def _load(candidate: PluginCandidate) -> tuple[PluginContribution, Any]:
        try:
            loaded = candidate.load()
            contribution = loaded.contribute() if isinstance(loaded, ToolProviderPlugin) else loaded
            if not isinstance(contribution, PluginContribution):
                raise PluginContractError("provider did not return a PluginContribution")
            if contribution.descriptor != candidate.descriptor:
                raise PluginContractError("loaded plugin descriptor does not match discovery")
            if candidate.descriptor.source == "isolated_descriptor":
                PluginCatalogBuilder._validate_isolated_contribution(contribution)
            return contribution.validate(), loaded
        except PluginContractError as exc:
            raise PluginCatalogError("invalid_plugin", str(exc)) from exc
        except Exception as exc:
            raise PluginCatalogError(
                "plugin_load_failed", "Plugin provider failed to load"
            ) from exc

    @staticmethod
    def _validate_isolated_contribution(contribution: PluginContribution) -> None:
        host_components = (
            *contribution.effect_analyzers,
            *contribution.result_processors,
            *contribution.validators,
            *contribution.approval_presenters,
            *contribution.runtime_backends,
        )
        if host_components:
            raise PluginContractError(
                "isolated providers cannot contribute executable host components"
            )
        if any(
            entry.executor_id == "in_process" or entry.result_adapter_factory is not None
            for entry in contribution.tools
        ):
            raise PluginContractError(
                "isolated provider tools require a host-managed transport binding"
            )

    @staticmethod
    async def _health(target: Any) -> tuple[bool, str | None]:
        if not isinstance(target, HealthProbe):
            return True, None
        try:
            report = await target.check()
        except Exception:
            return False, "health_check_failed"
        return report.healthy, report.reason

    @staticmethod
    def _reject_duplicate_provider(
        providers: dict[str, ProviderStatus], descriptor: PluginDescriptor
    ) -> None:
        if descriptor.provider_id in providers:
            raise PluginCatalogError(
                "plugin_conflict", "Multiple plugins use the same provider identity"
            )

    def _assemble(
        self,
        providers: dict[str, ProviderStatus],
        contributions: list[PluginContribution],
    ) -> PluginCatalog:
        tools: dict[str, Tool] = {}
        tool_bindings: dict[str, ToolContribution] = {}
        component_ids: set[str] = set()
        backend_ids: dict[str, RuntimeBackendContribution] = {}
        effect_bindings: set[str] = set()
        analyzers: list[ComponentContribution] = []
        processors: list[ComponentContribution] = []
        validators: list[ComponentContribution] = []
        presenters: list[ComponentContribution] = []
        for contribution in sorted(
            contributions,
            key=lambda item: (
                item.descriptor.provider_id,
                item.descriptor.plugin_id,
                item.descriptor.version,
            ),
        ):
            for entry in contribution.tools:
                name = entry.tool.spec.name
                if name in tools:
                    raise PluginCatalogError(
                        "plugin_conflict", f"Duplicate model-visible tool name: {name}"
                    )
                catalog_tool = CatalogTool(entry.tool)
                tools[name] = catalog_tool
                tool_bindings[name] = replace(entry, tool=catalog_tool)
            for entry in contribution.runtime_backends:
                self._claim_component(component_ids, entry.identity.component_id)
                if entry.backend_id in backend_ids:
                    raise PluginCatalogError(
                        "plugin_conflict", f"Duplicate runtime backend: {entry.backend_id}"
                    )
                backend_ids[entry.backend_id] = entry
            self._append_components(
                contribution.effect_analyzers,
                analyzers,
                component_ids,
                effect_bindings=effect_bindings,
            )
            self._append_components(contribution.result_processors, processors, component_ids)
            self._append_components(contribution.validators, validators, component_ids)
            self._append_components(contribution.approval_presenters, presenters, component_ids)
        payload = self._digest_payload(
            providers,
            tools,
            tool_bindings,
            analyzers,
            processors,
            validators,
            presenters,
            backend_ids,
        )
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return PluginCatalog(
            digest=digest,
            providers=MappingProxyType(dict(sorted(providers.items()))),
            tools=MappingProxyType(dict(sorted(tools.items()))),
            tool_bindings=MappingProxyType(dict(sorted(tool_bindings.items()))),
            effect_analyzers=tuple(analyzers),
            result_processors=tuple(processors),
            validators=tuple(validators),
            approval_presenters=tuple(presenters),
            runtime_backends=MappingProxyType(dict(sorted(backend_ids.items()))),
        )

    @staticmethod
    def _claim_component(component_ids: set[str], component_id: str) -> None:
        if component_id in component_ids:
            raise PluginCatalogError(
                "plugin_conflict", f"Duplicate component identity: {component_id}"
            )
        component_ids.add(component_id)

    def _append_components(
        self,
        source: tuple[ComponentContribution, ...],
        target: list[ComponentContribution],
        component_ids: set[str],
        *,
        effect_bindings: set[str] | None = None,
    ) -> None:
        for entry in source:
            self._claim_component(component_ids, entry.identity.component_id)
            if effect_bindings is not None:
                binding = entry.applicability.model_dump_json()
                if binding in effect_bindings:
                    raise PluginCatalogError(
                        "plugin_conflict", "Ambiguous effect analyzer applicability binding"
                    )
                effect_bindings.add(binding)
            target.append(entry)

    @staticmethod
    def _digest_payload(
        providers: dict[str, ProviderStatus],
        tools: dict[str, Tool],
        tool_bindings: dict[str, ToolContribution],
        analyzers: list[ComponentContribution],
        processors: list[ComponentContribution],
        validators: list[ComponentContribution],
        presenters: list[ComponentContribution],
        backends: dict[str, RuntimeBackendContribution],
    ) -> dict[str, Any]:
        def components(entries: list[ComponentContribution]) -> list[dict[str, Any]]:
            return [
                {
                    "identity": entry.identity.model_dump(mode="json"),
                    "applicability": entry.applicability.model_dump(mode="json"),
                }
                for entry in entries
            ]

        return {
            "providers": [
                {
                    "descriptor": status.descriptor.model_dump(mode="json"),
                    "state": status.state.value,
                }
                for _, status in sorted(providers.items())
            ],
            "tools": [
                {
                    "spec": tools[name].spec.model_dump(mode="json"),
                    "executor_id": tool_bindings[name].executor_id,
                    "result_adapter_id": tool_bindings[name].result_adapter_id,
                }
                for name in sorted(tools)
            ],
            "effect_analyzers": components(analyzers),
            "result_processors": components(processors),
            "validators": components(validators),
            "approval_presenters": components(presenters),
            "runtime_backends": [
                {
                    "backend_id": name,
                    "identity": entry.identity.model_dump(mode="json"),
                }
                for name, entry in sorted(backends.items())
            ],
        }
