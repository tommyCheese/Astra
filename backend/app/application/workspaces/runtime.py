from __future__ import annotations

import asyncio
import fcntl
import hashlib
import mimetypes
import os
import shutil
import stat
import tarfile
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from app.application.workspaces.artifacts import ArtifactCollector, LocalArtifactStore
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.repositories.workspaces import WorkspaceRepository, validate_workspace_path
from app.infrastructure.sandbox.runtime import PROTECTED_WORKSPACE_PATHS
from app.infrastructure.tools.base import ToolExecutionError


@dataclass(frozen=True)
class ManifestEntry:
    checksum: str
    size_bytes: int
    mime_type: str | None


class WorkspaceRuntimeService:
    _write_locks: ClassVar[dict[str, asyncio.Lock]] = {}
    PROTECTED_PATHS = PROTECTED_WORKSPACE_PATHS

    def __init__(
        self,
        repository: WorkspaceRepository,
        root: str,
        *,
        max_files: int,
        max_bytes: int,
        max_file_bytes: int,
        artifact_store_path: str | None = None,
    ):
        self.repository = repository
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes
        self.artifact_store = (
            LocalArtifactStore(artifact_store_path) if artifact_store_path else None
        )

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

    def protected_paths(self, workspace_dir: Path) -> set[str]:
        root = workspace_dir.resolve(strict=True)
        if not root.is_relative_to(self.root):
            raise ToolExecutionError(
                "sandbox_policy_violation", "Workspace is outside the managed root"
            )
        return {
            candidate.relative_to(root).as_posix()
            for candidate in root.rglob("*")
            if candidate.name in PROTECTED_WORKSPACE_PATHS
        }

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
        names, total_size = self._archive_entries(archive_path)
        if any(self._unsafe_archive_name(name) for name in names):
            raise ToolExecutionError(
                "sandbox_policy_violation", "Archive path traversal is not allowed"
            )
        if len(names) > self.max_files or total_size > self.max_bytes:
            raise ToolExecutionError(
                "artifact_limit_exceeded", "Archive expansion exceeds Workspace quota"
            )
        return names

    @staticmethod
    def _archive_entries(archive_path: Path) -> tuple[list[str], int]:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                return [entry.filename for entry in entries], sum(
                    entry.file_size for entry in entries
                )
        if tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path) as archive:
                members = archive.getmembers()
                if any(WorkspaceRuntimeService._unsafe_tar_member(member) for member in members):
                    raise ToolExecutionError(
                        "sandbox_policy_violation", "Archive contains unsafe entries"
                    )
                return [member.name for member in members], sum(member.size for member in members)
        raise ToolExecutionError("invalid_artifact", "Unsupported archive format")

    @staticmethod
    def _unsafe_tar_member(member) -> bool:
        if member.issym() or member.islnk():
            return True
        return not member.isfile() and not member.isdir()

    @staticmethod
    def _unsafe_archive_name(name: str) -> bool:
        candidate = Path(name)
        return candidate.is_absolute() or ".." in candidate.parts or "\x00" in name

    async def capture_changes(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        workspace_dir: Path,
        before: dict[str, ManifestEntry],
        before_protected_paths: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        after = self.scan(workspace_dir)
        self._reject_protected_changes(
            workspace_dir, before, after, before_protected_paths
        )
        workspace = await self.repository.for_run(run_id)
        changes: list[dict[str, Any]] = []
        for relative_path in sorted(before.keys() | after.keys()):
            previous = before.get(relative_path)
            current = after.get(relative_path)
            if previous == current:
                continue
            kind = await self._record_workspace_change(
                workspace.id,
                run_id,
                tool_call_id,
                workspace_dir,
                relative_path,
                previous,
                current,
            )
            changes.append({"path": relative_path, "kind": kind})
        return changes

    def _reject_protected_changes(self, workspace_dir, before, after, previous_paths) -> None:
        current_paths = self.protected_paths(workspace_dir)
        path_changes = current_paths ^ previous_paths if previous_paths is not None else set()
        content_changes = [
            path
            for path, entry in after.items()
            if any(part in PROTECTED_WORKSPACE_PATHS for part in Path(path).parts)
            and before.get(path) != entry
        ]
        if not content_changes and not path_changes:
            return
        self._remove_new_protected_paths(
            workspace_dir, before, [*content_changes, *path_changes]
        )
        raise ToolExecutionError(
            "sandbox_policy_violation",
            "Tool execution attempted to modify a protected Workspace path",
        )

    async def _record_workspace_change(
        self, workspace_id, run_id, tool_call_id, workspace_dir, path, previous, current
    ) -> str:
        kind = self._change_kind(previous, current)
        entry = current or previous
        security = self._change_security(workspace_dir, path, current)
        is_deliverable = self._is_deliverable_change(path, current, security)
        await self.repository.record_change(
            workspace_id=workspace_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            relative_path=path,
            change_kind=kind,
            before_checksum=previous.checksum if previous else None,
            after_checksum=current.checksum if current else None,
            mime_type=entry.mime_type if entry else None,
            size_bytes=current.size_bytes if current else None,
            security_status=security,
            deliverable_candidate=is_deliverable,
            metadata={"trust_label": "untrusted_workspace_content"},
        )
        if is_deliverable:
            await self._snapshot_workspace_file(run_id, tool_call_id, workspace_dir, path, current)
        if current is not None:
            await self.repository.upsert_file(
                workspace_id,
                path,
                mime_type=current.mime_type,
                size_bytes=current.size_bytes,
                checksum=current.checksum,
                security_status=security,
                deliverable_candidate=is_deliverable,
                metadata={"trust_label": "untrusted_workspace_content"},
            )
        return kind

    @staticmethod
    def _change_kind(previous, current) -> str:
        if previous is None:
            return "created"
        return "deleted" if current is None else "modified"

    def _change_security(self, workspace_dir, path, current) -> str:
        return "deleted" if current is None else self._security_status(workspace_dir / path)

    def _is_deliverable_change(self, path, current, security) -> bool:
        if current is None or security != "verified":
            return False
        return self._deliverable_candidate(path)

    async def _snapshot_workspace_file(
        self, run_id, tool_call_id, workspace_dir, relative_path, current
    ) -> None:
        if self.artifact_store is None:
            return
        existing = await self.repository.find_workspace_snapshot(
            run_id=run_id, relative_path=relative_path, checksum=current.checksum
        )
        if existing is not None:
            return
        source = workspace_dir / relative_path
        storage_key = self.artifact_store.put(source, source.suffix.lower())
        try:
            await self.repository.create_workspace_snapshot(
                run_id=run_id,
                tool_call_id=tool_call_id,
                relative_path=relative_path,
                mime_type=current.mime_type,
                size_bytes=current.size_bytes,
                checksum=current.checksum,
                storage_key=storage_key,
            )
        except BaseException:
            self.artifact_store.delete(storage_key)
            raise

    @staticmethod
    def _remove_new_protected_paths(
        workspace_dir: Path,
        before: dict[str, ManifestEntry],
        changed_paths: list[str],
    ) -> None:
        protected_roots: set[Path] = set()
        for relative_path in changed_paths:
            parts = Path(relative_path).parts
            protected_index = next(
                index
                for index, part in enumerate(parts)
                if part in PROTECTED_WORKSPACE_PATHS
            )
            protected_roots.add(Path(*parts[: protected_index + 1]))
        for relative_root in sorted(protected_roots, reverse=True):
            prefix = f"{relative_root.as_posix()}/"
            existed_before = any(
                path == relative_root.as_posix() or path.startswith(prefix)
                for path in before
            )
            if existed_before:
                continue
            target = workspace_dir / relative_root
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if len(relative_root.parts) == 1:
                target.mkdir(mode=0o755)

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
