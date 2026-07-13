import asyncio
import json
import sys
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

    monkeypatch.setattr(service, "_run_with_progress", successful_command)
    state = await service.start([{"name": "openpyxl", "version": "3.1.5"}])
    await service.tasks[state["build"]["id"]]

    build_command = commands[0]
    assert build_command[1] == "build"
    assert "--progress" not in build_command
    assert service.read()["build"]["status"] == "succeeded"
    assert service.read()["active_image"].startswith("astra-data-viz:custom-")


async def test_active_build_can_be_cancelled(tmp_path, monkeypatch):
    service = RuntimeProfileService(
        Settings(
            runtime_profile_path=str(tmp_path / "profile.json"),
            sandbox_runtime_image="astra-data-viz:test",
        )
    )

    async def wait_forever(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_run_with_progress", wait_forever)
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

    monkeypatch.setattr(service, "_run_with_progress", wait_forever)
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
