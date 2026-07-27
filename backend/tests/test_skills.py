import base64
import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import runs as runs_api
from app.core.config import Settings, get_settings
from app.db.models import Base, RunSkillSnapshotRecord, SkillBlobRecord
from app.db.session import get_session
from app.main import create_app
from app.skills.catalog import SkillActivationService, SkillCatalogBuilder
from app.skills.packages import SkillPackageError, normalize_skill_path, parse_skill_package
from app.skills.storage import SkillService, SkillStorageError, ensure_builtin_skills


def skill_md(name: str = "research-notes", body: str = "Follow the workflow.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Collect and organize research notes.\n"
        "compatibility: Astra 0.1+\n"
        "allowed-tools: web_search sandbox\n"
        "metadata:\n"
        "  category: research\n"
        "---\n\n"
        f"{body}\n"
    )


def test_skill_package_validation_and_digest_drift():
    files = {
        "SKILL.md": skill_md(),
        "references/checklist.md": "# Checklist\n",
        "scripts/prepare.py": "print('ready')\n",
        "assets/icon.bin": b"\x00\x01",
    }
    package, normalized = parse_skill_package(
        files,
        directory_name="research-notes",
    )
    assert package.qualified_identity == "custom:research-notes"
    assert package.frontmatter.compatibility == "Astra 0.1+"
    assert package.requested_tool_patterns == ["web_search", "sandbox"]
    assert [item.path for item in package.resources] == sorted(files)
    assert normalized["SKILL.md"].startswith(b"---")

    changed, _ = parse_skill_package(
        {**files, "references/checklist.md": "# Changed\n"},
        directory_name="research-notes",
    )
    assert changed.digest != package.digest
    unchanged, _ = parse_skill_package(
        dict(reversed(list(files.items()))),
        directory_name="research-notes",
    )
    assert unchanged.digest == package.digest


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({"SKILL.md": "missing frontmatter"}, "skill.frontmatter_missing"),
        ({"SKILL.md": skill_md("astra-private")}, "skill.identity_reserved"),
        (
            {"SKILL.md": skill_md(), "../escape.txt": "no"},
            "skill.path_invalid",
        ),
        (
            {"SKILL.md": skill_md(), "scripts/native.so": b"\x00"},
            "skill.executable_binary",
        ),
        (
            {"SKILL.md": skill_md(), "notes/data.bin": b"\x00"},
            "skill.unexpected_binary",
        ),
    ],
)
def test_skill_package_rejects_unsafe_inputs(files, code):
    with pytest.raises(SkillPackageError) as raised:
        parse_skill_package(files, directory_name="research-notes")
    assert code in {item.code for item in raised.value.diagnostics}


def test_skill_package_enforces_limits_and_paths():
    with pytest.raises(SkillPackageError):
        parse_skill_package(
            {"SKILL.md": skill_md(), "references/large.md": "x" * 20},
            directory_name="research-notes",
            max_file_bytes=10,
        )
    for path in ("", "/etc/passwd", "a/../b", r"a\b"):
        with pytest.raises(ValueError):
            normalize_skill_path(path)


async def test_skill_revision_storage_restore_export_and_dedup(session):
    settings = Settings(model_provider="mock")
    service = SkillService(session, settings)
    skill = await service.create_custom(
        "research-notes",
        "Collect and organize research notes.",
        files={
            "SKILL.md": skill_md(),
            "references/checklist.md": "# Checklist\n",
        },
    )
    original_token = skill.draft.revision_token
    revision = await service.publish(skill.id, original_token)
    first_digest = revision.digest
    published_token = skill.draft.revision_token
    assert published_token != original_token
    with pytest.raises(SkillStorageError) as concurrent:
        await service.publish(skill.id, original_token)
    assert concurrent.value.code == "SKILL_DRAFT_STALE"
    await service.update_draft(
        skill.id,
        published_token,
        [
            {
                "action": "write",
                "path": "references/checklist.md",
                "content": "# Updated\n",
            }
        ],
    )
    assert skill.draft.revision_token != published_token
    with pytest.raises(SkillStorageError) as stale:
        await service.update_draft(skill.id, original_token, [])
    assert stale.value.code == "SKILL_DRAFT_STALE"

    revision2 = await service.publish(skill.id, skill.draft.revision_token)
    assert revision2.digest != first_digest
    await service.restore(skill.id, revision.id)
    assert await service.read_file(skill, "references/checklist.md") == b"# Checklist\n"

    archive = await service.export_zip(skill)
    with zipfile.ZipFile(io.BytesIO(archive)) as exported:
        assert exported.read("research-notes/SKILL.md") == skill_md().encode()
        round_trip_files = {
            info.filename.removeprefix("research-notes/"): exported.read(info)
            for info in exported.infolist()
            if not info.is_dir()
        }
    round_trip, _ = parse_skill_package(
        round_trip_files,
        directory_name="research-notes",
    )
    assert round_trip.digest == revision.digest
    blob_count = await session.scalar(select(func.count()).select_from(SkillBlobRecord))
    assert blob_count == 3


