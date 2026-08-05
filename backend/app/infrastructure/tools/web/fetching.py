"""Securely fetch public HTTP resources for the Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx

from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolSpec,
    ToolExecutionError,
)
from app.infrastructure.tools.web.content import extract_source, validate_crawler_plan
from app.infrastructure.tools.web.security import (
    decode_response_body,
    read_limited_body,
    validate_content_length,
    validate_fetch_content_type,
    validate_public_http_target,
    validate_public_http_url,
)

if TYPE_CHECKING:
    from app.common.core.config import AstraRuntimeSettings


@dataclass(frozen=True)
class WebFetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    redirect_count: int


class WebFetchTool(AstraTool):
    spec = AstraToolSpec(
        name="web_fetch",
        version="0.4.0",
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
        task_capabilities=["information.read", "source.retrieve", "evidence.extract"],
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

    def __init__(self, settings: AstraRuntimeSettings):
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
        response = await self._fetch_response(url)
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

    async def _fetch_response(self, url: str) -> WebFetchResponse:
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
                return await self._get_with_safe_redirects(
                    client,
                    url,
                    max_response_bytes=self.settings.crawler_max_response_bytes,
                )
        except ToolExecutionError:
            raise
        except httpx.HTTPError as exc:
            raise ToolExecutionError("fetch_failed", str(exc)) from exc

    async def _get_with_safe_redirects(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        max_redirects: int = 5,
        max_response_bytes: int | None = None,
    ) -> WebFetchResponse:
        byte_limit = max_response_bytes or self.settings.crawler_max_response_bytes
        current_url = url
        for redirect_count in range(max_redirects + 1):
            await validate_public_http_target(
                current_url,
                allow_proxy_fake_ip=self.settings.crawler_allow_proxy_fake_ip,
            )
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
                return WebFetchResponse(
                    requested_url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=body,
                    redirect_count=redirect_count,
                )
        raise ToolExecutionError("fetch_failed", "Too many redirects")
