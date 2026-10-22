from app.pipelines import surya_pipeline, tesseract_pipeline, learned_anchor_pipeline  # noqa: F401
from app.fast import pipeline as fast_pipeline  # noqa: F401  (registers "fast_ocr")

__all__ = ["surya_pipeline", "tesseract_pipeline", "learned_anchor_pipeline", "fast_pipeline"]
