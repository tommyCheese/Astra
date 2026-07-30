from app.core.config import Settings
from app.runner.model_client import MockModelClient
from app.runner.node_worker import ReadOnlyAgentNodeExecutor
from app.tools.base import Tool, ToolRegistry, ToolSpec


class SelectionTool(Tool):
    def __init__(
        self,
        name: str,
        task_capability: str,
        *,
        side_effect_level: str = "read_only",
        idempotent: bool = True,
    ):
        self.spec = ToolSpec(
            name=name,
            version="test",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission=(
                "network_read"
                if side_effect_level == "read_only"
                else "workspace_write"
            ),
            side_effect_level=side_effect_level,
            idempotent=idempotent,
            task_capabilities=[task_capability],
        )

    async def run(self, tool_input, *, context=None):
        return {"status": "succeeded", "data": {}}


def test_parallel_executor_exposes_only_safe_semantic_capabilities():
    registry = ToolRegistry().extend(
        [
            SelectionTool("safe.search", "information.search"),
            SelectionTool(
                "write.workspace",
                "workspace.execute",
                side_effect_level="workspace_write",
            ),
        ]
    )
    executor = ReadOnlyAgentNodeExecutor(
        Settings(model_provider="mock"),
        model_client=MockModelClient(),
        tool_registry=registry,
    )

    assert executor.safe_capabilities == {
        "safe.search",
        "information.search",
    }
    assert "network_read" not in executor.safe_capabilities
    assert "workspace.execute" not in executor.safe_capabilities


def test_parallel_and_serial_resolution_share_matching_semantics():
    registry = ToolRegistry().extend(
        [
            SelectionTool("search.b", "information.search"),
            SelectionTool("search.a", "information.search"),
            SelectionTool(
                "unsafe.search",
                "information.search",
                idempotent=False,
            ),
        ]
    )
    executor = ReadOnlyAgentNodeExecutor(
        Settings(model_provider="mock"),
        model_client=MockModelClient(),
        tool_registry=registry,
    )

    safe = executor.resolver.resolve(
        ["information.search"],
        require_read_only=True,
        require_idempotent=True,
    )
    serial = executor.resolver.resolve(["information.search"])

    assert safe.candidate_names == ("search.a", "search.b")
    assert serial.candidate_names == ("search.a", "search.b", "unsafe.search")


def test_side_effect_only_requirement_is_not_parallel_eligible():
    registry = ToolRegistry().extend(
        [
            SelectionTool(
                "workspace.writer",
                "workspace.execute",
                side_effect_level="workspace_write",
            )
        ]
    )
    executor = ReadOnlyAgentNodeExecutor(
        Settings(model_provider="mock"),
        model_client=MockModelClient(),
        tool_registry=registry,
    )

    resolution = executor.resolver.resolve(
        ["workspace.execute"],
        require_read_only=True,
        require_idempotent=True,
    )

    assert resolution.candidate_names == ()
    assert resolution.capability_gaps == ("workspace.execute",)
    assert "workspace.execute" not in executor.safe_capabilities
