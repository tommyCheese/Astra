from __future__ import annotations

from collections.abc import Mapping

from app.infrastructure.tools.base import (
    CapabilityAvailability,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
    validate_json_schema,
)

DEFAULT_TOOL_AUTHORITIES = frozenset(
    {
        "network_read",
        "sandboxed_compute",
        "temporary_compute",
        "artifact_write",
        "dependency_change",
        "command_execute",
        "process_execute",
        "process_execute_unknown",
        "workspace_read",
        "workspace_write",
        "workspace_delete",
        "network_write",
        "external_write",
        "sensitive_data_read",
        "credential_use",
        "delegation_create",
        "permission_change",
    }
)


class ToolRouter:
    def __init__(
        self,
        registry: ToolRegistry,
        allowed_tools: set[str] | None = None,
        *,
        allowed_capabilities: set[str] | None = None,
        allowed_permissions: set[str] | None = None,
        allowed_risks: set[str] | None = None,
        available_backends: set[str] | None = None,
    ):
        self.registry = registry
        self.allowed_tools = allowed_tools
        self.allowed_capabilities = set(
            DEFAULT_TOOL_AUTHORITIES if allowed_capabilities is None else allowed_capabilities
        )
        self.allowed_permissions = set(
            self.allowed_capabilities if allowed_permissions is None else allowed_permissions
        )
        self.allowed_risks = set(
            {"low", "sandboxed", "high"} if allowed_risks is None else allowed_risks
        )
        self.available_backends = set(
            {"in_process"} if available_backends is None else available_backends
        )

    def resolve(
        self,
        tool_name: str | None,
        tool_input: dict,
        *,
        validate_input: bool = True,
    ):
        if not tool_name:
            raise ToolExecutionError("invalid_decision", "Agent decision did not include a tool")
        tool = self.registry.get(tool_name)
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            raise ToolExecutionError("tool_not_allowed", f"Tool is not allowed: {tool_name}")
        if validate_input:
            self.validate_input(tool.spec, tool_input)
        if not set(tool.spec.capabilities) <= self.allowed_capabilities:
            raise ToolExecutionError(
                "tool_not_allowed", f"Tool capability is not allowed: {tool_name}"
            )
        if not set(tool.spec.permissions) <= self.allowed_permissions:
            raise ToolExecutionError(
                "permission_denied", f"Tool permission is not allowed: {tool_name}"
            )
        if tool.spec.risk not in self.allowed_risks:
            raise ToolExecutionError("permission_denied", f"Tool risk is not allowed: {tool_name}")
        if tool.spec.execution_backend not in self.available_backends:
            raise ToolExecutionError(
                "sandbox_unavailable", f"Tool backend is unavailable: {tool.spec.execution_backend}"
            )
        return tool

    @staticmethod
    def validate_input(spec: ToolSpec, tool_input: dict) -> None:
        try:
            validate_json_schema(tool_input, spec.input_schema, path="input")
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError(
                "invalid_input", f"Tool input does not match the schema for {spec.name}"
            ) from exc

    def availability(self, tool_name: str) -> CapabilityAvailability:
        try:
            self.resolve(tool_name, {}, validate_input=False)
            return CapabilityAvailability(capability=tool_name, available=True)
        except ToolExecutionError as exc:
            return CapabilityAvailability(
                capability=tool_name, available=False, reason=exc.category
            )

    def eligible_specs(self) -> tuple[Mapping[str, ToolSpec], dict[str, dict]]:
        eligible, unavailable = {}, {}
        for name, spec in self.registry.specs().items():
            status = self.availability(name)
            if status.available:
                eligible[name] = spec
            else:
                unavailable[name] = status.model_dump()
        return eligible, unavailable
