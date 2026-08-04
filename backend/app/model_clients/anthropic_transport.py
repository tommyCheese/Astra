from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.model_clients.contracts import (
    DeferredUsageInvocation,
    StreamFieldCallbacks,
)
from app.model_clients.reasoning import ModelReasoningConfig
from app.model_clients.response_parsing import StreamingJsonFieldExtractor


@dataclass(frozen=True)
class AnthropicRequest:
    url: str
    api_key: str
    model: str
    messages: list[dict[str, str]]
    reasoning: ModelReasoningConfig
    callbacks: StreamFieldCallbacks = field(default_factory=dict)


@dataclass(frozen=True)
class AnthropicResponse:
    content: str
    request_id: str | None
    usage: dict[str, Any]
    emitted_fields: frozenset[str]


class AnthropicTransport:
    """Own the Anthropic Messages HTTP and SSE wire protocol."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def send(
        self,
        request: AnthropicRequest,
        usage_invocation: DeferredUsageInvocation,
    ) -> AnthropicResponse:
        if request.callbacks:
            return await self._stream(request, usage_invocation)
        return await self._post(request)

    async def _stream(
        self,
        request: AnthropicRequest,
        usage_invocation: DeferredUsageInvocation,
    ) -> AnthropicResponse:
        chunks: list[str] = []
        usage: dict[str, Any] = {}
        emitted_fields: set[str] = set()
        extractor = StreamingJsonFieldExtractor(request.callbacks)
        async with self.client.stream(
            "POST",
            request.url,
            headers=self._headers(request, stream=True),
            json={**self._payload(request), "stream": True},
        ) as response:
            response.raise_for_status()
            request_id = response.headers.get("request-id")
            async for line in response.aiter_lines():
                event = self._parse_event(line)
                if event is None:
                    continue
                self._collect_usage(event, usage)
                text_delta = self._text_delta(event)
                if not text_delta:
                    continue
                chunks.append(text_delta)
                await self._publish_deltas(
                    extractor,
                    text_delta,
                    request.callbacks,
                    emitted_fields,
                    usage_invocation,
                )
        return AnthropicResponse(
            content="".join(chunks).strip(),
            request_id=request_id,
            usage=usage,
            emitted_fields=frozenset(emitted_fields),
        )

    async def _post(self, request: AnthropicRequest) -> AnthropicResponse:
        response = await self.client.post(
            request.url,
            headers=self._headers(request, stream=False),
            json=self._payload(request),
        )
        response.raise_for_status()
        body = response.json()
        content = "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return AnthropicResponse(
            content=content,
            request_id=response.headers.get("request-id"),
            usage=usage,
            emitted_fields=frozenset(),
        )

    @staticmethod
    def _headers(request: AnthropicRequest, *, stream: bool) -> dict[str, str]:
        return {
            "x-api-key": request.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "text/event-stream" if stream else "application/json",
            "accept-encoding": "identity",
        }

    @staticmethod
    def _payload(request: AnthropicRequest) -> dict[str, Any]:
        system_prompt = "\n\n".join(
            message["content"] for message in request.messages if message["role"] == "system"
        )
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": 8192,
            "messages": [
                message for message in request.messages if message["role"] in {"user", "assistant"}
            ],
            **request.reasoning.request_params,
        }
        if system_prompt:
            payload["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        return payload

    @staticmethod
    def _parse_event(line: str) -> dict[str, Any] | None:
        if not line.startswith("data:"):
            return None
        encoded_event = line[5:].strip()
        if not encoded_event or encoded_event == "[DONE]":
            return None
        try:
            event = json.loads(encoded_event)
        except (TypeError, ValueError):
            return None
        return event if isinstance(event, dict) else None

    @staticmethod
    def _collect_usage(event: dict[str, Any], usage: dict[str, Any]) -> None:
        event_usage = event.get("usage")
        if isinstance(event_usage, dict):
            usage.update(event_usage)
        message = event.get("message")
        message_usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(message_usage, dict):
            usage.update(message_usage)

    @staticmethod
    def _text_delta(event: dict[str, Any]) -> str | None:
        delta = event.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
            return None
        text = delta.get("text")
        return text if isinstance(text, str) else None

    @staticmethod
    async def _publish_deltas(
        extractor: StreamingJsonFieldExtractor,
        text_delta: str,
        callbacks: StreamFieldCallbacks,
        emitted_fields: set[str],
        usage_invocation: DeferredUsageInvocation,
    ) -> None:
        for field_name, field_delta in extractor.feed(text_delta):
            if field_delta and field_delta not in {"\0", "\1"}:
                emitted_fields.add(field_name)
            await callbacks[field_name](field_delta)
            usage_invocation.start()
