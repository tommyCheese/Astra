from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from trafilatura import extract as extract_main_content
from trafilatura import extract_metadata

from app.tools.web_common import iso_now
from app.tools.web_fetch_output import build_fetch_output, quality_warnings


class ContentExtractor(HTMLParser):
    SKIPPED_TAGS = frozenset({"script", "style", "svg", "noscript"})
    VOID_TAGS = frozenset(
        {
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
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.metadata: dict[str, Any] = {}
        self.elements: list[dict[str, Any]] = []
        self.links: list[str] = []
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._skip_depth = 0
        self._current_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {key.lower(): value or "" for key, value in attrs}
        if self._consume_skipped_tag(tag):
            return
        self._capture_structural_tag(tag, attr_dict)

    def _consume_skipped_tag(self, tag: str) -> bool:
        if self._skip_depth:
            if tag in self.SKIPPED_TAGS:
                self._skip_depth += 1
            return True
        if tag in self.SKIPPED_TAGS:
            self._skip_depth += 1
            return True
        return False

    def _capture_structural_tag(self, tag: str, attr_dict: dict[str, str]) -> None:
        if tag == "title":
            self._current_title = True
        if tag == "meta":
            self._capture_meta(attr_dict)
            return
        if tag == "a" and attr_dict.get("href"):
            self.links.append(attr_dict["href"])
        if tag not in self.VOID_TAGS:
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
    requested_url = requested_url or url
    common = {
        "url": url,
        "status_code": status_code,
        "content_type": content_type,
        "max_chars": max_chars,
        "min_quality_chars": min_quality_chars,
        "requested_url": requested_url,
        "redirect_count": redirect_count,
        "response_bytes": response_bytes,
    }
    if "html" not in content_type and "<html" not in body[:500].lower():
        return _extract_plain_text(body=body, **common)
    return _extract_html(
        body=body,
        query=query,
        snippet=snippet,
        crawler_plan=crawler_plan,
        **common,
    )


def _extract_plain_text(
    *,
    body: str,
    url: str,
    status_code: int,
    content_type: str,
    max_chars: int,
    min_quality_chars: int,
    requested_url: str,
    redirect_count: int,
    response_bytes: int | None,
) -> dict[str, Any]:
    normalized_body = normalize_space(body)
    content = normalized_body[:max_chars]
    warnings = quality_warnings(content, "", min_quality_chars)
    truncated = len(normalized_body) > max_chars
    if truncated:
        warnings.append("正文超过内容上限，已截断并保留稳定快照摘要。")
    metadata = {
        "content_type": content_type,
        "requested_url": requested_url,
        "final_url": url,
        "redirect_count": redirect_count,
        "response_bytes": response_bytes,
    }
    return build_fetch_output(
        url,
        status_code,
        None,
        None,
        content,
        metadata,
        "plain_text",
        warnings,
        iso_now(),
        min_quality_chars,
        requested_url=requested_url,
        truncated=truncated,
    )


def _extract_html(
    *,
    body: str,
    url: str,
    status_code: int,
    content_type: str,
    query: str,
    snippet: str,
    crawler_plan: dict[str, Any],
    max_chars: int,
    min_quality_chars: int,
    requested_url: str,
    redirect_count: int,
    response_bytes: int | None,
) -> dict[str, Any]:
    parser = ContentExtractor()
    parser.feed(body)
    document_metadata = extract_metadata(body, default_url=url)
    title, description, metadata = _html_metadata(
        parser,
        document_metadata,
        url=url,
        content_type=content_type,
        requested_url=requested_url,
        redirect_count=redirect_count,
        response_bytes=response_bytes,
    )
    strategy, content, warnings = _html_content(
        body,
        url=url,
        parser=parser,
        description=description,
        snippet=snippet,
        crawler_plan=crawler_plan,
    )
    warnings[:0] = quality_warnings(content, query, min_quality_chars)
    if not content and snippet:
        strategy, content = "fallback_snippet", snippet
        warnings.append("页面正文不可用，使用搜索摘要作为弱证据。")
    normalized_content = normalize_space(content)
    truncated = len(normalized_content) > max_chars
    if truncated:
        warnings.append("正文超过内容上限，已截断并保留稳定快照摘要。")
    return build_fetch_output(
        url,
        status_code,
        title,
        description,
        normalized_content[:max_chars],
        metadata,
        strategy,
        warnings,
        iso_now(),
        min_quality_chars,
        requested_url=requested_url,
        truncated=truncated,
    )


def _html_metadata(
    parser: ContentExtractor,
    document_metadata: Any,
    *,
    url: str,
    content_type: str,
    requested_url: str,
    redirect_count: int,
    response_bytes: int | None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    title = (
        metadata_attribute(document_metadata, "title")
        or normalize_space(" ".join(parser.title_parts))
        or parser.metadata.get("og:title")
    )
    description = metadata_attribute(document_metadata, "description") or first_present(
        parser.metadata, ["description", "og:description", "twitter:description"]
    )
    metadata = {
        "content_type": content_type,
        "description": description,
        "site_name": metadata_attribute(document_metadata, "sitename")
        or parser.metadata.get("og:site_name"),
        "published_at": metadata_attribute(document_metadata, "date")
        or first_present(
            parser.metadata, ["article:published_time", "datepublished", "publishdate", "date"]
        ),
        "author": metadata_attribute(document_metadata, "author")
        or first_present(parser.metadata, ["author", "article:author"]),
        "requested_url": requested_url,
        "final_url": url,
        "canonical_url": metadata_attribute(document_metadata, "url"),
        "redirect_count": redirect_count,
        "response_bytes": response_bytes,
        "links": _public_links(parser.links, url),
    }
    return title, description, metadata


def _public_links(links: list[str], base_url: str) -> list[str]:
    return [
        urljoin(base_url, link)
        for link in links
        if urlparse(urljoin(base_url, link)).scheme in {"http", "https"}
    ][:100]


def _html_content(
    body: str,
    *,
    url: str,
    parser: ContentExtractor,
    description: str | None,
    snippet: str,
    crawler_plan: dict[str, Any],
) -> tuple[str, str, list[str]]:
    strategy = choose_strategy(crawler_plan, parser, description)
    if strategy != "trafilatura":
        content = extract_content_by_strategy(
            strategy, parser, description, snippet, crawler_plan.get("selectors", [])
        )
        return strategy, content, []
    content, warnings = _trafilatura_content(body, url, crawler_plan)
    if content:
        return strategy, content, warnings
    fallback = extract_content_by_strategy(
        "html_fallback", parser, description, snippet, crawler_plan.get("selectors", [])
    )
    return "html_fallback", fallback, warnings


def _trafilatura_content(
    body: str,
    url: str,
    crawler_plan: dict[str, Any],
) -> tuple[str, list[str]]:
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
            prune_xpath=selectors_to_prune_xpath(crawler_plan.get("exclude_selectors", [])),
        )
        return content or "", []
    except Exception:
        return "", ["主正文提取器失败，已使用安全的 HTML 文本回退。"]


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
                f"//*[contains(concat(' ', normalize-space(@class), ' '), ' {class_name} ')]"
            )
        elif "." in selector:
            tag, class_name = selector.split(".", 1)
            xpath.append(
                f"//{tag}[contains(concat(' ', normalize-space(@class), ' '), ' {class_name} ')]"
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
        if any(_matches_selector(element, selector) for selector in selectors):
            selected.append(element["text"])
    return normalize_space(" ".join(selected))


def _matches_selector(element: dict[str, Any], selector: str) -> bool:
    tag = element["tag"]
    attrs = element["attrs"]
    classes = attrs.get("class", "").split()
    if selector == tag:
        return True
    if selector.startswith("."):
        return selector[1:] in classes
    if selector.startswith("#"):
        return attrs.get("id") == selector[1:]
    if "." not in selector:
        return False
    selector_tag, selector_class = selector.split(".", 1)
    return selector_tag == tag and selector_class in classes


def first_present(metadata: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        if metadata.get(key):
            return metadata[key]
    return None


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
