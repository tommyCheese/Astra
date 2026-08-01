from __future__ import annotations

import math
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from app.memory.domain import MemoryNamespace, MemoryStatus, normalize_memory_kind

_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2EFF),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
    (0x20000, 0x2FA1F),
)
_STRUCTURAL_TAG_KEYS = ("tags", "keywords", "tool_names")
_TASK_SIGNATURE_KEY = "task_signature"
_ENVIRONMENT_SIGNATURE_KEY = "environment_signature"
_SCORE_PRECISION = 12


def _string_value(value: str | Enum | None) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, float(value)))


def _rounded(value: float) -> float:
    return round(float(value), _SCORE_PRECISION)


def as_utc(value: datetime) -> datetime:
    """Normalize persisted timestamps; SQLite commonly returns naive UTC values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def tokenize_text(text: str) -> tuple[str, ...]:
    """Tokenize Latin words and overlapping CJK uni/bi-grams deterministically."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    chunks: list[tuple[bool, str]] = []
    buffer: list[str] = []
    buffer_is_cjk: bool | None = None

    def flush() -> None:
        nonlocal buffer, buffer_is_cjk
        if buffer:
            chunks.append((bool(buffer_is_cjk), "".join(buffer)))
        buffer = []
        buffer_is_cjk = None

    for character in normalized:
        character_is_cjk = _is_cjk(character)
        if not character_is_cjk and not character.isalnum():
            flush()
            continue
        if buffer and character_is_cjk != buffer_is_cjk:
            flush()
        buffer.append(character)
        buffer_is_cjk = character_is_cjk
    flush()

    tokens: list[str] = []
    for is_cjk, chunk in chunks:
        if not is_cjk:
            tokens.append(chunk)
            continue
        tokens.extend(chunk)
        tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return tuple(tokens)


def lexical_overlap(query_tokens: Sequence[str], candidate_tokens: Sequence[str]) -> float:
    """Return bounded multiset cosine similarity."""
    if not query_tokens or not candidate_tokens:
        return 0.0
    query_counts = Counter(query_tokens)
    candidate_counts = Counter(candidate_tokens)
    numerator = sum(
        query_count * candidate_counts.get(token, 0) for token, query_count in query_counts.items()
    )
    if not numerator:
        return 0.0
    query_norm = math.sqrt(sum(count * count for count in query_counts.values()))
    candidate_norm = math.sqrt(sum(count * count for count in candidate_counts.values()))
    return _clamp(numerator / (query_norm * candidate_norm), 0.0, 1.0)