async def test_builtin_loading_clone_and_readonly(session):
    settings = Settings(model_provider="mock")
    await ensure_builtin_skills(session, settings)
    service = SkillService(session, settings)
    builtins = [item for item in await service.list_skills() if item.origin == "builtin"]
    assert {item.name for item in builtins} >= {
        "astra-skill-authoring",
        "astra-sandbox-example",
    }
    with pytest.raises(SkillStorageError) as readonly:
        await service.update_draft(builtins[0].id, "token", [])
    assert readonly.value.code == "SKILL_BUILTIN_READONLY"
    clone = await service.clone_builtin(builtins[0].id, "my-authoring")
    assert clone.origin == "custom"
    assert b"name: my-authoring" in await service.read_file(clone, "SKILL.md")


async def test_catalog_snapshot_activation_and_resource_verification(session, tmp_path):
    settings = Settings(model_provider="mock")
    service = SkillService(session, settings)
    skill = await service.create_custom(
        "research-notes",
        "Collect and organize research notes.",
        files={
            "SKILL.md": skill_md(),
            "references/checklist.md": "# Checklist\n",
            "scripts/prepare.py": "print('ready')\n",
            "assets/icon.svg": "<svg></svg>",
        },
    )
    await service.publish(skill.id, skill.draft.revision_token)
    from app.repositories.runs import RunRepository

    run = await RunRepository(session).create_task_run("research", {})
    catalog = await SkillCatalogBuilder(session).build(goal="research")
    snapshot = await SkillCatalogBuilder(session).freeze(
        run.id, "standard", catalog
    )
    activation = SkillActivationService(session, max_active=2, max_resource_bytes=100)
    result = await activation.activate(
        run.id,
        "custom:research-notes",
        initiator="model",
        reason="matched goal",
    )
    assert result["instructions"] == "Follow the workflow."
    assert "references/checklist.md" in {
        item["path"] for item in result["resources"]
    }
    assert await activation.read_resource(
        run.id, "custom:research-notes", "references/checklist.md"
    ) == b"# Checklist\n"
    blocks = await activation.prompt_blocks(run.id)
    assert blocks[0]["digest"] == catalog.entries[0].digest
    assert snapshot.catalog_digest == catalog.digest
    mounted = await activation.materialize_inputs(
        run.id,
        [
            {
                "qualified_identity": "custom:research-notes",
                "revision_id": catalog.entries[0].revision_id,
                "digest": catalog.entries[0].digest,
            }
        ],
        tmp_path,
    )
    assert {item["path"] for item in mounted} == {
        "assets/icon.svg",
        "scripts/prepare.py",
    }
    script = tmp_path / "skills/custom/research-notes/scripts/prepare.py"
    assert script.read_text() == "print('ready')\n"
    assert script.stat().st_mode & 0o222 == 0
    with pytest.raises(ValueError):
        await activation.read_resource(
            run.id, "custom:research-notes", "../outside.md"
        )
    script_resource = next(
        item
        for item in catalog.entries[0].resources
        if item.path == "scripts/prepare.py"
    )
    script_blob = await session.get(SkillBlobRecord, script_resource.digest)
    assert script_blob is not None
    script_blob.content = b"tampered"
    with pytest.raises(ValueError, match="digest verification"):
        await activation.materialize_inputs(
            run.id,
            [
                {
                    "qualified_identity": "custom:research-notes",
                    "revision_id": catalog.entries[0].revision_id,
                    "digest": catalog.entries[0].digest,
                }
            ],
            tmp_path / "tampered",
        )


async def test_catalog_is_deterministic_shortlisted_and_capability_filtered(session):
    settings = Settings(model_provider="mock")
    service = SkillService(session, settings)
    for name, tool in (
        ("alpha-notes", "web_search"),
        ("beta-notes", "sandbox"),
        ("gamma-notes", ""),
    ):
        content = skill_md(name).replace(
            "allowed-tools: web_search sandbox",
            f"allowed-tools: {tool}" if tool else "",
        )
        skill = await service.create_custom(
            name,
            f"{name} documentation",
            files={"SKILL.md": content},
        )
        await service.publish(skill.id, skill.draft.revision_token)

    builder = SkillCatalogBuilder(session, metadata_chars=1_000_000)
    first = await builder.build(runtime_capabilities={"web_search"})
    second = await builder.build(runtime_capabilities={"web_search"})
    assert first.digest == second.digest
    assert [item.qualified_identity for item in first.entries] == [
        "custom:alpha-notes",
        "custom:gamma-notes",
    ]

    short = await SkillCatalogBuilder(session, metadata_chars=450).build(
        goal="beta research",
        explicit_identities=["custom:beta-notes"],
    )
    assert short.truncated is True
    assert "custom:beta-notes" in {
        item.qualified_identity for item in short.entries
    }


