from typing import Any

from sqlalchemy import select

from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.workspaces import ArtifactRecord, SandboxJobRecord


class RunArtifactSandboxStore:
    async def create_artifact(
        self,
        run_id: str,
        artifact_type: str,
        *,
        content_ref: str | None = None,
        path: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        sandbox_job_id: str | None = None,
        mime_type: str | None = None,
        size_bytes: int = 0,
        checksum: str | None = None,
        storage_key: str | None = None,
        security_status: str = "pending",
        provenance: dict[str, Any] | None = None,
        plan_node_id: str | None = None,
        agent_execution_id: str | None = None,
    ) -> ArtifactRecord:
        artifact = ArtifactRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            type=artifact_type,
            path=path,
            content_ref=content_ref,
            metadata_=metadata or {},
            tool_call_id=tool_call_id,
            plan_node_id=plan_node_id,
            sandbox_job_id=sandbox_job_id,
            mime_type=mime_type,
            size_bytes=size_bytes,
            checksum=checksum,
            storage_key=storage_key,
            security_status=security_status,
            provenance=provenance or {},
        )
        self.session.add(artifact)
        await self.session.flush()
        await self.add_event(
            run_id,
            "artifact.created",
            {"artifact_id": artifact.id, "type": artifact.type, "path": artifact.path},
        )
        await self.session.flush()
        return artifact

    async def get_artifact_with_workspace(self, artifact_id: str):
        result = await self.session.execute(
            select(ArtifactRecord, TaskRecord.workspace_id)
            .join(RunRecord, ArtifactRecord.run_id == RunRecord.id)
            .join(TaskRecord, RunRecord.task_id == TaskRecord.id)
            .where(ArtifactRecord.id == artifact_id)
        )
        return result.one_or_none()

    async def list_artifacts(self, run_id: str | None = None) -> list[ArtifactRecord]:
        query = select(ArtifactRecord)
        if run_id is not None:
            query = query.where(ArtifactRecord.run_id == run_id)
        result = await self.session.execute(query.order_by(ArtifactRecord.created_at, ArtifactRecord.id))
        return list(result.scalars().all())

    async def create_sandbox_job(
        self,
        run_id: str,
        *,
        tool_call_id: str | None,
        executor: str,
        runtime_profile: dict[str, Any],
        resource_limits: dict[str, Any],
        input_artifact_ids: list[str] | None = None,
    ) -> SandboxJobRecord:
        job = SandboxJobRecord(
            run_id=run_id,
            tool_call_id=tool_call_id,
            executor=executor,
            runtime_profile=runtime_profile,
            resource_limits=resource_limits,
            input_artifact_ids=input_artifact_ids or [],
            output_artifact_ids=[],
        )
        self.session.add(job)
        await self.session.flush()
        await self.add_event(
            run_id,
            "sandbox_job.created",
            {"sandbox_job_id": job.id, "tool_call_id": tool_call_id, "status": job.status},
        )
        await self.session.flush()
        return job

    async def transition_sandbox_job(self, job_id: str, status: str, **updates) -> SandboxJobRecord:
        from app.infrastructure.sandbox.runtime import transition

        job = await self.session.get(SandboxJobRecord, job_id)
        if job is None:
            raise ValueError(f"SandboxJob not found: {job_id}")
        transition(job.status, status)
        job.status = status
        if status == "running":
            job.started_at = utc_now()
        if status in {"succeeded", "failed", "timed_out", "cancelled"}:
            job.completed_at = utc_now()
        for key, value in updates.items():
            setattr(job, key, value)
        await self.add_event(
            job.run_id,
            "sandbox_job.status_changed",
            {"sandbox_job_id": job.id, "status": status, "exit_reason": job.exit_reason},
        )
        await self.session.flush()
        return job
