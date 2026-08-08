from __future__ import annotations

import os
import tempfile
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from app.infrastructure.repositories.workspaces import validate_workspace_path
from app.infrastructure.sandbox.runtime import PROTECTED_WORKSPACE_PATHS
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolSpec,
    ToolExecutionContext,
    ToolExecutionError,
    ToolResultEnvelope,
)

MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
MAX_READ_CHARACTERS = 100_000
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_BYTES = 16 * 1024 * 1024


def _workspace_root(context: ToolExecutionContext | None, *, write: bool = False) -> Path:
    if context is None or context.workspace_path is None:
        raise ToolExecutionError("workspace_unavailable", "A Task Workspace is required")
    if write and context.workspace_mode != "read_write":
        raise ToolExecutionError("permission_denied", "Workspace write access is required")
    try:
        return context.workspace_path.resolve(strict=True)
    except OSError as exc:
        raise ToolExecutionError("workspace_unavailable", "Task Workspace is unavailable") from exc


def _relative_parts(relative_path: str, *, allow_root: bool = False) -> tuple[str, ...]:
    if allow_root and relative_path in {"", "."}:
        return ()
    try:
        normalized = validate_workspace_path(relative_path)
    except ValueError as exc:
        raise ToolExecutionError("invalid_input", "Workspace path must be relative and normalized") from exc
    parts = Path(normalized).parts
    if any(part in PROTECTED_WORKSPACE_PATHS for part in parts):
        raise ToolExecutionError("permission_denied", "Protected Workspace paths are not accessible")
    return parts


def _resolve_path(
    root: Path,
    relative_path: str,
    *,
    allow_root: bool = False,
    must_exist: bool = True,
) -> Path:
    parts = _relative_parts(relative_path, allow_root=allow_root)
    candidate = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ToolExecutionError("sandbox_policy_violation", "Workspace links are not allowed")
    if must_exist and not candidate.exists():
        raise ToolExecutionError("workspace_path_not_found", "Workspace path does not exist")
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(root):
        raise ToolExecutionError("sandbox_policy_violation", "Workspace path escaped its root")
    return resolved


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise ToolExecutionError("invalid_input", "Workspace path must reference a file")
    metadata = path.stat()
    if metadata.st_nlink > 1:
        raise ToolExecutionError("sandbox_policy_violation", "Workspace hard links are not allowed")
    if metadata.st_size > MAX_TEXT_FILE_BYTES:
        raise ToolExecutionError("output_limit_exceeded", "Workspace text file exceeds the read limit")
    try:
        data = path.read_bytes()
        if b"\x00" in data:
            raise UnicodeDecodeError("utf-8", data, 0, 1, "binary data")
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError("unsupported_file_type", "Workspace file is not UTF-8 text") from exc


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_hidden(root: Path, path: Path) -> bool:
    parts = path.relative_to(root).parts
    return any(part.startswith(".") for part in parts)


def _is_protected(root: Path, path: Path) -> bool:
    return any(part in PROTECTED_WORKSPACE_PATHS for part in path.relative_to(root).parts)


def _atomic_write(path: Path, content: str) -> int:
    data = content.encode("utf-8")
    if len(data) > MAX_TEXT_FILE_BYTES:
        raise ToolExecutionError("artifact_limit_exceeded", "Workspace text exceeds the write limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".astra-write-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(data)


class WorkspaceListTool(AstraTool):
    spec = AstraToolSpec(
        name="workspace.list",
        version="1.0.0",
        description="List files and directories in the current Task Workspace with bounded results.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 1000},
                "glob": {"type": "string", "maxLength": 200},
                "recursive": {"type": "boolean"},
                "include_hidden": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "type": {"type": "string", "enum": ["file", "directory"]},
                            "size_bytes": {"type": "integer", "minimum": 0},
                        },
                        "required": ["path", "type", "size_bytes"],
                        "additionalProperties": False,
                    },
                },
                "truncated": {"type": "boolean"},
            },
            "required": ["path", "entries", "truncated"],
            "additionalProperties": False,
        },
        permission="workspace_read",
        side_effect_level="read_only",
        task_capabilities=["workspace.inspect", "workspace.list"],
        risk="low",
        resource_profile={"workspace": "read_only", "network": "none"},
    )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        root = _workspace_root(context)
        base = _resolve_path(root, str(tool_input.get("path", ".")), allow_root=True)
        if not base.is_dir():
            raise ToolExecutionError("invalid_input", "Workspace list path must be a directory")
        recursive = bool(tool_input.get("recursive", True))
        include_hidden = bool(tool_input.get("include_hidden", False))
        pattern = str(tool_input.get("glob", "*"))
        limit = int(tool_input.get("max_results", 200))
        candidates = base.rglob("*") if recursive else base.iterdir()
        entries: list[dict[str, Any]] = []
        truncated = False
        for path in sorted(candidates, key=lambda item: item.as_posix()):
            if (
                path.is_symlink()
                or _is_protected(root, path)
                or (not include_hidden and _is_hidden(root, path))
            ):
                continue
            relative = _relative(root, path)
            if not fnmatchcase(relative, pattern) and not fnmatchcase(path.name, pattern):
                continue
            if len(entries) >= limit:
                truncated = True
                break
            entries.append(
                {
                    "path": relative,
                    "type": "directory" if path.is_dir() else "file",
                    "size_bytes": path.stat().st_size if path.is_file() else 0,
                }
            )
        return ToolResultEnvelope(
            data={"path": _relative(root, base) if base != root else ".", "entries": entries, "truncated": truncated}
        ).model_dump(mode="json")


