from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from typing import Any

from app.common.schemas.context_compaction import CompactionContextItem, ContextTokenAccounting

Tokenizer = Callable[[str], int]


class TokenAccountingService:
    """Provider-neutral token accounting with explicit provenance.

    A configured tokenizer wins for projections. Provider-reported prompt/input
    usage wins when it describes the active request. The local fallback applies a
    safety margin and is always labelled as estimated.
    """

    def __init__(self, tokenizer: Tokenizer | None = None, *, estimate_margin: float = 1.12):
        self.tokenizer = tokenizer
        self.estimate_margin = estimate_margin

    def count_text(self, text: str) -> tuple[int, str, bool]:
        if self.tokenizer is not None:
            return max(0, int(self.tokenizer(text))), "configured_tokenizer", False
        cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
        other = max(0, len(text) - cjk)
        estimated = cjk + math.ceil(other / 3.2)
        return math.ceil(estimated * self.estimate_margin), "astra_conservative_estimate", True

    def count_value(self, value: Any) -> tuple[int, str, bool]:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        )
        return self.count_text(text)

    def count_items(self, items: Iterable[CompactionContextItem]) -> tuple[int, str, bool]:
        total = 0
        sources: set[str] = set()
        estimated = False
        for item in items:
            if item.token_count:
                total += item.token_count
                sources.add("item_token_count")
                continue
            count, source, item_estimated = self.count_value(item.content or item.summary or "")
            total += count
            sources.add(source)
            estimated = estimated or item_estimated
        return total, "+".join(sorted(sources)) or "empty", estimated

    @staticmethod
    def reported_input_tokens(usage: dict[str, Any] | None) -> int | None:
        if not isinstance(usage, dict):
            return None
        for key in ("input_tokens", "prompt_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                return value
        return None

    def account(
        self,
        *,
        context_window: int,
        output_reserve: int,
        compaction_output_reserve: int,
        protected_prefix: Iterable[CompactionContextItem] = (),
        checkpoint: Iterable[CompactionContextItem] = (),
        body: Iterable[CompactionContextItem] = (),
        recent_tail: Iterable[CompactionContextItem] = (),
        prefill_tokens: int = 0,
        reported_usage: dict[str, Any] | None = None,
    ) -> ContextTokenAccounting:
        prefix_tokens, prefix_source, prefix_estimated = self.count_items(protected_prefix)
        checkpoint_tokens, checkpoint_source, checkpoint_estimated = self.count_items(checkpoint)
        body_tokens, body_source, body_estimated = self.count_items(body)
        tail_tokens, tail_source, tail_estimated = self.count_items(recent_tail)
        projected_total = prefix_tokens + checkpoint_tokens + body_tokens + tail_tokens
        reported = self.reported_input_tokens(reported_usage)
        total = reported if reported is not None else projected_total
        usable_input = max(0, context_window - output_reserve - compaction_output_reserve)
        source = (
            "provider_reported_usage"
            if reported is not None
            else "+".join(sorted({prefix_source, checkpoint_source, body_source, tail_source} - {"empty"})) or "empty"
        )
        return ContextTokenAccounting(
            context_window=context_window,
            output_reserve=output_reserve,
            compaction_output_reserve=compaction_output_reserve,
            usable_input=usable_input,
            protected_prefix_tokens=prefix_tokens,
            checkpoint_tokens=checkpoint_tokens,
            body_tokens=body_tokens,
            recent_tail_tokens=tail_tokens,
            total_tokens=total,
            prefill_tokens=max(0, prefill_tokens),
            source=source,
            estimated=(
                False if reported is not None else prefix_estimated or checkpoint_estimated or body_estimated or tail_estimated
            ),
        )
