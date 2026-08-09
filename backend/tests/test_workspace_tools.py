from pathlib import Path

import pytest

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.tools.base import ToolExecutionContext, ToolExecutionError
from app.infrastructure.tools.registry import build_application_tool_registry
from app.infrastructure.tools.workspace import (
    WorkspaceEditTool,
    WorkspaceListTool,
    WorkspaceReadTool,
    WorkspaceSearchTool,
    WorkspaceWriteTool,
)


def context(workspace: Path, *, mode: str = "read_only") -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="run-1",
        tool_call_id="call-1",
        step_id=None,
        trace_id="trace-1",
        artifact_service=None,
        sandbox_service=None,
        task_id="task-1",
        workspace_path=workspace,
        workspace_mode=mode,
    )


async def test_workspace_list_read_and_search_are_bounded_and_hide_control_paths(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("first\nneedle here\nlast\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("private", encoding="utf-8")
    execution = context(tmp_path)

    listed = await WorkspaceListTool().run({"path": ".", "glob": "*.py"}, context=execution)
    assert listed["data"]["entries"] == [{"path": "src/app.py", "type": "file", "size_bytes": 23}]

    read = await WorkspaceReadTool().run({"path": "src/app.py", "line_start": 2, "line_end": 2}, context=execution)
    assert read["data"] == {
        "path": "src/app.py",
        "content": "needle here\n",
        "line_start": 2,
        "line_end": 2,
        "total_lines": 3,
        "truncated": True,
    }

    searched = await WorkspaceSearchTool().run({"query": "NEEDLE", "glob": "*.py"}, context=execution)
    assert searched["data"]["matches"] == [{"path": "src/app.py", "line": 2, "column": 1, "text": "needle here"}]
    assert searched["data"]["scanned_files"] == 1


async def test_workspace_write_and_edit_are_atomic_and_conflict_aware(tmp_path):
    execution = context(tmp_path, mode="read_write")
    written = await WorkspaceWriteTool().run({"path": "notes/result.md", "content": "alpha\nbeta\n"}, context=execution)
    assert written["data"] == {
        "path": "notes/result.md",
        "size_bytes": 11,
        "created": True,
    }
    assert (tmp_path / "notes" / "result.md").read_text(encoding="utf-8") == "alpha\nbeta\n"

    edited = await WorkspaceEditTool().run(
        {"path": "notes/result.md", "old_text": "beta", "new_text": "gamma"},
        context=execution,
    )
    assert edited["data"]["replacements"] == 1
    assert (tmp_path / "notes" / "result.md").read_text(encoding="utf-8") == "alpha\ngamma\n"

    (tmp_path / "notes" / "result.md").write_text("same same", encoding="utf-8")
    with pytest.raises(ToolExecutionError) as error:
        await WorkspaceEditTool().run(
            {"path": "notes/result.md", "old_text": "same", "new_text": "new"},
            context=execution,
        )
    assert error.value.category == "edit_conflict"
    assert (tmp_path / "notes" / "result.md").read_text(encoding="utf-8") == "same same"


async def test_workspace_tools_reject_traversal_links_protected_paths_and_unapproved_writes(
    tmp_path,
):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)

    for path in ("../outside.txt", ".git/config", "link.txt"):
        with pytest.raises(ToolExecutionError):
            await WorkspaceReadTool().run({"path": path}, context=context(tmp_path))

    with pytest.raises(ToolExecutionError) as error:
        await WorkspaceWriteTool().run({"path": "result.txt", "content": "blocked"}, context=context(tmp_path))
    assert error.value.category == "permission_denied"


def test_workspace_tools_are_registered_without_loop_coupling_and_honor_tool_toggles():
    registry = build_application_tool_registry(
        AstraRuntimeSettings(sandbox_enabled=False, tool_states={"workspace.edit": False})
    )

    assert set(registry.specs()) == {
        "forget",
        "remember",
        "swarm",
        "workspace.list",
        "workspace.read",
        "workspace.search",
        "workspace.write",
    }
    assert registry.specs()["workspace.write"].permissions == ["workspace_write"]
    assert registry.specs()["workspace.read"].side_effect_level == "read_only"
