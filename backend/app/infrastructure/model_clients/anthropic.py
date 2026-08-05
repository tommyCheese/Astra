import json
import logging
import time
from typing import Any

import httpx

from app.domain.agent_profile import ModelOperation
from app.infrastructure.model_clients.contracts import (
    AnswerDeltaCallback,
    DeferredUsageInvocation,
    ModelOutputError,
    StreamFieldCallbacks,
)
from app.infrastructure.model_clients.normalization import parse_json_object
from app.infrastructure.model_clients.openai_compatible import OpenAICompatibleModelClient
from app.infrastructure.model_clients.reasoning import (
    ModelReasoningConfig,
    attach_reasoning_usage,
    resolve_model_reasoning,
)
from app.infrastructure.model_clients.transports.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTransport,
)

logger = logging.getLogger("astra.model")


class AnthropicModelClient(OpenAICompatibleModelClient):
    """Anthropic Messages API adapter preserving Astra's structured model contract."""

    async def _chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        operation: ModelOperation,
        attempt: int = 0,
        stream_field: str | None = None,
        on_field_delta: AnswerDeltaCallback | None = None,
        stream_callbacks: StreamFieldCallbacks | None = None,
        usage_operation: str | None = None,
    ) -> dict[str, Any]:
        reasoning_config = self._resolve_reasoning(operation)
        started = time.perf_counter()
        usage_invocation = self._usage_invocation(operation, usage_operation, attempt)
        callbacks = dict(stream_callbacks or {})
        emitted_stream_fields: frozenset[str] = frozenset()
        if stream_field and on_field_delta:
            callbacks[stream_field] = on_field_delta
        thinking_notifier = self._model_thinking_notifier(operation, attempt, reasoning_config)
        try:
            request = AnthropicRequest(
                url=self.settings.model_base_url.rstrip("/") + "/messages",
                api_key=self.settings.model_api_key,
                model=self.settings.model_name,
                messages=messages,
                reasoning=reasoning_config,
                callbacks=callbacks,
                thinking_callback=thinking_notifier.callback,
            )

            response = await AnthropicTransport(self._client()).send(request, usage_invocation)
            await thinking_notifier.finish()
            emitted_stream_fields = response.emitted_fields
            if not response.content:
                raise ModelOutputError("Anthropic endpoint returned no text content")
            payload = parse_json_object(response.content)
            await self._finish_success(usage_invocation, response, reasoning_config, started)
            return payload
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, ModelOutputError) as exc:
            await thinking_notifier.finish(failed=True)
            return await self._handle_chat_error(
                exc=exc,
                messages=messages,
                operation=operation,
                attempt=attempt,
                emitted_stream_fields=emitted_stream_fields,
                stream_field=stream_field,
                on_field_delta=on_field_delta,
                stream_callbacks=stream_callbacks,
                usage_operation=usage_operation,
                usage_invocation=usage_invocation,
                reasoning_config=reasoning_config,
                started=started,
            )

    def _resolve_reasoning(self, operation: ModelOperation) -> ModelReasoningConfig:
        return resolve_model_reasoning(
            provider=self.settings.model_provider,
            model=self.settings.model_name,
            effort=self.reasoning_effort,
            operation=operation,
            thinking=self.model_thinking,
        )

    def _usage_invocation(
        self,
        operation: ModelOperation,
        usage_operation: str | None,
        attempt: int,
    ) -> DeferredUsageInvocation:
        return DeferredUsageInvocation(
            self.usage_recorder,
            provider=self.settings.model_provider,
            model=self.settings.model_name,
            operation=usage_operation or operation.value,
            attempt=attempt + 1,
        )

    async def _finish_success(
        self,
        usage_invocation: DeferredUsageInvocation,
        response: AnthropicResponse,
        reasoning_config: ModelReasoningConfig,
        started: float,
    ) -> None:
        if self.usage_recorder is None:
            return
        await self.usage_recorder.finish(
            await usage_invocation.resolve(),
            status="succeeded",
            duration_ms=round((time.perf_counter() - started) * 1000),
            request_id=response.request_id,
            usage=attach_reasoning_usage(response.usage, reasoning_config),
        )

    async def _handle_chat_error(
        self,
        *,
        exc: Exception,
        messages: list[dict[str, str]],
        operation: ModelOperation,
        attempt: int,
        emitted_stream_fields: frozenset[str],
        stream_field: str | None,
        on_field_delta: AnswerDeltaCallback | None,
        stream_callbacks: StreamFieldCallbacks | None,
        usage_operation: str | None,
        usage_invocation: DeferredUsageInvocation,
        reasoning_config: ModelReasoningConfig,
        started: float,
    ) -> dict[str, Any]:
        if self.usage_recorder is not None:
            await self.usage_recorder.finish(
                await usage_invocation.resolve(),
                status="failed",
                duration_ms=round((time.perf_counter() - started) * 1000),
                usage=attach_reasoning_usage(None, reasoning_config),
                error=exc,
            )
        if (
            attempt == 0
            and "summary" not in emitted_stream_fields
            and not isinstance(exc, httpx.HTTPError)
        ):
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": "Return only one valid JSON object matching the requested schema.",
                },
            ]
            return await self._chat_json(
                retry_messages,
                operation=operation,
                attempt=1,
                stream_field=stream_field,
                on_field_delta=on_field_delta,
                stream_callbacks=stream_callbacks,
                usage_operation=usage_operation,
            )
        if isinstance(exc, httpx.HTTPStatusError):
            raise ModelOutputError(
                f"Model endpoint returned HTTP {exc.response.status_code}"
            ) from exc
        if isinstance(exc, ModelOutputError):
            raise exc
        raise ModelOutputError("Anthropic returned non-JSON content") from exc
