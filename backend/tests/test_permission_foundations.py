from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.db.models import WorkspaceFileRecord, utc_now
from app.repositories.permissions import PermissionRepository
from app.repositories.runs import RunRepository
from app.repositories.workspaces import WorkspaceRepository, validate_workspace_path
from app.schemas.permissions import (
    ActionEffectPlan,
    EffectItem,
    PermissionConditions,
    PermissionRequest,
    PermissionSubject,
)


def test_permission_and_effect_schemas_preserve_identity_and_integrity_fields():
    request = PermissionRequest(
        subject=PermissionSubject(
            agent_id="agent:main",
            task_id="task-1",
            run_id="run-1",
            delegation_chain=["user:1", "task:1", "run:1"],
        ),
        action="workspace.file.write",
        resource="task://task-1/workspace/reports/summary.md",
        conditions=PermissionConditions(
            tool_name="bash_execute",
            tool_version="1.1",
            analyzer_version="2",
        ),
        effect_plan_hash="sha256:effect",
    )
    plan = ActionEffectPlan(
        tool_name="bash_execute",
        tool_version="1.1",
        summary="Create reports/summary.md",
        effects=[
            EffectItem(
                kind="workspace_write",
                resource="reports/summary.md",
                risk="moderate",
                persistent=True,
            )
        ],
        required_permissions=["process_execute", "workspace_write"],
        analyzer_version="2",
        approval_required=True,
    )

    assert request.subject.delegation_chain[-1] == "run:1"
    assert plan.effects[0].kind.value == "workspace_write"
    with pytest.raises(ValidationError):
        ActionEffectPlan(
            tool_name="file_write",
            tool_version="1",
            summary="Write a file",
            analyzer_version="1",
            approval_required=True,
        )


def test_workspace_paths_reject_escape_and_shell_ambiguous_names():
    assert validate_workspace_path("reports/summary.md") == "reports/summary.md"
    for unsafe in ("../secret", "/etc/passwd", "-rf", "dir\\file", "a/../b"):
        with pytest.raises(ValueError):
            validate_workspace_path(unsafe)


async def test_workspace_repository_persists_files_tombstones_and_checkpoints(session):
    run = await RunRepository(session).create_task_run("Workspace persistence", {})
    repository = WorkspaceRepository(session)
    workspace = await repository.get_or_create(run.task_id)
    file = await repository.upsert_file(
        workspace.id,
        "reports/summary.md",
        mime_type="text/markdown",
        size_bytes=12,
        checksum="sha256:one",
        security_status="safe",
        deliverable_candidate=True,
    )
    change = await repository.record_change(
        workspace_id=workspace.id,
        run_id=run.id,
        relative_path=file.relative_path,
        change_kind="deleted",
        before_checksum=file.checksum,
    )
    checkpoint = await repository.create_checkpoint(
        workspace_id=workspace.id,
        run_id=run.id,
        manifest={"files": []},
        manifest_hash="sha256:manifest",
    )
    persisted_file = await session.get(WorkspaceFileRecord, file.id)

    assert workspace.storage_key.endswith(run.task_id)
    assert change.change_kind == "deleted"
    assert persisted_file is not None
    assert persisted_file.status == "deleted"
    assert persisted_file.deleted_at is not None
    assert checkpoint.manifest_hash == "sha256:manifest"