class WorkspaceReadTool(AstraTool):
    spec = AstraToolSpec(
        name="workspace.read",
        version="1.0.0",
        description="Read a bounded UTF-8 text range from a file in the current Task Workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 1000},
                "line_start": {"type": "integer", "minimum": 1},
                "line_end": {"type": "integer", "minimum": 1},
                "max_characters": {"type": "integer", "minimum": 1, "maximum": MAX_READ_CHARACTERS},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "line_start": {"type": "integer", "minimum": 1},
                "line_end": {"type": "integer", "minimum": 0},
                "total_lines": {"type": "integer", "minimum": 0},
                "truncated": {"type": "boolean"},
            },
            "required": ["path", "content", "line_start", "line_end", "total_lines", "truncated"],
            "additionalProperties": False,
        },
        permission="workspace_read",
        side_effect_level="read_only",
        task_capabilities=["workspace.inspect", "workspace.read"],
        risk="low",
        resource_profile={"workspace": "read_only", "network": "none"},
    )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        root = _workspace_root(context)
        relative_path = str(tool_input["path"])
        text = _read_text(_resolve_path(root, relative_path))
        lines = text.splitlines(keepends=True)
        start = int(tool_input.get("line_start", 1))
        requested_end = int(tool_input.get("line_end", len(lines) or 1))
        if requested_end < start:
            raise ToolExecutionError("invalid_input", "line_end must be greater than or equal to line_start")
        end = min(requested_end, len(lines))
        limit = int(tool_input.get("max_characters", 50_000))
        content = "".join(lines[start - 1 : end])
        truncated = len(content) > limit or requested_end < len(lines)
        content = content[:limit]
        return ToolResultEnvelope(
            data={
                "path": relative_path,
                "content": content,
                "line_start": start,
                "line_end": end,
                "total_lines": len(lines),
                "truncated": truncated,
            }
        ).model_dump(mode="json")


class WorkspaceSearchTool(AstraTool):
    spec = AstraToolSpec(
        name="workspace.search",
        version="1.0.0",
        description="Search UTF-8 Workspace files for literal text with file and result limits.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "path": {"type": "string", "maxLength": 1000},
                "glob": {"type": "string", "maxLength": 200},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "matches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "line": {"type": "integer", "minimum": 1},
                            "column": {"type": "integer", "minimum": 1},
                            "text": {"type": "string"},
                        },
                        "required": ["path", "line", "column", "text"],
                        "additionalProperties": False,
                    },
                },
                "scanned_files": {"type": "integer", "minimum": 0},
                "truncated": {"type": "boolean"},
            },
            "required": ["query", "matches", "scanned_files", "truncated"],
            "additionalProperties": False,
        },
        permission="workspace_read",
        side_effect_level="read_only",
        task_capabilities=["workspace.inspect", "workspace.search"],
        risk="low",
        resource_profile={"workspace": "read_only", "network": "none"},
    )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        root = _workspace_root(context)
        base = _resolve_path(root, str(tool_input.get("path", ".")), allow_root=True)
        pattern = str(tool_input.get("glob", "*"))
        case_sensitive = bool(tool_input.get("case_sensitive", False))
        query = str(tool_input["query"])
        needle = query if case_sensitive else query.casefold()
        limit = int(tool_input.get("max_results", 100))
        candidates = [base] if base.is_file() else base.rglob("*")
        matches: list[dict[str, Any]] = []
        scanned_files = 0
        scanned_bytes = 0
        truncated = False
        for path in candidates:
            if not path.is_file() or path.is_symlink() or _is_hidden(root, path) or _is_protected(root, path):
                continue
            relative = _relative(root, path)
            if not fnmatchcase(relative, pattern) and not fnmatchcase(path.name, pattern):
                continue
            size = path.stat().st_size
            if size > MAX_TEXT_FILE_BYTES:
                continue
            if scanned_files >= MAX_SEARCH_FILES or scanned_bytes + size > MAX_SEARCH_BYTES:
                truncated = True
                break
            scanned_files += 1
            scanned_bytes += size
            try:
                text = _read_text(path)
            except ToolExecutionError as exc:
                if exc.category == "unsupported_file_type":
                    continue
                raise
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                column = haystack.find(needle)
                if column < 0:
                    continue
                matches.append(
                    {"path": relative, "line": line_number, "column": column + 1, "text": line[:1000]}
                )
                if len(matches) >= limit:
                    truncated = True
                    break
            if truncated and len(matches) >= limit:
                break
        return ToolResultEnvelope(
            data={
                "query": query,
                "matches": matches,
                "scanned_files": scanned_files,
                "truncated": truncated,
            }
        ).model_dump(mode="json")


