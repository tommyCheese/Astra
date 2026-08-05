from app.application.context_compaction import (
    TokenAccountingService,
    build_compaction_policy,
    evaluate_compaction_trigger,
    project_shadow_compaction,
    select_recent_tail,
)
from app.common.core.config import Settings
from app.common.schemas.context_compaction import ContextItem, ContextOwnerRole


def item(item_id: str, tokens: int) -> ContextItem:
    return ContextItem(id=item_id, kind="observation", content=item_id, token_count=tokens)


def test_accounting_prefers_reported_usage_and_reserves_compaction_output():
    service = TokenAccountingService()
    accounting = service.account(
        context_window=1_000,
        output_reserve=100,
        compaction_output_reserve=50,
        protected_prefix=(item("prefix", 100),),
        body=(item("body", 300),),
        reported_usage={"prompt_tokens": 475},
    )
    assert accounting.usable_input == 850
    assert accounting.total_tokens == 475
    assert accounting.source == "provider_reported_usage"
    assert accounting.estimated is False


def test_recent_tail_selects_newest_that_fit_then_restores_chronology():
    selected = select_recent_tail(
        (item("one", 4), item("two", 6), item("three", 5), item("four", 4)),
        10,
    )
    assert [entry.id for entry in selected.items] == ["three", "four"]
    assert selected.token_count == 9


def test_body_after_prefix_trigger_still_enforces_full_hard_cap():
    settings = Settings(
        context_compaction_v2_enabled=True,
        context_compaction_root_enabled=True,
        context_auto_compact_ratio=0.8,
        context_compaction_recovery_ratio=0.55,
    )
    policy = build_compaction_policy(settings, ContextOwnerRole.root_execution)
    accounting = TokenAccountingService().account(
        context_window=1_000,
        output_reserve=100,
        compaction_output_reserve=50,
        protected_prefix=(item("prefix", 700),),
        body=(item("body", 201),),
    )
    decision = evaluate_compaction_trigger(accounting, policy)
    assert decision.hard_cap_exceeded is True
    assert decision.should_compact is True


def test_model_downshift_and_shadow_projection_do_not_install():
    settings = Settings(
        context_compaction_v2_enabled=True,
        context_compaction_child_enabled=True,
    )
    policy = build_compaction_policy(settings, ContextOwnerRole.child_execution)
    accounting = TokenAccountingService().account(
        context_window=16_384,
        output_reserve=2_000,
        compaction_output_reserve=1_000,
        protected_prefix=(item("prefix", 2_000),),
        body=(item("body", 11_000),),
    )
    decision = evaluate_compaction_trigger(
        accounting,
        policy,
        model_changed=True,
        previous_context_window=32_768,
    )
    projection = project_shadow_compaction(accounting, policy, expected_checkpoint_tokens=800)
    assert decision.should_compact is True
    assert "model_downshift" in decision.reasons
    assert projection.would_install is False
    assert projection.projected_tokens_after < accounting.total_tokens
