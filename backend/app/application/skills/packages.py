from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Mapping
from pathlib import PurePosixPath

import yaml

from app.application.skills.contracts import (
    SkillDiagnostic,
    SkillFrontmatter,
    SkillOrigin,
    SkillPackage,
    SkillResource,
)

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".bash",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".html",
    ".css",
    ".svg",
}
DISALLOWED_EXTENSIONS = {".exe", ".dll", ".dylib", ".so", ".class", ".jar", ".wasm"}
SUSPICIOUS_PATTERNS = {
    "skill.policy_bypass": re.compile(
        r"ignore (?:all |any )?(?:previous|platform|system) instructions|"
        r"bypass (?:approval|permission|sandbox|policy)",
        re.I,
    ),
    "skill.secret_exfiltration": re.compile(
        r"(?:upload|send|exfiltrat|curl).{0,80}(?:secret|credential|api[_ -]?key|token)",
        re.I | re.S,
    ),
    "skill.obfuscated_payload": re.compile(
        r"(?:base64\s+-d|frombase64string|eval\s*\(|exec\s*\().{0,120}",
        re.I | re.S,
    ),
}


class SkillPackageError(ValueError):
    def __init__(self, diagnostics: list[SkillDiagnostic]):
        self.diagnostics = diagnostics
        super().__init__("; ".join(item.message for item in diagnostics))