async def test_permission_repository_persists_identity_delegation_catalog_and_data_flow(session):
    run = await RunRepository(session).create_task_run("Permission persistence", {})
    repository = PermissionRepository(session)
    parent = await repository.create_identity(
        identity_type="main_agent",
        principal="agent:astra-main",
        run_id=run.id,
    )
    child = await repository.create_identity(
        identity_type="subagent",
        principal="agent:researcher",
        run_id=run.id,
        parent_identity_id=parent.id,
    )
    delegation = await repository.create_delegation(
        parent_identity_id=parent.id,
        child_identity_id=child.id,
        delegated_scope={
            "actions": ["workspace.file.read"],
            "resources": ["task://workspace/**"],
        },
    )
    snapshot = await repository.freeze_tool_catalog(
        run.id,
        catalog=[{"name": "web_search", "version": "1"}],
        digest="sha256:catalog",
    )
    same_snapshot = await repository.freeze_tool_catalog(
        run.id,
        catalog=[{"name": "web_search", "version": "1"}],
        digest="sha256:catalog",
    )
    with pytest.raises(ValueError):
        await repository.freeze_tool_catalog(
            run.id,
            catalog=[{"name": "web_search", "version": "2"}],
            digest="sha256:changed",
        )
    credential = await repository.create_credential_grant(
        run_id=run.id,
        agent_identity_id=child.id,
        service="example",
        scopes=["records.read"],
        resources=["record:1"],
        actions=["read"],
        expires_at=utc_now() + timedelta(minutes=5),
    )
    state = await repository.update_data_flow_state(
        run.id,
        expected_version=0,
        trust_sources=["workspace", "web"],
        data_labels=["internal"],
        prohibited_destinations=["private-network"],
    )
    state = await repository.update_data_flow_state(
        run.id,
        expected_version=state.state_version,
        data_labels=["internal", "personal"],
    )

    assert delegation.parent_identity_id == parent.id
    assert snapshot.id == same_snapshot.id
    assert credential.task_id == run.task_id
    assert credential.agent_identity_id == child.id
    assert state.state_version == 2
    assert state.data_labels == ["internal", "personal"]


async def test_approval_persistence_freezes_effects_and_creates_scoped_lease(session):
    repository = RunRepository(session)
    run = await repository.create_task_run("Effect approval persistence", {})
    turn = await repository.create_agent_turn(
        run.id,
        1,
        "call_tool",
        "Create report",
        selected_tool="file_write",
        phase="prepared",
    )
    call = await repository.start_tool_call(
        run.id,
        None,
        "file_write",
        "1",
        {"path": "reports/summary.md"},
        "workspace_write",
        "persistent_side_effect",
        status="awaiting_approval",
    )
    effect_plan = {
        "tool_name": "file_write",
        "tool_version": "1",
        "summary": "Create reports/summary.md",
        "effects": [
            {
                "kind": "workspace_write",
                "resource": "reports/summary.md",
                "persistent": True,
            }
        ],
        "analyzer_version": "2",
        "approval_required": True,
    }
    approval = await repository.create_approval_request(
        run_id=run.id,
        turn_id=turn.id,
        tool_call_id=call.id,
        tool_name="file_write",
        tool_version="1",
        frozen_input={"path": "reports/summary.md"},
        input_hash="sha256:input",
        frozen_effect_plan=effect_plan,
        effect_plan_hash="sha256:effect",
        analyzer_version="2",
        analyzer_digest="sha256:analyzer",
        preview="Create reports/summary.md",
        permission="workspace_write",
        impact="persistent_side_effect",
        similar_matcher={
            "kind": "resource_glob",
            "resource_matcher": {"glob": "reports/**"},
        },
    )
    waiting = await repository.set_waiting_state(
        run.id,
        {"kind": "tool_approval", "approval_id": approval.id, "tool_call_id": call.id},
    )
    await repository.decide_approval(
        run.id,
        approval.id,
        "allow_similar",
        continuation_token=waiting.waiting_state["continuation_token"],
        reviewer_identity={"identity_type": "user", "principal": "user:local"},
    )
    grants = await repository.list_approval_grants(run.id, "file_write", "1")

    assert approval.effect_plan_hash == "sha256:effect"
    assert approval.analyzer_version == "2"
    assert approval.frozen_effect_plan["effects"][0]["kind"] == "workspace_write"
    assert len(grants) == 1
    assert grants[0].scope == "run"
    assert grants[0].task_id == run.task_id
    assert grants[0].effect_kinds == ["workspace_write"]
    assert grants[0].resource_matcher == {"glob": "reports/**"}
