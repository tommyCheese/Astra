"""Validate consolidation proposals against a frozen input manifest."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.memory.consolidation.models import (
    ConsolidationAction,
    ConsolidationInputManifest,
    ConsolidationOperation,
    ConsolidationProposal,
    normalize_text,
)


@dataclass(frozen=True, slots=True)
class ConsolidationValidationIssue:
    code: str
    detail: str
    operation_id: str | None = None
    memory_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "operation_id": self.operation_id,
            "memory_id": self.memory_id,
        }


@dataclass(frozen=True, slots=True)
class ConsolidationValidationReport:
    input_hash: str
    proposal_hash: str
    issues: tuple[ConsolidationValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "input_hash": self.input_hash,
            "proposal_hash": self.proposal_hash,
            "issues": [issue.to_dict() for issue in self.issues],
        }


_INSTRUCTION_PATTERNS = (
    re.compile(
        r"\bignore\s+(?:all\s+)?(?:previous|prior|trusted|system)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:replace|override|rewrite)\b.{0,80}"
        r"\b(?:system\s+prompt|agent\s+profile|autodream|memory\s+governance)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"<\s*/?\s*astra_(?:runtime_context|skill)\b", re.IGNORECASE),
    re.compile(r"(?:忽略|覆盖|替换).{0,40}(?:系统|受信任|协议|配置|指令)"),
)
_AUTHORITY_PATTERNS = (
    re.compile(
        r"\b(?:enable|grant|expand|bypass|weaken|override|install|modify|change)\b"
        r".{0,80}\b(?:tool|permission|credential|approval|sandbox|security|policy|"
        r"profile|skill)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:tool|permission|credential|approval|sandbox|security|policy|profile|"
        r"skill)\b.{0,80}\b(?:enable|grant|expand|bypass|weaken|override|install|"
        r"modify|change)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:启用|授予|扩大|绕过|削弱|覆盖|安装|修改|变更).{0,40}"
        r"(?:工具|权限|凭据|审批|沙箱|安全|策略|Profile|Skill)",
        re.IGNORECASE,
    ),
)
_PROTECTED_KEY_PATTERN = re.compile(
    r"(?:enable|grant|expand|bypass|override|install|modify|change|disable|remove)"
    r".*(?:tool|permission|credential|approval|sandbox|security|policy|profile|skill)"
    r"|(?:credential|secret|api_key|approval_bypass|sandbox_exception|security_override)",
    re.IGNORECASE,
)


def _contains_instruction_override(value: str) -> bool:
    return any(pattern.search(value) for pattern in _INSTRUCTION_PATTERNS)


def _contains_authority_change(value: str) -> bool:
    return any(pattern.search(value) for pattern in _AUTHORITY_PATTERNS)


def _protected_structured_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = normalize_text(str(raw_key))
            if _PROTECTED_KEY_PATTERN.search(key):
                return key
            found = _protected_structured_key(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _protected_structured_key(nested)
            if found is not None:
                return found
    return None


def validate_proposal(
    manifest: ConsolidationInputManifest,
    proposal: ConsolidationProposal,
    *,
    extra_issues: Iterable[ConsolidationValidationIssue] = (),
) -> ConsolidationValidationReport:
    items = {item.id: item for item in manifest.items}
    issues = list(extra_issues)
    output_keys: set[str] = set()
    replaced_ids: set[str] = set()
    for operation in proposal.operations:
        _validate_output_key(operation, output_keys, issues)
        _validate_sources(operation, items, issues)
        _validate_replacements(operation, items, replaced_ids, issues)
        _validate_action(operation, issues)
        _validate_namespace(operation, manifest, issues)
        _validate_authority(operation, issues)
    normalized_issues = tuple(sorted(issues, key=_issue_sort_key))
    return ConsolidationValidationReport(
        input_hash=manifest.input_hash,
        proposal_hash=proposal.proposal_hash,
        issues=normalized_issues,
    )


def _validate_output_key(
    operation: ConsolidationOperation,
    output_keys: set[str],
    issues: list[ConsolidationValidationIssue],
) -> None:
    if operation.memory_key in output_keys:
        issues.append(
            _issue(
                "duplicate_output_key",
                "Multiple operations produce the same normalized Memory key",
                operation,
            )
        )
    output_keys.add(operation.memory_key)


def _validate_sources(
    operation: ConsolidationOperation,
    items: dict[str, Any],
    issues: list[ConsolidationValidationIssue],
) -> None:
    if not operation.source_memory_ids:
        issues.append(
            _issue(
                "source_coverage",
                "A consolidation output must cite at least one input Memory",
                operation,
            )
        )
    for source_id in operation.source_memory_ids:
        source = items.get(source_id)
        if source is None:
            issues.append(
                _issue(
                    "namespace_isolation",
                    "A consolidation source is outside the frozen namespace working region",
                    operation,
                    source_id,
                )
            )
        elif not any(reference.accessible for reference in source.sources):
            issues.append(
                _issue(
                    "source_coverage",
                    "An input Memory has no accessible frozen provenance",
                    operation,
                    source_id,
                )
            )


def _validate_replacements(
    operation: ConsolidationOperation,
    items: dict[str, Any],
    replaced_ids: set[str],
    issues: list[ConsolidationValidationIssue],
) -> None:
    for replacement_id in operation.replace_memory_ids:
        if replacement_id in replaced_ids:
            issues.append(
                _issue(
                    "duplicate_replacement",
                    "An input Memory cannot be replaced more than once",
                    operation,
                    replacement_id,
                )
            )
        replaced_ids.add(replacement_id)
        replacement = items.get(replacement_id)
        if replacement is None:
            issues.append(
                _issue(
                    "namespace_isolation",
                    "A replacement target is outside the frozen working region",
                    operation,
                    replacement_id,
                )
            )
            continue
        _validate_replacement_shape(operation, replacement_id, replacement, issues)


def _validate_replacement_shape(
    operation: ConsolidationOperation,
    replacement_id: str,
    replacement: Any,
    issues: list[ConsolidationValidationIssue],
) -> None:
    if replacement_id not in operation.source_memory_ids:
        issues.append(
            _issue(
                "source_coverage",
                "Every replacement target must also be a cited source",
                operation,
                replacement_id,
            )
        )
    if replacement.kind != operation.kind or replacement.scope != operation.scope:
        issues.append(
            _issue(
                "type_isolation",
                "A replacement cannot change the Memory kind or scope of its input",
                operation,
                replacement_id,
            )
        )


def _validate_action(
    operation: ConsolidationOperation, issues: list[ConsolidationValidationIssue]
) -> None:
    if operation.action is ConsolidationAction.replace and not operation.replace_memory_ids:
        issues.append(
            _issue(
                "replacement_required",
                "A replace operation must identify replacement targets",
                operation,
            )
        )
    if operation.action is ConsolidationAction.add and operation.replace_memory_ids:
        issues.append(
            _issue(
                "unexpected_replacement",
                "An add operation cannot identify replacement targets",
                operation,
            )
        )


def _validate_namespace(
    operation: ConsolidationOperation,
    manifest: ConsolidationInputManifest,
    issues: list[ConsolidationValidationIssue],
) -> None:
    if operation.scope != manifest.namespace_type:
        issues.append(
            _issue(
                "namespace_isolation",
                "Output scope does not match the frozen namespace type",
                operation,
            )
        )


def _validate_authority(
    operation: ConsolidationOperation, issues: list[ConsolidationValidationIssue]
) -> None:
    inspection_text = operation.content + "\\n" + operation.structured_data_json
    if _contains_instruction_override(inspection_text):
        issues.append(
            _issue(
                "instruction_isolation",
                "Output contains an attempt to replace trusted instructions",
                operation,
            )
        )
    protected_key = _protected_structured_key(operation.structured_data)
    if not _contains_authority_change(inspection_text) and protected_key is None:
        return
    detail = "Output attempts to change protected runtime authority"
    if protected_key is not None:
        detail += f" through structured field {protected_key}"
    issues.append(_issue("protected_authority", detail, operation))


def _issue(
    code: str,
    detail: str,
    operation: ConsolidationOperation,
    memory_id: str | None = None,
) -> ConsolidationValidationIssue:
    return ConsolidationValidationIssue(
        code=code,
        detail=detail,
        operation_id=operation.operation_id,
        memory_id=memory_id,
    )


def _issue_sort_key(issue: ConsolidationValidationIssue) -> tuple[str, str, str, str]:
    return (
        issue.code,
        issue.operation_id or "",
        issue.memory_id or "",
        issue.detail,
    )
