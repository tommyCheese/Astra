import asyncio
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.runtime_profiles import CORE_DEPENDENCIES, RuntimeProfileService, normalize_dependencies


def test_dependencies_are_normalized_sorted_and_pinned():
    assert normalize_dependencies(
        [
            {"name": "OpenPyXL", "version": "3.1.5"},
            {"name": "polars", "version": "1.31.0"},
        ]
    ) == [
        {"name": "openpyxl", "version": "3.1.5"},
        {"name": "polars", "version": "1.31.0"},
    ]


def test_dependency_version_is_optional_and_means_latest():
    assert normalize_dependencies([{"name": "openpyxl", "version": ""}]) == [
        {"name": "openpyxl", "version": ""},
    ]


def test_runtime_profile_exposes_locked_core_dependency_versions(tmp_path):
    service = RuntimeProfileService(Settings(runtime_profile_path=str(tmp_path / "profile.json")))

    assert service.read()["core_dependencies"] == CORE_DEPENDENCIES
    assert {item["name"] for item in CORE_DEPENDENCIES} == {
        "matplotlib",
        "numpy",
        "pandas",
        "pillow",
        "pyarrow",
        "scipy",
        "seaborn",
    }
    runtime_project = (
        Path(__file__).parents[2] / "runtimes" / "data-viz" / "pyproject.toml"
    ).read_text()
    for item in CORE_DEPENDENCIES:
        assert f"{item['name']}=={item['version']}" in runtime_project


@pytest.mark.parametrize(
    "dependency",
    [
        {"name": "requests", "version": "*"},
        {"name": "pkg @ https://example.com/pkg.whl", "version": "1.0.0"},
        {"name": "numpy", "version": "2.0.0"},
    ],
)
def test_dependencies_reject_unsafe_or_protected_values(dependency):
    with pytest.raises(ValueError):
        normalize_dependencies([dependency])


def test_dependencies_reject_duplicates_after_normalization():
    with pytest.raises(ValueError, match="依赖重复"):
        normalize_dependencies(
            [
                {"name": "my_pkg", "version": "1.0.0"},
                {"name": "my-pkg", "version": "1.0.0"},
            ]
        )


async def test_profile_write_is_atomic_and_finished_tasks_are_removed(tmp_path, monkeypatch):
    settings = Settings(
        runtime_profile_path=str(tmp_path / "runtime" / "profile.json"),
        sandbox_runtime_image="astra-data-viz:test",
    )
    service = RuntimeProfileService(settings)

    async def successful_build(build_id, dependencies, digest):
        state = service.read()
        state["build"]["status"] = "succeeded"
        service.write(state)

    monkeypatch.setattr(service, "_build", successful_build)
    state = await service.start([])
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert state["build"]["status"] == "queued"
    assert service.read()["build"]["status"] == "succeeded"
    assert service.tasks == {}
    persisted = json.loads((tmp_path / "runtime" / "profile.json").read_text())
    assert persisted["dependencies"] == []
    assert "core_dependencies" not in persisted
    assert not (tmp_path / "runtime" / "profile.json.tmp").exists()


async def test_build_progress_uses_live_subprocess_output(tmp_path):
    service = RuntimeProfileService(Settings(runtime_profile_path=str(tmp_path / "profile.json")))
    service.write(
        {
            "dependencies": [],
            "active_image": "base",
            "dependency_digest": "base",
            "build": {"id": "build-1", "status": "building", "progress": 5, "log": "start"},
        }
    )

    returncode = await service._run_with_progress(
        "build-1",
        [sys.executable, "-c", "print('installing dependency')"],
        phase="构建镜像并安装依赖",
        start=10,
        end=82,
    )

    build = service.read()["build"]
    assert returncode == 0
    assert build["progress"] == 11
    assert build["phase"] == "构建镜像并安装依赖"
    assert build["log"] == "installing dependency"


async def test_failed_build_command_preserves_recent_error_output(tmp_path):
    service = RuntimeProfileService(Settings(runtime_profile_path=str(tmp_path / "profile.json")))
    service.write(
        {
            "dependencies": [],
            "active_image": "base",
            "dependency_digest": "base",
            "build": {"id": "build-1", "status": "building", "progress": 5, "log": "start"},
        }
    )

    with pytest.raises(RuntimeError, match="package not found") as exc_info:
        await service._run_with_progress(
            "build-1",
            [
                sys.executable,
                "-c",
                "import sys; print('package not found'); raise SystemExit(17)",
            ],
            phase="构建镜像并安装依赖",
            start=10,
            end=82,
        )

    assert "退出码 17" in str(exc_info.value)


