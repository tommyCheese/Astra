from __future__ import annotations

from app.tools.base import Tool, ToolRegistry, ToolSpec
from app.tools.bash import BashExecuteTool
from app.tools.chart import ChartRenderTool
from app.tools.router import ToolRouter
from app.tools.selection import CapabilityToolResolver
from app.tools.web.fetching import WebFetchTool
from app.tools.web.search import WebSearchTool


class StaticTool(Tool):
    def __init__(
        self,
        name: str,
        task_capabilities: list[str],
        *,
        side_effect_level: str = "read_only",
        idempotent: bool = True,
        execution_backend: str = "in_process",
        provider_id: str = "test.provider",
    ):
        self.spec = ToolSpec(
            name=name,
            version="1",
            description=f"private description for {name}",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission="network_read",
            side_effect_level=side_effect_level,
            task_capabilities=task_capabilities,
            capabilities=["network_read"],
            permissions=["network_read"],
            idempotent=idempotent,
            execution_backend=execution_backend,
            provider_id=provider_id,
            provider_digest="sha256:test",
        )

    async def run(self, tool_input, *, context=None):
        return {}


def registry(*tools: Tool) -> ToolRegistry:
    return ToolRegistry().extend(tools)


def test_task_capabilities_are_separate_from_security_authorities():
    spec = ToolSpec(
        name="search.one",
        version="1",
        input_schema={},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.search"],
    )

    assert spec.task_capabilities == ["information.search"]
    assert spec.capabilities == ["network_read"]
    assert spec.permissions == ["network_read"]


def test_semantic_resolution_uses_router_eligibility_and_stable_ordering():
    tools = registry(
        StaticTool("zeta.search", ["information.search"]),
        StaticTool("alpha.search", ["information.search"]),
        StaticTool(
            "offline.search",
            ["information.search"],
            execution_backend="sandbox.remote",
        ),
    )
    resolution = CapabilityToolResolver(
        ToolRouter(tools, available_backends={"in_process"})
    ).resolve(["information.search"])

    assert resolution.candidate_names == ("alpha.search", "zeta.search")
    assert all(
        candidate.matched_capabilities == ("information.search",)
        for candidate in resolution.candidates
    )
    assert [(item.tool_name, item.reason) for item in resolution.rejections] == [
        ("offline.search", "sandbox_unavailable")
    ]
    assert resolution.capability_gaps == ()


def test_exact_tool_name_is_not_accepted_as_a_semantic_capability():
    tools = registry(
        StaticTool("legacy.search", ["information.search"]),
        StaticTool("replacement.search", ["information.search"]),
    )
    resolution = CapabilityToolResolver(ToolRouter(tools)).resolve(["legacy.search"])

    assert resolution.candidate_names == ()
    assert resolution.capability_gaps == ("legacy.search",)


def test_successful_observations_accumulate_only_within_the_selected_plan_node():
    tools = registry(
        StaticTool(
            "search",
            ["information.search", "source.discover"],
        ),
        StaticTool(
            "fetch",
            ["information.read", "source.retrieve", "evidence.extract"],
        ),
    )
    observations = [
        {
            "plan_node_id": "node-a",
            "status": "succeeded",
            "data": {"tool_name": "search"},
        },
        {
            "plan_node_id": "node-b",
            "status": "succeeded",
            "data": {"tool_name": "fetch"},
        },
        {
            "plan_node_id": "node-a",
            "status": "failed",
            "data": {"tool_name": "fetch"},
        },
    ]

    resolution = CapabilityToolResolver(ToolRouter(tools)).resolve(
        ["information.search", "source.retrieve"],
        observations=observations,
        plan_node_id="node-a",
    )

    assert resolution.satisfied_capabilities == ("information.search",)
    assert resolution.unresolved_capabilities == ("source.retrieve",)
    assert resolution.candidate_names == ("fetch",)
    assert resolution.candidates[0].matched_capabilities == ("source.retrieve",)


def test_safety_constraints_exclusions_gaps_and_audit_payload_are_bounded():
    tools = registry(
        StaticTool("read.compute", ["computation.execute"]),
        StaticTool(
            "write.compute",
            ["computation.execute"],
            side_effect_level="external_side_effect",
            idempotent=False,
        ),
    )
    resolution = CapabilityToolResolver(ToolRouter(tools)).resolve(
        ["computation.execute"],
        require_read_only=True,
        require_idempotent=True,
        excluded_tools={"read.compute"},
        plan_node_id="node-sensitive",
    )

    assert resolution.candidate_names == ()
    assert resolution.capability_gaps == ("computation.execute",)
    assert [(item.tool_name, item.reason) for item in resolution.rejections] == [
        ("read.compute", "excluded"),
        ("write.compute", "side_effect_not_read_only"),
    ]
    audit = resolution.audit_payload()
    assert audit["plan_node_id"] == "node-sensitive"
    assert audit["capability_gaps"] == ["computation.execute"]
    assert "description" not in str(audit)
    assert "input_schema" not in str(audit)


def test_empty_requirement_keeps_all_safe_eligible_tools_available():
    tools = registry(
        StaticTool("beta", []),
        StaticTool("alpha", ["information.search"]),
        StaticTool("unsafe", ["workspace.execute"], idempotent=False),
    )

    resolution = CapabilityToolResolver(ToolRouter(tools)).resolve(
        [],
        require_idempotent=True,
    )

    assert resolution.candidate_names == ("alpha", "beta")
    assert resolution.unresolved_capabilities == ()
    assert resolution.capability_gaps == ()


def test_builtin_tools_declare_provider_neutral_task_capabilities():
    assert WebSearchTool.spec.task_capabilities == [
        "information.search",
        "source.discover",
    ]
    assert WebFetchTool.spec.task_capabilities == [
        "information.read",
        "source.retrieve",
        "evidence.extract",
    ]
    assert ChartRenderTool.spec.task_capabilities == [
        "data.visualize",
        "artifact.render",
    ]
    assert BashExecuteTool.spec.task_capabilities == [
        "workspace.execute",
        "computation.execute",
    ]