class WorkspaceWriteTool(AstraTool):
    spec = AstraToolSpec(
        name="workspace.write",
        version="1.0.0",
        description="Atomically create or replace a UTF-8 text file in the current Task Workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 1000},
                "content": {"type": "string", "maxLength": MAX_TEXT_FILE_BYTES},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "size_bytes": {"type": "integer", "minimum": 0},
                "created": {"type": "boolean"},
            },
            "required": ["path", "size_bytes", "created"],
            "additionalProperties": False,
        },
        permission="workspace_write",
        side_effect_level="workspace_write",
        task_capabilities=["workspace.write", "artifact.create"],
        risk="sandboxed",
        resource_profile={"workspace": "read_write", "network": "none"},
    )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        root = _workspace_root(context, write=True)
        relative_path = str(tool_input["path"])
        path = _resolve_path(root, relative_path, must_exist=False)
        existed = path.exists()
        if existed and not path.is_file():
            raise ToolExecutionError("invalid_input", "Workspace write path must be a file")
        size = _atomic_write(path, str(tool_input["content"]))
        return ToolResultEnvelope(
            data={"path": relative_path, "size_bytes": size, "created": not existed}
        ).model_dump(mode="json")


class WorkspaceEditTool(AstraTool):
    spec = AstraToolSpec(
        name="workspace.edit",
        version="1.0.0",
        description="Apply an exact, atomic text replacement to a UTF-8 file in the current Task Workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 1000},
                "old_text": {"type": "string", "minLength": 1, "maxLength": 200_000},
                "new_text": {"type": "string", "maxLength": 200_000},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "size_bytes": {"type": "integer", "minimum": 0},
                "replacements": {"type": "integer", "minimum": 1},
            },
            "required": ["path", "size_bytes", "replacements"],
            "additionalProperties": False,
        },
        permission="workspace_write",
        side_effect_level="workspace_write",
        task_capabilities=["workspace.edit", "code.edit"],
        risk="sandboxed",
        idempotent=False,
        resource_profile={"workspace": "read_write", "network": "none"},
    )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        root = _workspace_root(context, write=True)
        relative_path = str(tool_input["path"])
        path = _resolve_path(root, relative_path)
        content = _read_text(path)
        old_text = str(tool_input["old_text"])
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ToolExecutionError("edit_conflict", "old_text was not found in the Workspace file")
        replace_all = bool(tool_input.get("replace_all", False))
        if occurrences > 1 and not replace_all:
            raise ToolExecutionError("edit_conflict", "old_text is not unique; set replace_all explicitly")
        replacements = occurrences if replace_all else 1
        updated = content.replace(old_text, str(tool_input["new_text"]), -1 if replace_all else 1)
        size = _atomic_write(path, updated)
        return ToolResultEnvelope(
            data={"path": relative_path, "size_bytes": size, "replacements": replacements}
        ).model_dump(mode="json")


def workspace_tools() -> tuple[AstraTool, ...]:
    return (
        WorkspaceListTool(),
        WorkspaceReadTool(),
        WorkspaceSearchTool(),
        WorkspaceWriteTool(),
        WorkspaceEditTool(),
    )
