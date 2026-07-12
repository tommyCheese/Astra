import ipaddress
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import Settings
from app.tools.base import Tool, ToolExecutionError, ToolSpec


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


class WebSearchTool(Tool):
    spec = ToolSpec(
        name="web_search",
        version="0.2.0",
        description="Search the web through a configured provider and return candidate sources.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "num_results": {"type": "integer"},
                "language": {"type": "string"},
                "region": {"type": "string"},
            },
        },
        output_schema={"type": "object", "required": ["query", "provider", "candidates"]},
        permission="network_read",
        side_effect_level="read_only",
        timeout_seconds=20,
        retry_policy={"max_attempts": 1},
        error_categories=["invalid_input", "missing_credentials", "search_failed"],
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        query = str(tool_input.get("query", "")).strip()
        if not query:
            raise ToolExecutionError("invalid_input", "web_search requires a non-empty query")
        provider = self.settings.web_search_provider
        if provider == "google":
            return await self._google_search(query, tool_input)
        if provider == "brave":
            return await self._brave_search(query)
        raise ToolExecutionError(
            "provider_not_configured",
            f"Unsupported web search provider: {self.settings.web_search_provider}",
        )

    async def _google_search(self, query: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        api_key = self.settings.google_search_api_key or self.settings.web_search_api_key
        search_engine_id = self.settings.google_search_engine_id
        if not api_key or not search_engine_id:
            raise ToolExecutionError(
                "missing_credentials",
                "GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID are required",
            )

        try:
            num_results = int(
                tool_input.get("num_results") or self.settings.google_search_result_count
            )
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError("invalid_input", "num_results must be an integer") from exc
        num_results = max(1, min(num_results, 10))
        language = str(tool_input.get("language") or self.settings.google_search_language or "")
        region = str(tool_input.get("region") or self.settings.google_search_region or "")
        params = {
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
            async with httpx.AsyncClient(timeout=self.spec.timeout_seconds) as client:
                response = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params=params,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError("search_failed", str(exc)) from exc

        candidates = normalize_google_items(response.json())
        warnings = []
        if not candidates:
            warnings.append("Google 搜索没有返回候选来源。")
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

    async def _brave_search(self, query: str) -> dict[str, Any]:
        if not self.settings.web_search_api_key:
            raise ToolExecutionError("missing_credentials", "WEB_SEARCH_API_KEY is required")
        try:
            async with httpx.AsyncClient(timeout=self.spec.timeout_seconds) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 5},
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
            "parameters": {"count": 5},
            "candidate_count": len(candidates),
            "warnings": [],
            "candidates": candidates,
        }


