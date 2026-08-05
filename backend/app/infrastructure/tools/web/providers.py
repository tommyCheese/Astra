"""Provider clients and provider-specific search response parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from app.infrastructure.tools.base import ToolExecutionError
from app.infrastructure.tools.web.output import normalize_space


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_link(url: str) -> str:
    return urlparse(url).netloc


def normalize_google_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    retrieved_at = iso_now()
    for rank, item in enumerate(payload.get("items", []), start=1):
        url = item.get("link", "")
        pagemap = item.get("pagemap") or {}
        candidates.append(
            {
                "url": url,
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "rank": rank,
                "display_link": item.get("displayLink") or display_link(url),
                "provider": "google",
                "metadata": {
                    "cache_id": item.get("cacheId"),
                    "mime": item.get("mime"),
                    "formatted_url": item.get("formattedUrl"),
                    "metatags": pagemap.get("metatags", [])[:1],
                },
                "retrieved_at": retrieved_at,
            }
        )
    return candidates


def normalize_bing_rss(payload: str) -> list[dict[str, Any]]:
    """Normalize Bing's public RSS search result format."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ToolExecutionError("search_failed", "Bing returned invalid RSS") from exc
    retrieved_at = iso_now()
    candidates = []
    for rank, item in enumerate(root.findall("./channel/item"), start=1):
        url = normalize_space(item.findtext("link") or "")
        if not url:
            continue
        candidates.append(
            {
                "url": url,
                "title": normalize_space(item.findtext("title") or ""),
                "snippet": normalize_space(item.findtext("description") or ""),
                "rank": rank,
                "display_link": display_link(url),
                "provider": "bing",
                "metadata": {},
                "retrieved_at": retrieved_at,
            }
        )
    return candidates


def normalize_search_result_url(url: str) -> str:
    """Resolve provider redirect links to the public result URL."""
    absolute = urljoin("https://html.duckduckgo.com", url)
    parsed = urlparse(absolute)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return absolute


