"""
Phase 12 — the GPU inference concurrency guard (app/core/concurrency.py).

Pure-logic tests: no real GPU/model calls, just the semaphore mechanics and
its no-op-by-default behavior.
"""
import threading
import time

from app.core.config import settings
import app.core.concurrency as concurrency_module
from app.core.concurrency import gpu_inference_slot


def _reset_semaphore_cache():
    concurrency_module._semaphore = None
    concurrency_module._semaphore_limit = None


def test_default_setting_is_a_complete_noop():
    # GPU_INFERENCE_CONCURRENCY defaults to 0 — today's behavior (unbounded)
    # must be unchanged: the context manager must not block at all.
    assert settings.GPU_INFERENCE_CONCURRENCY == 0
    entered = []

    def worker():
        with gpu_inference_slot():
            entered.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    assert len(entered) == 20


def test_configured_limit_actually_bounds_concurrency(monkeypatch):
    monkeypatch.setattr(settings, "GPU_INFERENCE_CONCURRENCY", 2)
    _reset_semaphore_cache()
    try:
        max_concurrent = {"value": 0}
        current = {"value": 0}
        lock = threading.Lock()

        def worker():
            with gpu_inference_slot():
                with lock:
                    current["value"] += 1
                    max_concurrent["value"] = max(max_concurrent["value"], current["value"])
                time.sleep(0.05)
                with lock:
                    current["value"] -= 1

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert max_concurrent["value"] <= 2
    finally:
        _reset_semaphore_cache()


def test_semaphore_is_released_even_if_the_wrapped_call_raises(monkeypatch):
    monkeypatch.setattr(settings, "GPU_INFERENCE_CONCURRENCY", 1)
    _reset_semaphore_cache()
    try:
        try:
            with gpu_inference_slot():
                raise RuntimeError("simulated model failure")
        except RuntimeError:
            pass

        # If the slot wasn't released, this would deadlock (timeout) rather
        # than complete — so we bound the wait and assert it finished.
        acquired = threading.Event()

        def worker():
            with gpu_inference_slot():
                acquired.set()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2)
        assert acquired.is_set()
    finally:
        _reset_semaphore_cache()
