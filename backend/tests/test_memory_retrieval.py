from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.application.memory.retrieval import (
    MemoryRetrievalBudget,
    MemoryRetrievalCandidate,
    MemoryRetrievalPolicy,
    MemoryRetrievalQuery,
    MemoryScoreWeights,
    estimate_text_tokens,
    evaluate_memory_eligibility,
    lexical_overlap,
    retrieve_memories,
    structural_tags,
    tokenize_text,
)
from app.domain.memory import MemoryNamespace, MemoryNamespaceType

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
SESSION = MemoryNamespace(MemoryNamespaceType.session, "session-a")
OTHER_SESSION = MemoryNamespace(MemoryNamespaceType.session, "session-b")
RUN = MemoryNamespace(MemoryNamespaceType.run, "run-a")


def candidate(
    memory_id: str,
    content: str,
    *,
    namespace: MemoryNamespace = SESSION,
    kind: str = "semantic_fact",
    status: str = "active",
    confidence: float = 0.8,
    importance: float = 0.5,
    utility: float = 0.0,
    observed_at: datetime | None = NOW,
    updated_at: datetime | None = NOW,
    structured_data=None,
    provenance=None,
    accessible_source_count: int = 1,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> MemoryRetrievalCandidate:
    return MemoryRetrievalCandidate(
        id=memory_id,
        namespace_type=namespace.type.value,
        namespace_id=namespace.id,
        kind=kind,
        status=status,
        content=content,
        structured_data=structured_data or {},
        provenance={"run_id": "source-run"} if provenance is None else provenance,
        confidence=confidence,
        importance=importance,
        utility_score=utility,
        observed_at=observed_at,
        updated_at=updated_at,
        accessible_source_count=accessible_source_count,
        valid_from=valid_from,
        valid_to=valid_to,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


def query(
    text: str = "请用中文回答 memory retrieval",
    *,
    namespaces=frozenset({SESSION}),
    **updates,
) -> MemoryRetrievalQuery:
    return MemoryRetrievalQuery(
        text=text,
        namespaces=namespaces,
        as_of=NOW,
        **updates,
    )


def test_tokenization_normalizes_latin_width_case_and_overlapping_cjk():
    tokens = tokenize_text("ＧＰＴ-5 使用中文回答，Memory_RETRIEVAL!")

    assert "gpt" in tokens
    assert "5" in tokens
    assert "memory" in tokens
    assert "retrieval" in tokens
    assert {"中", "文", "中文", "回答"}.issubset(tokens)
    assert tokenize_text("中文") == ("中", "文", "中文")
    assert tokenize_text("MEMORY") == tokenize_text("ｍｅｍｏｒｙ")


def test_lexical_overlap_is_bounded_and_uses_cjk_and_latin_tokens():
    exact = lexical_overlap(tokenize_text("中文 memory"), tokenize_text("中文 memory"))
    partial = lexical_overlap(
        tokenize_text("中文 memory"),
        tokenize_text("请使用中文回答 retrieval"),
    )

    assert exact == pytest.approx(1.0)
    assert 0 < partial < exact
    assert lexical_overlap((), tokenize_text("anything")) == 0


def test_structural_tags_only_extract_allowlisted_exact_fields():
    tags = structural_tags(
        {
            "tags": ["Python", " 数据 "],
            "keywords": "Memory",
            "tool_names": ["catalog_search"],
            "task_signature": "QA-v2",
            "environment_signature": "PROD",
            "secret": "must-not-be-indexed",
        }
    )

    assert tags == frozenset(
        {
            "python",
            "数据",
            "memory",
            "catalog_search",
            "task:qa-v2",
            "environment:prod",
        }
    )


def test_eligibility_filters_namespace_lifecycle_time_kind_and_sources():
    policy = MemoryRetrievalPolicy(
        minimum_confidence=0.7,
        allowed_kinds=frozenset({"semantic_fact"}),
    )
    cases = {
        "wrong-namespace": candidate("wrong-namespace", "memory", namespace=OTHER_SESSION),
        "candidate": candidate("candidate", "memory", status="candidate"),
        "future": candidate("future", "memory", valid_from=NOW + timedelta(seconds=1)),
        "ended": candidate("ended", "memory", valid_to=NOW),
        "expired": candidate("expired", "memory", expires_at=NOW),
        "revoked": candidate("revoked", "memory", revoked_at=NOW),
        "unknown-kind": candidate("unknown-kind", "memory", kind="legacy_magic"),
        "low-confidence": candidate("low-confidence", "memory", confidence=0.6),
        "no-source": candidate(
            "no-source",
            "memory",
            provenance={},
            accessible_source_count=0,
        ),
    }

    decisions = {
        memory_id: evaluate_memory_eligibility(memory, query(), policy)
        for memory_id, memory in cases.items()
    }

    assert "namespace_not_allowed" in decisions["wrong-namespace"].reasons
    assert "lifecycle_ineligible" in decisions["candidate"].reasons
    assert "not_yet_valid" in decisions["future"].reasons
    assert "no_longer_valid" in decisions["ended"].reasons
    assert "expired" in decisions["expired"].reasons
    assert "revoked" in decisions["revoked"].reasons
    assert "unsupported_kind" in decisions["unknown-kind"].reasons
    assert "confidence_below_minimum" in decisions["low-confidence"].reasons
    assert decisions["no-source"].reasons == (
        "missing_provenance",
        "source_inaccessible",
    )


def test_obsolete_kind_is_rejected_in_every_namespace():
    obsolete_run = candidate(
        "obsolete-run",
        "obsolete",
        namespace=RUN,
        kind="source_summary",
    )
    obsolete_session = candidate(
        "obsolete-session",
        "obsolete",
        namespace=SESSION,
        kind="source_summary",
    )
    policy = MemoryRetrievalPolicy()

    run_decision = evaluate_memory_eligibility(
        obsolete_run,
        query(namespaces=frozenset({RUN})),
        policy,
    )
    session_decision = evaluate_memory_eligibility(
        obsolete_session,
        query(),
        policy,
    )

    assert run_decision.reasons == ("unsupported_kind",)
    assert session_decision.reasons == ("unsupported_kind",)


def test_required_tags_are_hard_filters_and_affinity_tags_are_score_components():
    memory = candidate(
        "tagged",
        "unrelated text",
        structured_data={
            "tags": ["python"],
            "task_signature": "qa-v2",
            "environment_signature": "prod",
        },
    )
    matching_query = query(
        text="",
        tags=frozenset({"python"}),
        required_tags=frozenset({"python"}),
        task_signature="qa-v2",
        environment_signature="prod",
    )
    missing_query = query(text="", required_tags=frozenset({"rust"}))

    result = retrieve_memories([memory], matching_query)
    missing = retrieve_memories([memory], missing_query)

    assert result.selected[0].score.tags == 1
    assert missing.selected == ()
    assert missing.excluded[0].reasons == ("required_tags_missing",)


def test_scoring_components_are_bounded_and_utility_can_penalize():
    weights = MemoryScoreWeights(
        lexical=1,
        kind=1,
        tags=1,
        recency=1,
        confidence=1,
        importance=1,
        utility=1,
        semantic=0,
    )
    policy = MemoryRetrievalPolicy(
        utility_bound=2,
        weights=weights,
    )
    matching_query = query(
        kind_affinities={"semantic_fact": 1},
        tags=frozenset({"python"}),
    )
    helpful = candidate(
        "helpful",
        "中文 memory retrieval",
        confidence=2,
        importance=2,
        utility=100,
        structured_data={"tags": ["python"]},
    )
    harmful = candidate(
        "harmful",
        "中文 memory retrieval",
        confidence=2,
        importance=2,
        utility=-100,
        structured_data={"tags": ["python"]},
    )

    result = retrieve_memories([harmful, helpful], matching_query, policy=policy)
    helpful_score, harmful_score = (item.score for item in result.ranked)

    assert helpful_score.utility == 1
    assert harmful_score.utility == -1
    assert helpful_score.total > harmful_score.total
    for value in (
        helpful_score.lexical,
        helpful_score.kind,
        helpful_score.tags,
        helpful_score.recency,
        helpful_score.confidence,
        helpful_score.importance,
        helpful_score.total,
    ):
        assert 0 <= value <= 1


def test_recency_is_identical_for_naive_and_aware_utc_timestamps():
    naive = candidate(
        "naive",
        "memory",
        observed_at=(NOW - timedelta(days=30)).replace(tzinfo=None),
    )
    aware = candidate(
        "aware",
        "memory",
        observed_at=NOW - timedelta(days=30),
    )

    result = retrieve_memories([naive, aware], query("memory"))
    scores = {item.candidate.id: item.score.recency for item in result.ranked}

    assert scores == {"aware": 0.5, "naive": 0.5}


class FixedSemanticScorer:
    def __init__(self):
        self.calls = []

    def score_many(self, recall_query, candidates):
        self.calls.append((recall_query.text, tuple(item.id for item in candidates)))
        return {"semantic": 10, "other": -10, "unknown": 1}


def test_optional_semantic_scorer_is_batched_and_scores_are_clamped():
    scorer = FixedSemanticScorer()
    memories = [
        candidate("semantic", "no lexical match"),
        candidate("other", "no lexical match"),
    ]
    result = retrieve_memories(
        memories,
        query("different"),
        semantic_scorer=scorer,
    )

    assert scorer.calls == [("different", ("other", "semantic"))]
    scores = {item.candidate.id: item.score.semantic for item in result.ranked}
    assert scores == {"semantic": 1, "other": 0}
    assert result.ranked[0].candidate.id == "semantic"


def test_ranking_is_reproducible_with_updated_at_then_id_tie_breaks():
    newer = candidate(
        "z-newer",
        "same",
        updated_at=NOW,
        observed_at=NOW - timedelta(days=1),
    )
    older_b = candidate(
        "b-older",
        "same",
        updated_at=NOW - timedelta(days=1),
        observed_at=NOW - timedelta(days=1),
    )
    older_a = candidate(
        "a-older",
        "same",
        updated_at=NOW - timedelta(days=1),
        observed_at=NOW - timedelta(days=1),
    )

    first = retrieve_memories([older_b, newer, older_a], query("same"))
    second = retrieve_memories([newer, older_a, older_b], query("same"))

    first_ids = [item.candidate.id for item in first.selected]
    second_ids = [item.candidate.id for item in second.selected]
    assert first_ids == second_ids == ["z-newer", "a-older", "b-older"]
    assert [item.score.as_dict() for item in first.selected] == [
        item.score.as_dict() for item in second.selected
    ]


def test_budget_selection_skips_oversized_item_and_keeps_complete_fitting_items():
    oversized = candidate("oversized", "memory " * 20, importance=1)
    fitting = candidate("fitting", "memory", importance=0)
    budget = MemoryRetrievalBudget(
        max_items=1,
        max_characters=len(fitting.content),
        max_tokens=estimate_text_tokens(fitting.content),
        per_item_token_overhead=0,
    )

    result = retrieve_memories(
        [fitting, oversized],
        query("memory"),
        budget=budget,
    )

    assert [item.candidate.id for item in result.selected] == ["fitting"]
    exclusions = {item.memory_id: item.reasons for item in result.excluded}
    assert exclusions["oversized"] == ("character_budget", "token_budget")
    assert result.used_characters == len(fitting.content)
    assert result.used_tokens == estimate_text_tokens(fitting.content)


def test_item_budget_excludes_remaining_ranked_items():
    memories = [candidate("a", "same"), candidate("b", "same")]

    result = retrieve_memories(
        memories,
        query("same"),
        budget=MemoryRetrievalBudget(
            max_items=1,
            max_characters=None,
            max_tokens=None,
        ),
    )

    assert [item.candidate.id for item in result.selected] == ["a"]
    assert result.excluded[-1].memory_id == "b"
    assert result.excluded[-1].reasons == ("item_budget",)


def test_minimum_score_and_duplicate_ids_are_rejected_deterministically():
    low = candidate("low", "unrelated", confidence=0, importance=0, utility=-1)
    result = retrieve_memories(
        [low],
        query("different"),
        policy=MemoryRetrievalPolicy(minimum_score=0.1),
    )

    assert result.selected == ()
    assert result.excluded[0].stage == "score"
    assert result.excluded[0].reasons == ("score_below_minimum",)
    with pytest.raises(ValueError, match="candidate IDs must be unique"):
        retrieve_memories([low, low], query())
