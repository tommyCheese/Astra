"""Safe in-process plugin diagnostics used by logs, tests, and metrics adapters."""

from __future__ import annotations

import logging
from collections import Counter
from threading import Lock
from time import perf_counter
from typing import Any

logger = logging.getLogger("astra.plugins")


class PluginDiagnostics:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._durations_ms: Counter[str] = Counter()
        self._lock = Lock()

    def record(
        self,
        event: str,
        *,
        duration_ms: float | None = None,
        level: int = logging.INFO,
        **dimensions: Any,
    ) -> None:
        safe_dimensions = {
            key: value
            for key, value in dimensions.items()
            if key in {"provider_id", "stage", "category", "state", "tool_name"}
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        with self._lock:
            self._counts[event] += 1
            if duration_ms is not None:
                self._durations_ms[event] += round(duration_ms, 3)
        suffix = " ".join(f"{key}={value}" for key, value in sorted(safe_dimensions.items()))
        logger.log(level, "plugin.%s%s", event, f" {suffix}" if suffix else "")

    def timer(self) -> float:
        return perf_counter()

    def elapsed_ms(self, started: float) -> float:
        return (perf_counter() - started) * 1000

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            return {
                "counts": dict(sorted(self._counts.items())),
                "duration_ms": dict(sorted(self._durations_ms.items())),
            }

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._durations_ms.clear()


plugin_diagnostics = PluginDiagnostics()