def normalize_skill_path(raw_path: str) -> str:
    if not raw_path or "\\" in raw_path or "\x00" in raw_path:
        raise ValueError("Skill resource path is invalid")
    candidate = PurePosixPath(raw_path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Skill resource path escapes its root")
    normalized = candidate.as_posix()
    if len(normalized) > 512:
        raise ValueError("Skill resource path is too long")
    return normalized


def _resource_kind(path: str) -> str:
    if path == "SKILL.md":
        return "instructions"
    head = path.split("/", 1)[0]
    return {
        "scripts": "script",
        "references": "reference",
        "assets": "asset",
    }.get(head, "other")


def _is_text(path: str, content: bytes) -> bool:
    if PurePosixPath(path).suffix.lower() in TEXT_EXTENSIONS or path == "SKILL.md":
        try:
            content.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    return False


def _content_digest(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        encoded = path.encode("utf-8")
        content = files[path]
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def parse_skill_package(
    files: Mapping[str, bytes | str],
    *,
    origin: SkillOrigin = SkillOrigin.custom,
    directory_name: str | None = None,
    max_files: int = 256,
    max_file_bytes: int = 2 * 1024 * 1024,
    max_package_bytes: int = 20 * 1024 * 1024,
    max_instruction_chars: int = 40_000,
    reject_reserved_custom_identity: bool = True,
    raise_on_safety: bool = True,
) -> tuple[SkillPackage, dict[str, bytes]]:
    diagnostics, normalized = _normalize_files(
        files,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_package_bytes=max_package_bytes,
    )
    skill_text = _skill_document(normalized, diagnostics)
    raw_frontmatter, instructions = _parse_frontmatter(skill_text, diagnostics)
    _validate_frontmatter(
        raw_frontmatter,
        diagnostics,
        origin=origin,
        directory_name=directory_name,
        reject_reserved_custom_identity=reject_reserved_custom_identity,
    )
    _validate_package_content(
        normalized,
        instructions,
        raw_frontmatter,
        diagnostics,
        max_instruction_chars=max_instruction_chars,
    )
    _raise_for_diagnostics(diagnostics, raise_on_safety=raise_on_safety)
    frontmatter = SkillFrontmatter.model_validate(raw_frontmatter)
    resources = _resources(normalized)
    return (
        SkillPackage(
            origin=origin,
            qualified_identity=f"{origin.value}:{frontmatter.name}",
            frontmatter=frontmatter,
            instructions=instructions,
            digest=_content_digest(normalized),
            resources=resources,
            diagnostics=diagnostics,
            requested_tool_patterns=(frontmatter.allowed_tools or "").split(),
        ),
        normalized,
    )


def _normalize_files(
    files: Mapping[str, bytes | str],
    *,
    max_files: int,
    max_file_bytes: int,
    max_package_bytes: int,
) -> tuple[list[SkillDiagnostic], dict[str, bytes]]:
    if len(files) > max_files:
        raise SkillPackageError([SkillDiagnostic(code="skill.too_many_files", message="Skill 文件数量超过限制。")])
    diagnostics: list[SkillDiagnostic] = []
    normalized: dict[str, bytes] = {}
    total_bytes = 0
    for raw_path, raw_content in files.items():
        try:
            path = normalize_skill_path(str(raw_path))
        except ValueError as exc:
            diagnostics.append(
                SkillDiagnostic(
                    code="skill.path_invalid",
                    message=str(exc),
                    severity="critical",
                    path=str(raw_path)[:512],
                )
            )
            continue
        content = raw_content.encode("utf-8") if isinstance(raw_content, str) else bytes(raw_content)
        total_bytes += len(content)
        if len(content) > max_file_bytes:
            diagnostics.append(
                SkillDiagnostic(
                    code="skill.file_too_large",
                    message="Skill 文件超过单文件大小限制。",
                    path=path,
                )
            )
        if PurePosixPath(path).suffix.lower() in DISALLOWED_EXTENSIONS:
            diagnostics.append(
                SkillDiagnostic(
                    code="skill.executable_binary",
                    message="Skill 包含不允许的可执行二进制文件。",
                    severity="critical",
                    path=path,
                )
            )
        normalized[path] = content
    if total_bytes > max_package_bytes:
        diagnostics.append(
            SkillDiagnostic(
                code="skill.package_too_large",
                message="Skill 包总大小超过限制。",
                severity="critical",
            )
        )
    return diagnostics, normalized


def _skill_document(normalized: dict[str, bytes], diagnostics: list[SkillDiagnostic]) -> str:
    skill_bytes = normalized.get("SKILL.md")
    if skill_bytes is None:
        diagnostics.append(SkillDiagnostic(code="skill.instructions_missing", message="Skill 缺少 SKILL.md。"))
        raise SkillPackageError(diagnostics)
    try:
        return skill_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        diagnostics.append(
            SkillDiagnostic(
                code="skill.instructions_not_utf8",
                message="SKILL.md 必须使用 UTF-8 编码。",
                path="SKILL.md",
            )
        )
        raise SkillPackageError(diagnostics) from exc


def _parse_frontmatter(skill_text: str, diagnostics: list[SkillDiagnostic]) -> tuple[dict[str, object], str]:
    match = FRONTMATTER_RE.match(skill_text)
    if not match:
        diagnostics.append(
            SkillDiagnostic(
                code="skill.frontmatter_missing",
                message="SKILL.md 必须以 YAML frontmatter 开始。",
                path="SKILL.md",
                line=1,
            )
        )
        raise SkillPackageError(diagnostics)
    try:
        raw_frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        diagnostics.append(
            SkillDiagnostic(
                code="skill.frontmatter_invalid",
                message="SKILL.md frontmatter 不是有效 YAML。",
                path="SKILL.md",
                line=getattr(getattr(exc, "problem_mark", None), "line", 0) + 1,
            )
        )
        raise SkillPackageError(diagnostics) from exc
    if not isinstance(raw_frontmatter, dict):
        diagnostics.append(
            SkillDiagnostic(
                code="skill.frontmatter_invalid",
                message="SKILL.md frontmatter 必须是对象。",
                path="SKILL.md",
            )
        )
        raise SkillPackageError(diagnostics)
    return raw_frontmatter, skill_text[match.end() :].strip()


def _validate_frontmatter(
    frontmatter: dict[str, object],
    diagnostics: list[SkillDiagnostic],
    *,
    origin: SkillOrigin,
    directory_name: str | None,
    reject_reserved_custom_identity: bool,
) -> None:
    name, description = frontmatter.get("name"), frontmatter.get("description")
    _validate_identity(
        name,
        diagnostics,
        origin=origin,
        directory_name=directory_name,
        reject_reserved_custom_identity=reject_reserved_custom_identity,
    )
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        diagnostics.append(_frontmatter_diagnostic("skill.description_invalid", "Skill description 必须为 1–1024 个字符。"))
    _validate_optional_frontmatter(frontmatter, diagnostics)


def _validate_identity(
    name: object,
    diagnostics: list[SkillDiagnostic],
    *,
    origin: SkillOrigin,
    directory_name: str | None,
    reject_reserved_custom_identity: bool,
) -> None:
    if not _valid_skill_name(name):
        diagnostics.append(_frontmatter_diagnostic("skill.name_invalid", "Skill name 必须为 1–64 位小写字母、数字或单连字符。"))
    if isinstance(name, str) and directory_name and name != directory_name:
        diagnostics.append(_frontmatter_diagnostic("skill.directory_name_mismatch", "Skill name 必须与父目录名称一致。"))
    if _reserved_identity(name, origin, reject_reserved_custom_identity):
        diagnostics.append(_frontmatter_diagnostic("skill.identity_reserved", "astra- 前缀仅供 Astra 内建 Skill 使用。"))


def _valid_skill_name(name: object) -> bool:
    return isinstance(name, str) and bool(NAME_RE.fullmatch(name)) and "--" not in name


def _reserved_identity(name: object, origin: SkillOrigin, reject_reserved: bool) -> bool:
    return origin == SkillOrigin.custom and reject_reserved and isinstance(name, str) and name.startswith("astra-")


def _validate_optional_frontmatter(frontmatter: dict[str, object], diagnostics: list[SkillDiagnostic]) -> None:
    compatibility = frontmatter.get("compatibility")
    if compatibility is not None and (not isinstance(compatibility, str) or len(compatibility) > 500):
        diagnostics.append(
            _frontmatter_diagnostic("skill.compatibility_invalid", "Skill compatibility 必须是至多 500 字符的字符串。")
        )
    if not isinstance(frontmatter.get("metadata", {}), dict):
        diagnostics.append(_frontmatter_diagnostic("skill.metadata_invalid", "Skill metadata 必须是对象。"))
    allowed_tools = frontmatter.get("allowed-tools")
    if allowed_tools is not None and not isinstance(allowed_tools, str):
        diagnostics.append(_frontmatter_diagnostic("skill.allowed_tools_invalid", "allowed-tools 必须是空格分隔的字符串。"))


def _frontmatter_diagnostic(code: str, message: str) -> SkillDiagnostic:
    return SkillDiagnostic(code=code, message=message, path="SKILL.md")


def _validate_package_content(
    normalized: dict[str, bytes],
    instructions: str,
    frontmatter: dict[str, object],
    diagnostics: list[SkillDiagnostic],
    *,
    max_instruction_chars: int,
) -> None:
    if len(instructions) > max_instruction_chars:
        diagnostics.append(_frontmatter_diagnostic("skill.instructions_too_large", "SKILL.md 指令超过上下文限制。"))
    if frontmatter.get("compatibility") is None and any(_resource_kind(path) == "script" for path in normalized):
        diagnostics.append(
            SkillDiagnostic(
                code="skill.compatibility_undeclared",
                message="包含脚本的 Skill 建议声明 compatibility 运行环境要求。",
                severity="warning",
                path="SKILL.md",
            )
        )
    for path, content in normalized.items():
        _validate_resource(path, content, diagnostics)


def _validate_resource(path: str, content: bytes, diagnostics: list[SkillDiagnostic]) -> None:
    if not _is_text(path, content):
        if _resource_kind(path) != "asset":
            diagnostics.append(
                SkillDiagnostic(
                    code="skill.unexpected_binary",
                    message="二进制文件仅允许位于 assets/。",
                    severity="critical",
                    path=path,
                )
            )
        return
    text = content.decode("utf-8")
    for code, pattern in SUSPICIOUS_PATTERNS.items():
        if pattern.search(text):
            diagnostics.append(
                SkillDiagnostic(
                    code=code,
                    message="检测到需要人工确认的高风险指令或代码模式。",
                    severity="critical",
                    path=path,
                )
            )


def _raise_for_diagnostics(diagnostics: list[SkillDiagnostic], *, raise_on_safety: bool) -> None:
    safety_codes = {
        "skill.policy_bypass",
        "skill.secret_exfiltration",
        "skill.obfuscated_payload",
        "skill.executable_binary",
        "skill.unexpected_binary",
    }
    errors = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.severity in {"error", "critical"} and (raise_on_safety or diagnostic.code not in safety_codes)
    ]
    if errors:
        raise SkillPackageError(diagnostics)


def _resources(normalized: dict[str, bytes]) -> list[SkillResource]:
    resources: list[SkillResource] = []
    for path in sorted(normalized):
        content = normalized[path]
        resources.append(
            SkillResource(
                path=path,
                digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
                size_bytes=len(content),
                media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                kind=_resource_kind(path),
                text=_is_text(path, content),
            )
        )
    return resources
