"""
DOCINT — Process-Level Model Registry
======================================
Holds ONE instance of every heavy model (Surya layout, Surya OCR, TATR table,
Tesseract engine) that is shared across all jobs processed by the same worker
process.

How it works with RQ:
  • The RQ worker parent process calls `preload_all_models()` once before
    entering the job-dispatch loop.
  • When RQ forks a child for each job, the child inherits the parent's memory
    (copy-on-write), so the already-loaded model weights are immediately
    available — no reload needed per job.
  • The API server process does NOT call preload_all_models() — models are
    only needed in the worker process.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.config import settings

logger = get_logger(__name__)

# ── Singleton model instances (None until preload_all_models() is called) ─────
_surya_layout_engine = None
_surya_ocr_engine = None
_tatr_engine = None
_tesseract_engine = None

_models_loaded = False


def get_surya_layout_engine():
    """Return the singleton SuryaLayoutEngine, initializing if needed."""
    global _surya_layout_engine
    if _surya_layout_engine is None:
        from app.ocr.surya_layout_engine import SuryaLayoutEngine
        _surya_layout_engine = SuryaLayoutEngine()
    return _surya_layout_engine


def get_surya_ocr_engine():
    """Return the singleton SuryaOCREngine, initializing if needed."""
    global _surya_ocr_engine
    if _surya_ocr_engine is None:
        from app.ocr.surya_ocr_engine import SuryaOCREngine
        _surya_ocr_engine = SuryaOCREngine()
    return _surya_ocr_engine


def get_tatr_engine():
    """Return the singleton TATRTableEngine, initializing if needed."""
    global _tatr_engine
    if _tatr_engine is None:
        from app.ocr.tatr_engine import TATRTableEngine
        _tatr_engine = TATRTableEngine()
    return _tatr_engine


def get_tesseract_engine():
    """Return the singleton TesseractOCREngine, initializing if needed."""
    global _tesseract_engine
    if _tesseract_engine is None:
        from app.ocr.tesseract import TesseractOCREngine
        _tesseract_engine = TesseractOCREngine()
    return _tesseract_engine


def preload_all_models() -> None:
    """
    Eagerly load and warm-up all model weights into GPU/RAM.

    Called ONCE by run_worker.py before starting the RQ worker loop.
    The forked job subprocesses inherit the already-loaded models from the
    parent process via copy-on-write — zero reload cost per job.
    """
    global _models_loaded
    if _models_loaded:
        logger.info("model_registry_already_loaded_skip")
        return

    logger.info("model_registry_preloading_all_models")
    t_start = __import__("time").perf_counter()

    errors = []

    # ── Tesseract (always present, no GPU needed) ────────────────────────
    try:
        engine = get_tesseract_engine()
        logger.info("model_registry_tesseract_ready")
    except Exception as exc:
        logger.warning("model_registry_tesseract_failed", error=str(exc))
        errors.append(f"tesseract: {exc}")

    # ── Surya Layout ─────────────────────────────────────────────────────
    if settings.SURYA_LAYOUT_ENABLED:
        try:
            layout_engine = get_surya_layout_engine()
            layout_engine.load_model()
            logger.info("model_registry_surya_layout_ready")
        except Exception as exc:
            logger.warning("model_registry_surya_layout_failed", error=str(exc))
            errors.append(f"surya_layout: {exc}")

    # ── Surya OCR ────────────────────────────────────────────────────────
    try:
        ocr_engine = get_surya_ocr_engine()
        ocr_engine.load_model()
        logger.info("model_registry_surya_ocr_ready")
    except Exception as exc:
        logger.warning("model_registry_surya_ocr_failed", error=str(exc))
        errors.append(f"surya_ocr: {exc}")

    # ── TATR Table Transformer ────────────────────────────────────────────
    try:
        tatr = get_tatr_engine()
        tatr.load_model()
        logger.info("model_registry_tatr_ready")
    except Exception as exc:
        logger.warning("model_registry_tatr_failed", error=str(exc))
        errors.append(f"tatr: {exc}")

    elapsed = ((__import__("time").perf_counter() - t_start) * 1000)
    _models_loaded = True

    if errors:
        logger.warning(
            "model_registry_preload_partial",
            elapsed_ms=int(elapsed),
            failed=errors,
        )
    else:
        logger.info(
            "model_registry_preload_complete",
            elapsed_ms=int(elapsed),
        )
