from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_THINKING_CAPABILITY_VERSION = 2

ModelThinkingDepth = Literal["minimal", "low", "medium", "high", "xhigh", "max"]
ModelThinkingToggle = Literal["optional", "always_on", "unavailable"]


class ModelThinkingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    depth: ModelThinkingDepth | None = None
    capability_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_depth(self) -> ModelThinkingSelection:
        if self.enabled and self.depth is None:
            raise ValueError("depth is required when model thinking is enabled")
        return self


class RunModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=160)
    api_key: str = Field(default="", max_length=4096)
    base_url: str = Field(default="", max_length=2048)
    thinking: ModelThinkingSelection | None = None


class ModelThinkingDepthOption(BaseModel):
    id: ModelThinkingDepth
    label: str


class ModelThinkingCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    supported: bool
    toggle: ModelThinkingToggle
    depths: list[ModelThinkingDepthOption] = Field(default_factory=list)
    default_enabled: bool
    default_depth: ModelThinkingDepth | None = None
    reason: str | None = None
    adapter: str
    capability_version: int = Field(
        default=MODEL_THINKING_CAPABILITY_VERSION,
        ge=1,
    )


class ModelThinkingReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)


class ModelThinkingCapabilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ModelThinkingReference] = Field(min_length=1, max_length=100)


class ModelThinkingCapabilitiesResponse(BaseModel):
    capabilities: list[ModelThinkingCapability]


class ModelContextReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)


class ModelContextCapabilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ModelContextReference] = Field(min_length=1, max_length=100)


class ModelContextCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    window_tokens: int
    max_output_tokens: int | None = None
    source: Literal["catalog", "fallback"]
    verified: bool
    documentation_url: str | None = None
    capability_version: Literal[2] = 2


class ModelContextCapabilitiesResponse(BaseModel):
    capabilities: list[ModelContextCapability]


class RuntimeDefaultModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    configured: bool


class ModelThinkingAdjustment(BaseModel):
    field: str
    requested: Any
    effective: Any
    reason: str


class EffectiveModelThinking(BaseModel):
    enabled: bool
    depth: ModelThinkingDepth | None = None


class ModelThinkingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: ModelThinkingSelection | None = None
    effective: EffectiveModelThinking
    source: Literal["explicit_model_control", "model_default"]
    adapter: str
    adjustments: list[ModelThinkingAdjustment] = Field(default_factory=list)
    capability_version: int = Field(
        default=MODEL_THINKING_CAPABILITY_VERSION,
        ge=1,
    )