class DuckDuckGoHTMLParser(HTMLParser):
    """Extract result links and snippets from DuckDuckGo's server-rendered HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._active: str | None = None
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._current = {
                "url": normalize_search_result_url(attributes.get("href", "")),
                "title": "",
                "snippet": "",
            }
            self.results.append(self._current)
            self._active = "title"
        elif self._current is not None and (
            "result__snippet" in classes or "result-snippet" in classes
        ):
            self._active = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "div"}:
            self._active = None

    def handle_data(self, data: str) -> None:
        if self._current is None or self._active is None:
            return
        text = normalize_space(data)
        if text:
            self._current[self._active] = normalize_space(f"{self._current[self._active]} {text}")


class SearchProviderClient:
    timeout_seconds = 20

    def __init__(self, settings):
        self.settings = settings

    def search_parameters(self, tool_input: dict[str, Any]) -> tuple[int, str, str]:
        return self._search_parameters(tool_input)

    async def search_one(self, query: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        provider = self.settings.web_search_provider
        if provider == "auto":
            return await self._auto_search(query, tool_input)
        output = await self._run_provider(provider, query, tool_input)
        return self._with_audit(
            output,
            provider_mode="explicit",
            provider_attempts=[self._successful_attempt(output)],
            degraded=provider in {"bing", "duckduckgo"},
        )

    async def _run_provider(
        self, provider: str, query: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        if provider == "bing":
            return await self._bing_search(query, tool_input)
        if provider == "duckduckgo":
            return await self._duckduckgo_search(query, tool_input)
        if provider == "google":
            return await self._google_search(query, tool_input)
        if provider == "brave":
            return await self._brave_search(query, tool_input)
        raise ToolExecutionError(
            "provider_not_configured",
            f"Unsupported web search provider: {provider}",
        )

    async def _auto_search(self, query: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        if self.settings.google_search_api_key and self.settings.google_search_engine_id:
            output = await self._google_search(query, tool_input)
            return self._with_audit(
                output,
                provider_mode="auto",
                provider_attempts=[self._successful_attempt(output)],
                degraded=False,
            )
        if self.settings.web_search_api_key:
            output = await self._brave_search(query, tool_input)
            return self._with_audit(
                output,
                provider_mode="auto",
                provider_attempts=[self._successful_attempt(output)],
                degraded=False,
            )
        return await self._keyless_search(query, tool_input)

    async def _keyless_search(self, query: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        fallback_warnings: list[str] = []
        try:
            bing_output = await self._bing_search(query, tool_input)
        except ToolExecutionError as exc:
            if exc.category != "search_failed":
                raise
            attempts.append(
                {"provider": "bing", "status": "failed", "error_category": exc.category}
            )
            fallback_warnings.append("Bing 搜索失败，已回退到 DuckDuckGo。")
        else:
            attempts.append(self._successful_attempt(bing_output))
            if bing_output.get("candidate_count", 0) > 0:
                return self._with_audit(
                    bing_output,
                    provider_mode="auto",
                    provider_attempts=attempts,
                    degraded=True,
                    extra_warnings=self._keyless_warnings(),
                )
            fallback_warnings.append("Bing 搜索没有返回候选来源，已回退到 DuckDuckGo。")

        try:
            duckduckgo_output = await self._duckduckgo_search(query, tool_input)
        except ToolExecutionError as exc:
            if exc.category != "search_failed":
                raise
            attempts.append(
                {"provider": "duckduckgo", "status": "failed", "error_category": exc.category}
            )
            summary = ", ".join(
                f"{attempt['provider']}:{attempt['error_category']}"
                for attempt in attempts
                if attempt["status"] == "failed"
            )
            raise ToolExecutionError(
                "search_failed", f"Keyless web search providers failed ({summary})"
            ) from exc

        attempts.append(self._successful_attempt(duckduckgo_output))
        return self._with_audit(
            duckduckgo_output,
            provider_mode="auto",
            provider_attempts=attempts,
            degraded=True,
            extra_warnings=[*self._keyless_warnings(), *fallback_warnings],
        )

    @staticmethod
    def _successful_attempt(output: dict[str, Any]) -> dict[str, Any]:
        candidate_count = int(output.get("candidate_count", 0))
        return {
            "provider": str(output.get("provider", "")),
            "status": "succeeded" if candidate_count > 0 else "empty",
            "candidate_count": candidate_count,
        }

    @staticmethod
    def _keyless_warnings() -> list[str]:
        return ["当前使用无密钥公共搜索入口，不保证商业生产环境的可用性或结果 SLA。"]

    @staticmethod
    def _with_audit(
        output: dict[str, Any],
        *,
        provider_mode: str,
        provider_attempts: list[dict[str, Any]],
        degraded: bool,
        extra_warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        warnings = list(output.get("warnings", []))
        for warning in extra_warnings or []:
            if warning not in warnings:
                warnings.append(warning)
        return output | {
            "provider_mode": provider_mode,
            "provider_attempts": provider_attempts,
            "degraded": degraded,
            "warnings": warnings,
        }

    def _search_parameters(self, tool_input: dict[str, Any]) -> tuple[int, str, str]:
        try:
            num_results = int(
                tool_input.get("num_results") or self.settings.google_search_result_count
            )
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError("invalid_input", "num_results must be an integer") from exc
        num_results = max(1, min(num_results, 10))
        language = str(tool_input.get("language") or self.settings.google_search_language or "")
        region = str(tool_input.get("region") or self.settings.google_search_region or "")
        return num_results, language, region

    async def _bing_search(self, query: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        num_results, language, region = self._search_parameters(tool_input)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers={
                    "User-Agent": "Astra/0.1",
                    "Accept": "application/rss+xml,application/xml,text/xml",
                    "Accept-Language": language or "zh-CN,zh;q=0.9,en;q=0.8",
                },
            ) as client:
                response = await client.get(
                    "https://www.bing.com/search",
                    params={"q": query, "format": "rss", "count": num_results},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError("search_failed", str(exc)) from exc
        candidates = normalize_bing_rss(response.text)[:num_results]
        warnings = [] if candidates else ["Bing 搜索没有返回候选来源。"]
        return {
            "query": query,
            "provider": "bing",
            "parameters": {
                "num_results": num_results,
                "language": language,
                "region": region,
            },
            "candidate_count": len(candidates),
            "warnings": warnings,
            "candidates": candidates,
        }

    async def _duckduckgo_search(self, query: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        num_results, language, region = self._search_parameters(tool_input)
        params = {"q": query}
        if region:
            params["kl"] = region
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": language or "zh-CN,zh;q=0.9,en;q=0.8",
                },
            ) as client:
                response = await client.get("https://html.duckduckgo.com/html/", params=params)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError("search_failed", str(exc)) from exc

        parser = DuckDuckGoHTMLParser()
        parser.feed(response.text)
        retrieved_at = iso_now()
        candidates = []
        for item in parser.results:
            url = item.get("url", "")
            if not url or urlparse(url).scheme not in {"http", "https"}:
                continue
            candidates.append(
                {
                    "url": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "rank": len(candidates) + 1,
                    "display_link": display_link(url),
                    "provider": "duckduckgo",
                    "metadata": {},
                    "retrieved_at": retrieved_at,
                }
            )
            if len(candidates) >= num_results:
                break
        warnings = [] if candidates else ["DuckDuckGo 搜索没有返回可解析的候选来源。"]
        return {
            "query": query,
            "provider": "duckduckgo",
            "parameters": {
                "num_results": num_results,
                "language": language,
                "region": region,
            },
            "candidate_count": len(candidates),
            "warnings": warnings,
            "candidates": candidates,
        }

    async def _google_search(self, query: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        api_key = self.settings.google_search_api_key or self.settings.web_search_api_key
        search_engine_id = self.settings.google_search_engine_id
        if not api_key or not search_engine_id:
            raise ToolExecutionError(
                "missing_credentials",
                "GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID are required",
            )

        num_results, language, region = self._search_parameters(tool_input)
        params: dict[str, Any] = {
            "key": api_key,
            "cx": search_engine_id,
            "q": query,
            "num": num_results,
            "safe": self.settings.google_search_safe,
        }
        if language:
            params["lr"] = language
        if region:
            params["gl"] = region

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params=params,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError("search_failed", str(exc)) from exc

        candidates = normalize_google_items(response.json())
        warnings = [] if candidates else ["Google 搜索没有返回候选来源。"]
        return {
            "query": query,
            "provider": "google",
            "parameters": {
                "num_results": num_results,
                "language": language,
                "region": region,
                "safe": self.settings.google_search_safe,
            },
            "candidate_count": len(candidates),
            "warnings": warnings,
            "candidates": candidates,
        }

    async def _brave_search(
        self, query: str, tool_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.settings.web_search_api_key:
            raise ToolExecutionError("missing_credentials", "WEB_SEARCH_API_KEY is required")
        num_results, language, region = self._search_parameters(tool_input or {})
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={
                        "q": query,
                        "count": num_results,
                        **({"search_lang": language} if language else {}),
                        **({"country": region} if region else {}),
                    },
                    headers={"X-Subscription-Token": self.settings.web_search_api_key},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError("search_failed", str(exc)) from exc
        data = response.json()
        candidates: list[dict[str, Any]] = []
        now = iso_now()
        for rank, item in enumerate(data.get("web", {}).get("results", []), start=1):
            url = item.get("url", "")
            candidates.append(
                {
                    "url": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("description", ""),
                    "rank": rank,
                    "display_link": display_link(url),
                    "provider": "brave",
                    "metadata": {},
                    "retrieved_at": now,
                }
            )
        return {
            "query": query,
            "provider": "brave",
            "parameters": {
                "count": num_results,
                "language": language,
                "region": region,
            },
            "candidate_count": len(candidates),
            "warnings": [],
            "candidates": candidates,
        }
