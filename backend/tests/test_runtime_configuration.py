import json
from pathlib import Path

import pytest

from app.common.core.config import AstraRuntimeSettings
from app.domain.agent_profile import AgentProfileConfigurationError
from app.infrastructure.sandbox.profiles import (
    CORE_DEPENDENCIES,
    RuntimeProfileService,
    normalize_dependencies,
)


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
    service = RuntimeProfileService(AstraRuntimeSettings(runtime_profile_path=str(tmp_path / "profile.json")))

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


def test_runtime_profile_exposes_updates_and_resets_agent_profile(tmp_path):
    profile_path = tmp_path / "profile.json"
    service = RuntimeProfileService(AstraRuntimeSettings(runtime_profile_path=str(profile_path)))
    initial = service.read()["agent_profile"]
    assert initial["source"] == "default"
    assert set(initial["documents"]) == {"identity", "soul", "memory", "autodream"}
    assert initial["default_documents"] == initial["documents"]

    documents = dict(initial["documents"])
    documents["identity"] = documents["identity"].replace(
        "Astra 是面向真实任务执行的通用 AI Agent",
        "Astra 是可由本机用户定制的通用 AI Agent",
    )
    updated = service.update_agent_profile(documents)

    assert updated["source"] == "user"
    assert updated["version"] != initial["version"]
    assert updated["default_documents"] == initial["documents"]
    assert service.active_agent_profile().manifest.version == updated["version"]
    persisted = json.loads(profile_path.read_text())["agent_profile"]
    assert set(persisted) == {"documents"}

    restored = service.reset_agent_profile()
    assert restored["source"] == "default"
    assert restored["version"] == initial["version"]
    assert "agent_profile" not in json.loads(profile_path.read_text())


def test_invalid_agent_profile_update_is_atomic(tmp_path):
    profile_path = tmp_path / "profile.json"
    service = RuntimeProfileService(AstraRuntimeSettings(runtime_profile_path=str(profile_path)))
    initial = service.read()["agent_profile"]
    documents = dict(initial["documents"])
    documents["identity"] = "invalid"

    with pytest.raises(AgentProfileConfigurationError):
        service.update_agent_profile(documents)

    assert service.read()["agent_profile"] == initial
    assert not profile_path.exists()


def test_memory_settings_are_applied_and_survive_service_restart(tmp_path):
    profile_path = tmp_path / "profile.json"
    settings = AstraRuntimeSettings(runtime_profile_path=str(profile_path))
    service = RuntimeProfileService(settings)
    defaults = service.read()["memory_settings"]
    assert defaults["recall_enabled"] is False
    assert defaults["write_enabled"] is True

    updated = {
        **defaults,
        "recall_enabled": True,
        "retrieval_max_items": 5,
        "retrieval_max_tokens": 1200,
        "retrieval_min_confidence": 0.4,
        "retrieval_min_score": 0.2,
        "autodream_enabled": True,
        "autodream_scan_seconds": 900,
        "autodream_min_candidates": 4,
    }
    assert service.update_memory_settings(updated) == updated
    assert settings.agent_memory_cross_session_enabled is True

    restarted_settings = AstraRuntimeSettings(runtime_profile_path=str(profile_path))
    restarted = RuntimeProfileService(restarted_settings)
    assert restarted.memory_settings() == updated
    assert restarted_settings.agent_memory_autodream_enabled is True


def test_obsolete_shadow_memory_settings_are_rejected(tmp_path):
    profile_path = tmp_path / "profile.json"
    defaults = RuntimeProfileService(
        AstraRuntimeSettings(runtime_profile_path=str(tmp_path / "defaults.json"))
    ).memory_settings()
    legacy = {**defaults, "cross_session_mode": "shadow"}
    legacy.pop("recall_enabled")
    profile_path.write_text(json.dumps({"memory_settings": legacy}))

    with pytest.raises(ValueError, match="字段不完整"):
        RuntimeProfileService(AstraRuntimeSettings(runtime_profile_path=str(profile_path)))


def test_invalid_memory_settings_update_is_atomic(tmp_path):
    profile_path = tmp_path / "profile.json"
    settings = AstraRuntimeSettings(runtime_profile_path=str(profile_path))
    service = RuntimeProfileService(settings)
    initial = service.memory_settings()

    with pytest.raises(ValueError, match="超出允许范围"):
        service.update_memory_settings({**initial, "retrieval_min_score": 2.0})

    assert service.memory_settings() == initial
    assert not profile_path.exists()


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
