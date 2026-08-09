from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.agent_profile import AgentProfile, AgentProfileLoader

MEMORY_SETTING_BOUNDS = {
    "retrieval_max_items": (0, 50),
    "retrieval_max_tokens": (0, 32_000),
    "retrieval_min_confidence": (0.0, 1.0),
    "retrieval_min_score": (0.0, 1.0),
    "autodream_scan_seconds": (60, 604_800),
    "autodream_min_candidates": (2, 100),
}


class SandboxRuntimeConfiguration:
    """Owns persisted Agent Profile and Memory runtime settings."""

    def __init__(self, settings: Any, path: Path):
        self.settings = settings
        self.path = path
        self.packaged_profile = AgentProfileLoader().load()
        persisted = self.read_persisted()
        self.active_profile = self._load_agent_profile(persisted)
        if persisted.get("memory_settings") is not None:
            normalized = self._normalize_memory_settings(persisted["memory_settings"])
            self._apply_memory_settings(normalized)

    def read_persisted(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def write_state(self, value: dict[str, Any], *, persist_memory_settings: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        persisted = {key: item for key, item in value.items() if key not in {"core_dependencies", "image_policy"}}
        self._normalize_persisted_profile(persisted)
        if not persist_memory_settings and "memory_settings" not in self.read_persisted():
            persisted.pop("memory_settings", None)
        temporary.write_text(json.dumps(persisted, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    @staticmethod
    def _normalize_persisted_profile(persisted: dict[str, Any]) -> None:
        profile = persisted.get("agent_profile")
        if not isinstance(profile, dict) or "source" not in profile:
            return
        if profile.get("source") == "user":
            persisted["agent_profile"] = {"documents": profile.get("documents", {})}
        else:
            persisted.pop("agent_profile", None)

    def _load_agent_profile(self, state: dict[str, Any]) -> AgentProfile:
        value = state.get("agent_profile")
        if value is None:
            return self.packaged_profile
        documents = value.get("documents") if isinstance(value, dict) else None
        if not isinstance(documents, dict):
            raise ValueError("Agent Profile 运行时配置无效")
        return AgentProfileLoader().load(documents)

    @staticmethod
    def _profile_documents(profile: AgentProfile) -> dict[str, str]:
        return {document.name: document.content for document in profile.manifest.documents}

    def profile_view(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "user" if state.get("agent_profile") is not None else "default",
            "version": self.active_profile.manifest.version,
            "documents": self._profile_documents(self.active_profile),
            "default_documents": self._profile_documents(self.packaged_profile),
        }

    def update_agent_profile(self, documents: dict[str, str]) -> dict[str, Any]:
        profile = AgentProfileLoader().load(documents)
        state = self.read_persisted()
        state["agent_profile"] = {"documents": self._profile_documents(profile)}
        self.write_state(state)
        self.active_profile = profile
        return self.profile_view(state)

    def reset_agent_profile(self) -> dict[str, Any]:
        state = self.read_persisted()
        state.pop("agent_profile", None)
        self.write_state(state)
        self.active_profile = self.packaged_profile
        return self.profile_view(state)

    def memory_settings(self) -> dict[str, Any]:
        return {
            "write_enabled": self.settings.agent_memory_write_enabled,
            "recall_enabled": self.settings.agent_memory_cross_session_enabled,
            "retrieval_max_items": self.settings.agent_memory_retrieval_max_items,
            "retrieval_max_tokens": self.settings.agent_memory_retrieval_max_tokens,
            "retrieval_min_confidence": self.settings.agent_memory_retrieval_min_confidence,
            "retrieval_min_score": self.settings.agent_memory_retrieval_min_score,
            "autodream_enabled": self.settings.agent_memory_autodream_enabled,
            "autodream_scan_seconds": self.settings.agent_memory_autodream_scan_seconds,
            "autodream_min_candidates": self.settings.agent_memory_autodream_min_candidates,
        }

    def update_memory_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_memory_settings(value)
        state = self.read_persisted()
        state["memory_settings"] = normalized
        self.write_state(state, persist_memory_settings=True)
        self._apply_memory_settings(normalized)
        return self.memory_settings()

    def _normalize_memory_settings(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("记忆运行设置必须是对象")
        if set(value) != set(self.memory_settings()):
            raise ValueError("记忆运行设置字段不完整")
        boolean_fields = ("write_enabled", "recall_enabled", "autodream_enabled")
        if any(not isinstance(value[field], bool) for field in boolean_fields):
            raise ValueError("记忆运行开关必须是布尔值")
        normalized = {field: value[field] for field in boolean_fields}
        for field, bounds in MEMORY_SETTING_BOUNDS.items():
            normalized[field] = self._bounded_setting(field, value[field], bounds)
        return normalized

    @staticmethod
    def _bounded_setting(field: str, raw: Any, bounds: tuple[float, float]) -> Any:
        integer = field in {
            "retrieval_max_items",
            "retrieval_max_tokens",
            "autodream_scan_seconds",
            "autodream_min_candidates",
        }
        valid_type = type(raw) is int if integer else type(raw) in {int, float}
        if not valid_type:
            raise ValueError(f"记忆运行设置 {field} 必须是{'整数' if integer else '数字'}")
        if not bounds[0] <= raw <= bounds[1]:
            raise ValueError(f"记忆运行设置 {field} 超出允许范围")
        return raw

    def _apply_memory_settings(self, value: dict[str, Any]) -> None:
        mapping = {
            "agent_memory_write_enabled": "write_enabled",
            "agent_memory_cross_session_enabled": "recall_enabled",
            "agent_memory_retrieval_max_items": "retrieval_max_items",
            "agent_memory_retrieval_max_tokens": "retrieval_max_tokens",
            "agent_memory_retrieval_min_confidence": "retrieval_min_confidence",
            "agent_memory_retrieval_min_score": "retrieval_min_score",
            "agent_memory_autodream_enabled": "autodream_enabled",
            "agent_memory_autodream_scan_seconds": "autodream_scan_seconds",
            "agent_memory_autodream_min_candidates": "autodream_min_candidates",
        }
        for setting_name, value_name in mapping.items():
            setattr(self.settings, setting_name, value[value_name])
