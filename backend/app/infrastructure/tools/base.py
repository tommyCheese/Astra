from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ToolExecutionError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message

    def to_payload(self) -> dict[str, str]:
        return {"category": self.category, "message": self.message}


class AstraToolSpec(BaseModel):
    name: str
    version: str
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: str
    side_effect_level: str
    timeout_seconds: int = 20
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    error_categories: list[str] = Field(default_factory=list)
    idempotent: bool = True
    # Provider-neutral task abilities used for execution-time tool selection.
    # These are intentionally separate from ``capabilities`` and ``permissions``,
    # which describe the tool's security authority ceiling.
    task_capabilities: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    risk: str = "low"
    execution_backend: str = "in_process"
    resource_profile: dict[str, Any] = Field(default_factory=dict)
    artifact_behavior: dict[str, Any] = Field(default_factory=dict)
    provider_id: str = "astra.builtin"
    provider_digest: str = "builtin"
    trust_level: str = "platform"

    def model_post_init(self, __context: Any) -> None:
        if not self.permissions:
            self.permissions = [self.permission]
        if not self.capabilities:
            self.capabilities = [self.permission]


class ToolArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    mime_type: str
    content_url: str | None = None
    size_bytes: int = 0
    checksum: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResultError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)


class ToolResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1"] = "1"
    status: Literal["succeeded", "failed"] = "succeeded"
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ToolArtifactReference] = Field(default_factory=list)
    error: ToolResultError | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "ToolResultEnvelope":
        if self.status == "failed" and not self.error:
            raise ValueError("failed tool result requires an error")
        if self.status == "succeeded" and self.error:
            raise ValueError("successful tool result cannot include an error")
        return self


def validate_tool_result(output: dict[str, Any], spec: AstraToolSpec) -> ToolResultEnvelope:
    """Validate the host envelope and the tool-declared schema without leaking payload data."""
    try:
        envelope = ToolResultEnvelope.model_validate(output)
        if envelope.status == "succeeded":
            validate_json_schema(envelope.data, spec.output_schema, path="data")
    except (TypeError, ValueError, ValidationError) as exc:
        raise ToolExecutionError(
            "invalid_result", f"AstraTool returned an invalid result for {spec.name}"
        ) from exc
    return envelope


def validate_json_schema(value: Any, schema: dict[str, Any], *, path: str = "value") -> None:
    """Validate the bounded JSON Schema subset accepted by tool manifests."""
    if not schema:
        return
    _validate_alternatives(value, schema.get("anyOf"), path)
    _validate_declared_type(value, schema, path)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    if isinstance(value, dict):
        _validate_object(value, schema, path)
    elif isinstance(value, list):
        _validate_array(value, schema, path)
    elif isinstance(value, str):
        _validate_string(value, schema, path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path)


def _validate_alternatives(value: Any, alternatives: Any, path: str) -> None:
    if alternatives is None:
        return
    if not isinstance(alternatives, list) or not alternatives:
        raise ValueError(f"{path} has an invalid anyOf declaration")
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            raise ValueError(f"{path} has an invalid anyOf declaration")
        try:
            validate_json_schema(value, alternative, path=path)
            return
        except ValueError:
            continue
    raise ValueError(f"{path} does not match any allowed schema")


def _validate_declared_type(value: Any, schema: dict[str, Any], path: str) -> None:
    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected is not None and expected not in type_checks:
        raise ValueError(f"{path} has an unsupported type declaration")
    if expected in type_checks and not type_checks[expected](value):
        raise ValueError(f"{path} does not match type {expected}")


def _validate_object(value: dict, schema: dict[str, Any], path: str) -> None:
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError(f"{path} has an invalid required declaration")
    if any(key not in value for key in required):
        raise ValueError(f"{path} is missing required properties")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"{path} has invalid properties")
    _validate_properties(value, properties, path)
    if schema.get("additionalProperties") is False and set(value) - set(properties):
        raise ValueError(f"{path} has additional properties")


def _validate_properties(value: dict, properties: dict, path: str) -> None:
    for key, child_schema in properties.items():
        if key not in value:
            continue
        if not isinstance(child_schema, dict):
            raise ValueError(f"{path}.{key} has an invalid schema")
        validate_json_schema(value[key], child_schema, path=f"{path}.{key}")


def _validate_array(value: list, schema: dict[str, Any], path: str) -> None:
    if "items" in schema:
        item_schema = schema["items"]
        if not isinstance(item_schema, dict):
            raise ValueError(f"{path} has invalid items")
        for index, item in enumerate(value):
            validate_json_schema(item, item_schema, path=f"{path}[{index}]")
    if "minItems" in schema and len(value) < int(schema["minItems"]):
        raise ValueError(f"{path} has too few items")
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        raise ValueError(f"{path} has too many items")


def _validate_string(value: str, schema: dict[str, Any], path: str) -> None:
    if "minLength" in schema and len(value) < int(schema["minLength"]):
        raise ValueError(f"{path} is shorter than allowed")
    if "maxLength" in schema and len(value) > int(schema["maxLength"]):
        raise ValueError(f"{path} is longer than allowed")


def _validate_number(value: int | float, schema: dict[str, Any], path: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise ValueError(f"{path} is below the minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise ValueError(f"{path} is above the maximum")


class CapabilityAvailability(BaseModel):
    capability: str
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    tool_call_id: str
    step_id: str | None
    trace_id: str
    artifact_service: Any
    sandbox_service: Any
    task_id: str | None = None
    workspace_path: Path | None = None
    workspace_mode: str = "none"
    effect_plan: dict[str, Any] | None = None
    runtime_identity_id: str | None = None
    skill_bindings: tuple[dict[str, str], ...] = ()
    skill_draft_test: bool = False
    skill_input_provider: Any = None
    agent_execution_id: str | None = None
    delegation_context: Any = None


async def materialize_skill_inputs(
    context: ToolExecutionContext | None,
    input_dir: Path,
) -> list[dict[str, str]]:
    if (
        context is None
        or not context.skill_bindings
        or context.skill_input_provider is None
    ):
        return []
    return await context.skill_input_provider.materialize_inputs(
        context.run_id,
        list(context.skill_bindings),
        input_dir,
    )


class AstraTool(ABC):
    spec: AstraToolSpec

    @abstractmethod
    async def run(
        self, tool_input: dict[str, Any], *, context: ToolExecutionContext | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError


class AstraToolRegistry:
    def __init__(self, *, plugin_catalog: Any | None = None) -> None:
        self._tools: dict[str, AstraTool] = {}
        self._plugin_catalog = plugin_catalog

    @property
    def plugin_catalog(self) -> Any | None:
        return self._plugin_catalog

    def register(self, tool: AstraTool) -> None:
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> AstraTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError("tool_not_allowed", f"AstraTool is not registered: {name}") from exc

    def specs(self) -> dict[str, AstraToolSpec]:
        return {name: tool.spec for name, tool in self._tools.items()}

    def tools(self) -> tuple[AstraTool, ...]:
        return tuple(self._tools.values())

    def extend(self, tools: Iterable[AstraTool]) -> "AstraToolRegistry":
        for tool in tools:
            self.register(tool)
        return self

    @classmethod
    def compose(cls, *registries: "AstraToolRegistry") -> "AstraToolRegistry":
        catalogs = [registry.plugin_catalog for registry in registries if registry.plugin_catalog]
        if len({id(catalog) for catalog in catalogs}) > 1:
            raise ValueError("Cannot compose registries from different plugin catalogs")
        combined = cls(plugin_catalog=catalogs[0] if catalogs else None)
        for registry in registries:
            combined.extend(registry._tools.values())
        return combined
