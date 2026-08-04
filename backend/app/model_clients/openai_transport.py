from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agent_profile import ModelOperation
from app.model_clients.contracts import (
    DeferredUsageInvocation,
    ModelOutputError,
    StreamFieldCallbacks,
)
from app.model_clients.response_parsing import StreamingJsonFieldExtractor
from app.runner.model_reasoning import ModelReasoningConfig, attach_reasoning_usage

logger = logging.getLogger("astra.model")


@dataclass(frozen=True)
class OpenAIChatRequest:
    url: str
    provider: str
    model: str
    api_key: str
    operation: ModelOperation
    messages: list[dict[str, str]]
    reasoning: ModelReasoningConfig
    callbacks: StreamFieldCallbacks = field(default_factory=dict)


@dataclass(frozen=True)
class OpenAIChatResponse:
    content: str
    request_id: str | None
    usage: dict[str, Any] | None
    emitted_fields: frozenset[str]
    status_code: int
    chunk_count: int


class OpenAIChatTransport:
    """Own the OpenAI-compatible HTTP and SSE protocol, not model semantics."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def send(
        self,
        request: OpenAIChatRequest,
        usage_invocation: DeferredUsageInvocation,
    ) -> OpenAIChatResponse:
        started = time.perf_counter()
        logger.info(
            "model.request.start operation=%s provider=%s model=%s endpoint=%s messages=%s",
            request.operation,
            request.provider,
            request.model,
            request.url,
            len(request.messages),
        )
        async with self.client.stream(
            "POST",
            request.url,
            headers=self._headers(request.api_key),
            json=self._payload(request),
        ) as response:
            request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await self._finish_failed_request(
                    usage_invocation, request, started, request_id, exc
                )
                raise ModelOutputError(
                    f"Model endpoint returned HTTP {response.status_code}"
                ) from exc
            parsed = await self._read_response(response, request.callbacks, usage_invocation)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "model.request.complete operation=%s status=%s chunks=%s content_chars=%s duration_ms=%.1f",
            request.operation,
            response.status_code,
            parsed.chunk_count,
            len(parsed.content),
            elapsed_ms,
        )
        return OpenAIChatResponse(
            content=parsed.content,
            request_id=request_id,
            usage=parsed.usage,
            emitted_fields=parsed.emitted_fields,
            status_code=response.status_code,
            chunk_count=parsed.chunk_count,
        )

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
        }

    @staticmethod
    def _payload(request: OpenAIChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **request.reasoning.request_params,
        }
        if request.provider == "openai":
            system_prompt = "\n\n".join(
                message["content"] for message in request.messages if message["role"] == "system"
            )
            if system_prompt:
                digest = hashlib.sha256(system_prompt.encode()).hexdigest()[:32]
                payload["prompt_cache_key"] = f"astra:{digest}"
        if request.reasoning.include_json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _read_response(
        self,
        response: httpx.Response,
        callbacks: StreamFieldCallbacks,
        usage_invocation: DeferredUsageInvocation,
    ) -> OpenAIChatResponse:
        if "text/event-stream" in response.headers.get("content-type", ""):
            return await self._read_event_stream(response, callbacks, usage_invocation)
        return await self._read_json_response(response)

    async def _read_event_stream(
        self,
        response: httpx.Response,
        callbacks: StreamFieldCallbacks,
        usage_invocation: DeferredUsageInvocation,
    ) -> OpenAIChatResponse:
        chunks: list[str] = []
        usage: dict[str, Any] | None = None
        emitted_fields: set[str] = set()
        extractor = StreamingJsonFieldExtractor(callbacks) if callbacks else None
        async for line in response.aiter_lines():
            event = self._parse_sse_line(line)
            if event is None:
                continue
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            content_delta = self._content_delta(event)
            if not content_delta:
                continue
            chunks.append(content_delta)
            if extractor is not None:
                await self._publish_field_deltas(
                    extractor, content_delta, callbacks, emitted_fields, usage_invocation
                )
        return OpenAIChatResponse(
            content="".join(chunks),
            request_id=None,
            usage=usage,
            emitted_fields=frozenset(emitted_fields),
            status_code=response.status_code,
            chunk_count=len(chunks),
        )

    @staticmethod
    def _parse_sse_line(line: str) -> dict[str, Any] | None:
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
    def _content_delta(event: dict[str, Any]) -> str | None:
        try:
            delta = event["choices"][0]["delta"].get("content")
        except (KeyError, IndexError, TypeError):
            return None
        return delta if isinstance(delta, str) else None

    @staticmethod
    async def _publish_field_deltas(
        extractor: StreamingJsonFieldExtractor,
        content_delta: str,
        callbacks: StreamFieldCallbacks,
        emitted_fields: set[str],
        usage_invocation: DeferredUsageInvocation,
    ) -> None:
        for field_name, field_delta in extractor.feed(content_delta):
            if field_delta and field_delta not in {"\0", "\1"}:
                emitted_fields.add(field_name)
            await callbacks[field_name](field_delta)
            usage_invocation.start()

    @staticmethod
    async def _read_json_response(response: httpx.Response) -> OpenAIChatResponse:
        try:
            body = json.loads((await response.aread()).decode())
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else None
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not text")
        except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ModelOutputError("Model endpoint returned an unsupported response shape") from exc
        return OpenAIChatResponse(
            content=content,
            request_id=None,
            usage=usage,
            emitted_fields=frozenset(),
            status_code=response.status_code,
            chunk_count=1,
        )

    @staticmethod
    async def _finish_failed_request(
        usage_invocation: DeferredUsageInvocation,
        request: OpenAIChatRequest,
        started: float,
        request_id: str | None,
        error: Exception,
    ) -> None:
        recorder = usage_invocation.recorder
        if recorder is None:
            return
        await recorder.finish(
            await usage_invocation.resolve(),
            status="failed",
            duration_ms=round((time.perf_counter() - started) * 1000),
            request_id=request_id,
            usage=attach_reasoning_usage(None, request.reasoning),
            error=error,
        )
