from __future__ import annotations

import difflib
from typing import Any


def skill_git_diff(
    before_files: dict[str, bytes],
    after_files: dict[str, bytes],
) -> tuple[str, list[dict[str, Any]]]:
    patches: list[str] = []
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before_files) | set(after_files)):
        before = before_files.get(path)
        after = after_files.get(path)
        if before == after:
            continue
        status = _change_status(before, after)
        patch = _file_patch(path, before, after, status)
        patches.append(patch)
        changes.append({"path": path, "status": status, "patch": patch})
    return "\n".join(patches), changes


def _change_status(before: bytes | None, after: bytes | None) -> str:
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "modified"


def _file_patch(path: str, before: bytes | None, after: bytes | None, status: str) -> str:
    header = [f"diff --git a/{path} b/{path}\n"]
    if status == "added":
        header.append("new file mode 100644\n")
    elif status == "removed":
        header.append("deleted file mode 100644\n")
    try:
        before_lines = [] if before is None else before.decode("utf-8").splitlines(keepends=True)
        after_lines = [] if after is None else after.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return "".join(header) + f"Binary files a/{path} and b/{path} differ\n"
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="/dev/null" if before is None else f"a/{path}",
        tofile="/dev/null" if after is None else f"b/{path}",
    )
    return "".join([*header, *diff])
