from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import stat
import tarfile
import zipfile
import asyncio
import fcntl
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.repositories.workspaces import WorkspaceRepository, validate_workspace_path
from app.db.models import utc_now
from app.artifacts import ArtifactCollector
from app.tools.base import ToolExecutionError


@dataclass(frozen=True)
class ManifestEntry:
    checksum: str
    size_bytes: int
    mime_type: str | None


class WorkspaceRuntimeService:
    _write_locks: dict[str, asyncio.Lock] = {}
    PROTECTED_PATHS = (".astra", ".git", ".codex")

    def __init__(
        self,
        repository: WorkspaceRepository,
        root: str,
        *,
        max_files: int,
        max_bytes: int,
        max_file_bytes: int,
    ):
        self.repository = repository
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes

    async def prepare(self, task_id: str) -> Path:
        workspace = await self.repository.get_or_create(
            task_id,
            storage_key=f"tasks/{task_id}",
            quotas={
                "max_files": self.max_files,
                "max_total_bytes": self.max_bytes,
                "max_file_bytes": self.max_file_bytes,
            },
        )
        path = self._resolve_storage_key(workspace.storage_key)
        path.mkdir(parents=True, exist_ok=True, mode=0o777)
        path.chmod(0o777)
        for relative in self.PROTECTED_PATHS:
            protected = path / relative
            if not protected.exists():
                protected.mkdir(mode=0o755)
        return path

    def scan(self, workspace_dir: Path) -> dict[str, ManifestEntry]:
        root = workspace_dir.resolve(strict=True)
        if not root.is_relative_to(self.root):
            raise ToolExecutionError(
                "sandbox_policy_violation", "Workspace is outside the managed root"
            )
        manifest: dict[str, ManifestEntry] = {}
        total_bytes = 0
        for current, directories, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            if depth > 32:
                raise ToolExecutionError(
                    "sandbox_policy_violation", "Workspace path depth exceeds policy"
                )
            for directory in list(directories):
                candidate = current_path / directory
                mode = candidate.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ToolExecutionError(
                        "sandbox_policy_violation", "Workspace links are not allowed"
                    )
            for filename in filenames:
                path = current_path / filename
                metadata = path.lstat()
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink > 1
                ):
                    raise ToolExecutionError(
                        "sandbox_policy_violation", "Unsupported Workspace file type"
                    )
                relative = path.relative_to(root).as_posix()
                try:
                    validate_workspace_path(relative)
                except ValueError as exc:
                    raise ToolExecutionError(
                        "sandbox_policy_violation", "Workspace filename is unsafe"
                    ) from exc
                size = path.stat().st_size
                if size > self.max_file_bytes:
                    raise ToolExecutionError(
                        "artifact_limit_exceeded", "Workspace file exceeds quota"
                    )
                total_bytes += size
                if total_bytes > self.max_bytes or len(manifest) >= self.max_files:
                    raise ToolExecutionError(
                        "artifact_limit_exceeded", "Workspace exceeds quota"
                    )
                manifest[relative] = ManifestEntry(
                    checksum=self._checksum(path),
                    size_bytes=size,
                    mime_type=mimetypes.guess_type(path.name)[0],
                )
        return manifest

    @asynccontextmanager
    async def access_guard(self, workspace_dir: Path, mode: str):
        if mode != "read_write":
            yield
            return
        key = str(workspace_dir.resolve())
        lock = self._write_locks.setdefault(key, asyncio.Lock())
        async with lock:
            lock_dir = self.root / ".locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock_path = lock_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.lock"
            handle = lock_path.open("a+b")
            try:
                await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
                handle.close()

    async def create_checkpoint(self, *, run_id: str, workspace_dir: Path) -> dict[str, Any]:
        manifest = self.scan(workspace_dir)
        payload = {
            path: {
                "checksum": entry.checksum,
                "size_bytes": entry.size_bytes,
                "mime_type": entry.mime_type,
            }
            for path, entry in sorted(manifest.items())
        }
        digest = hashlib.sha256(
            repr(sorted((path, value["checksum"]) for path, value in payload.items())).encode()
        ).hexdigest()
        workspace = await self.repository.for_run(run_id)
        checkpoint = await self.repository.create_checkpoint(
            workspace_id=workspace.id,
            run_id=run_id,
            manifest=payload,
            manifest_hash=digest,
        )
        return {"id": checkpoint.id, "manifest_hash": digest, "files": len(payload)}

    async def delete(self, task_id: str) -> None:
        workspace = await self.repository.get_or_create(task_id)
        path = self._resolve_storage_key(workspace.storage_key)
        if path.exists():
            shutil.rmtree(path)
        workspace.status = "deleted"
        workspace.deleted_at = utc_now()
        await self.repository.session.commit()

    def validate_archive(self, archive_path: Path) -> list[str]:
        names: list[str]
        total_size = 0
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as archive:
                names = [entry.filename for entry in archive.infolist()]
                total_size = sum(entry.file_size for entry in archive.infolist())
        elif tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path) as archive:
                members = archive.getmembers()
                if any(member.issym() or member.islnk() or not (member.isfile() or member.isdir()) for member in members):
                    raise ToolExecutionError(
                        "sandbox_policy_violation", "Archive contains unsafe entries"
                    )
                names = [member.name for member in members]
                total_size = sum(member.size for member in members)
        else:
            raise ToolExecutionError("invalid_artifact", "Unsupported archive format")
        for name in names:
            candidate = Path(name)
            if candidate.is_absolute() or ".." in candidate.parts or "\x00" in name:
                raise ToolExecutionError(
                    "sandbox_policy_violation", "Archive path traversal is not allowed"
                )
        if len(names) > self.max_files or total_size > self.max_bytes:
            raise ToolExecutionError(
                "artifact_limit_exceeded", "Archive expansion exceeds Workspace quota"
            )
        return names

    async def capture_changes(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        workspace_dir: Path,
        before: dict[str, ManifestEntry],
    ) -> list[dict[str, Any]]:
        after = self.scan(workspace_dir)
        workspace = await self.repository.for_run(run_id)
        changes: list[dict[str, Any]] = []
        for relative_path in sorted(before.keys() | after.keys()):
            previous = before.get(relative_path)
            current = after.get(relative_path)
            if previous == current:
                continue
            if previous is None:
                kind = "created"
            elif current is None:
                kind = "deleted"
            else:
                kind = "modified"
            entry = current or previous
            security_status = "deleted" if current is None else self._security_status(
                workspace_dir / relative_path
            )
            deliverable_candidate = (
                current is not None
                and security_status == "verified"
                and self._deliverable_candidate(relative_path)
            )
            await self.repository.record_change(
                workspace_id=workspace.id,
                run_id=run_id,
                tool_call_id=tool_call_id,
                relative_path=relative_path,
                change_kind=kind,
                before_checksum=previous.checksum if previous else None,
                after_checksum=current.checksum if current else None,
                mime_type=entry.mime_type if entry else None,
                size_bytes=current.size_bytes if current else None,
                security_status=security_status,
                deliverable_candidate=deliverable_candidate,
                metadata={"trust_label": "untrusted_workspace_content"},
            )
            if current is not None:
                await self.repository.upsert_file(
                    workspace.id,
                    relative_path,
                    mime_type=current.mime_type,
                    size_bytes=current.size_bytes,
                    checksum=current.checksum,
                    security_status=security_status,
                    deliverable_candidate=deliverable_candidate,
                    metadata={"trust_label": "untrusted_workspace_content"},
                )
            changes.append({"path": relative_path, "kind": kind})
        return changes

    def resolve_file(self, workspace_dir: Path, relative_path: str) -> Path:
        root = workspace_dir.resolve(strict=True)
        target = (root / relative_path).resolve(strict=True)
        if not target.is_relative_to(root) or target.is_symlink() or not target.is_file():
            raise ToolExecutionError(
                "sandbox_policy_violation", "Workspace file path is invalid"
            )
        return target

    def _resolve_storage_key(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        if not path.is_relative_to(self.root):
            raise ToolExecutionError(
                "sandbox_policy_violation", "Invalid Workspace storage key"
            )
        return path

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _deliverable_candidate(relative_path: str) -> bool:
        hidden_parts = {".git", ".venv", "node_modules", "__pycache__"}
        dependency_files = {
            "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock",
            "poetry.lock", "Pipfile.lock",
        }
        path = Path(relative_path)
        return (
            not any(part.startswith(".") or part in hidden_parts for part in path.parts)
            and path.name not in dependency_files
            and path.suffix.lower() in ArtifactCollector.MAGIC
        )

    def _security_status(self, path: Path) -> str:
        try:
            ArtifactCollector(
                path.parent,
                max_files=self.max_files,
                max_bytes=self.max_bytes,
            ).inspect(path)
        except ToolExecutionError:
            return "rejected"
        return "verified"
