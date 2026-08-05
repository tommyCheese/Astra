from __future__ import annotations

import io
import zipfile

from app.application.skills.errors import SkillStorageError
from app.application.skills.packages import normalize_skill_path
from app.common.core.config import AstraRuntimeSettings


def read_skill_archive(archive: bytes, settings: AstraRuntimeSettings) -> dict[str, bytes]:
    if len(archive) > settings.skills_max_package_bytes:
        raise SkillStorageError("SKILL_ARCHIVE_TOO_LARGE", "Skill 压缩包超过大小限制。")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            files = _read_bundle(bundle, settings)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise SkillStorageError("SKILL_ARCHIVE_INVALID", "Skill 压缩包无效。") from exc
    return _strip_single_root(files)


def _read_bundle(bundle: zipfile.ZipFile, settings: AstraRuntimeSettings) -> dict[str, bytes]:
    entries = bundle.infolist()
    if len(entries) > settings.skills_max_files + 8:
        raise SkillStorageError("SKILL_ARCHIVE_TOO_MANY_FILES", "Skill 文件过多。")
    if sum(entry.file_size for entry in entries) > settings.skills_max_package_bytes:
        raise SkillStorageError("SKILL_ARCHIVE_EXPANDS_TOO_LARGE", "Skill 解压后超过大小限制。")
    files: dict[str, bytes] = {}
    for entry in entries:
        if entry.is_dir():
            continue
        path = _validated_entry_path(entry, settings)
        if path in files:
            raise SkillStorageError(
                "SKILL_ARCHIVE_DUPLICATE_PATH", "Skill 压缩包包含重复文件路径。"
            )
        files[path] = bundle.read(entry)
    return files


def _validated_entry_path(entry: zipfile.ZipInfo, settings: AstraRuntimeSettings) -> str:
    if entry.file_size > settings.skills_max_file_bytes:
        raise SkillStorageError("SKILL_FILE_TOO_LARGE", "Skill 文件超过大小限制。")
    file_kind = entry.external_attr >> 16 & 0o170000
    if file_kind not in {0, 0o100000}:
        raise SkillStorageError(
            "SKILL_SPECIAL_FILE_NOT_ALLOWED", "Skill 压缩包不允许符号链接或特殊文件。"
        )
    try:
        return normalize_skill_path(entry.filename)
    except ValueError as exc:
        raise SkillStorageError("SKILL_ARCHIVE_PATH_INVALID", "Skill 压缩包包含越界路径。") from exc


def _strip_single_root(files: dict[str, bytes]) -> dict[str, bytes]:
    if "SKILL.md" in files:
        return files
    candidates = [path for path in files if path.endswith("/SKILL.md")]
    if len(candidates) != 1:
        raise SkillStorageError("SKILL_ARCHIVE_ROOT_INVALID", "压缩包必须包含一个 Skill 根目录。")
    prefix = candidates[0][: -len("SKILL.md")]
    return {
        path[len(prefix) :]: content for path, content in files.items() if path.startswith(prefix)
    }


def write_skill_archive(name: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(files):
            bundle.writestr(f"{name}/{path}", files[path])
    return buffer.getvalue()
