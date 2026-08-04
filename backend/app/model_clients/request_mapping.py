from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.agent_profile import ModelOperation
from app.context_compaction.service import CompactionGeneration

ChatJson = Callable[..., Awaitable[dict[str, Any]]]


def active_skill_identities(context: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    for active_skill in context.get("active_skills", []):
        if isinstance(active_skill, str):
            identities.add(active_skill)
        elif isinstance(active_skill, dict):
            identity = active_skill.get("qualified_identity")
            if isinstance(identity, str):
                identities.add(identity)
    return identities


async def generate_context_checkpoint(
    chat_json: ChatJson,
    prompt: str,
    *,
    provider: str,
    model: str,
) -> CompactionGeneration:
    checkpoint = await chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are executing Astra's Provider-neutral checkpoint prompt. "
                    "Return one JSON object and do not emit hidden reasoning."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        operation=ModelOperation.MEMORY,
        usage_operation="context_compaction",
    )
    return CompactionGeneration(output=checkpoint, provider=provider, model=model)
