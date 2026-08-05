from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelContextWindow:
    provider: str
    model: str
    tokens: int
    max_output_tokens: int | None
    source: str
    verified: bool
    documentation_url: str | None


@dataclass(frozen=True)
class ModelContextCatalogEntry:
    providers: tuple[str, ...]
    model_prefixes: tuple[str, ...]
    window_tokens: int
    max_output_tokens: int | None
    documentation_url: str


_CONTEXT_WINDOW_CATALOG: tuple[ModelContextCatalogEntry, ...] = (
    ModelContextCatalogEntry(
        ("openai",),
        ("gpt-5.6",),
        1_050_000,
        128_000,
        "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
    ),
    ModelContextCatalogEntry(
        ("openai",),
        ("gpt-4.1",),
        1_047_576,
        32_768,
        "https://developers.openai.com/api/docs/models/gpt-4.1",
    ),
    ModelContextCatalogEntry(
        ("openai",),
        ("gpt-5",),
        400_000,
        128_000,
        "https://developers.openai.com/api/docs/models/gpt-5",
    ),
    ModelContextCatalogEntry(
        ("openai",),
        ("o1", "o3", "o4"),
        200_000,
        100_000,
        "https://developers.openai.com/api/docs/models",
    ),
    ModelContextCatalogEntry(
        ("openai",),
        ("gpt-4o", "gpt-4-turbo"),
        128_000,
        16_384,
        "https://developers.openai.com/api/docs/models",
    ),
    ModelContextCatalogEntry(
        ("anthropic",),
        ("claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5"),
        1_000_000,
        128_000,
        "https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    ModelContextCatalogEntry(
        ("anthropic",),
        ("claude-opus-4-6", "claude-sonnet-4-6"),
        1_000_000,
        128_000,
        "https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    ModelContextCatalogEntry(
        ("anthropic",),
        ("claude",),
        200_000,
        64_000,
        "https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    ModelContextCatalogEntry(
        ("google", "gemini"),
        ("gemini",),
        1_048_576,
        65_536,
        "https://ai.google.dev/gemini-api/docs/models",
    ),
    ModelContextCatalogEntry(
        ("deepseek",),
        ("deepseek-v4", "deepseek-chat", "deepseek-reasoner"),
        1_000_000,
        384_000,
        "https://api-docs.deepseek.com/quick_start/pricing/",
    ),
    ModelContextCatalogEntry(
        ("xai",),
        ("grok-4.5",),
        500_000,
        None,
        "https://docs.x.ai/developers/pricing",
    ),
    ModelContextCatalogEntry(
        ("xai",),
        ("grok-4.3", "grok-4.20"),
        1_000_000,
        None,
        "https://docs.x.ai/developers/pricing",
    ),
)


def resolve_context_window(
    provider: str,
    model: str,
    *,
    fallback_tokens: int = 131_072,
) -> ModelContextWindow:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()
    catalog_provider = normalized_provider
    catalog_model = normalized_model
    if normalized_provider == "openrouter" and "/" in normalized_model:
        catalog_provider, catalog_model = normalized_model.split("/", 1)

    catalog_tokens: int | None = None
    catalog_max_output: int | None = None
    documentation_url: str | None = None
    for entry in _CONTEXT_WINDOW_CATALOG:
        if any(item in catalog_provider for item in entry.providers) and any(
            catalog_model.startswith(item) for item in entry.model_prefixes
        ):
            catalog_tokens = entry.window_tokens
            catalog_max_output = entry.max_output_tokens
            documentation_url = entry.documentation_url
            break

    if catalog_tokens is not None:
        tokens = catalog_tokens
        source = "catalog"
        verified = True
    else:
        tokens = fallback_tokens
        source = "fallback"
        verified = False

    return ModelContextWindow(
        provider=provider,
        model=model,
        tokens=tokens,
        max_output_tokens=catalog_max_output,
        source=source,
        verified=verified,
        documentation_url=documentation_url,
    )
