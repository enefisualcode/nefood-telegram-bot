"""Phase 3F.1: lightweight performance instrumentation.

Two independent, deliberately tiny pieces:

- StageTimer: collects named monotonic-clock durations for one request
  (photo download, preprocessing, Gemini, decomposition, ...) and renders a
  concise one-line summary for the logs.
- DurationTracker: remembers a short in-memory history of recent
  *successful* total-analysis durations, so the bot can show the user a
  rough "usually takes about N-M seconds" range instead of either silence
  or a made-up number.

Neither of these ever logs or stores image bytes, the Gemini/Telegram/USDA
API keys, or anything persisted to disk - it's all in-process, and gone the
moment the bot restarts.
"""

from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager


class StageTimer:
    """Records named stage durations for one operation, in the order they occur."""

    def __init__(self):
        self._stages: list[tuple[str, float]] = []
        self._start = time.perf_counter()

    @contextmanager
    def stage(self, name: str):
        """Time one named stage. Safe to use even if the stage body raises -
        the duration up to the failure is still recorded, so a partial
        summary is available in the logs for debugging."""
        start = time.perf_counter()
        try:
            yield
        finally:
            self._stages.append((name, time.perf_counter() - start))

    @property
    def stages(self) -> list[tuple[str, float]]:
        return list(self._stages)

    @property
    def total(self) -> float:
        return time.perf_counter() - self._start

    def summary(self, total_label: str = "total") -> str:
        """A concise, grep-friendly one-liner: 'stage=0.12s stage2=1.30s total=1.42s'."""
        parts = [f"{name}={duration:.2f}s" for name, duration in self._stages]
        parts.append(f"{total_label}={self.total:.2f}s")
        return " ".join(parts)


# --------------------------------------------------------------------------
# Recent-duration tracking, for the "biasanya sekitar N-M detik" estimate.
# --------------------------------------------------------------------------

MIN_SAMPLES_FOR_ESTIMATE = 3
MAX_HISTORY = 20

NO_ESTIMATE_MESSAGE = "Mohon tunggu sebentar."


class DurationTracker:
    """A small, bounded, in-memory history of recent successful durations.

    Only ever holds plain floats (seconds) - no per-user data, no
    persistence. A failed analysis must never be recorded here (see
    `record`'s caller in bot.py, which only calls this after a full
    successful result was produced) so a string of timeouts can't corrupt
    the estimate shown to the next user.
    """

    def __init__(self, max_history: int = MAX_HISTORY, min_samples: int = MIN_SAMPLES_FOR_ESTIMATE):
        self._durations: deque[float] = deque(maxlen=max_history)
        self._min_samples = min_samples

    def record(self, seconds: float) -> None:
        if seconds is not None and seconds > 0:
            self._durations.append(float(seconds))

    @property
    def sample_count(self) -> int:
        return len(self._durations)

    @property
    def has_enough_history(self) -> bool:
        return self.sample_count >= self._min_samples

    def estimate_range(self) -> tuple[float, float] | None:
        """A rough (low, high) range in seconds from recent samples.

        None when there isn't enough history yet - the caller must show a
        plain "please wait" instead of fabricating a number.
        """
        if not self.has_enough_history:
            return None
        return (min(self._durations), max(self._durations))

    def estimate_message(self) -> str:
        """User-facing Indonesian text - never a promise of an exact time."""
        estimate = self.estimate_range()
        if estimate is None:
            return NO_ESTIMATE_MESSAGE
        low, high = estimate
        low_s, high_s = round(low), round(high)
        if low_s == high_s:
            return f"⏱️ Biasanya sekitar {low_s} detik."
        return f"⏱️ Biasanya sekitar {low_s}–{high_s} detik."


_default_tracker: DurationTracker | None = None


def get_tracker() -> DurationTracker:
    """One tracker per bot process - deliberately not persisted."""
    global _default_tracker
    if _default_tracker is None:
        _default_tracker = DurationTracker()
    return _default_tracker