def _normalize_tag(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _tag_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        normalized = _normalize_tag(value)
        return (normalized,) if normalized else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized = tuple(_normalize_tag(item) for item in value)
        return tuple(item for item in normalized if item)
    return ()


def structural_tags(structured_data: Mapping[str, Any] | None) -> frozenset[str]:
    """Extract only allowlisted exact-match tags from untrusted structured data."""
    data = structured_data if isinstance(structured_data, Mapping) else {}
    tags = {tag for key in _STRUCTURAL_TAG_KEYS for tag in _tag_values(data.get(key))}
    task_signature = _normalize_tag(data.get(_TASK_SIGNATURE_KEY))
    if task_signature:
        tags.add(f"task:{task_signature}")
    environment_signature = _normalize_tag(data.get(_ENVIRONMENT_SIGNATURE_KEY))
    if environment_signature:
        tags.add(f"environment:{environment_signature}")
    return frozenset(tags)


def _query_tags(query: MemoryRetrievalQuery) -> frozenset[str]:
    tags = {
        normalized
        for tag in (*query.tags, *query.required_tags)
        if (normalized := _normalize_tag(tag))
    }
    task_signature = _normalize_tag(query.task_signature)
    if task_signature:
        tags.add(f"task:{task_signature}")
    environment_signature = _normalize_tag(query.environment_signature)
    if environment_signature:
        tags.add(f"environment:{environment_signature}")
    return frozenset(tags)


def _tag_overlap(query_tags: frozenset[str], candidate_tags: frozenset[str]) -> float:
    if not query_tags:
        return 0.0
    return len(query_tags & candidate_tags) / len(query_tags)


def estimate_text_tokens(text: str) -> int:
    """Conservative tokenizer-independent estimate for complete-item budgeting."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    cjk_characters = sum(_is_cjk(character) for character in normalized)
    other_characters = max(0, len(normalized) - cjk_characters)
    estimated = cjk_characters + math.ceil(other_characters / 4)
    return max(1, estimated) if normalized else 0


@dataclass(frozen=True)
class MemoryRetrievalCandidate:
    id: str
    namespace_type: str
    namespace_id: str
    kind: str
    status: str
    content: str
    structured_data: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    importance: float = 0.5
    utility_score: float = 0.0
    version: int = 1
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    updated_at: datetime | None = None
    accessible_source_count: int = 0


@dataclass(frozen=True)
class MemoryRetrievalQuery:
    text: str
    namespaces: frozenset[MemoryNamespace]
    as_of: datetime
    kind_affinities: Mapping[str, float] = field(default_factory=dict)
    tags: frozenset[str] = field(default_factory=frozenset)
    required_tags: frozenset[str] = field(default_factory=frozenset)
    task_signature: str | None = None
    environment_signature: str | None = None


@dataclass(frozen=True)
class MemoryScoreWeights:
    lexical: float = 0.45
    kind: float = 0.10
    tags: float = 0.10
    recency: float = 0.10
    confidence: float = 0.10
    importance: float = 0.10
    utility: float = 0.05
    semantic: float = 0.20

    def __post_init__(self) -> None:
        values = (
            self.lexical,
            self.kind,
            self.tags,
            self.recency,
            self.confidence,
            self.importance,
            self.utility,
            self.semantic,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Memory score weights must be finite and non-negative")
        if not any(values):
            raise ValueError("At least one Memory score weight must be positive")


@dataclass(frozen=True)
class MemoryRetrievalPolicy:
    minimum_confidence: float = 0.0
    allowed_kinds: frozenset[str] | None = None
    require_provenance: bool = True
    require_accessible_source: bool = True
    recency_half_life_days: float = 30.0
    utility_bound: float = 1.0
    minimum_score: float = 0.0
    weights: MemoryScoreWeights = field(default_factory=MemoryScoreWeights)

    def __post_init__(self) -> None:
        if not math.isfinite(self.minimum_confidence) or not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if not math.isfinite(self.recency_half_life_days) or self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")
        if not math.isfinite(self.utility_bound) or self.utility_bound <= 0:
            raise ValueError("utility_bound must be positive")
        if not math.isfinite(self.minimum_score) or not 0.0 <= self.minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1")


@dataclass(frozen=True)
class MemoryRetrievalBudget:
    max_items: int = 8
    max_characters: int | None = 8_000
    max_tokens: int | None = 2_000
    per_item_token_overhead: int = 8
    per_item_character_overhead: int = 0

    def __post_init__(self) -> None:
        values = (
            self.max_items,
            self.max_characters,
            self.max_tokens,
            self.per_item_token_overhead,
            self.per_item_character_overhead,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("Memory retrieval budgets must be non-negative")


@dataclass(frozen=True)
class MemoryEligibilityDecision:
    memory_id: str
    eligible: bool
    reasons: tuple[str, ...]
    normalized_kind: str | None


@dataclass(frozen=True)
class MemoryScoreComponents:
    lexical: float
    kind: float
    tags: float
    recency: float
    confidence: float
    importance: float
    utility: float
    semantic: float | None
    total: float

    def as_dict(self) -> dict[str, float | None]:
        return {
            "lexical": self.lexical,
            "kind": self.kind,
            "tags": self.tags,
            "recency": self.recency,
            "confidence": self.confidence,
            "importance": self.importance,
            "utility": self.utility,
            "semantic": self.semantic,
            "total": self.total,
        }


@dataclass(frozen=True)
class ScoredMemory:
    candidate: MemoryRetrievalCandidate
    normalized_kind: str | None
    score: MemoryScoreComponents
    character_cost: int
    token_cost: int


@dataclass(frozen=True)
class MemoryRetrievalExclusion:
    memory_id: str
    stage: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MemorySelection:
    selected: tuple[ScoredMemory, ...]
    excluded: tuple[MemoryRetrievalExclusion, ...]
    used_characters: int
    used_tokens: int


@dataclass(frozen=True)
class MemoryRetrievalResult:
    ranked: tuple[ScoredMemory, ...]
    selected: tuple[ScoredMemory, ...]
    excluded: tuple[MemoryRetrievalExclusion, ...]
    used_characters: int
    used_tokens: int


class SemanticScorer(Protocol):
    """Optional batch interface; implementations must return bounded similarity by ID."""

    def score_many(
        self,
        query: MemoryRetrievalQuery,
        candidates: Sequence[MemoryRetrievalCandidate],
    ) -> Mapping[str, float]: ...


def evaluate_memory_eligibility(
    candidate: MemoryRetrievalCandidate,
    query: MemoryRetrievalQuery,
    policy: MemoryRetrievalPolicy,
) -> MemoryEligibilityDecision:
    reasons: list[str] = []
    namespace_pairs = {(namespace.type.value, namespace.id) for namespace in query.namespaces}
    namespace_type = _string_value(candidate.namespace_type).strip()
    namespace_id = _string_value(candidate.namespace_id).strip()
    if (
        not namespace_type
        or not namespace_id
        or (
            namespace_type,
            namespace_id,
        )
        not in namespace_pairs
    ):
        reasons.append("namespace_not_allowed")

    if _string_value(candidate.status) != MemoryStatus.active.value:
        reasons.append("lifecycle_ineligible")

    as_of = as_utc(query.as_of)
    if candidate.valid_from is not None and as_utc(candidate.valid_from) > as_of:
        reasons.append("not_yet_valid")
    if candidate.valid_to is not None and as_utc(candidate.valid_to) <= as_of:
        reasons.append("no_longer_valid")
    if candidate.expires_at is not None and as_utc(candidate.expires_at) <= as_of:
        reasons.append("expired")
    if candidate.revoked_at is not None and as_utc(candidate.revoked_at) <= as_of:
        reasons.append("revoked")

    raw_kind = _string_value(candidate.kind)
    normalized_kind = normalize_memory_kind(raw_kind)
    if normalized_kind is None:
        reasons.append("unsupported_kind")

    normalized_allowed_kinds = (
        {
            kind.value
            for raw_allowed_kind in policy.allowed_kinds
            if (kind := normalize_memory_kind(raw_allowed_kind)) is not None
        }
        if policy.allowed_kinds is not None
        else None
    )
    if normalized_allowed_kinds is not None and (
        normalized_kind is None or normalized_kind.value not in normalized_allowed_kinds
    ):
        reasons.append("kind_not_allowed")

    confidence = _clamp(candidate.confidence, 0.0, 1.0)
    if confidence < policy.minimum_confidence:
        reasons.append("confidence_below_minimum")
    if not str(candidate.content or "").strip():
        reasons.append("empty_content")

    has_provenance = bool(candidate.provenance) or candidate.accessible_source_count > 0
    if policy.require_provenance and not has_provenance:
        reasons.append("missing_provenance")
    if policy.require_accessible_source and candidate.accessible_source_count <= 0:
        reasons.append("source_inaccessible")

    candidate_tags = structural_tags(candidate.structured_data)
    required_tags = {
        normalized for tag in query.required_tags if (normalized := _normalize_tag(tag))
    }
    if required_tags and not required_tags.issubset(candidate_tags):
        reasons.append("required_tags_missing")

    return MemoryEligibilityDecision(
        memory_id=candidate.id,
        eligible=not reasons,
        reasons=tuple(reasons),
        normalized_kind=normalized_kind.value if normalized_kind is not None else None,
    )


def _normalized_kind_affinities(query: MemoryRetrievalQuery) -> dict[str, float]:
    affinities: dict[str, float] = {}
    for raw_kind, affinity in query.kind_affinities.items():
        kind = normalize_memory_kind(raw_kind)
        if kind is not None:
            affinities[kind.value] = _clamp(affinity, 0.0, 1.0)
    return affinities


def _recency_score(
    candidate: MemoryRetrievalCandidate,
    *,
    as_of: datetime,
    half_life_days: float,
) -> float:
    timestamp = candidate.observed_at or candidate.valid_from or candidate.updated_at
    if timestamp is None:
        return 0.0
    age_seconds = max(0.0, (as_utc(as_of) - as_utc(timestamp)).total_seconds())
    age_days = age_seconds / 86_400
    return 0.5 ** (age_days / half_life_days)


def score_memory_candidate(
    candidate: MemoryRetrievalCandidate,
    query: MemoryRetrievalQuery,
    policy: MemoryRetrievalPolicy,
    *,
    normalized_kind: str | None = None,
    semantic_score: float | None = None,
) -> ScoredMemory:
    query_tokens = tokenize_text(query.text)
    candidate_tokens = tokenize_text(candidate.content)
    lexical = lexical_overlap(query_tokens, candidate_tokens)

    kind_affinities = _normalized_kind_affinities(query)
    kind = kind_affinities.get(normalized_kind or "", 0.0)
    query_tags = _query_tags(query)
    tags = _tag_overlap(query_tags, structural_tags(candidate.structured_data))
    recency = _recency_score(
        candidate,
        as_of=query.as_of,
        half_life_days=policy.recency_half_life_days,
    )
    confidence = _clamp(candidate.confidence, 0.0, 1.0)
    importance = _clamp(candidate.importance, 0.0, 1.0)
    utility = (
        _clamp(
            candidate.utility_score,
            -policy.utility_bound,
            policy.utility_bound,
        )
        / policy.utility_bound
    )
    semantic = _clamp(semantic_score, 0.0, 1.0) if semantic_score is not None else None

    weights = policy.weights
    weighted_signals: list[tuple[float, float]] = [
        (recency, weights.recency),
        (confidence, weights.confidence),
        (importance, weights.importance),
        (utility, weights.utility),
    ]
    if query_tokens:
        weighted_signals.append((lexical, weights.lexical))
    if kind_affinities:
        weighted_signals.append((kind, weights.kind))
    if query_tags:
        weighted_signals.append((tags, weights.tags))
    if semantic is not None:
        weighted_signals.append((semantic, weights.semantic))
    denominator = sum(weight for _, weight in weighted_signals)
    total = (
        _clamp(
            sum(signal * weight for signal, weight in weighted_signals) / denominator,
            0.0,
            1.0,
        )
        if denominator
        else 0.0
    )
    character_cost = len(candidate.content)
    token_cost = estimate_text_tokens(candidate.content)
    return ScoredMemory(
        candidate=candidate,
        normalized_kind=normalized_kind,
        score=MemoryScoreComponents(
            lexical=_rounded(lexical),
            kind=_rounded(kind),
            tags=_rounded(tags),
            recency=_rounded(recency),
            confidence=_rounded(confidence),
            importance=_rounded(importance),
            utility=_rounded(utility),
            semantic=_rounded(semantic) if semantic is not None else None,
            total=_rounded(total),
        ),
        character_cost=character_cost,
        token_cost=token_cost,
    )


def rank_scored_memories(memories: Sequence[ScoredMemory]) -> tuple[ScoredMemory, ...]:
    """Rank independently of database/input ordering with explicit stable tie breaks."""
    ranked = sorted(memories, key=lambda item: item.candidate.id)
    ranked.sort(
        key=lambda item: as_utc(
            item.candidate.updated_at
            or item.candidate.observed_at
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    ranked.sort(key=lambda item: item.score.total, reverse=True)
    return tuple(ranked)


def select_scored_memories(
    memories: Sequence[ScoredMemory],
    budget: MemoryRetrievalBudget,
) -> MemorySelection:
    selected: list[ScoredMemory] = []
    excluded: list[MemoryRetrievalExclusion] = []
    used_characters = 0
    used_tokens = 0
    for memory in rank_scored_memories(memories):
        character_cost = memory.character_cost + budget.per_item_character_overhead
        token_cost = memory.token_cost + budget.per_item_token_overhead
        reasons: list[str] = []
        if len(selected) >= budget.max_items:
            reasons.append("item_budget")
        if (
            budget.max_characters is not None
            and used_characters + character_cost > budget.max_characters
        ):
            reasons.append("character_budget")
        if budget.max_tokens is not None and used_tokens + token_cost > budget.max_tokens:
            reasons.append("token_budget")
        if reasons:
            excluded.append(
                MemoryRetrievalExclusion(
                    memory_id=memory.candidate.id,
                    stage="budget",
                    reasons=tuple(reasons),
                )
            )
            continue
        selected.append(memory)
        used_characters += character_cost
        used_tokens += token_cost
    return MemorySelection(
        selected=tuple(selected),
        excluded=tuple(excluded),
        used_characters=used_characters,
        used_tokens=used_tokens,
    )


def retrieve_memories(
    candidates: Sequence[MemoryRetrievalCandidate],
    query: MemoryRetrievalQuery,
    *,
    policy: MemoryRetrievalPolicy | None = None,
    budget: MemoryRetrievalBudget | None = None,
    semantic_scorer: SemanticScorer | None = None,
) -> MemoryRetrievalResult:
    effective_policy = policy or MemoryRetrievalPolicy()
    effective_budget = budget or MemoryRetrievalBudget()
    ordered_candidates = sorted(candidates, key=lambda candidate: candidate.id)
    if len({candidate.id for candidate in ordered_candidates}) != len(ordered_candidates):
        raise ValueError("Memory retrieval candidate IDs must be unique")

    eligibility_exclusions: list[MemoryRetrievalExclusion] = []
    eligible: list[tuple[MemoryRetrievalCandidate, MemoryEligibilityDecision]] = []
    for candidate in ordered_candidates:
        decision = evaluate_memory_eligibility(candidate, query, effective_policy)
        if decision.eligible:
            eligible.append((candidate, decision))
        else:
            eligibility_exclusions.append(
                MemoryRetrievalExclusion(
                    memory_id=candidate.id,
                    stage="eligibility",
                    reasons=decision.reasons,
                )
            )

    eligible_candidates = [candidate for candidate, _ in eligible]
    semantic_scores = (
        semantic_scorer.score_many(query, eligible_candidates)
        if semantic_scorer is not None and eligible_candidates
        else {}
    )
    scored = [
        score_memory_candidate(
            candidate,
            query,
            effective_policy,
            normalized_kind=decision.normalized_kind,
            semantic_score=semantic_scores.get(candidate.id),
        )
        for candidate, decision in eligible
    ]
    score_exclusions = [
        MemoryRetrievalExclusion(
            memory_id=memory.candidate.id,
            stage="score",
            reasons=("score_below_minimum",),
        )
        for memory in scored
        if memory.score.total < effective_policy.minimum_score
    ]
    ranked = rank_scored_memories(
        [memory for memory in scored if memory.score.total >= effective_policy.minimum_score]
    )
    selection = select_scored_memories(ranked, effective_budget)
    return MemoryRetrievalResult(
        ranked=ranked,
        selected=selection.selected,
        excluded=(
            *eligibility_exclusions,
            *score_exclusions,
            *selection.excluded,
        ),
        used_characters=selection.used_characters,
        used_tokens=selection.used_tokens,
    )
