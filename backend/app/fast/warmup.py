"""
Worker start-up checks for fast_ocr. Fail early and loudly, with the name of
what is missing, instead of discovering it in the middle of a job.
"""
from __future__ import annotations

import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StartupCheckError(RuntimeError):
    pass


def check_tesseract() -> str:
    from app.core.model_registry import get_tesseract_engine
    eng = get_tesseract_engine()
    if not getattr(eng, "_is_loaded", True):
        eng.load_model()
    version = eng.engine_version
    if version == "unknown":
        raise StartupCheckError("Tesseract binary not found or not runnable (set TESSERACT_CMD).")
    return version


def check_tatr_weights() -> str | None:
    """Only needed when the TATR fallback for borderless tables is switched on."""
    if not settings.FAST_TATR_FALLBACK_ENABLED:
        return None
    root = Path(settings.model_cache_path)
    found = any(
        p.exists() and any(p.rglob("*table-transformer-structure-recognition*"))
        for p in (root, root / "huggingface")
    )
    if not found:
        raise StartupCheckError(
            "FAST_TATR_FALLBACK_ENABLED=true but the table-transformer-structure-recognition weights "
            "are not in MODEL_CACHE_DIR/huggingface. Run scripts/download_all_models.py or disable the fallback."
        )
    return "table-transformer-structure-recognition"


def warm_vlm() -> bool:
    """Load the vision model into Ollama now (keep_alive), so the first repair is not a cold start."""
    if not settings.FAST_VLM_FALLBACK_ENABLED:
        return False
    try:
        from ollama import Client
        t0 = time.perf_counter()
        Client(host=settings.OLLAMA_BASE_URL.rstrip("/"), timeout=settings.FAST_VLM_TIMEOUT_S).chat(
            model=settings.AI_MODEL, messages=[{"role": "user", "content": "ok"}],
            options={"num_predict": 1, "num_ctx": 512}, keep_alive=settings.FAST_VLM_KEEP_ALIVE,
        )
        logger.info("fast_vlm_warm", model=settings.AI_MODEL, ms=int((time.perf_counter() - t0) * 1000))
        return True
    except Exception as exc:
        logger.warning("fast_vlm_warm_failed", error=str(exc), note="VLM repair will be skipped until Ollama answers")
        return False


def run_startup_checks(vlm: bool = True) -> dict:
    """vlm=False skips the (slow) model warm-up so the caller can run it in the background."""
    report = {"tesseract": check_tesseract(), "tatr": check_tatr_weights(), "vlm_warm": warm_vlm() if vlm else "background"}
    try:
        import sys
        torch = sys.modules.get("torch")          # do not import torch here: it costs seconds before the worker registers
        report["cuda_available"] = bool(torch.cuda.is_available()) if torch is not None else None
    except Exception:
        report["cuda_available"] = None
    logger.info("fast_startup_ok", **report)
    return report
