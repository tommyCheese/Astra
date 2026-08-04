from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tools.base import Tool

PLUGIN_PROTOCOL_VERSION = "1"


class PluginContractError(ValueError):
    pass


class PluginLifecycleState(str, Enum):
    discovered = "discovered"
    verified = "verified"
    loaded = "loaded"
    healthy = "healthy"
    enabled = "enabled"
    disabled = "disabled"
    unhealthy = "unhealthy"
    draining = "draining"


class PluginDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=200)
    provider_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    digest: str = Field(min_length=1, max_length=256)
    protocol_version: str = PLUGIN_PROTOCOL_VERSION
    trust_level: Literal["platform", "managed", "trusted", "untrusted"] = "untrusted"
    source: Literal["builtin", "managed_package", "isolated_descriptor"]
    enabled: bool = True
    configuration_schema: dict[str, Any] = Field(default_factory=dict)
    configuration_revision: str = "default"

    @model_validator(mode="after")
    def validate_protocol(self) -> PluginDescriptor:
        if self.protocol_version != PLUGIN_PROTOCOL_VERSION:
            raise ValueError("unsupported plugin protocol version")
        return self


class ComponentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    component_id: str = Field(min_length=1, max_length=240)
    provider_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    digest: str = Field(min_length=1, max_length=256)


class ApplicabilityBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_names: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    result_kinds: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_selector(self) -> ApplicabilityBinding:
        if not any((self.tool_names, self.capabilities, self.result_kinds, self.media_types)):
            raise ValueError("applicability binding requires at least one selector")
        return self

    def matches(
        self,
        *,
        tool_name: str,
        capabilities: set[str] | None = None,
        result_kind: str | None = None,
        media_types: set[str] | None = None,
    ) -> bool:
        return bool(
            tool_name in self.tool_names
            or set(self.capabilities) & (capabilities or set())
            or (result_kind is not None and result_kind in self.result_kinds)
            or set(self.media_types) & (media_types or set())
        )


@dataclass(frozen=True)
class ToolContribution:
    tool: Tool
    executor_id: str


@dataclass(frozen=True)
class ComponentContribution:
    identity: ComponentIdentity
    applicability: ApplicabilityBinding
    factory: Callable[[], Any]

@dataclass(frozen=True)
class RuntimeBackendContribution:
    identity: ComponentIdentity
    backend_id: str
    backend: Any


@dataclass(frozen=True)
class PluginContribution:
    descriptor: PluginDescriptor
    tools: tuple[ToolContribution, ...] = ()
    effect_analyzers: tuple[ComponentContribution, ...] = ()
    result_processors: tuple[ComponentContribution, ...] = ()
    validators: tuple[ComponentContribution, ...] = ()
    approval_presenters: tuple[ComponentContribution, ...] = ()
    runtime_backends: tuple[RuntimeBackendContribution, ...] = ()

    def validate(self) -> PluginContribution:
        provider_id = self.descriptor.provider_id
        for entry in self.tools:
            if entry.tool.spec.provider_id != provider_id:
                raise PluginContractError("tool provider identity does not match plugin")
            if not entry.executor_id:
                raise PluginContractError("tool contribution requires an executor binding")
        components = (
            *self.effect_analyzers,
            *self.result_processors,
            *self.validators,
            *self.approval_presenters,
        )
        for entry in components:
            if entry.identity.provider_id != provider_id:
                raise PluginContractError("component provider identity does not match plugin")
            if not callable(entry.factory):
                raise PluginContractError("component contribution requires a factory")
        for entry in self.runtime_backends:
            if entry.identity.provider_id != provider_id:
                raise PluginContractError("runtime backend provider identity does not match plugin")
            if not entry.backend_id:
                raise PluginContractError("runtime backend requires an identifier")
        return self
