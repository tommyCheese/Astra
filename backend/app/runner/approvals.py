import hashlib
import json
import re
import shlex
from typing import Any

SHELL_META = re.compile(r"(?:&&|\|\||[|;&<>`]|\$\(|\$\{|\n|\r)")
SECRET_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|authorization|password)\s*[:=]\s*(?:bearer\s+)?\S+"
)


def canonical_input(tool_input: dict[str, Any]) -> str:
    return json.dumps(tool_input, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def input_hash(tool_input: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_input(tool_input).encode()).hexdigest()


def safe_preview(tool_name: str, tool_input: dict[str, Any], limit: int = 1000) -> str:
    if tool_name == "bash_execute":
        value = str(tool_input.get("command", ""))
    else:
        value = canonical_input(tool_input)
    return SECRET_VALUE.sub(r"\1=[REDACTED]", value)[:limit]


def similar_matcher(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name != "bash_execute":
        return None
    command = str(tool_input.get("command", "")).strip()
    if not command or SHELL_META.search(command):
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens or any("=" in token and index == 0 for index, token in enumerate(tokens)):
        return None
    prefix_length = 2 if tokens[0] in {"npm", "pnpm", "yarn", "git", "python", "python3"} and len(tokens) > 1 else 1
    return {"kind": "command_prefix", "tokens": tokens[:prefix_length]}


def matcher_matches(matcher: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    kind = matcher.get("kind")
    if kind == "exact":
        return matcher.get("input_hash") == input_hash(tool_input)
    if kind != "command_prefix":
        return False
    command = str(tool_input.get("command", "")).strip()
    if not command or SHELL_META.search(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    prefix = matcher.get("tokens")
    return isinstance(prefix, list) and bool(prefix) and tokens[: len(prefix)] == prefix

