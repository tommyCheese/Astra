"""Language-neutral, capability-limited transport for isolated Tool Providers."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.infrastructure.plugins.interfaces import (
    PluginHealthProbe,
    PluginHealthReport,
    RuntimeBackend,
)
from app.infrastructure.tools.base import (
    AstraToolSpec,
    ToolExecutionContext,
    ToolExecutionError,
)

ISOLATED_TRANSPORT_PROTOCOL_VERSION = "1"


class IsolatedExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    tool_call_id: str
    trace_id: str
    permissions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    network_allowed: bool = False
    credential_references: tuple[str, ...] = ()


class IsolatedInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1"] = ISOLATED_TRANSPORT_PROTOCOL_VERSION
    request_id: str
    provider_id: str
    tool_name: str
    tool_version: str
    input: dict[str, Any]
    context: IsolatedExecutionContext


class IsolatedInvocationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)


class IsolatedInvocationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1"] = ISOLATED_TRANSPORT_PROTOCOL_VERSION
    request_id: str
    provider_id: str
    tool_name: str
    status: Literal["succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: IsolatedInvocationError | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.status == "succeeded" and (self.result is None or self.error is not None):
            raise ValueError("successful isolated response requires only a result")
        if self.status == "failed" and (self.error is None or self.result is not None):
            raise ValueError("failed isolated response requires only a safe error")


class IsolatedHealthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1"] = ISOLATED_TRANSPORT_PROTOCOL_VERSION
    provider_id: str


class IsolatedHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1"] = ISOLATED_TRANSPORT_PROTOCOL_VERSION
    provider_id: str
    healthy: bool
    reason: str | None = Field(default=None, max_length=240)


class IsolatedCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1"] = ISOLATED_TRANSPORT_PROTOCOL_VERSION
    provider_id: str
    request_id: str


class IsolatedProviderTransport(ABC):
    """A JSON-only boundary implementable by a process, container, or remote service."""

    @abstractmethod
    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def cancel(self, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    async def health(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class IsolatedRuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_time_seconds: float = Field(default=20.0, gt=0, le=300)
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    max_concurrency: int = Field(default=4, ge=1, le=64)
    network_allowed: bool = False
    credential_references: tuple[str, ...] = ()


class IsolatedProviderRuntimeBackend(RuntimeBackend, PluginHealthProbe):
    def __init__(
        self,
        provider_id: str,
        transport: IsolatedProviderTransport,
        policy: IsolatedRuntimePolicy | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.transport = transport
        self.policy = policy or IsolatedRuntimePolicy()
        self._semaphore = asyncio.Semaphore(self.policy.max_concurrency)

    async def execute(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        *,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        if spec.provider_id != self.provider_id:
            raise ToolExecutionError(
                "isolated_provider_mismatch", "Isolated provider identity does not match"
            )
        if context is None:
            raise ToolExecutionError(
                "isolated_context_missing", "Isolated execution requires an audited context"
            )
        request = IsolatedInvocationRequest(
            request_id=f"isolated_{uuid4().hex}",
            provider_id=self.provider_id,
            tool_name=spec.name,
            tool_version=spec.version,
            input=tool_input,
            context=IsolatedExecutionContext(
                run_id=context.run_id,
                tool_call_id=context.tool_call_id,
                trace_id=context.trace_id,
                permissions=tuple(spec.permissions),
                capabilities=tuple(spec.capabilities),
                network_allowed=self.policy.network_allowed,
                credential_references=self.policy.credential_references,
            ),
        )
        try:
            raw = await asyncio.wait_for(
                self._invoke(request),
                timeout=self.policy.wall_time_seconds,
            )
        except TimeoutError as exc:
            await self._cancel(request.request_id)
            raise ToolExecutionError(
                "isolated_timeout", "Isolated provider exceeded its wall-time limit"
            ) from exc
        except asyncio.CancelledError:
            await asyncio.shield(self._cancel(request.request_id))
            raise
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                "isolated_provider_crash", "Isolated provider transport failed"
            ) from exc
        self._enforce_response_size(raw)
        try:
            response = IsolatedInvocationResponse.model_validate(raw)
        except (TypeError, ValueError, ValidationError) as exc:
            raise ToolExecutionError(
                "isolated_protocol_invalid", "Isolated provider returned an invalid response"
            ) from exc
        if (
            response.request_id != request.request_id
            or response.provider_id != self.provider_id
            or response.tool_name != spec.name
        ):
            raise ToolExecutionError(
                "isolated_identity_forged", "Isolated provider response identity is invalid"
            )
        if response.status == "failed":
            assert response.error is not None
            raise ToolExecutionError(response.error.category, response.error.message)
        assert response.result is not None
        return response.result

    async def check(self) -> PluginHealthReport:
        request = IsolatedHealthRequest(provider_id=self.provider_id)
        try:
            raw = await asyncio.wait_for(
                self.transport.health(request.model_dump(mode="json")),
                timeout=min(self.policy.wall_time_seconds, 10.0),
            )
            self._enforce_response_size(raw)
            response = IsolatedHealthResponse.model_validate(raw)
            if response.provider_id != self.provider_id:
                return PluginHealthReport(False, "health_identity_mismatch")
            return PluginHealthReport(response.healthy, response.reason)
        except Exception:
            return PluginHealthReport(False, "health_check_failed")

    async def _invoke(self, request: IsolatedInvocationRequest) -> dict[str, Any]:
        async with self._semaphore:
            return await self.transport.invoke(request.model_dump(mode="json"))

    async def _cancel(self, request_id: str) -> None:
        request = IsolatedCancellationRequest(
            provider_id=self.provider_id,
            request_id=request_id,
        )
        try:
            await asyncio.wait_for(
                self.transport.cancel(request.model_dump(mode="json")),
                timeout=min(self.policy.wall_time_seconds, 5.0),
            )
        except Exception:
            return

    def _enforce_response_size(self, payload: Any) -> None:
        try:
            size = len(json.dumps(payload, separators=(",", ":")).encode())
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError(
                "isolated_protocol_invalid", "Isolated provider returned non-JSON data"
            ) from exc
        if size > self.policy.max_response_bytes:
            raise ToolExecutionError(
                "isolated_response_too_large", "Isolated provider response exceeds its limit"
            )