async def test_runtime_build_command_supports_docker_without_buildx(tmp_path, monkeypatch):
    service = RuntimeProfileService(
        Settings(
            runtime_profile_path=str(tmp_path / "profile.json"),
            sandbox_runtime_image="astra-data-viz:test",
        )
    )
    commands = []

    async def successful_command(build_id, command, **kwargs):
        commands.append(command)
        return 0

    async def successful_cleanup(*args):
        return True

    monkeypatch.setattr(service, "_run_with_progress", successful_command)
    monkeypatch.setattr(service, "_remove_managed_image", successful_cleanup)
    state = await service.start([{"name": "openpyxl", "version": "3.1.5"}])
    await service.tasks[state["build"]["id"]]

    build_command = commands[0]
    assert build_command[1] == "build"
    assert "--progress" not in build_command
    assert service.read()["build"]["status"] == "succeeded"
    assert service.read()["active_image"].startswith("astra-data-viz:custom-")
    assert service.read()["images"][0]["image"] == service.read()["active_image"]


async def test_unpinned_dependency_is_resolved_persisted_and_content_addressed(
    tmp_path, monkeypatch
):
    service = RuntimeProfileService(
        Settings(
            runtime_profile_path=str(tmp_path / "profile.json"),
            sandbox_runtime_image="astra-data-viz:test",
        )
    )
    commands = []

    async def successful_command(build_id, command, **kwargs):
        commands.append(command)
        if kwargs.get("capture_output"):
            return 'ASTRA_DEPENDENCIES={"openpyxl": "3.1.5"}'
        return 0

    async def successful_cleanup(*args):
        return True

    monkeypatch.setattr(service, "_run_with_progress", successful_command)
    monkeypatch.setattr(service, "_remove_managed_image", successful_cleanup)
    state = await service.start([{"name": "openpyxl", "version": ""}])
    await service.tasks[state["build"]["id"]]

    profile = service.read()
    resolved = [{"name": "openpyxl", "version": "3.1.5"}]
    expected_digest = hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest()[:16]
    assert "--no-cache" in commands[0]
    assert any(command[1] == "tag" for command in commands)
    assert profile["dependencies"] == resolved
    assert profile["dependency_digest"] == expected_digest
    assert profile["active_image"] == f"astra-data-viz:custom-{expected_digest}"


async def test_runtime_image_retention_protects_active_and_recent_images(tmp_path, monkeypatch):
    service = RuntimeProfileService(
        Settings(
            runtime_profile_path=str(tmp_path / "profile.json"),
            sandbox_runtime_image="astra-data-viz:test",
            runtime_image_keep_recent=1,
            runtime_image_retention_days=30,
        )
    )
    now = datetime.now(timezone.utc)
    service.write(
        {
            "dependencies": [],
            "active_image": "astra-data-viz:custom-active",
            "dependency_digest": "active",
            "build": {"id": "build-1", "status": "succeeded", "log": "done"},
            "images": [
                {"image": "astra-data-viz:custom-active", "activated_at": now.isoformat()},
                {
                    "image": "astra-data-viz:custom-recent",
                    "activated_at": (now - timedelta(days=1)).isoformat(),
                },
                {
                    "image": "astra-data-viz:custom-overflow",
                    "activated_at": (now - timedelta(days=2)).isoformat(),
                },
                {
                    "image": "astra-data-viz:custom-expired",
                    "activated_at": (now - timedelta(days=40)).isoformat(),
                },
            ],
        }
    )
    removed = []

    async def remove(build_id, image):
        removed.append(image)
        return True

    monkeypatch.setattr(service, "_remove_managed_image", remove)

    assert await service._prune_images("build-1") is False
    assert removed == [
        "astra-data-viz:custom-overflow",
        "astra-data-viz:custom-expired",
    ]
    assert [item["image"] for item in service.read()["images"]] == [
        "astra-data-viz:custom-active",
        "astra-data-viz:custom-recent",
    ]


