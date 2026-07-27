from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.artifacts import ArtifactStore, LocalArtifactStore
from app.core.config import Settings
from app.db.models import TaskRecord
from app.repositories.conversations import ConversationRepository

logger = logging.getLogger("astra.conversation_lifecycle")


@dataclass(frozen=True)
class ConversationDeletionOutcome:
    artifact_keys: int
    cleanup_failures: int


class ConversationLifecycleService:
    def __init__(
        self,
        settings: Settings,
        *,
        artifact_store: ArtifactStore | None = None,
    ):
        self.settings = settings
        self.artifact_store = artifact_store or LocalArtifactStore(
            settings.artifact_store_path
        )

    async def delete(
        self, repo: ConversationRepository, task: TaskRecord
    ) -> ConversationDeletionOutcome:
        task_id = task.id
        storage_keys = await repo.delete(task)
        cleanup_failures = 0

        for key in storage_keys:
            try:
                self.artifact_store.delete(key)
            except Exception:
                cleanup_failures += 1
                logger.warning(
                    "conversation.artifact_cleanup_failed conversation_id=%s key=%s",
                    task_id,
                    key,
                    exc_info=True,
                )

        workspace_root = Path(self.settings.task_workspace_store_path).resolve()
        workspace_path = (workspace_root / "tasks" / task_id).resolve()
        if not workspace_path.is_relative_to(workspace_root):
            cleanup_failures += 1
            logger.warning(
                "conversation.workspace_cleanup_rejected conversation_id=%s path=%s",
                task_id,
                workspace_path,
            )
        else:
            try:
                shutil.rmtree(workspace_path)
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failures += 1
                logger.warning(
                    "conversation.workspace_cleanup_failed conversation_id=%s path=%s",
                    task_id,
                    workspace_path,
                    exc_info=True,
                )

        return ConversationDeletionOutcome(
            artifact_keys=len(storage_keys),
            cleanup_failures=cleanup_failures,
        )
