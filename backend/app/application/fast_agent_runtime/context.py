from __future__ import annotations

from typing import Any

from app.common.schemas.agent.run_policy import FastRuntimeSnapshot
from app.infrastructure.tools.router import ToolRouter

FAST_FORBIDDEN_SKILL_CAPABILITIES = frozenset(
    {"planning", "verification", "reflection", "subagent", "delegation_create", "memory_write"}
)


def fast_compatible_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compatible = []
    for skill in skills:
        metadata = skill.get("metadata") if isinstance(skill.get("metadata"), dict) else {}
        capabilities = set(metadata.get("required_capabilities") or [])
        if metadata.get("recommended_answer_mode") == "trusted":
            continue
        if metadata.get("runtime") in {"trusted", "trusted-v1"} or metadata.get("trusted_only") is True:
            continue
        if capabilities & FAST_FORBIDDEN_SKILL_CAPABILITIES:
            continue
        compatible.append(skill)
    return compatible


class FastContextBuilder:
    def __init__(self, router: ToolRouter) -> None:
        self._router = router

    def build(
        self,
        *,
        snapshot: FastRuntimeSnapshot,
        active_skills: list[dict[str, Any]],
    ) -> dict[str, Any]:
        specs, unavailable = self._router.eligible_specs()
        manifests = {
            spec.name: {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "permission": spec.permission,
                "side_effect_level": spec.side_effect_level,
                "task_capabilities": spec.task_capabilities,
            }
            for _, spec in sorted(specs.items())
            if "delegation_create" not in spec.permissions
            and "memory_write" not in spec.permissions
            and "memory_delete" not in spec.permissions
        }
        return {
            "runtime": "fast-v1",
            "answer_mode": "standard",
            "messages": snapshot.messages,
            "recent_observations": snapshot.recent_observations,
            "observations": snapshot.recent_observations,
            "tool_manifests": manifests,
            "unavailable_tools": unavailable,
            "active_skills": fast_compatible_skills(active_skills),
            "allowed_actions": ["answer", "call_tool", "ask_user", "stop"],
        }
