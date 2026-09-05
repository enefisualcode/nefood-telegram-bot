"""Phase 3F.1: optional in-memory cache for repeated identical photos.

Keyed by a hash of the exact bytes sent to Gemini (i.e. after
preprocessing), so a resized copy is cached independently of whatever the
original upload looked like. Purely in-process and bounded in size: nothing
is written to disk, no image bytes are ever stored (only the small parsed
FoodAnalysis result), and the cache is gone the moment the bot restarts.

This is a plain best-effort speedup for the same photo appearing twice in
one process lifetime (e.g. an accidental resend) - it is not a substitute
for the USDA cache, and it does not change what gets analyzed or how.
"""

from __future__ import annotations

import hashlib

from services.food_vision import FoodAnalysis

MAX_ENTRIES = 32


class VisionCache:
    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._max_entries = max_entries
        self._store: dict[str, FoodAnalysis] = {}
        self._order: list[str] = []

    @staticmethod
    def key_for(image_bytes: bytes) -> str:
        """A content-addressed key - identical bytes always hash the same."""
        return hashlib.sha256(image_bytes).hexdigest()

    def get(self, key: str) -> FoodAnalysis | None:
        return self._store.get(key)

    def put(self, key: str, analysis: FoodAnalysis) -> None:
        if key in self._store:
            return
        self._store[key] = analysis
        self._order.append(key)
        while len(self._order) > self._max_entries:
            oldest = self._order.pop(0)
            self._store.pop(oldest, None)

    def __len__(self) -> int:
        return len(self._store)


_default_cache: VisionCache | None = None


def get_cache() -> VisionCache:
    """One cache per bot process - deliberately not persisted."""
    global _default_cache
    if _default_cache is None:
        _default_cache = VisionCache()
    return _default_cache
