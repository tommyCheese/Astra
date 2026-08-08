import base64
import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.skills.builtin_catalog import ensure_builtin_skills
from app.common.core.config import AstraRuntimeSettings, get_settings
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.infrastructure.db.model_base import AstraOrmRecordBase
from app.infrastructure.db.session import get_session
from app.infrastructure.model_clients.mock import MockModelClient
from app.main import create_app


def skill_md(name: str = "research-notes", body: str = "Follow the workflow.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Collect and organize research notes.\n"
        "compatibility: Astra 0.1+\n"
        "allowed-tools: catalog_search sandbox\n"
        "metadata:\n"
        "  category: research\n"
        "---\n\n"
        f"{body}\n"
    )


class DirectFinalizeSkillClient(MockModelClient):
    def __init__(self):
        self.blocks_seen_at_first_decision: list[dict] = []

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.blocks_seen_at_first_decision = list(self.skill_blocks)
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="Skill 已绑定，直接完成。",
        ), AgentFinalAnswer(summary="已按 Skill 完成。")


@pytest.fixture
async def skill_client(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    scheduled_runs: list[str] = []

    async def record_schedule(run_id, settings):
        scheduled_runs.append(run_id)

    settings = AstraRuntimeSettings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
    app = create_app(settings)
    monkeypatch.setattr(app.state.container.run_dispatcher, "_run_starter", record_schedule)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with session_factory() as session:
        await ensure_builtin_skills(session, settings)
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.schedule_calls = scheduled_runs
        yield client
    await engine.dispose()


async def test_skill_api_authoring_publish_and_run_selection(skill_client):
    created = await skill_client.post(
        "/api/skills",
        json={
            "name": "research-notes",
            "description": "Collect and organize research notes.",
        },
    )
    assert created.status_code == 200
    skill = created.json()
    token = skill["draft_revision_token"]

    saved = await skill_client.put(
        f"/api/skills/{skill['id']}/draft/files",
        json={
            "revision_token": token,
            "operations": [
                {
                    "action": "write",
                    "path": "references/checklist.md",
                    "content": "# Checklist\n",
                }
            ],
        },
    )
    assert saved.status_code == 200
    token = saved.json()["revision_token"]
    published = await skill_client.post(
        f"/api/skills/{skill['id']}/publish",
        json={"revision_token": token},
    )
    assert published.status_code == 200

    run = await skill_client.post(
        "/api/runs",
        json={
            "goal": "Organize these notes",
            "answer_mode": "standard",
            "skill_ids": ["custom:research-notes"],
        },
    )
    assert run.status_code == 200, run.text
    audit = await skill_client.get(f"/api/runs/{run.json()['run_id']}/skills")
    assert audit.status_code == 200
    assert audit.json()["activations"][0]["qualified_identity"] == "custom:research-notes"
    quick_view = await skill_client.get(f"/api/runs/{run.json()['run_id']}")
    assert quick_view.status_code == 200
    assert quick_view.json()["answer_mode"] == "standard"
    assert quick_view.json()["steps"] == []

    detail = await skill_client.get(f"/api/skills/{skill['id']}")
    draft_token = detail.json()["draft_revision_token"]
    draft_edit = await skill_client.put(
        f"/api/skills/{skill['id']}/draft/files",
        json={
            "revision_token": draft_token,
            "operations": [
                {
                    "action": "write",
                    "path": "references/test-only.md",
                    "content": "Draft-only content",
                }
            ],
        },
    )
    assert draft_edit.status_code == 200
    draft_token = draft_edit.json()["revision_token"]
    draft_test = await skill_client.post(
        f"/api/skills/{skill['id']}/test-runs",
        json={
            "revision_token": draft_token,
            "goal": "Test the Draft",
            "answer_mode": "standard",
        },
    )
    assert draft_test.status_code == 200, draft_test.text
    test_audit = await skill_client.get(f"/api/runs/{draft_test.json()['run_id']}/skills")
    assert test_audit.status_code == 200
    assert test_audit.json()["draft_test"] is True
    assert test_audit.json()["answer_mode"] == "standard"
    test_digest = test_audit.json()["catalog"][0]["digest"]
    test_view = await skill_client.get(f"/api/runs/{draft_test.json()['run_id']}")
    assert test_view.json()["steps"] == []
    assert test_view.json()["model_policy"]["thinking"]["source"] == "model_default"
    assert test_view.json()["model_policy"]["thinking"]["capability_version"] == 2
    ordinary_catalog = await skill_client.get("/api/skills/catalog")
    published_entry = next(
        item
        for item in ordinary_catalog.json()["skills"]
        if item["qualified_identity"] == "custom:research-notes"
    )
    assert published_entry["digest"] != test_digest
    edited_again = await skill_client.put(
        f"/api/skills/{skill['id']}/draft/files",
        json={
            "revision_token": draft_token,
            "operations": [
                {
                    "action": "write",
                    "path": "references/after-test.md",
                    "content": "Later edit",
                }
            ],
        },
    )
    assert edited_again.status_code == 200
    frozen_again = await skill_client.get(f"/api/runs/{draft_test.json()['run_id']}/skills")
    assert frozen_again.json()["catalog"][0]["digest"] == test_digest
    trusted_test = await skill_client.post(
        f"/api/skills/{skill['id']}/test-runs",
        json={
            "revision_token": edited_again.json()["revision_token"],
            "goal": "Trusted Draft test",
            "answer_mode": "trusted",
        },
    )
    assert trusted_test.status_code == 200
    trusted_audit = await skill_client.get(f"/api/runs/{trusted_test.json()['run_id']}/skills")
    assert trusted_audit.json()["draft_test"] is True
    assert trusted_audit.json()["answer_mode"] == "trusted"
    metrics = await skill_client.get("/api/skills/metrics/summary")
    assert metrics.status_code == 200
    assert metrics.json()["answer_modes"]["standard"] >= 2
    assert metrics.json()["answer_modes"]["trusted"] >= 1
    assert metrics.json()["catalog_metadata_chars"]["max"] > 0
    assert "catalog_to_first_model_ms" in metrics.json()


async def test_run_skill_selection_validation_and_atomic_activation(skill_client):
    async def publish(name: str) -> str:
        created = (
            await skill_client.post(
                "/api/skills",
                json={"name": name, "description": f"{name} test Skill"},
            )
        ).json()
        response = await skill_client.post(
            f"/api/skills/{created['id']}/publish",
            json={"revision_token": created["draft_revision_token"]},
        )
        assert response.status_code == 200, response.text
        return f"custom:{name}"

    identities = [await publish("atomic-one"), await publish("atomic-two")]

    duplicate = await skill_client.post(
        "/api/runs",
        json={"goal": "Duplicate", "skill_ids": [identities[0], identities[0]]},
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "REQUEST_INVALID"

    malformed = await skill_client.post(
        "/api/runs",
        json={"goal": "Malformed", "skill_ids": ["custom:Hello Astra"]},
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "REQUEST_INVALID"

    unavailable = await skill_client.post(
        "/api/runs",
        json={
            "goal": "Atomic failure",
            "skill_ids": [identities[0], "custom:no-longer-available"],
        },
    )
    assert unavailable.status_code == 422
    assert unavailable.json()["error"]["code"] == "SKILL_SELECTION_INVALID"
    assert unavailable.json()["error"]["details"]["qualified_identity"] == (
        "custom:no-longer-available"
    )
    assert (await skill_client.get("/api/runs")).json() == []
    assert skill_client.schedule_calls == []

    created = await skill_client.post(
        "/api/runs",
        json={"goal": "Atomic success", "skill_ids": identities[:2]},
    )
    assert created.status_code == 200, created.text
    audit = await skill_client.get(f"/api/runs/{created.json()['run_id']}/skills")
    assert {item["qualified_identity"] for item in audit.json()["activations"]} == set(
        identities[:2]
    )
    assert all(item["initiator"] == "explicit" for item in audit.json()["activations"])
    assert skill_client.schedule_calls == [created.json()["run_id"]]


async def test_zip_import_is_draft_only_and_rejects_escape(skill_client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("imported/SKILL.md", skill_md("imported"))
    response = await skill_client.post(
        "/api/skills/import",
        json={
            "filename": "imported.zip",
            "content_base64": base64.b64encode(buffer.getvalue()).decode(),
        },
    )
    assert response.status_code == 200
    assert response.json()["lifecycle_state"] == "draft"

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../SKILL.md", skill_md("bad"))
    response = await skill_client.post(
        "/api/skills/import",
        json={
            "filename": "bad.zip",
            "content_base64": base64.b64encode(bad.getvalue()).decode(),
        },
    )
    assert response.status_code == 422


async def test_skill_api_stale_write_preview_and_history(skill_client):
    created = (
        await skill_client.post(
            "/api/skills",
            json={"name": "editor-check", "description": "Editor check"},
        )
    ).json()
    token = created["draft_revision_token"]
    saved = await skill_client.put(
        f"/api/skills/{created['id']}/draft/files",
        json={
            "revision_token": token,
            "operations": [
                {
                    "action": "write",
                    "path": "SKILL.md",
                    "content": skill_md("editor-check", "<script>alert(1)</script>safe"),
                }
            ],
        },
    )
    assert saved.status_code == 200
    stale = await skill_client.put(
        f"/api/skills/{created['id']}/draft/files",
        json={
            "revision_token": token,
            "operations": [
                {
                    "action": "write",
                    "path": "references/stale.md",
                    "content": "stale",
                }
            ],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SKILL_DRAFT_STALE"

    preview = await skill_client.get(
        f"/api/skills/{created['id']}/preview",
    )
    assert preview.status_code == 200
    assert "<script>" not in preview.json()["markdown"]

    current = saved.json()["revision_token"]
    cleaned = await skill_client.put(
        f"/api/skills/{created['id']}/draft/files",
        json={
            "revision_token": current,
            "operations": [
                {
                    "action": "write",
                    "path": "SKILL.md",
                    "content": skill_md("editor-check"),
                }
            ],
        },
    )
    assert cleaned.status_code == 200
    current = cleaned.json()["revision_token"]
    publish = await skill_client.post(
        f"/api/skills/{created['id']}/publish",
        json={"revision_token": current},
    )
    assert publish.status_code == 200
    history = await skill_client.get(f"/api/skills/{created['id']}/revisions")
    assert history.status_code == 200
    assert history.json()[0]["digest"] == publish.json()["digest"]
    revision_id = history.json()[0]["id"]
    revision_detail = await skill_client.get(f"/api/skills/{created['id']}/revisions/{revision_id}")
    assert revision_detail.status_code == 200
    assert revision_detail.json()["files"][0]["readonly"] is True
    historical_file = await skill_client.get(
        f"/api/skills/{created['id']}/revisions/{revision_id}/file",
        params={"path": "SKILL.md"},
    )
    assert historical_file.status_code == 200
    assert historical_file.json()["readonly"] is True
    assert "editor-check" in historical_file.json()["content"]

    current_detail = await skill_client.get(f"/api/skills/{created['id']}")
    next_draft = await skill_client.put(
        f"/api/skills/{created['id']}/draft/files",
        json={
            "revision_token": current_detail.json()["draft_revision_token"],
            "operations": [
                {
                    "action": "write",
                    "path": "SKILL.md",
                    "content": skill_md("editor-check", "second revision"),
                }
            ],
        },
    )
    assert next_draft.status_code == 200
    second_publish = await skill_client.post(
        f"/api/skills/{created['id']}/publish",
        json={"revision_token": next_draft.json()["revision_token"]},
    )
    assert second_publish.status_code == 200
    historical_diff = await skill_client.get(
        f"/api/skills/{created['id']}/revisions/{revision_id}/diff"
    )
    assert historical_diff.status_code == 200
    assert historical_diff.json()["base_version"] == 1
    assert historical_diff.json()["target_version"] == 2
    assert "diff --git a/SKILL.md b/SKILL.md" in historical_diff.json()["patch"]
    assert "+second revision" in historical_diff.json()["patch"]
