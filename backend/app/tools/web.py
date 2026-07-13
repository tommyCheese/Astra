import asyncio
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from charset_normalizer import from_bytes
from trafilatura import extract as extract_main_content
from trafilatura import extract_metadata

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
            self._current[self._active] = normalize_space(
                f"{self._current[self._active]} {text}"
            )


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
        if provider == "bing":
            return await self._bing_search(query, tool_input)
        if provider == "duckduckgo":
            return await self._duckduckgo_search(query, tool_input)
        if provider == "google":
            return await self._google_search(query, tool_input)
        if provider == "brave":
            return await self._brave_search(query)
        raise ToolExecutionError(
            "provider_not_configured",
            f"Unsupported web search provider: {self.settings.web_search_provider}",
        )

    async def _bing_search(
        self, query: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            num_results = int(
                tool_input.get("num_results") or self.settings.google_search_result_count
            )
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError("invalid_input", "num_results must be an integer") from exc
        num_results = max(1, min(num_results, 10))
        language = str(tool_input.get("language") or self.settings.google_search_language or "")
        region = str(tool_input.get("region") or self.settings.google_search_region or "")
        try:
            async with httpx.AsyncClient(
                timeout=self.spec.timeout_seconds,
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

    async def _duckduckgo_search(
        self, query: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            num_results = int(
                tool_input.get("num_results") or self.settings.google_search_result_count
            )
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError("invalid_input", "num_results must be an integer") from exc
        num_results = max(1, min(num_results, 10))
        language = str(tool_input.get("language") or self.settings.google_search_language or "")
        region = str(tool_input.get("region") or self.settings.google_search_region or "")
        params = {"q": query}
        if region:
            params["kl"] = region
        try:
            async with httpx.AsyncClient(
                timeout=self.spec.timeout_seconds,
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


@dataclass(frozen=True)
class FetchedResponse:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    redirect_count: int


class WebFetchTool(Tool):
    spec = ToolSpec(
        name="web_fetch",
        version="0.3.0",
        description=(
            "Securely fetch a public HTTP(S) URL with bounded streaming and extract its "
            "main readable content and metadata."
        ),
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
        error_categories=[
            "invalid_input",
            "permission_denied",
            "fetch_failed",
            "unsupported_content_type",
            "response_too_large",
            "extract_failed",
        ],
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        url = str(tool_input.get("url", "")).strip()
        if not url:
            raise ToolExecutionError("invalid_input", "web_fetch requires a URL")
        query = str(tool_input.get("query", "") or "")
        snippet = str(tool_input.get("snippet", "") or "")
        crawler_plan = validate_crawler_plan(tool_input.get("crawler_plan"))
        if not self.settings.allow_network_read:
            raise ToolExecutionError("permission_denied", "Network read is disabled")
        validate_public_http_url(url)

        try:
            timeout = httpx.Timeout(
                self.spec.timeout_seconds,
                connect=min(10.0, self.spec.timeout_seconds),
                read=self.spec.timeout_seconds,
                write=5.0,
                pool=5.0,
            )
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=4,
                    max_keepalive_connections=2,
                    keepalive_expiry=5.0,
                ),
                trust_env=False,
                headers={
                    "User-Agent": "AstraWebFetcher/0.3",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/json,text/plain,"
                        "application/xml;q=0.9,text/xml;q=0.9"
                    ),
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            ) as client:
                response = await self._get_with_safe_redirects(
                    client,
                    url,
                    max_response_bytes=self.settings.crawler_max_response_bytes,
                )
        except ToolExecutionError:
            raise
        except httpx.HTTPError as exc:
            raise ToolExecutionError("fetch_failed", str(exc)) from exc

        content_type = response.headers.get("content-type", "")
        return extract_source(
            url=response.final_url,
            status_code=response.status_code,
            body=decode_response_body(response.body, content_type),
            content_type=content_type,
            query=query,
            snippet=snippet,
            crawler_plan=crawler_plan,
            max_chars=self.settings.crawler_max_content_chars,
            min_quality_chars=self.settings.crawler_min_quality_chars,
            requested_url=response.requested_url,
            redirect_count=response.redirect_count,
            response_bytes=len(response.body),
        )

    async def _get_with_safe_redirects(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        max_redirects: int = 5,
        max_response_bytes: int | None = None,
    ) -> FetchedResponse:
        byte_limit = max_response_bytes or self.settings.crawler_max_response_bytes
        current_url = url
        for redirect_count in range(max_redirects + 1):
            await validate_public_http_target(current_url)
            async with client.stream("GET", current_url, follow_redirects=False) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ToolExecutionError(
                            "fetch_failed", "Redirect response did not include a Location header"
                        )
                    current_url = urljoin(str(response.url), location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                validate_fetch_content_type(content_type)
                validate_content_length(response.headers.get("content-length"), byte_limit)
                body = await read_limited_body(response, byte_limit)
                return FetchedResponse(
                    requested_url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=body,
                    redirect_count=redirect_count,
                )
        raise ToolExecutionError("fetch_failed", "Too many redirects")


def validate_public_http_url(url: str) -> None:
    """Perform strict structural validation before resolving or requesting a URL."""
    if any(ord(character) < 32 or character.isspace() for character in url):
        raise ToolExecutionError("invalid_input", "URL contains whitespace or control characters")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolExecutionError("invalid_input", "web_fetch only supports HTTP(S) URLs")
    if parsed.username or parsed.password:
        raise ToolExecutionError("permission_denied", "URLs containing credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if len(hostname) > 253:
        raise ToolExecutionError("invalid_input", "URL hostname is too long")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ToolExecutionError("permission_denied", "Local network targets are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolExecutionError("invalid_input", "URL contains an invalid port") from exc
    expected_port = 80 if parsed.scheme == "http" else 443
    if port is not None and port != expected_port:
        raise ToolExecutionError(
            "permission_denied", "Only standard HTTP and HTTPS ports are allowed"
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    validate_public_ip(address)


def validate_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if not address.is_global:
        raise ToolExecutionError(
            "permission_denied", "Private or reserved network targets are not allowed"
        )


async def validate_public_http_target(url: str) -> set[str]:
    """Resolve every A/AAAA target and reject the hop if any address is non-public."""
    validate_public_http_url(url)
    parsed = urlparse(url)
    hostname = parsed.hostname.rstrip(".").lower()  # type: ignore[union-attr]
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        validate_public_ip(literal)
        return {str(literal)}

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise ToolExecutionError("fetch_failed", f"Unable to resolve URL hostname: {hostname}") from exc
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses:
        raise ToolExecutionError("fetch_failed", f"URL hostname has no address records: {hostname}")
    for value in addresses:
        validate_public_ip(ipaddress.ip_address(value))
    return addresses


def validate_fetch_content_type(content_type: str) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    allowed_application_types = {
        "application/atom+xml",
        "application/json",
        "application/rss+xml",
        "application/xhtml+xml",
        "application/xml",
    }
    if media_type and not media_type.startswith("text/") and media_type not in allowed_application_types:
        raise ToolExecutionError(
            "unsupported_content_type", f"Unsupported response content type: {media_type}"
        )


def validate_content_length(content_length: str | None, byte_limit: int) -> None:
    if not content_length:
        return
    try:
        declared_size = int(content_length)
    except ValueError:
        return
    if declared_size > byte_limit:
        raise ToolExecutionError(
            "response_too_large", f"Response exceeds the {byte_limit} byte limit"
        )


async def read_limited_body(response: httpx.Response, byte_limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > byte_limit:
            raise ToolExecutionError(
                "response_too_large", f"Response exceeds the {byte_limit} byte limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def decode_response_body(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\"']+)", content_type, re.I)
    if charset_match:
        try:
            return body.decode(charset_match.group(1).strip())
        except (LookupError, UnicodeDecodeError):
            pass
    detected = from_bytes(body).best()
    if detected is not None:
        return str(detected)
    return body.decode("utf-8", errors="replace")


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
        "trafilatura",
        "readability",
        "metadata_first",
        "selector_extract",
        "plain_text",
        "fallback_snippet",
    }
    if not isinstance(raw_plan, dict):
        return {
            "strategy": "trafilatura",
            "selectors": [],
            "exclude_selectors": [],
            "target": "main_content",
        }
    strategy = raw_plan.get("strategy", "trafilatura")
    if strategy not in allowed:
        strategy = "trafilatura"
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
    requested_url: str | None = None,
    redirect_count: int = 0,
    response_bytes: int | None = None,
) -> dict[str, Any]:
    retrieved_at = iso_now()
    requested_url = requested_url or url
    if "html" not in content_type and "<html" not in body[:500].lower():
        content = normalize_space(body)[:max_chars]
        warnings = [] if len(content) >= min_quality_chars else ["正文过短，可能不足以支撑总结。"]
        return build_fetch_output(
            url,
            status_code,
            None,
            None,
            content,
            {
                "content_type": content_type,
                "requested_url": requested_url,
                "final_url": url,
                "redirect_count": redirect_count,
                "response_bytes": response_bytes,
            },
            "plain_text",
            warnings,
            retrieved_at,
            min_quality_chars,
            requested_url=requested_url,
        )

    parser = ContentExtractor()
    parser.feed(body)
    document_metadata = extract_metadata(body, default_url=url)
    title = (
        metadata_attribute(document_metadata, "title")
        or normalize_space(" ".join(parser.title_parts))
        or parser.metadata.get("og:title")
    )
    description = (
        metadata_attribute(document_metadata, "description")
        or parser.metadata.get("description")
        or parser.metadata.get("og:description")
        or parser.metadata.get("twitter:description")
    )
    metadata = {
        "content_type": content_type,
        "description": description,
        "site_name": metadata_attribute(document_metadata, "sitename")
        or parser.metadata.get("og:site_name"),
        "published_at": metadata_attribute(document_metadata, "date")
        or first_present(
            parser.metadata,
            ["article:published_time", "datepublished", "publishdate", "date"],
        ),
        "author": metadata_attribute(document_metadata, "author")
        or first_present(parser.metadata, ["author", "article:author"]),
        "requested_url": requested_url,
        "final_url": url,
        "canonical_url": metadata_attribute(document_metadata, "url"),
        "redirect_count": redirect_count,
        "response_bytes": response_bytes,
    }
    strategy = choose_strategy(crawler_plan, parser, description)
    extraction_warnings: list[str] = []
    if strategy == "trafilatura":
        try:
            content = extract_main_content(
                body,
                url=url,
                output_format="txt",
                include_comments=False,
                include_tables=True,
                include_links=False,
                deduplicate=True,
                favor_precision=True,
                prune_xpath=selectors_to_prune_xpath(
                    crawler_plan.get("exclude_selectors", [])
                ),
            ) or ""
        except Exception:
            content = ""
            extraction_warnings.append("主正文提取器失败，已使用安全的 HTML 文本回退。")
        if not content:
            strategy = "html_fallback"
            content = extract_content_by_strategy(
                strategy,
                parser,
                description,
                snippet,
                crawler_plan.get("selectors", []),
            )
    else:
        content = extract_content_by_strategy(
            strategy,
            parser,
            description,
            snippet,
            crawler_plan.get("selectors", []),
        )
    warnings = quality_warnings(content, query, min_quality_chars)
    warnings.extend(extraction_warnings)
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
        requested_url=requested_url,
    )


def choose_strategy(
    crawler_plan: dict[str, Any], parser: ContentExtractor, description: str | None
) -> str:
    strategy = crawler_plan.get("strategy", "trafilatura")
    if strategy == "readability":
        strategy = "trafilatura"
    if strategy == "selector_extract" and crawler_plan.get("selectors"):
        return strategy
    if description and len(parser.elements) < 2:
        return "metadata_first"
    return strategy


def metadata_attribute(document: Any, name: str) -> str | None:
    value = getattr(document, name, None) if document is not None else None
    return normalize_space(str(value)) if value else None


def selectors_to_prune_xpath(selectors: list[str]) -> list[str] | None:
    xpath: list[str] = []
    for selector in selectors:
        if selector.startswith("#"):
            xpath.append(f"//*[@id='{selector[1:]}']")
        elif selector.startswith("."):
            class_name = selector[1:]
            xpath.append(
                "//*[contains(concat(' ', normalize-space(@class), ' '), "
                f"' {class_name} ')]"
            )
        elif "." in selector:
            tag, class_name = selector.split(".", 1)
            xpath.append(
                f"//{tag}[contains(concat(' ', normalize-space(@class), ' '), "
                f"' {class_name} ')]"
            )
        else:
            xpath.append(f"//{selector}")
    return xpath or None


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
    *,
    requested_url: str | None = None,
) -> dict[str, Any]:
    content = normalize_space(content)
    quality_score = min(1.0, len(content) / max(float(min_quality_chars), 1.0))
    return {
        "url": url,
        "requested_url": requested_url or url,
        "final_url": url,
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
    if settings.tool_web_search_enabled:
        registry.register(WebSearchTool(settings))
    if settings.tool_web_fetch_enabled:
        registry.register(WebFetchTool(settings))
    return registry
