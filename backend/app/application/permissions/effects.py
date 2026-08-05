from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import PurePosixPath
from typing import Any

from app.common.schemas.permissions import ActionEffectPlan, EffectItem, EffectKind
from app.infrastructure.plugins.interfaces import ToolEffectAnalyzer
from app.infrastructure.tools.base import AstraToolSpec

ANALYZER_VERSION = "1"
ANALYZER_DIGEST = hashlib.sha256(b"astra-effect-analyzer-v1").hexdigest()

READ_ONLY_COMMANDS = {
    "cat",
    "cut",
    "diff",
    "du",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "sort",
    "stat",
    "tail",
    "test",
    "wc",
}
WRITE_COMMANDS = {
    "cp",
    "install",
    "mkdir",
    "mv",
    "tee",
    "touch",
    "truncate",
}
DELETE_COMMANDS = {"rm", "rmdir", "unlink"}
NETWORK_COMMANDS = {"curl", "wget", "nc", "ncat", "ssh", "scp", "rsync"}
SAFE_SHELL_BUILTINS = {"echo", "printf", "true", "false"}
SHELL_COMPLEX = re.compile(r"(?:&&|\|\||[|;&`]|\$\(|\$\{|[<>]\(|\n|\r)")
REDIRECTION = re.compile(r"(?<!<)(?:>>?|[0-9]+>>?)\s*([^\s;&|]+)")


