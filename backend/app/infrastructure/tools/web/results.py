"""Validate logical searches and normalize evidence candidates."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.domain.grounding.identity import candidate_id, canonical_url, search_trace_id
from app.infrastructure.tools.base import ToolExecutionError
from app.infrastructure.tools.web.output import iso_now

SearchRequest = dict[str, str | None]
SearchOutput = dict[str, Any]


class WebSearchResultNormalizer:
    """Validate search requests and project provider output into Astra evidence candidates."""

    def __init__(self, search_parameters: Callable[[dict[str, Any]], tuple[int, str, str]]):
        self._search_parameters = search_parameters

    def logical_queries(self, tool_input: dict[str, Any]) -> list[SearchRequest]:
        raw_queries = tool_input.get("queries")
        if raw_queries is None:
            raw_queries = [tool_input.get("query")]
        if not isinstance(raw_queries, list) or not 1 <= len(raw_queries) <= 4:
            raise ToolExecutionError(
                "invalid_input", "web_search requires between one and four queries"
            )
        return [self._logical_query(raw_query) for raw_query in raw_queries]

    @staticmethod
    def _logical_query(raw_query: Any) -> SearchRequest:
        if isinstance(raw_query, str):
            query, purpose = raw_query.strip(), None
        elif isinstance(raw_query, dict):
            query = str(raw_query.get("query") or "").strip()
            purpose = str(raw_query.get("purpose") or "").strip() or None
        else:
            query, purpose = "", None
        if not query:
            raise ToolExecutionError(
                "invalid_input", "web_search requires non-empty logical queries"
            )
        return {"query": query[:1000], "purpose": purpose}

    def constraints(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        raw_filters = tool_input.get("filters")
        filters = raw_filters if isinstance(raw_filters, dict) else {}
        max_results, language, region = self._search_parameters(tool_input)
        return {
            "language": language or None,
            "region": region or None,
            "after": self._optional_text(filters.get("after")),
            "before": self._optional_text(filters.get("before")),
            "include_domains": self._domains(filters, "include_domains"),
            "exclude_domains": self._domains(filters, "exclude_domains"),
            "content_types": self._content_types(filters),
            "max_results": max_results,
        }

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _domains(filters: dict[str, Any], name: str) -> list[str]:
        values = filters.get(name) or []
        if not isinstance(values, list):
            raise ToolExecutionError("invalid_input", f"{name} must be an array")
        normalized = [
            str(value).strip().lower().lstrip(".")
            for value in values[:16]
            if re.fullmatch(r"[a-z0-9.-]+", str(value).strip().lower().lstrip("."))
        ]
        return list(dict.fromkeys(normalized))

    @staticmethod
    def _content_types(filters: dict[str, Any]) -> list[str]:
        values = filters.get("content_types") or []
        if not isinstance(values, list):
            raise ToolExecutionError("invalid_input", "content_types must be an array")
        return [str(value).strip().lower() for value in values[:8] if str(value).strip()]

    def decorate(
        self,
        output: SearchOutput,
        *,
        request: SearchRequest,
        ordinal: int,
        constraints: dict[str, Any],
        invocation_scope: str | None,
    ) -> SearchOutput:
        query = str(request["query"])
        trace_id = search_trace_id(query, ordinal, invocation_scope)
        audit = self._constraint_audit(str(output.get("provider") or "unknown"), constraints)
        candidates = self._filtered_candidates(output, constraints, audit)
        normalized = self._normalize_candidates(candidates, trace_id)
        retrieved_at = iso_now()
        trace = {
            "id": trace_id,
            "query": query,
            "purpose": request.get("purpose"),
            "provider": output.get("provider"),
            "constraints": constraints,
            "constraint_audit": audit,
            "retrieved_at": retrieved_at,
        }
        return output | {
            "query": query,
            "query_count": 1,
            "constraints": constraints,
            "constraint_audit": audit,
            "search_traces": [trace],
            "candidate_count": len(normalized),
            "candidates": normalized,
            "retrieved_at": retrieved_at,
        }

    @staticmethod
    def _constraint_audit(provider: str, constraints: dict[str, Any]) -> dict[str, list[str]]:
        applied = ["max_results"]
        unsupported = [name for name in ("after", "before") if constraints.get(name)]
        if constraints.get("language"):
            applied.append("language")
        if constraints.get("region"):
            target = applied if provider in {"google", "brave", "duckduckgo"} else unsupported
            target.append("region")
        return {"applied": applied, "emulated": [], "post_filtered": [], "unsupported": unsupported}

    def _filtered_candidates(
        self,
        output: SearchOutput,
        constraints: dict[str, Any],
        audit: dict[str, list[str]],
    ) -> list[SearchOutput]:
        candidates = list(output.get("candidates") or [])
        for constraint_name in ("include_domains", "exclude_domains", "content_types"):
            values = constraints.get(constraint_name)
            if values:
                candidates = self._post_filter_candidates(candidates, constraint_name, values)
                audit["post_filtered"].append(constraint_name)
        return candidates

    @staticmethod
    def _normalize_candidates(candidates: list[SearchOutput], trace_id: str) -> list[SearchOutput]:
        normalized: list[SearchOutput] = []
        for rank, candidate in enumerate(candidates, start=1):
            url = str(candidate.get("url") or "")
            if not url:
                continue
            normalized_url = canonical_url(url)
            normalized.append(
                candidate
                | {
                    "candidate_id": candidate_id(trace_id, normalized_url),
                    "search_trace_id": trace_id,
                    "canonical_url": normalized_url,
                    "provider_rank": int(candidate.get("rank") or rank),
                    "evidence_strength": "candidate_only",
                }
            )
        return normalized

    @staticmethod
    def _post_filter_candidates(
        candidates: list[SearchOutput], name: str, values: list[str]
    ) -> list[SearchOutput]:
        filter_candidates = {
            "include_domains": _include_domains,
            "exclude_domains": _exclude_domains,
            "content_types": _include_content_types,
        }[name]
        return filter_candidates(candidates, values)


def _include_domains(candidates: list[SearchOutput], domains: list[str]) -> list[SearchOutput]:
    return [candidate for candidate in candidates if _matches_domain(candidate, domains)]


def _exclude_domains(candidates: list[SearchOutput], domains: list[str]) -> list[SearchOutput]:
    return [candidate for candidate in candidates if not _matches_domain(candidate, domains)]


def _include_content_types(
    candidates: list[SearchOutput], content_types: list[str]
) -> list[SearchOutput]:
    extensions = {"pdf": (".pdf",), "web": ("", ".html", ".htm", "/")}
    allowed_extensions = tuple(
        extension
        for content_type in content_types
        for extension in extensions.get(content_type, ())
    )
    if not allowed_extensions:
        return candidates
    return [
        candidate
        for candidate in candidates
        if urlparse(str(candidate.get("url") or "")).path.lower().endswith(allowed_extensions)
    ]


def combine_outputs(outputs: list[SearchOutput]) -> SearchOutput:
    candidates = _flatten(outputs, "candidates")
    providers = list(dict.fromkeys(str(output.get("provider")) for output in outputs))
    modes = {output.get("provider_mode") for output in outputs}
    return {
        "query": str(outputs[0]["query"]),
        "queries": [_query_projection(output) for output in outputs],
        "query_count": len(outputs),
        "provider": providers[0] if len(providers) == 1 else "mixed",
        "provider_mode": outputs[0].get("provider_mode") if len(modes) == 1 else "mixed",
        "provider_attempts": _provider_attempts(outputs),
        "degraded": any(bool(output.get("degraded")) for output in outputs),
        "parameters": {"query_count": len(outputs)},
        "constraints": outputs[0].get("constraints", {}),
        "constraint_audit": _combined_constraint_audit(outputs),
        "search_traces": _flatten(outputs, "search_traces"),
        "candidate_count": len(candidates),
        "warnings": list(dict.fromkeys(_flatten(outputs, "warnings"))),
        "candidates": candidates,
        "retrieved_at": iso_now(),
    }


def _matches_domain(candidate: SearchOutput, domains: list[str]) -> bool:
    hostname = (urlparse(str(candidate.get("url") or "")).hostname or "").lower()
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _flatten(outputs: list[SearchOutput], field: str) -> list[Any]:
    return [item for output in outputs for item in output.get(field, [])]


def _query_projection(output: SearchOutput) -> SearchOutput:
    return {
        "query": output["query"],
        "purpose": output["search_traces"][0].get("purpose"),
    }


def _provider_attempts(outputs: list[SearchOutput]) -> list[SearchOutput]:
    return [
        attempt | {"search_trace_id": output["search_traces"][0]["id"]}
        for output in outputs
        for attempt in output.get("provider_attempts", [])
    ]


def _combined_constraint_audit(outputs: list[SearchOutput]) -> dict[str, list[str]]:
    return {
        field: list(
            dict.fromkeys(item for output in outputs for item in output["constraint_audit"][field])
        )
        for field in ("applied", "emulated", "post_filtered", "unsupported")
    }
