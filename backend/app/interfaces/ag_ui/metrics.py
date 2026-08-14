from __future__ import annotations

import logging
from collections import Counter
from threading import Lock

from app.interfaces.ag_ui.compatibility import ASTRA_AG_UI_PROFILE_VERSION

logger = logging.getLogger("astra.ag_ui")


class AgUiMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: Counter[str] = Counter()

    def increment(self, name: str, *, event_type: str | None = None) -> None:
        with self._lock:
            self._counters[name] += 1
            if event_type:
                self._counters[f"{name}:{event_type}"] += 1
        logger.info(
            "ag_ui.metric profile=%s metric=%s event_type=%s",
            ASTRA_AG_UI_PROFILE_VERSION,
            name,
            event_type or "none",
        )

    def gauge(self, name: str, delta: int) -> None:
        with self._lock:
            self._counters[name] += delta

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = dict(self._counters)
        return {"profileVersion": ASTRA_AG_UI_PROFILE_VERSION, "counters": counters}


ag_ui_metrics = AgUiMetrics()
