from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.infrastructure.plugins.contracts import (
    PluginApplicabilityBinding,
    PluginComponentContribution,
    PluginComponentIdentity,
    PluginContractError,
    PluginContribution,
    PluginDescriptor,
    PluginLifecycleState,
    PluginToolContribution,
)
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolSpec,
    ToolExecutionError,
    ToolResultEnvelope,
    validate_tool_result,
)


class ExampleTool(AstraTool):
    spec = AstraToolSpec(
        name="example.read",
        version="1",
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        permission="network_read",
        side_effect_level="read_only",
        provider_id="example.provider",
        provider_digest="sha256:provider",
        trust_level="managed",
    )

    async def run(self, tool_input, *, context=None):
        return ToolResultEnvelope(data={"value": "ok"}).model_dump(mode="json")


def descriptor(**updates):
    values = {
        "plugin_id": "example.plugin",
        "provider_id": "example.provider",
        "version": "1",
        "digest": "sha256:provider",
        "source": "managed_package",
        "trust_level": "managed",
    }
    values.update(updates)
    return PluginDescriptor(**values)


def test_plugin_contract_is_versioned_frozen_and_tracks_lifecycle():
    item = descriptor()

    assert item.protocol_version == "1"
    assert PluginLifecycleState.enabled.value == "enabled"
    with pytest.raises(ValidationError):
        item.version = "2"
    with pytest.raises(ValidationError, match="unsupported plugin protocol"):
        descriptor(protocol_version="999")


def test_applicability_matches_explicit_tool_capability_result_and_media():
    binding = PluginApplicabilityBinding(
        tool_names=("example.read",),
        capabilities=("network_read",),
        result_kinds=("evidence",),
        media_types=("application/json",),
    )

    assert binding.matches(tool_name="example.read")
    assert binding.matches(tool_name="other", capabilities={"network_read"})
    assert binding.matches(tool_name="other", result_kind="evidence")
    assert binding.matches(tool_name="other", media_types={"application/json"})
    assert not binding.matches(tool_name="other")


def test_malformed_contribution_fails_with_safe_contract_error():
    identity = PluginComponentIdentity(component_id="processor", provider_id="other.provider", version="1", digest="sha256:x")
    contribution = PluginContribution(
        descriptor=descriptor(),
        tools=(PluginToolContribution(tool=ExampleTool(), executor_id="in_process"),),
        result_processors=(
            PluginComponentContribution(
                identity=identity,
                applicability=PluginApplicabilityBinding(tool_names=("example.read",)),
                factory=SimpleNamespace,
            ),
        ),
    )

    with pytest.raises(PluginContractError, match="component provider identity"):
        contribution.validate()


def test_tool_result_envelope_is_strict_versioned_and_validates_output_schema():
    valid = validate_tool_result(ToolResultEnvelope(data={"value": "ok"}).model_dump(mode="json"), ExampleTool.spec)
    assert valid.protocol_version == "1"
    assert valid.data == {"value": "ok"}

    with pytest.raises(ToolExecutionError) as missing:
        validate_tool_result(ToolResultEnvelope(data={}).model_dump(), ExampleTool.spec)
    assert missing.value.category == "invalid_result"

    with pytest.raises(ToolExecutionError) as extra:
        validate_tool_result(
            ToolResultEnvelope(data={"value": "ok"}).model_dump() | {"secret": "leak"},
            ExampleTool.spec,
        )
    assert extra.value.category == "invalid_result"
    assert "leak" not in extra.value.message


def test_failed_tool_result_requires_a_safe_error_and_skips_success_schema():
    failed = validate_tool_result(
        {
            "protocol_version": "1",
            "status": "failed",
            "data": {},
            "error": {"category": "upstream_failed", "message": "Provider unavailable"},
        },
        ExampleTool.spec,
    )

    assert failed.error.category == "upstream_failed"
    with pytest.raises(ValidationError):
        ToolResultEnvelope(status="failed")
    with pytest.raises(ValidationError):
        ToolResultEnvelope(
            status="failed",
            error={"category": "failed", "message": "safe", "private": "leak"},
        )