async def test_frozen_catalog_survives_republish_and_rejects_drift(session):
    settings = Settings(model_provider="mock")
    service = SkillService(session, settings)
    skill = await service.create_custom(
        "snapshot-notes",
        "Snapshot behavior",
        files={
            "SKILL.md": skill_md("snapshot-notes"),
            "references/data.md": "first",
        },
    )
    first_revision = await service.publish(skill.id, skill.draft.revision_token)
    from app.repositories.runs import RunRepository

    run = await RunRepository(session).create_task_run("snapshot", {})
    builder = SkillCatalogBuilder(session)
    frozen = await builder.build()
    await builder.freeze(run.id, "standard", frozen)
    await service.update_draft(
        skill.id,
        skill.draft.revision_token,
        [{"action": "write", "path": "references/data.md", "content": "second"}],
    )
    await service.publish(skill.id, skill.draft.revision_token)

    activation = SkillActivationService(session)
    await activation.activate(
        run.id,
        "custom:snapshot-notes",
        initiator="model",
        reason="snapshot test",
    )
    assert (
        await activation.read_resource(
            run.id, "custom:snapshot-notes", "references/data.md"
        )
        == b"first"
    )

    first_revision.digest = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="revision_unavailable"):
        await activation.activate(
            run.id,
            "custom:snapshot-notes",
            initiator="model",
            reason="digest drift",
        )


async def test_activation_quota_deactivation_and_revocation(session):
    settings = Settings(model_provider="mock")
    service = SkillService(session, settings)
    revisions = []
    for name in ("quota-one", "quota-two"):
        skill = await service.create_custom(
            name,
            name,
            files={"SKILL.md": skill_md(name)},
        )
        revisions.append(await service.publish(skill.id, skill.draft.revision_token))
    from app.repositories.runs import RunRepository

    run = await RunRepository(session).create_task_run("quota", {})
    builder = SkillCatalogBuilder(session)
    await builder.freeze(run.id, "standard", await builder.build())
    activation = SkillActivationService(session, max_active=1)
    await activation.activate(
        run.id, "custom:quota-one", initiator="explicit", reason="selected"
    )
    with pytest.raises(ValueError, match="activation_budget_exceeded"):
        await activation.activate(
            run.id, "custom:quota-two", initiator="model", reason="automatic"
        )
    await activation.deactivate(run.id, "custom:quota-one", reason="finished")
    revisions[1].revoked_at = revisions[1].published_at
    with pytest.raises(ValueError, match="revision_revoked"):
        await activation.activate(
            run.id, "custom:quota-two", initiator="model", reason="automatic"
        )
    snapshot = await session.scalar(
        select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == run.id)
    )
    assert snapshot is not None
    assert snapshot.activations == []


@pytest.fixture
async def skill_client(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    async def noop_runner(run_id, settings):
        return None

    monkeypatch.setattr(runs_api, "start_run_in_process", noop_runner)
    settings = Settings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
    app = create_app(settings)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with session_factory() as session:
        await ensure_builtin_skills(session, settings)
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
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
    test_audit = await skill_client.get(
        f"/api/runs/{draft_test.json()['run_id']}/skills"
    )
    assert test_audit.status_code == 200
    assert test_audit.json()["draft_test"] is True
    assert test_audit.json()["answer_mode"] == "standard"
    test_digest = test_audit.json()["catalog"][0]["digest"]
    test_view = await skill_client.get(f"/api/runs/{draft_test.json()['run_id']}")
    assert test_view.json()["steps"] == []
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
    frozen_again = await skill_client.get(
        f"/api/runs/{draft_test.json()['run_id']}/skills"
    )
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
    trusted_audit = await skill_client.get(
        f"/api/runs/{trusted_test.json()['run_id']}/skills"
    )
    assert trusted_audit.json()["draft_test"] is True
    assert trusted_audit.json()["answer_mode"] == "trusted"
    metrics = await skill_client.get("/api/skills/metrics/summary")
    assert metrics.status_code == 200
    assert metrics.json()["answer_modes"]["standard"] >= 2
    assert metrics.json()["answer_modes"]["trusted"] >= 1
    assert metrics.json()["catalog_metadata_chars"]["max"] > 0
    assert "catalog_to_first_model_ms" in metrics.json()


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
                    "content": skill_md(
                        "editor-check", "<script>alert(1)</script>safe"
                    ),
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
