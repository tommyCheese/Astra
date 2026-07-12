import asyncio
import json

import pytest

from app.core.config import Settings
from app.runtime_profiles import RuntimeProfileService, normalize_dependencies


def test_dependencies_are_normalized_sorted_and_pinned():
    assert normalize_dependencies([
        {"name": "OpenPyXL", "version": "3.1.5"},
        {"name": "polars", "version": "1.31.0"},
    ]) == [
        {"name": "openpyxl", "version": "3.1.5"},
        {"name": "polars", "version": "1.31.0"},
    ]


def test_dependency_version_is_optional_and_means_latest():
    assert normalize_dependencies([{"name": "openpyxl", "version": ""}]) == [
        {"name": "openpyxl", "version": ""},
    ]


@pytest.mark.parametrize("dependency", [
    {"name": "requests", "version": "*"},
    {"name": "pkg @ https://example.com/pkg.whl", "version": "1.0.0"},
    {"name": "numpy", "version": "2.0.0"},
])
def test_dependencies_reject_unsafe_or_protected_values(dependency):
    with pytest.raises(ValueError):
        normalize_dependencies([dependency])


def test_dependencies_reject_duplicates_after_normalization():
    with pytest.raises(ValueError, match="依赖重复"):
        normalize_dependencies([
            {"name": "my_pkg", "version": "1.0.0"},
            {"name": "my-pkg", "version": "1.0.0"},
        ])


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
    assert json.loads((tmp_path / "runtime" / "profile.json").read_text())["dependencies"] == []
    assert not (tmp_path / "runtime" / "profile.json.tmp").exists()