async def test_failed_runtime_image_cleanup_keeps_history_for_retry(tmp_path, monkeypatch):
    service = RuntimeProfileService(
        Settings(
            runtime_profile_path=str(tmp_path / "profile.json"),
            runtime_image_keep_recent=0,
            runtime_image_retention_days=0,
        )
    )
    service.write(
        {
            "dependencies": [],
            "active_image": "astra-data-viz:custom-active",
            "dependency_digest": "active",
            "build": {"id": "build-1", "status": "succeeded", "log": "done"},
            "images": [
                {"image": "astra-data-viz:custom-active", "activated_at": None},
                {"image": "astra-data-viz:custom-in-use", "activated_at": None},
            ],
        }
    )

    async def cannot_remove(*args):
        return False

    monkeypatch.setattr(service, "_remove_managed_image", cannot_remove)

    assert await service._prune_images("build-1") is True
    assert any(
        item["image"] == "astra-data-viz:custom-in-use"
        for item in service.read()["images"]
    )


async def test_runtime_image_cleanup_rejects_non_astra_tags(tmp_path, monkeypatch):
    service = RuntimeProfileService(Settings(runtime_profile_path=str(tmp_path / "profile.json")))
    called = False

    async def should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", should_not_run)

    assert await service._remove_managed_image("build-1", "postgres:latest") is False
    assert called is False


async def test_active_build_can_be_cancelled(tmp_path, monkeypatch):
    service = RuntimeProfileService(
        Settings(
            runtime_profile_path=str(tmp_path / "profile.json"),
            sandbox_runtime_image="astra-data-viz:test",
        )
    )

    async def wait_forever(*args, **kwargs):
        await asyncio.Event().wait()

    async def successful_cleanup(*args):
        return True

    monkeypatch.setattr(service, "_run_with_progress", wait_forever)
    monkeypatch.setattr(service, "_remove_managed_image", successful_cleanup)
    state = await service.start([{"name": "openpyxl", "version": "3.1.5"}])
    await asyncio.sleep(0)
    cancelled = await service.cancel(state["build"]["id"])

    assert cancelled["build"]["status"] == "cancelled"
    assert cancelled["build"]["phase"] == "已取消"
    assert cancelled["active_image"] == "astra-data-viz:test"


async def test_shutdown_cancels_owned_build_tasks(tmp_path, monkeypatch):
    service = RuntimeProfileService(
        Settings(
            runtime_profile_path=str(tmp_path / "profile.json"),
            sandbox_runtime_image="astra-data-viz:test",
        )
    )

    async def wait_forever(*args, **kwargs):
        await asyncio.Event().wait()

    async def successful_cleanup(*args):
        return True

    monkeypatch.setattr(service, "_run_with_progress", wait_forever)
    monkeypatch.setattr(service, "_remove_managed_image", successful_cleanup)
    await service.start([{"name": "openpyxl", "version": "3.1.5"}])
    await asyncio.sleep(0)

    await service.shutdown()
    await asyncio.sleep(0)

    assert service.tasks == {}
    assert service.read()["build"]["status"] == "cancelled"


def test_interrupted_build_is_recovered_as_cancelled(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "dependencies": [{"name": "polars", "version": ""}],
                "active_image": "base",
                "dependency_digest": "base",
                "build": {"id": "old-build", "status": "building", "log": "installing"},
            }
        )
    )

    service = RuntimeProfileService(
        Settings(runtime_profile_path=str(profile_path)), recover_interrupted=True
    )

    assert service.read()["build"]["status"] == "cancelled"
    assert service.read()["build"]["phase"] == "构建已中断"


async def test_startup_cleans_recovered_staging_image(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "dependencies": [{"name": "polars", "version": ""}],
                "active_image": "base",
                "dependency_digest": "base",
                "build": {"id": "abc123", "status": "building", "log": "installing"},
            }
        )
    )
    service = RuntimeProfileService(
        Settings(runtime_profile_path=str(profile_path)), recover_interrupted=True
    )
    removed = []

    async def remove(build_id, image):
        removed.append((build_id, image))
        return True

    monkeypatch.setattr(service, "_remove_managed_image", remove)

    await service.startup()

    assert removed == [("abc123", "astra-data-viz:build-abc123")]
    assert service.recovered_staging_images == []


def test_read_only_profile_service_does_not_cancel_an_active_build(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "dependencies": [{"name": "polars", "version": ""}],
                "active_image": "base",
                "dependency_digest": "base",
                "build": {"id": "active-build", "status": "building", "log": "installing"},
            }
        )
    )

    service = RuntimeProfileService(Settings(runtime_profile_path=str(profile_path)))

    assert service.read()["build"]["status"] == "building"