class WebFetchTool(Tool):
    spec = ToolSpec(
        name="web_fetch",
        version="0.2.0",
        description="Fetch a URL and adaptively extract its main readable content.",
        input_schema={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "query": {"type": "string"},
                "snippet": {"type": "string"},
                "crawler_plan": {"type": "object"},
            },
        },
        output_schema={
            "type": "object",
            "required": ["url", "status_code", "content", "extraction_strategy", "quality_score"],
        },
        permission="network_read",
        side_effect_level="read_only",
        timeout_seconds=20,
        retry_policy={"max_attempts": 1},
        error_categories=["invalid_input", "permission_denied", "fetch_failed", "extract_failed"],
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        url = str(tool_input.get("url", "")).strip()
        if not url:
            raise ToolExecutionError("invalid_input", "web_fetch requires a URL")
        validate_public_http_url(url)
        query = str(tool_input.get("query", "") or "")
        snippet = str(tool_input.get("snippet", "") or "")
        crawler_plan = validate_crawler_plan(tool_input.get("crawler_plan"))
        if not self.settings.allow_network_read:
            raise ToolExecutionError("permission_denied", "Network read is disabled")

        try:
            async with httpx.AsyncClient(
                timeout=self.spec.timeout_seconds,
                headers={"User-Agent": "AstraBot/0.1 (+https://github.com/tommyCheese/Astra)"},
            ) as client:
                response = await self._get_with_safe_redirects(client, url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError("fetch_failed", str(exc)) from exc

        return extract_source(
            url=url,
            status_code=response.status_code,
            body=response.text,
            content_type=response.headers.get("content-type", ""),
            query=query,
            snippet=snippet,
            crawler_plan=crawler_plan,
            max_chars=self.settings.crawler_max_content_chars,
            min_quality_chars=self.settings.crawler_min_quality_chars,
        )

    async def _get_with_safe_redirects(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        max_redirects: int = 5,
    ) -> httpx.Response:
        current_url = url
        for _ in range(max_redirects + 1):
            validate_public_http_url(current_url)
            response = await client.get(current_url, follow_redirects=False)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current_url = urljoin(str(response.url), location)
        raise ToolExecutionError("fetch_failed", "Too many redirects")


def validate_public_http_url(url: str) -> None:
    """Reject obviously unsafe fetch targets before issuing a request."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolExecutionError("invalid_input", "web_fetch only supports HTTP(S) URLs")
    if parsed.username or parsed.password:
        raise ToolExecutionError("permission_denied", "URLs containing credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ToolExecutionError("permission_denied", "Local network targets are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ToolExecutionError(
            "permission_denied", "Private or reserved network targets are not allowed"
        )


class ContentExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.metadata: dict[str, Any] = {}
        self.elements: list[dict[str, Any]] = []
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._skip_depth = 0
        self._current_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {key.lower(): value or "" for key, value in attrs}
        if self._skip_depth:
            if tag in {"script", "style", "svg", "noscript"}:
                self._skip_depth += 1
            return
        if tag in {"script", "style", "svg", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._current_title = True
        if tag == "meta":
            self._capture_meta(attr_dict)
            return
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self._stack.append((tag, attr_dict))

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in {"script", "style", "svg", "noscript"}:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._current_title = False
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        text = normalize_space(data)
        if not text or self._skip_depth:
            return
        if self._current_title:
            self.title_parts.append(text)
            return
        if not self._stack:
            return
        tag, attrs = self._stack[-1]
        if tag in {"p", "article", "main", "section", "li", "h1", "h2", "h3", "blockquote"}:
            self.elements.append(
                {
                    "tag": tag,
                    "attrs": attrs,
                    "text": text,
                    "path": [entry[0] for entry in self._stack],
                }
            )

    def _capture_meta(self, attrs: dict[str, str]) -> None:
        key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
        content = attrs.get("content")
        if key and content:
            self.metadata[key.lower()] = content


def validate_crawler_plan(raw_plan: Any) -> dict[str, Any]:
    allowed = {
        "readability",
        "metadata_first",
        "selector_extract",
        "plain_text",
        "fallback_snippet",
    }
    if not isinstance(raw_plan, dict):
        return {
            "strategy": "readability",
            "selectors": [],
            "exclude_selectors": [],
            "target": "main_content",
        }
    strategy = raw_plan.get("strategy", "readability")
    if strategy not in allowed:
        strategy = "readability"
    selectors = [item for item in raw_plan.get("selectors", []) if is_safe_selector(item)]
    exclude_selectors = [
        item for item in raw_plan.get("exclude_selectors", []) if is_safe_selector(item)
    ]
    return {
        "strategy": strategy,
        "selectors": selectors[:8],
        "exclude_selectors": exclude_selectors[:8],
        "target": str(raw_plan.get("target") or "main_content")[:80],
    }


def is_safe_selector(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 80:
        return False
    return bool(re.fullmatch(r"[#.]?[A-Za-z0-9_-]+|[A-Za-z0-9_-]+[.][A-Za-z0-9_-]+", value))


def extract_source(
    *,
    url: str,
    status_code: int,
    body: str,
    content_type: str,
    query: str,
    snippet: str,
    crawler_plan: dict[str, Any],
    max_chars: int,
    min_quality_chars: int,
) -> dict[str, Any]:
    retrieved_at = iso_now()
    if "html" not in content_type and "<html" not in body[:500].lower():
        content = normalize_space(body)[:max_chars]
        warnings = [] if len(content) >= min_quality_chars else ["正文过短，可能不足以支撑总结。"]
        return build_fetch_output(
            url,
            status_code,
            None,
            None,
            content,
            {"content_type": content_type},
            "plain_text",
            warnings,
            retrieved_at,
            min_quality_chars,
        )

    parser = ContentExtractor()
    parser.feed(body)
    title = normalize_space(" ".join(parser.title_parts)) or parser.metadata.get("og:title")
    description = (
        parser.metadata.get("description")
        or parser.metadata.get("og:description")
        or parser.metadata.get("twitter:description")
    )
    metadata = {
        "content_type": content_type,
        "description": description,
        "site_name": parser.metadata.get("og:site_name"),
        "published_at": first_present(
            parser.metadata,
            ["article:published_time", "datepublished", "publishdate", "date"],
        ),
        "author": first_present(parser.metadata, ["author", "article:author"]),
    }
    strategy = choose_strategy(crawler_plan, parser, description)
    content = extract_content_by_strategy(
        strategy,
        parser,
        description,
        snippet,
        crawler_plan.get("selectors", []),
    )
    warnings = quality_warnings(content, query, min_quality_chars)
    if not content and snippet:
        strategy = "fallback_snippet"
        content = snippet
        warnings.append("页面正文不可用，使用搜索摘要作为弱证据。")
    return build_fetch_output(
        url,
        status_code,
        title,
        description,
        content[:max_chars],
        metadata,
        strategy,
        warnings,
        retrieved_at,
        min_quality_chars,
    )


def choose_strategy(
    crawler_plan: dict[str, Any], parser: ContentExtractor, description: str | None
) -> str:
    strategy = crawler_plan.get("strategy", "readability")
    if strategy == "selector_extract" and crawler_plan.get("selectors"):
        return strategy
    if description and len(parser.elements) < 2:
        return "metadata_first"
    return strategy


def extract_content_by_strategy(
    strategy: str,
    parser: ContentExtractor,
    description: str | None,
    snippet: str,
    selectors: list[str],
) -> str:
    if strategy == "metadata_first":
        return normalize_space(" ".join([description or "", snippet]))
    if strategy == "selector_extract":
        selected = select_text(parser.elements, selectors)
        if selected:
            return selected
    candidates = [element["text"] for element in parser.elements]
    content = normalize_space(" ".join(candidates))
    if content:
        return content
    if description:
        return description
    return snippet


def select_text(
    elements: list[dict[str, Any]],
    selectors: list[str],
) -> str:
    if not selectors:
        return ""
    selected: list[str] = []
    for element in elements:
        tag = element["tag"]
        attrs = element["attrs"]
        for selector in selectors:
            if (
                selector == tag
                or selector.startswith(".")
                and selector[1:] in attrs.get("class", "").split()
                or selector.startswith("#")
                and attrs.get("id") == selector[1:]
            ):
                selected.append(element["text"])
            elif "." in selector:
                selector_tag, selector_class = selector.split(".", 1)
                if selector_tag == tag and selector_class in attrs.get("class", "").split():
                    selected.append(element["text"])
    return normalize_space(" ".join(selected))


def build_fetch_output(
    url: str,
    status_code: int,
    title: str | None,
    description: str | None,
    content: str,
    metadata: dict[str, Any],
    strategy: str,
    warnings: list[str],
    retrieved_at: str,
    min_quality_chars: int,
) -> dict[str, Any]:
    content = normalize_space(content)
    quality_score = min(1.0, len(content) / max(float(min_quality_chars), 1.0))
    return {
        "url": url,
        "status_code": status_code,
        "title": title,
        "description": description,
        "content": content,
        "metadata": metadata,
        "extraction_strategy": strategy,
        "quality_score": round(quality_score, 3),
        "content_length": len(content),
        "source_type": infer_source_type(url, metadata),
        "warnings": warnings,
        "retrieved_at": retrieved_at,
    }


def quality_warnings(content: str, query: str, min_quality_chars: int) -> list[str]:
    warnings = []
    if len(content) < min_quality_chars:
        warnings.append("正文过短，可能不足以支撑总结。")
    if query and content and not has_query_overlap(query, content):
        warnings.append("正文与查询词重叠较少，需要在总结中谨慎使用。")
    return warnings


def has_query_overlap(query: str, content: str) -> bool:
    query_terms = {term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)}
    content_lower = content.lower()
    return any(term in content_lower for term in list(query_terms)[:6])


def infer_source_type(url: str, metadata: dict[str, Any]) -> str:
    if metadata.get("published_at"):
        return "article"
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    return "web_page"


def first_present(metadata: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        if metadata.get(key):
            return metadata[key]
    return None


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def build_web_registry(settings: Settings):
    from app.tools.base import ToolRegistry

    registry = ToolRegistry()
    registry.register(WebSearchTool(settings))
    registry.register(WebFetchTool(settings))
    return registry
