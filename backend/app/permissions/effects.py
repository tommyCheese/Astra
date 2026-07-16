from __future__ import annotations

import hashlib
import json
import re
import shlex
from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import Any

from app.schemas.permissions import ActionEffectPlan, EffectItem, EffectKind
from app.tools.base import ToolSpec

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
SHELL_COMPLEX = re.compile(r"(?:&&|\|\||[;&`]|\$\(|\$\{|\n|\r)")
REDIRECTION = re.compile(r"(?<!<)(?:>>?|[0-9]+>>?)\s*([^\s;&|]+)")


def effect_plan_hash(plan: ActionEffectPlan) -> str:
    payload = plan.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class ToolEffectAnalyzer(ABC):
    @abstractmethod
    def analyze(
        self,
        spec: ToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan: ...


class DefaultEffectAnalyzer(ToolEffectAnalyzer):
    def analyze(
        self,
        spec: ToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan:
        if spec.name in {"web_search", "web_fetch"}:
            resource = (
                f"web://search/{str(tool_input.get('query', '')).strip()}"
                if spec.name == "web_search"
                else str(tool_input.get("url", "web://unknown"))
            )
            return self._plan(
                spec,
                "读取公开网络内容",
                [EffectItem(kind=EffectKind.network_read, resource=resource)],
                ["network_read"],
                approval_required=False,
                network_scope={"mode": "public_read"},
            )
        if spec.name == "bash_execute":
            return BashEffectAnalyzer().analyze(spec, tool_input, task_id=task_id)
        if spec.name == "chart.render":
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
                        resource=_workspace_resource(
                            task_id, str(tool_input["input_workspace_path"])
                        ),
                    )
                )
            return self._plan(
                spec,
                "读取任务数据并生成图表交付物",
                effects,
                ["workspace_read", "temporary_compute", "artifact_write"],
                approval_required=True,
            )
        declared = set(spec.permissions)
        mapped: list[EffectItem] = []
        path = str(
            tool_input.get("path")
            or tool_input.get("relative_path")
            or tool_input.get("output_path")
            or "**"
        )
        if "workspace_delete" in declared:
            mapped.append(
                EffectItem(
                    kind=EffectKind.workspace_delete,
                    resource=_workspace_resource(task_id, path),
                    risk="high",
                    reversible=False,
                    persistent=True,
                )
            )
        elif "workspace_write" in declared:
            mapped.append(
                EffectItem(
                    kind=EffectKind.workspace_write,
                    resource=_workspace_resource(task_id, path),
                    risk="moderate",
                    persistent=True,
                )
            )
        elif "workspace_read" in declared:
            mapped.append(
                EffectItem(
                    kind=EffectKind.workspace_read,
                    resource=_workspace_resource(task_id, path),
                )
            )
        mappings = {
            "artifact_write": (
                EffectKind.artifact_write,
                f"artifact://task/{task_id}/{tool_input.get('name', spec.name)}",
            ),
            "dependency_change": (
                EffectKind.dependency_change,
                f"task://{task_id}/dependencies/{tool_input.get('package', '**')}",
            ),
            "credential_use": (
                EffectKind.credential_use,
                f"credential://{tool_input.get('service', spec.name)}",
            ),
            "delegation_create": (
                EffectKind.delegation_create,
                f"identity://{tool_input.get('delegate', 'new-agent')}",
            ),
            "external_write": (
                EffectKind.external_write,
                str(tool_input.get("destination") or tool_input.get("url") or "external://unknown"),
            ),
        }
        for permission, (kind, resource) in mappings.items():
            if permission in declared:
                mapped.append(
                    EffectItem(
                        kind=kind,
                        resource=resource,
                        risk="high" if kind in {
                            EffectKind.credential_use,
                            EffectKind.delegation_create,
                            EffectKind.external_write,
                        } else "moderate",
                        reversible=False,
                        persistent=kind != EffectKind.credential_use,
                        data_labels=list(tool_input.get("data_labels", [])),
                    )
                )
        if mapped:
            return self._plan(
                spec,
                f"执行 {spec.name} 的声明式资源操作",
                _deduplicate_effects(mapped),
                list(spec.permissions),
                approval_required=is_side_effecting(
                    ActionEffectPlan(
                        tool_name=spec.name,
                        tool_version=spec.version,
                        summary="classification",
                        effects=mapped,
                        required_permissions=list(spec.permissions),
                        analyzer_version=ANALYZER_VERSION,
                    )
                ),
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
        return self._plan(
            spec,
            f"执行无法精确分类的工具 {spec.name}",
            effects,
            list(spec.permissions),
            approval_required=True,
        )

    @staticmethod
    def _plan(
        spec: ToolSpec,
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
        spec: ToolSpec,
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
        executable = PurePosixPath(tokens[0]).name if tokens else ""
        for match in REDIRECTION.finditer(command):
            effects.append(_path_mutation_effect(
                task_id, _clean_shell_path(match.group(1)), delete=False
            ))
        if executable in DELETE_COMMANDS:
            targets = [token for token in tokens[1:] if not token.startswith("-")] or ["**"]
            effects.extend(_path_mutation_effect(
                task_id, _clean_shell_path(target), delete=True
            ) for target in targets)
            summary = "删除任务工作区文件"
        elif executable == "find" and "-delete" in tokens:
            effects.append(_path_mutation_effect(task_id, "**", delete=True))
            summary = "删除 find 匹配的任务工作区文件"
        elif executable == "sed" and any(
            token == "-i" or token.startswith("-i") for token in tokens[1:]
        ):
            target = next(
                (token for token in reversed(tokens[1:]) if not token.startswith("-")),
                "**",
            )
            effects.append(_path_mutation_effect(task_id, target, delete=False))
            summary = "原地修改任务工作区文件"
        elif executable in WRITE_COMMANDS:
            target = next((token for token in reversed(tokens[1:]) if not token.startswith("-")), "**")
            effects.append(
                EffectItem(
                    kind=EffectKind.workspace_write,
                    resource=_workspace_resource(task_id, _clean_shell_path(target)),
                    risk="moderate",
                    persistent=True,
                )
            )
            summary = "创建或修改任务工作区文件"
        elif executable in NETWORK_COMMANDS:
            effects.append(
                EffectItem(
                    kind=EffectKind.network_write,
                    resource="network://untrusted-destination",
                    risk="high",
                    reversible=False,
                )
            )
            summary = "通过 Bash 访问外部网络"
        elif executable == "git" and _safe_git_invocation(tokens):
            effects.append(
                EffectItem(
                    kind=EffectKind.workspace_read,
                    resource=_workspace_resource(task_id, "**"),
                )
            )
            summary = "读取 Git 工作区状态"
        elif executable in READ_ONLY_COMMANDS and not complex_shell:
            effects.append(
                EffectItem(
                    kind=EffectKind.workspace_read,
                    resource=_workspace_resource(task_id, "**"),
                )
            )
            summary = "读取任务工作区"
        elif executable in SAFE_SHELL_BUILTINS and not complex_shell:
            effects.append(
                EffectItem(
                    kind=EffectKind.temporary_compute,
                    resource="sandbox://stdout",
                )
            )
            summary = "执行临时 Bash 计算"
        else:
            effects.append(
                EffectItem(
                    kind=EffectKind.process_execute_unknown,
                    resource=_workspace_resource(task_id, "**"),
                    risk="high",
                    reversible=False,
                    persistent=True,
                )
            )
            summary = "执行行为未知的 Bash 命令"
        effects = _deduplicate_effects(effects)
        if any(item.kind == EffectKind.workspace_delete for item in effects):
            summary = "删除任务工作区文件"
        elif any(item.kind == EffectKind.workspace_write for item in effects):
            summary = "创建或修改任务工作区文件"
        side_effecting = any(
            item.kind
            in {
                EffectKind.workspace_write,
                EffectKind.workspace_delete,
                EffectKind.artifact_write,
                EffectKind.network_write,
                EffectKind.external_write,
                EffectKind.process_execute_unknown,
            }
            for item in effects
        )
        permissions = ["process_execute", *sorted({item.kind.value for item in effects})]
        return DefaultEffectAnalyzer._plan(
            spec,
            summary,
            effects,
            permissions,
            approval_required=side_effecting,
            network_scope={
                "mode": "none"
                if not any(item.kind == EffectKind.network_write for item in effects)
                else "blocked"
            },
        )


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


def platform_denial_reason(plan: ActionEffectPlan) -> str | None:
    if plan.network_scope.get("mode") == "blocked":
        return "Bash runtime network access is prohibited by the platform sandbox policy."
    protected_prefixes = (
        "astra://",
        "host://",
    )
    for effect in plan.effects:
        if effect.resource.startswith(protected_prefixes):
            return "The action targets a protected control-plane or host resource."
    return None


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
