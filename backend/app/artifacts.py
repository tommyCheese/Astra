import hashlib
import json
import mimetypes
import shutil
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, Protocol

from app.tools.base import ArtifactRef, ToolExecutionError


class ArtifactStore(Protocol):
    def put(self, source: Path, suffix: str) -> str: ...
    def resolve(self, storage_key: str) -> Path: ...
    def delete(self, storage_key: str) -> None: ...


class LocalArtifactStore:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, source: Path, suffix: str) -> str:
        key = f"{datetime.now(timezone.utc):%Y/%m/%d}/{uuid.uuid4().hex}{suffix}"
        target = self.resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return key

    def resolve(self, storage_key: str) -> Path:
        target = (self.root / storage_key).resolve()
        if not target.is_relative_to(self.root):
            raise ToolExecutionError("sandbox_policy_violation", "Invalid artifact storage key")
        return target

    def delete(self, storage_key: str) -> None:
        self.resolve(storage_key).unlink(missing_ok=True)


class ArtifactCollector:
    MAGIC: ClassVar[dict[str, tuple[bytes, str]]] = {
        ".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
        ".jpg": (b"\xff\xd8\xff", "image/jpeg"),
        ".jpeg": (b"\xff\xd8\xff", "image/jpeg"),
        ".gif": (b"GIF8", "image/gif"),
        ".webp": (b"RIFF", "image/webp"),
        ".svg": (b"<", "image/svg+xml"),
        ".pdf": (b"%PDF-", "application/pdf"),
        ".json": (b"", "application/json"),
        ".html": (b"", "text/html"),
        ".txt": (b"", "text/plain"),
        ".md": (b"", "text/markdown"),
        ".csv": (b"", "text/csv"),
        ".tsv": (b"", "text/tab-separated-values"),
        ".py": (b"", "text/x-python"),
        ".js": (b"", "text/javascript"),
        ".ts": (b"", "text/typescript"),
        ".tsx": (b"", "text/typescript"),
        ".css": (b"", "text/css"),
        ".xml": (b"", "application/xml"),
        ".docx": (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ".xlsx": (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }

    def __init__(self, output_dir: Path, *, max_files: int, max_bytes: int):
        self.output_dir = output_dir.resolve()
        self.max_files = max_files
        self.max_bytes = max_bytes

    def collect(self) -> list[dict]:
        if any(path.is_symlink() for path in self.output_dir.rglob("*")):
            raise ToolExecutionError(
                "sandbox_policy_violation", "Artifact symlinks are not allowed"
            )
        files = [
            path for path in self.output_dir.rglob("*") if path.is_file() and not path.is_symlink()
        ]
        if len(files) > self.max_files:
            raise ToolExecutionError("artifact_limit_exceeded", "Artifact file count exceeded")
        total = sum(path.stat().st_size for path in files)
        if total > self.max_bytes:
            raise ToolExecutionError("artifact_limit_exceeded", "Artifact byte limit exceeded")
        return [self.inspect(path) for path in files]

    def inspect(self, path: Path) -> dict:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.output_dir):
            raise ToolExecutionError(
                "sandbox_policy_violation", "Artifact escaped output directory"
            )
        suffix = resolved.suffix.lower()
        if suffix not in self.MAGIC:
            raise ToolExecutionError("invalid_artifact", "Unsupported artifact type")
        prefix, expected_mime = self.MAGIC[suffix]
        data = resolved.read_bytes()
        if not data or (prefix and not data.lstrip().startswith(prefix)):
            raise ToolExecutionError("invalid_artifact", "Artifact content does not match its type")
        lowered = data.lower()
        if suffix == ".json":
            try:
                json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ToolExecutionError("invalid_artifact", "JSON artifact is malformed") from exc
        if suffix in {
            ".txt", ".md", ".csv", ".tsv", ".py", ".js", ".ts", ".tsx", ".css", ".xml"
        }:
            if b"\x00" in data:
                raise ToolExecutionError("invalid_artifact", "Text artifact contains binary data")
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ToolExecutionError("invalid_artifact", "Text artifact must be UTF-8") from exc
        if suffix in {".docx", ".xlsx"}:
            self._validate_office_archive(resolved, suffix)
        if suffix == ".svg" and any(
            token in lowered
            for token in (
                b"<script",
                b"javascript:",
                b"onload=",
                b"onerror=",
                b"http://",
                b"https://",
            )
        ):
            raise ToolExecutionError("invalid_artifact", "SVG contains active or external content")
        if suffix == ".html":
            csp_index = lowered.find(b"content-security-policy")
            script_index = lowered.find(b"<script")
            if (
                csp_index < 0
                or b"default-src 'none'" not in lowered
                or (script_index >= 0 and csp_index > script_index)
            ):
                raise ToolExecutionError(
                    "invalid_artifact", "HTML artifact is missing an effective restrictive CSP"
                )
        guessed = mimetypes.guess_type(resolved.name)[0]
        return {
            "path": resolved,
            "mime_type": expected_mime or guessed,
            "size_bytes": len(data),
            "checksum": hashlib.sha256(data).hexdigest(),
        }

    def _validate_office_archive(self, path: Path, suffix: str) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > self.max_files:
                    raise ToolExecutionError(
                        "artifact_limit_exceeded", "Office document contains too many entries"
                    )
                expanded = sum(item.file_size for item in entries)
                if expanded > self.max_bytes * 20:
                    raise ToolExecutionError(
                        "artifact_limit_exceeded", "Office document expansion exceeds policy"
                    )
                names = {item.filename for item in entries}
                for item in entries:
                    candidate = Path(item.filename)
                    if candidate.is_absolute() or ".." in candidate.parts:
                        raise ToolExecutionError(
                            "sandbox_policy_violation", "Office document contains path traversal"
                        )
                required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                if "[Content_Types].xml" not in names or required not in names:
                    raise ToolExecutionError(
                        "invalid_artifact", "Office document structure is incomplete"
                    )
                if any(name.lower().endswith((".bin", ".vba", ".vbs", ".exe")) for name in names):
                    raise ToolExecutionError(
                        "invalid_artifact", "Active Office document content is not allowed"
                    )
        except zipfile.BadZipFile as exc:
            raise ToolExecutionError("invalid_artifact", "Office artifact is malformed") from exc


def artifact_ref(record) -> ArtifactRef:
    return ArtifactRef(
        id=record.id,
        type=record.type,
        mime_type=record.mime_type or "application/octet-stream",
        content_url=f"/api/artifacts/{record.id}/content",
        size_bytes=record.size_bytes,
        checksum=record.checksum or "",
        metadata=record.metadata_ or {},
    )


class ArtifactService:
    def __init__(self, repo, store: ArtifactStore, *, max_files: int, max_bytes: int):
        self.repo, self.store = repo, store
        self.max_files, self.max_bytes = max_files, max_bytes

    async def persist_output(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        sandbox_job_id: str,
        output_dir: Path,
        provenance: dict,
    ) -> list[ArtifactRef]:
        refs = []
        for item in ArtifactCollector(
            output_dir, max_files=self.max_files, max_bytes=self.max_bytes
        ).collect():
            path = item.pop("path")
            key = self.store.put(path, path.suffix.lower())
            try:
                record = await self.repo.create_artifact(
                    run_id,
                    "sandbox_output",
                    path=None,
                    tool_call_id=tool_call_id,
                    sandbox_job_id=sandbox_job_id,
                    storage_key=key,
                    security_status="verified",
                    provenance=provenance,
                    metadata={"filename": path.name},
                    **item,
                )
            except BaseException:
                self.store.delete(key)
                raise
            refs.append(artifact_ref(record))
        return refs

def prune_store(store: LocalArtifactStore, records: list, retention_days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for record in records:
        if record.created_at < cutoff and record.storage_key:
            store.delete(record.storage_key)
            record.security_status = "expired"
            removed += 1
    return removed
