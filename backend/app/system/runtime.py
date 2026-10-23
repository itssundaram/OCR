"""
DOCINT - runtime profile: detect once, derive settings, apply the ones nobody set explicitly.

``apply_runtime_profile`` only touches a setting the operator did NOT set in the environment or
.env (pydantic's ``model_fields_set``), so a hand-tuned deployment keeps working exactly as before.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.system.hardware import HardwareProfile, detect_hardware
from app.system.profile_rules import RuntimeProfile, derive_runtime_profile

logger = get_logger(__name__)

# rule key -> settings attribute (+ optional converter)
SETTINGS_MAP: dict[str, tuple[str, Any]] = {
    "device": ("DEVICE", lambda v: v),
    "gpu_inference_concurrency": ("GPU_INFERENCE_CONCURRENCY", int),
    "ollama_num_ctx": ("OLLAMA_NUM_CTX", int),
    "cpu_workers": ("WORKER_COUNT", int),
    "page_threads": ("FAST_PAGE_THREADS", int),
}

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def get_runtime(refresh: bool = False, *, smoke: bool = False, probe_services: bool = True
                ) -> tuple[HardwareProfile, RuntimeProfile]:
    with _lock:
        if refresh or "hw" not in _cache:
            hw = detect_hardware(smoke=smoke, probe_services=probe_services, data_dir=settings.storage_path)
            _cache["hw"] = hw
            _cache["rp"] = derive_runtime_profile(hw, env=os.environ)
        return _cache["hw"], _cache["rp"]


def apply_runtime_profile(rp: RuntimeProfile | None = None) -> dict[str, Any]:
    """Set derived values on `settings` unless the operator set them. Returns what was applied."""
    if rp is None:
        _, rp = get_runtime()
    applied: dict[str, Any] = {}
    explicit = set(getattr(settings, "model_fields_set", set()))
    for key, (attr, conv) in SETTINGS_MAP.items():
        if key not in rp.values or attr in explicit:
            continue
        try:
            val = conv(rp.values[key])
            setattr(settings, attr, val)
            applied[attr] = val
        except Exception as exc:
            logger.warning("runtime_apply_failed", setting=attr, error=str(exc))
    # device also drives the GPU flags and the Surya device when they were left at their defaults
    dev = rp.values.get("device")
    if dev == "cuda":
        for attr, val in (("GPU_ENABLED", True), ("SURYA_DEVICE", "cuda")):
            if attr not in explicit:
                setattr(settings, attr, val)
                applied[attr] = val
    # One Tesseract thread per process: parallelism comes from processes (set before any subprocess starts).
    if "OMP_THREAD_LIMIT" not in os.environ and rp.values.get("tesseract_threads"):
        os.environ["OMP_THREAD_LIMIT"] = str(int(rp.values["tesseract_threads"]))
        applied["OMP_THREAD_LIMIT"] = os.environ["OMP_THREAD_LIMIT"]
    logger.info("runtime_profile_applied", fingerprint=rp.fingerprint, applied=applied,
                kept_explicit=sorted(a for a, _ in SETTINGS_MAP.values() if a in explicit))
    return applied


def configure_process(role: str) -> None:
    """Call once at process start (API / worker) BEFORE models load. Never raises."""
    try:
        hw, rp = get_runtime()
        apply_runtime_profile(rp)
        for w in rp.warnings:
            logger.warning("runtime_warning", role=role, message=w)
        logger.info("runtime_ready", role=role, fingerprint=rp.fingerprint, device=rp.values.get("device"),
                    vram_budget_mb=rp.values.get("vram_budget_mb"), cpu_workers=rp.values.get("cpu_workers"))
    except Exception as exc:  # hardware detection must never stop the app from starting
        logger.warning("runtime_configure_failed", role=role, error=str(exc))