def effect_plan_hash(plan: ActionEffectPlan) -> str:
    payload = plan.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class WebEffectAnalyzer(ToolEffectAnalyzer):
    def analyze(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan:
        resource = (
            f"web://search/{str(tool_input.get('query', '')).strip()}"
            if "query" in tool_input and not tool_input.get("url")
            else str(tool_input.get("url", "web://unknown"))
        )
        return _effect_plan(
            spec,
            "读取公开网络内容",
            [EffectItem(kind=EffectKind.network_read, resource=resource)],
            ["network_read"],
            approval_required=False,
            network_scope={"mode": "public_read"},
        )


class ChartEffectAnalyzer(ToolEffectAnalyzer):
    def analyze(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan:
        effects = [
            EffectItem(
                kind=EffectKind.temporary_compute,
                resource="sandbox://data-viz",
                persistent=False,
            ),
            EffectItem(
                kind=EffectKind.artifact_write,
                resource=f"artifact://task/{task_id}/chart",
                risk="moderate",
                persistent=True,
            ),
        ]
        if tool_input.get("input_workspace_path"):
            effects.append(
                EffectItem(
                    kind=EffectKind.workspace_read,
                    resource=_workspace_resource(task_id, str(tool_input["input_workspace_path"])),
                )
            )
        return _effect_plan(
            spec,
            "读取任务数据并生成图表交付物",
            effects,
            ["workspace_read", "temporary_compute", "artifact_write"],
            approval_required=True,
        )


class DefaultEffectAnalyzer(ToolEffectAnalyzer):
    def analyze(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan:
        specialized = _specialized_effect_plan(spec, tool_input, task_id)
        if specialized is not None:
            return specialized
        declared = set(spec.permissions)
        mapped = _declared_effects(spec, tool_input, task_id, declared)
        if mapped:
            classification = ActionEffectPlan(
                tool_name=spec.name,
                tool_version=spec.version,
                summary="classification",
                effects=mapped,
                required_permissions=list(spec.permissions),
                analyzer_version=ANALYZER_VERSION,
            )
            return _effect_plan(
                spec,
                f"执行 {spec.name} 的声明式资源操作",
                _deduplicate_effects(mapped),
                list(spec.permissions),
                approval_required=is_side_effecting(classification),
            )
        effects = [
            EffectItem(
                kind=EffectKind.process_execute_unknown,
                resource=f"tool://{spec.name}",
                risk="high",
                reversible=False,
                persistent=True,
            )
        ]
        return _effect_plan(
            spec,
            f"执行无法精确分类的工具 {spec.name}",
            effects,
            list(spec.permissions),
            approval_required=True,
        )


def _specialized_effect_plan(spec, tool_input, task_id) -> ActionEffectPlan | None:
    analyzers = {
        "web_search": WebEffectAnalyzer,
        "web_fetch": WebEffectAnalyzer,
        "bash_execute": BashEffectAnalyzer,
        "chart.render": ChartEffectAnalyzer,
    }
    analyzer = analyzers.get(spec.name)
    return analyzer().analyze(spec, tool_input, task_id=task_id) if analyzer else None


def _declared_effects(spec, tool_input, task_id, declared) -> list[EffectItem]:
    effects = []
    workspace_effect = _declared_workspace_effect(tool_input, task_id, declared)
    if workspace_effect:
        effects.append(workspace_effect)
    if "network_read" in declared:
        effects.append(
            EffectItem(
                kind=EffectKind.network_read,
                resource=str(tool_input.get("url") or f"provider://{spec.provider_id}/{spec.name}"),
            )
        )
    effects.extend(_mapped_declared_effects(spec, tool_input, task_id, declared))
    return effects


def _declared_workspace_effect(tool_input, task_id, declared) -> EffectItem | None:
    path = str(
        tool_input.get("path")
        or tool_input.get("relative_path")
        or tool_input.get("output_path")
        or "**"
    )
    if "workspace_delete" in declared:
        return EffectItem(
            kind=EffectKind.workspace_delete,
            resource=_workspace_resource(task_id, path),
            risk="high",
            reversible=False,
            persistent=True,
        )
    if "workspace_write" in declared:
        return EffectItem(
            kind=EffectKind.workspace_write,
            resource=_workspace_resource(task_id, path),
            risk="moderate",
            persistent=True,
        )
    if "workspace_read" in declared:
        return EffectItem(
            kind=EffectKind.workspace_read,
            resource=_workspace_resource(task_id, path),
        )
    return None


def _mapped_declared_effects(spec, tool_input, task_id, declared) -> list[EffectItem]:
    mappings = {
        "artifact_write": (EffectKind.artifact_write, f"artifact://task/{task_id}/{tool_input.get('name', spec.name)}"),
        "dependency_change": (EffectKind.dependency_change, f"task://{task_id}/dependencies/{tool_input.get('package', '**')}"),
        "credential_use": (EffectKind.credential_use, f"credential://{tool_input.get('service', spec.name)}"),
        "delegation_create": (EffectKind.delegation_create, f"identity://{tool_input.get('delegate', 'new-agent')}"),
        "external_write": (EffectKind.external_write, str(tool_input.get("destination") or tool_input.get("url") or "external://unknown")),
    }
    high_risk = {EffectKind.credential_use, EffectKind.delegation_create, EffectKind.external_write}
    return [
        EffectItem(
            kind=kind,
            resource=resource,
            risk="high" if kind in high_risk else "moderate",
            reversible=False,
            persistent=kind != EffectKind.credential_use,
            data_labels=list(tool_input.get("data_labels", [])),
        )
        for permission, (kind, resource) in mappings.items()
        if permission in declared
    ]

def _effect_plan(
    spec: AstraToolSpec,
    summary: str,
    effects: list[EffectItem],
    permissions: list[str],
    *,
    approval_required: bool,
    network_scope: dict[str, Any] | None = None,
) -> ActionEffectPlan:
    return ActionEffectPlan(
        tool_name=spec.name,
        tool_version=spec.version,
        summary=summary,
        cwd="/workspace" if any("workspace" in item.kind.value for item in effects) else None,
        effects=effects,
        required_permissions=permissions,
        network_scope=network_scope or {"mode": "none"},
        analyzer_version=ANALYZER_VERSION,
        analyzer_digest=ANALYZER_DIGEST,
        approval_required=approval_required,
    )


class BashEffectAnalyzer(ToolEffectAnalyzer):
    def analyze(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan:
        command = str(tool_input.get("command", "")).strip()
        effects: list[EffectItem] = []
        summary = "执行 Bash 命令"
        complex_shell = bool(SHELL_COMPLEX.search(command))
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = []
            complex_shell = True
        executable_token = tokens[0] if tokens else ""
        executable = PurePosixPath(executable_token).name if tokens else ""
        trusted_executable = bool(executable) and executable_token == executable
        for match in REDIRECTION.finditer(command):
            effects.append(
                _path_mutation_effect(task_id, _clean_shell_path(match.group(1)), delete=False)
            )
        classified = _classify_mutating_command(
            task_id, tokens, executable, trusted_executable, complex_shell
        ) or _classify_nonmutating_command(
            task_id, tokens, executable, trusted_executable
        )
        classified_effects, summary = classified
        effects.extend(classified_effects)
        return _bash_effect_plan(spec, summary, effects)


def _classify_mutating_command(task_id, tokens, executable, trusted, complex_shell):
    if complex_shell:
        return [_unknown_process_effect(task_id)], "执行行为复杂的 Bash 命令"
    if not trusted:
        return None
    if executable in DELETE_COMMANDS:
        targets = _simple_operands(tokens) or ["**"]
        effects = [
            _path_mutation_effect(task_id, _clean_shell_path(target), delete=True)
            for target in targets
        ]
        return effects, "删除任务工作区文件"
    if executable in WRITE_COMMANDS:
        return _write_command_effects(task_id, executable, tokens)
    return _special_file_mutation(task_id, tokens, executable)


def _special_file_mutation(task_id, tokens, executable):
    if executable == "find" and "-delete" in tokens:
        return [_path_mutation_effect(task_id, "**", delete=True)], "删除 find 匹配的任务工作区文件"
    if executable == "sed" and _sed_edits_in_place(tokens):
        target = next(
            (token for token in reversed(tokens[1:]) if not token.startswith("-")), "**"
        )
        return [_path_mutation_effect(task_id, target, delete=False)], "原地修改任务工作区文件"
    return None


def _sed_edits_in_place(tokens: list[str]) -> bool:
    return any(token == "-i" or token.startswith("-i") for token in tokens[1:])


def _classify_nonmutating_command(task_id, tokens, executable, trusted):
    if not trusted:
        return [_unknown_process_effect(task_id)], "执行行为未知的 Bash 命令"
    if executable in NETWORK_COMMANDS:
        effect = EffectItem(
            kind=EffectKind.network_write,
            resource="network://untrusted-destination",
            risk="high",
            reversible=False,
        )
        return [effect], "通过 Bash 访问外部网络"
    if executable == "git" and _safe_git_invocation(tokens):
        return [_workspace_read_effect(task_id)], "读取 Git 工作区状态"
    is_safe_read = (
        executable in READ_ONLY_COMMANDS
        and _safe_read_only_invocation(executable, tokens)
    )
    if is_safe_read:
        return [_workspace_read_effect(task_id)], "读取任务工作区"
    if executable in SAFE_SHELL_BUILTINS:
        effect = EffectItem(kind=EffectKind.temporary_compute, resource="sandbox://stdout")
        return [effect], "执行临时 Bash 计算"
    return [_unknown_process_effect(task_id)], "执行行为未知的 Bash 命令"


def _workspace_read_effect(task_id: str) -> EffectItem:
    return EffectItem(
        kind=EffectKind.workspace_read,
        resource=_workspace_resource(task_id, "**"),
    )


def _bash_effect_plan(spec: AstraToolSpec, summary: str, effects: list[EffectItem]):
    effects = _deduplicate_effects(effects)
    effect_kinds = {item.kind for item in effects}
    if EffectKind.workspace_delete in effect_kinds:
        summary = "删除任务工作区文件"
    elif EffectKind.workspace_write in effect_kinds:
        summary = "创建或修改任务工作区文件"
    side_effects = {
        EffectKind.workspace_write,
        EffectKind.workspace_delete,
        EffectKind.artifact_write,
        EffectKind.network_write,
        EffectKind.external_write,
        EffectKind.process_execute_unknown,
    }
    permissions = ["process_execute", *sorted(item.value for item in effect_kinds)]
    return _effect_plan(
        spec,
        summary,
        effects,
        permissions,
        approval_required=bool(effect_kinds & side_effects),
        network_scope={
            "mode": "blocked" if EffectKind.network_write in effect_kinds else "none"
        },
    )


def _write_command_effects(task_id: str, executable: str, tokens: list[str]):
    operands = _simple_operands(tokens)
    if operands is None:
        return [_unknown_process_effect(task_id)], "执行目标范围不明确的文件修改命令"
    if executable == "mv":
        return _move_command_effects(task_id, operands)
    if executable in {"cp", "install"}:
        return _copy_command_effects(task_id, operands)
    if executable in {"mkdir", "tee", "touch", "truncate"} and operands:
        effects = [
            _path_mutation_effect(task_id, _clean_shell_path(target), delete=False)
            for target in operands
        ]
        return effects, "创建或修改任务工作区文件"
    return [_unknown_process_effect(task_id)], "执行目标范围不明确的文件修改命令"


def _move_command_effects(task_id: str, operands: list[str]):
    if len(operands) < 2:
        return [_unknown_process_effect(task_id)], "执行目标范围不明确的文件修改命令"
    sources, destination = operands[:-1], operands[-1]
    effects = [
        _path_mutation_effect(task_id, _clean_shell_path(source), delete=True)
        for source in sources
    ]
    destination = f"{destination.rstrip('/')}/**" if len(sources) > 1 else destination
    effects.append(_path_mutation_effect(task_id, _clean_shell_path(destination), delete=False))
    return effects, "移动任务工作区文件"


def _copy_command_effects(task_id: str, operands: list[str]):
    if len(operands) < 2:
        return [_unknown_process_effect(task_id)], "执行目标范围不明确的文件修改命令"
    sources, destination = operands[:-1], operands[-1]
    effects = [
        EffectItem(
            kind=EffectKind.workspace_read,
            resource=_workspace_resource(task_id, _clean_shell_path(source)),
        )
        for source in sources
    ]
    destination = f"{destination.rstrip('/')}/**" if len(sources) > 1 else destination
    effects.append(_path_mutation_effect(task_id, _clean_shell_path(destination), delete=False))
    return effects, "复制或安装任务工作区文件"


def workspace_mount_mode(plan: ActionEffectPlan) -> str:
    kinds = {item.kind for item in plan.effects}
    if kinds & {
        EffectKind.workspace_write,
        EffectKind.workspace_delete,
        EffectKind.process_execute_unknown,
    }:
        return "read_write"
    if EffectKind.workspace_read in kinds:
        return "read_only"
    return "none"


def is_side_effecting(plan: ActionEffectPlan) -> bool:
    return any(
        item.persistent
        or item.kind
        in {
            EffectKind.workspace_write,
            EffectKind.workspace_delete,
            EffectKind.artifact_write,
            EffectKind.dependency_change,
            EffectKind.network_write,
            EffectKind.external_write,
            EffectKind.credential_use,
            EffectKind.delegation_create,
            EffectKind.permission_change,
            EffectKind.process_execute_unknown,
        }
        for item in plan.effects
    )


def grant_proposals(plan: ActionEffectPlan) -> list[dict[str, Any]]:
    if not is_side_effecting(plan):
        return []
    resources = [effect.resource for effect in plan.effects]
    effect_kinds = sorted({effect.kind.value for effect in plan.effects})
    if not resources or any(resource.endswith("/**") for resource in resources):
        return []
    resource_matcher = {"exact": resources[0]} if len(resources) == 1 else {"globs": resources}
    base = {
        "effect_kinds": effect_kinds,
        "resource_matcher": resource_matcher,
        "invocation_constraints": {
            "tool_name": plan.tool_name,
            "tool_version": plan.tool_version,
            "analyzer_version": plan.analyzer_version,
            "analyzer_digest": plan.analyzer_digest,
            "working_directory": plan.cwd,
        },
    }
    return [
        {"scope": "run", "label": "允许当前运行内的相同行为", **base},
        {"scope": "task", "label": "允许本任务后续运行内的相同行为", **base},
    ]


def _workspace_resource(task_id: str, path: str) -> str:
    normalized = path.removeprefix("/workspace/").removeprefix("./") or "**"
    if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        normalized = "**"
    return f"task://{task_id}/workspace/{normalized}"


def _clean_shell_path(value: str) -> str:
    return value.strip("'\"").replace("\\ ", " ")


def _path_mutation_effect(task_id: str, path: str, *, delete: bool) -> EffectItem:
    normalized = path.removeprefix("file://")
    if normalized == "/tmp" or normalized.startswith("/tmp/"):
        return EffectItem(
            kind=EffectKind.temporary_compute,
            resource=f"sandbox://tmp/{normalized.removeprefix('/tmp/').removeprefix('/tmp')}",
        )
    if normalized == "/output" or normalized.startswith("/output/"):
        return EffectItem(
            kind=EffectKind.artifact_write,
            resource=f"artifact://task/{task_id}/{normalized.removeprefix('/output/')}",
            risk="moderate",
            persistent=True,
        )
    if normalized.startswith(("/etc", "/proc", "/sys", "/dev", "/usr", "/bin", "/sbin")):
        return EffectItem(
            kind=EffectKind.workspace_delete if delete else EffectKind.workspace_write,
            resource=f"host://system{normalized}",
            risk="critical",
            reversible=False,
            persistent=True,
        )
    return EffectItem(
        kind=EffectKind.workspace_delete if delete else EffectKind.workspace_write,
        resource=_workspace_resource(task_id, normalized),
        risk="high" if delete else "moderate",
        reversible=not delete,
        persistent=True,
    )


def _unknown_process_effect(task_id: str) -> EffectItem:
    return EffectItem(
        kind=EffectKind.process_execute_unknown,
        resource=_workspace_resource(task_id, "**"),
        risk="high",
        reversible=False,
        persistent=True,
    )


def _simple_operands(tokens: list[str]) -> list[str] | None:
    """Return operands only when option parsing cannot change their meaning."""
    operands: list[str] = []
    options_finished = False
    for token in tokens[1:]:
        if token == "--" and not options_finished:
            options_finished = True
            continue
        if not options_finished and token.startswith("-"):
            return None
        operands.append(token)
    return operands


def _safe_read_only_invocation(executable: str, tokens: list[str]) -> bool:
    if executable == "find":
        unsafe = {
            "-delete",
            "-exec",
            "-execdir",
            "-ok",
            "-okdir",
            "-fprint",
            "-fprint0",
            "-fprintf",
            "-fls",
        }
        if any(token in unsafe for token in tokens[1:]):
            return False
    if executable == "sort" and any(
        token == "-o" or token.startswith("--output=") for token in tokens[1:]
    ):
        return False
    return not (executable == "diff" and any(token.startswith("--output=") for token in tokens[1:]))


def _safe_git_invocation(tokens: list[str]) -> bool:
    if len(tokens) < 2 or tokens[1] not in {"status", "diff", "log", "show", "branch"}:
        return False
    mutating_flags = {"-d", "-D", "-m", "-M", "--delete", "--move", "--set-upstream-to"}
    return not any(token in mutating_flags for token in tokens[2:])


def _deduplicate_effects(effects: list[EffectItem]) -> list[EffectItem]:
    result: list[EffectItem] = []
    seen: set[tuple[str, str]] = set()
    for effect in effects:
        key = (effect.kind.value, effect.resource)
        if key not in seen:
            seen.add(key)
            result.append(effect)
    return result
