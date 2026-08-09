from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from app.infrastructure.plugins.contracts import PluginContribution, PluginDescriptor
from app.infrastructure.plugins.interfaces import ToolProviderPlugin

PluginLoader = Callable[[], ToolProviderPlugin | PluginContribution]


@dataclass(frozen=True)
class PluginCandidate:
    descriptor: PluginDescriptor
    load: PluginLoader
    observed_digest: str


class PluginDiscoverySource(ABC):
    """A configured source; discovery never receives a Task Workspace path."""

    @abstractmethod
    def discover(self) -> tuple[PluginCandidate, ...]: ...


class BuiltinDiscoverySource(PluginDiscoverySource):
    def __init__(self, plugins: Iterable[ToolProviderPlugin | PluginContribution]):
        self._plugins = tuple(plugins)

    def discover(self) -> tuple[PluginCandidate, ...]:
        candidates = []
        for plugin in self._plugins:
            descriptor = plugin.descriptor
            if descriptor.source != "builtin":
                raise ValueError("Builtin discovery accepts only builtin plugin descriptors")
            candidates.append(
                PluginCandidate(
                    descriptor=descriptor,
                    load=lambda item=plugin: item,
                    observed_digest=descriptor.digest,
                )
            )
        return tuple(candidates)


@dataclass(frozen=True)
class ManagedPackageReference:
    descriptor: PluginDescriptor
    entry_point_name: str


class ManagedPackageDiscoverySource(PluginDiscoverySource):
    """Discover only administrator-pinned entry point names; loading happens after trust checks."""

    def __init__(
        self,
        references: Iterable[ManagedPackageReference],
        *,
        group: str = "astra.tool_provider_plugins",
        enabled: bool = False,
    ):
        self._references = tuple(references)
        self._group = group
        self._enabled = enabled

    def discover(self) -> tuple[PluginCandidate, ...]:
        if not self._enabled:
            return ()
        available = {item.name: item for item in metadata.entry_points(group=self._group)}
        candidates = []
        for reference in self._references:
            entry_point = available.get(reference.entry_point_name)
            if entry_point is None:
                continue
            candidates.append(
                PluginCandidate(
                    descriptor=reference.descriptor,
                    load=lambda item=entry_point: _load_entry_point(item),
                    observed_digest=_entry_point_digest(entry_point),
                )
            )
        return tuple(candidates)


@dataclass(frozen=True)
class IsolatedProviderReference:
    descriptor: PluginDescriptor
    contribution: PluginContribution


class IsolatedDescriptorDiscoverySource(PluginDiscoverySource):
    def __init__(self, references: Iterable[IsolatedProviderReference]):
        self._references = tuple(references)

    def discover(self) -> tuple[PluginCandidate, ...]:
        return tuple(
            PluginCandidate(
                descriptor=item.descriptor,
                load=lambda contribution=item.contribution: contribution,
                observed_digest=item.descriptor.digest,
            )
            for item in self._references
        )


def discover_candidates(
    sources: Iterable[PluginDiscoverySource],
) -> tuple[PluginCandidate, ...]:
    candidates = [candidate for source in sources for candidate in source.discover()]
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.descriptor.provider_id,
                item.descriptor.plugin_id,
                item.descriptor.version,
            ),
        )
    )


def _load_entry_point(entry_point: Any) -> ToolProviderPlugin | PluginContribution:
    loaded = entry_point.load()
    return loaded() if isinstance(loaded, type) else loaded


def _entry_point_digest(entry_point: Any) -> str:
    distribution = getattr(entry_point, "dist", None)
    if distribution is None:
        return "unverifiable"
    files = []
    for item in distribution.files or ():
        file_hash = getattr(item, "hash", None)
        if file_hash is not None:
            files.append(
                {
                    "path": str(item),
                    "mode": file_hash.mode,
                    "value": file_hash.value,
                }
            )
    if not files:
        return "unverifiable"
    payload = {
        "name": distribution.metadata.get("Name", ""),
        "version": distribution.version,
        "files": sorted(files, key=lambda item: item["path"]),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"
