"""
DOCINT — GPU inference concurrency guard (Phase 12 of the implementation plan)

Master prompt sections 19-20: "do not assume all models can remain loaded
simultaneously" / do not assume unlimited concurrent inference is safe on a
single GPU. Phase 24 (Performance) is where real VRAM/latency numbers get
measured on the actual deployment box; this module is only the MECHANISM
that acts on those numbers once they exist.

It ships with no limit applied by default (GPU_INFERENCE_CONCURRENCY=0, see
app/core/config.py), so today's throughput and behavior are unchanged until
someone sets a real, benchmarked value — per the plan's own instruction for
this phase: "benchmark before adding concurrency limits; don't guess them."
Guessing a number here (instead of leaving it a no-op) would be exactly the
kind of speculative plumbing the plan repeatedly warns against (see Phase 7,
15, 19-20's own "defer until there's a real need" notes).
"""
from __future__ import annotations

import threading

from app.core.config import settings

_lock = threading.Lock()
_semaphore: threading.Semaphore | None = None
_semaphore_limit: int | None = None


def _get_semaphore() -> threading.Semaphore | None:
    """Lazily builds (or rebuilds, if the configured limit changed) a
    process-wide semaphore bounding concurrent GPU-model calls. Returns None
    when GPU_INFERENCE_CONCURRENCY <= 0, meaning "no limit" — today's
    behavior, preserved by default."""
    global _semaphore, _semaphore_limit
    limit = settings.GPU_INFERENCE_CONCURRENCY
    if limit <= 0:
        return None
    with _lock:
        if _semaphore is None or _semaphore_limit != limit:
            _semaphore = threading.Semaphore(limit)
            _semaphore_limit = limit
        return _semaphore


class gpu_inference_slot:
    """Context manager: acquires one of GPU_INFERENCE_CONCURRENCY slots
    before a GPU-model call (layout detection, OCR, handwriting fallback,
    ...) and releases it after. A complete no-op when the setting is
    0/unset (see module docstring) — wrap a call site with this and nothing
    changes until the setting is actually tuned."""

    def __enter__(self) -> "gpu_inference_slot":
        self._sem = _get_semaphore()
        if self._sem is not None:
            self._sem.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._sem is not None:
            self._sem.release()
        return False
