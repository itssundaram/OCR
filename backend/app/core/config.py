"""
DOCINT — Centralized Application Configuration
All environment access is funnelled through this module.
No other module should call os.environ directly.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "DocInt"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_VERSION: str = "1.0.0"

    # ── API Server ────────────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # ── Oracle Database ───────────────────────────────────────────────────────
    ORACLE_HOST: str = "localhost"
    ORACLE_PORT: int = 1521
    ORACLE_SERVICE_NAME: str = "FREEPDB1"
    ORACLE_USER: str = "docint_user"
    ORACLE_PASSWORD: str = ""
    DATABASE_URL: str | None = Field(default=None)

    # ── Redis / Queue ─────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_QUEUE_NAME: str = "docint"
    JOB_TIMEOUT_SECONDS: int = 300         # Max seconds a Redis job may run

    # ── Storage ───────────────────────────────────────────────────────────────
    STORAGE_DIR: str = "./data/documents"
    RESULTS_DIR: str = "./data/results"
    THUMBNAILS_DIR: str = "./data/thumbnails"
    TEMP_DIR: str = "./data/temp"
    # Phase 3 (evidence assets): rendered page images and field/table crops.
    # Kept as their own settings — not derived from STORAGE_DIR — so a future
    # storage backend (NFS/S3/MinIO) can be swapped in per asset type if needed.
    PAGES_DIR: str = "./data/pages"
    CROPS_DIR: str = "./data/crops"
    LOG_DIR: str = "./data/logs"

    # Base directory for ALL downloaded ML model weights (Surya/HuggingFace,
    # and — reserved for later — PaddleOCR). The point of this setting: moving
    # from local CPU testing to the GPU server is a .env change only, never a
    # code change. Nothing in pipeline code ever hardcodes a cache path —
    # everything reads these settings (or the HF_HOME/TRANSFORMERS_CACHE env
    # vars this module sets from them at import time, below).
    MODEL_CACHE_DIR: str = "./data/models"
    # Optional explicit override if you want HuggingFace's cache somewhere
    # other than MODEL_CACHE_DIR/huggingface (e.g. a pre-existing shared cache
    # on the GPU server). Leave unset to use the MODEL_CACHE_DIR-derived default.
    HF_HOME_OVERRIDE: str | None = None
    # Reserved for the Paddle pipeline (not yet built — see the implementation
    # plan's Phase 7). Adding this now so that pipeline, whenever it's built,
    # has a configurable cache location from day one instead of needing a
    # follow-up config change.
    PADDLE_MODEL_DIR: str | None = None

    # ── OCR ───────────────────────────────────────────────────────────────────
    # Default matches the actually-deployed strategy (see .env). "surya_with_fallback"
    # is a valid opt-in value, not the default — do not flip this without updating .env
    # across every environment, since the two must always agree (Phase 1 audit, 2026-10-06).
    OCR_STRATEGY: Literal["tesseract_only", "surya", "surya_with_fallback"] = "surya"
    # Phase 4 of the implementation plan: app/pipelines/ now has real, working
    # QwenPipeline/LegacyOCRPipeline adapters registered behind this flag, but
    # services/extractor.py's live job flow does NOT consume them yet — that
    # cutover is a deliberately separate step (shadow-mode comparison against
    # current behavior first). Flipping this to True today does nothing yet.
    USE_PIPELINE_REGISTRY: bool = False

    # ── Preprocessing (Phase 5) ────────────────────────────────────────────────
    # Each stage can be disabled independently without a code change. All default
    # to True, matching the pipeline's existing always-on behavior exactly — set
    # one of these to False in .env only once you've confirmed it helps your
    # documents (master prompt section 5: "do not blindly apply every operation").
    PREPROCESS_ORIENTATION_ENABLED: bool = True
    PREPROCESS_DESKEW_ENABLED: bool = True
    PREPROCESS_CROP_BORDERS_ENABLED: bool = True

    # ── Confidence & field-level fallback (Phase 9) ────────────────────────────
    # Field-level pass/fail threshold (master prompt section 10: "not hardcoded
    # 90%"). Used to classify each extracted field as ok vs needs_review —
    # never applied at the whole-document level (section 9).
    CONFIDENCE_THRESHOLD: float = 0.75

    # Opt-in: when True, a future worker step may retry only the fields flagged
    # needs_review (weak OCR -> stronger OCR -> crop-only VLM) instead of the
    # whole document. Default False because the retry execution itself isn't
    # wired yet (needs Phase 16's bbox/crop evidence API) -- today this flag
    # only gates whether low-confidence fields get flagged, not re-extracted.
    FIELD_LEVEL_FALLBACK_ENABLED: bool = False

    # ── Redis/RQ job hardening (Phase 10) ──────────────────────────────────────
    # Bounded retry for transient infra failures only (Redis/DB connection
    # blips, timeouts) — never for deterministic errors (bad template,
    # unreadable document), which fail once and stay failed. See
    # app/services/extractor.py::_is_transient_error.
    JOB_MAX_RETRIES: int = 2
    JOB_RETRY_INTERVAL_SECONDS: int = 30

    # ── Parallel processing / GPU concurrency (Phase 12) ───────────────────────
    # Bounds how many threads may be inside a GPU-model call (Surya layout/OCR/
    # handwriting fallback) at once. 0 = unbounded (today's behavior, unchanged).
    # Per the plan's own instruction for this phase: do not guess a number here
    # — Phase 24 benchmarks real VRAM/latency on the actual deployment GPU and
    # that result is what should ever set this above 0.
    GPU_INFERENCE_CONCURRENCY: int = 0
    SURYA_LAYOUT_ENABLED: bool = True
    SURYA_LAYOUT_CONFIDENCE_THRESHOLD: float = 0.3
    HANDWRITING_FALLBACK_ENABLED: bool = True
    HANDWRITING_FALLBACK_THRESHOLD: float = 0.80
    LLM_CLEANING_ENABLED: bool = True
    LLM_CLEANING_THRESHOLD: float = 0.85
    SURYA_DEVICE: str = "cpu"
    # Path to the Tesseract binary. Defaults to the standard Windows install path.
    # Override via TESSERACT_CMD in .env if installed elsewhere.
    TESSERACT_CMD: str | None = None
    # Per-page OCR timeout in seconds (stdin pipe mode)
    TESSERACT_TIMEOUT: int = 30
    # Pages with fewer than this many extractable characters are treated as scanned.
    SCANNED_PAGE_TEXT_THRESHOLD: int = 50
    # DPI multiplier for PyMuPDF rendering (lower = faster; higher = more accurate for Tesseract)
    OCR_DPI_NATIVE: float = 1.5    # For text-native PDF pages (LLM vision only)
    OCR_DPI_SCANNED: float = 2.0   # For scanned/photographed pages (Tesseract reads these)
    # DPI for DIRECT_LLM_CALL path — Tesseract is skipped so we only need enough
    # resolution to fill LLM_IMAGE_MAX_PX. 1.0x (72 DPI) is enough for a 800px thumbnail.
    OCR_DPI_DIRECT_LLM: float = 1.0
    # Max parallel threads for per-page OCR (Tesseract is subprocess-based, GIL not an issue)
    OCR_MAX_WORKERS: int = 4
    # Skip img2table on native-text pages (PyMuPDF already has the text structure)
    IMG2TABLE_SKIP_NATIVE: bool = True
    # Max img2table passes per page: 1=bordered only, 2=+implicit, 3=+borderless
    IMG2TABLE_MAX_PASSES: int = 2

    # ── AI ────────────────────────────────────────────────────────────────────
    AI_PROVIDER: Literal["ollama", "transformers"] = "ollama"
    # Change AI_MODEL in .env to switch models — no code changes required.
    AI_MODEL: str = "llama3.2-vision"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # What to send to the LLM: text, image, or both
    LLM_PAYLOAD_MODE: Literal["text", "image", "both"] = "both"
    # Max pixel dimension for images sent to the vision model (smaller = faster tokens)
    LLM_IMAGE_MAX_PX: int = 800
    # Ollama generation options
    LLM_MAX_OUTPUT_TOKENS: int = 4096  # Cap on LLM response token length
    OLLAMA_NUM_PREDICT: int = 4096   # Max tokens to generate (aliased by LLM_MAX_OUTPUT_TOKENS)
    OLLAMA_NUM_CTX: int = 16384     # Context window size

    # Set ENABLE_OCR_CACHE=true to reuse stored OCR results on re-processing
    ENABLE_OCR_CACHE: bool = True

    # When True, Tesseract OCR and img2table are skipped entirely.
    # Images are sent directly to the Vision LLM. Requires LLM_PAYLOAD_MODE=image or both.
    DIRECT_LLM_CALL: bool = False

    # ── Hardware ──────────────────────────────────────────────────────────────
    GPU_ENABLED: bool = False
    DEVICE: Literal["cpu", "cuda", "mps"] = "cpu"

    # ── Processing ────────────────────────────────────────────────────────────
    MAX_UPLOAD_SIZE_MB: int = 50
    WORKER_COUNT: int = 1
    # Number of pages from the start of a document to process. 0 = all pages.
    MAX_PAGES_TO_PROCESS: int = 0

    # ── Smart Extraction ──────────────────────────────────────────────────────
    # If the fraction of fields found via vector search meets this threshold,
    # the LLM is skipped and extracted data is returned directly.
    DIRECT_RESPONSE_THRESHOLD: float = 0.75

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("DIRECT_LLM_CALL", mode="before")
    @classmethod
    def validate_direct_llm_payload(cls, v: bool, info) -> bool:
        """Guard against DIRECT_LLM_CALL=True with text-only payload mode.
        That combination would skip OCR (no text produced) and send no images,
        giving the LLM a completely empty prompt."""
        payload_mode = (info.data or {}).get("LLM_PAYLOAD_MODE", "both")
        if v and payload_mode == "text":
            raise ValueError(
                "DIRECT_LLM_CALL=True requires LLM_PAYLOAD_MODE=image or both. "
                "Cannot skip OCR while using text-only payload — the LLM would receive an empty prompt."
            )
        return v

    # ── Computed Properties ───────────────────────────────────────────────────

    @computed_field
    @property
    def database_url(self) -> str:
        """
        Returns the canonical SQLAlchemy Oracle URL.
        DATABASE_URL env var overrides component vars when set.
        Uses python-oracledb thin mode (no Oracle Instant Client required).
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"oracle+oracledb://{self.ORACLE_USER}:{self.ORACLE_PASSWORD}"
            f"@{self.ORACLE_HOST}:{self.ORACLE_PORT}"
            f"/?service_name={self.ORACLE_SERVICE_NAME}"
        )

    @computed_field
    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @computed_field
    @property
    def storage_path(self) -> Path:
        return Path(self.STORAGE_DIR).resolve()

    @computed_field
    @property
    def results_path(self) -> Path:
        return Path(self.RESULTS_DIR).resolve()

    @computed_field
    @property
    def thumbnails_path(self) -> Path:
        return Path(self.THUMBNAILS_DIR).resolve()

    @computed_field
    @property
    def pages_path(self) -> Path:
        return Path(self.PAGES_DIR).resolve()

    @computed_field
    @property
    def crops_path(self) -> Path:
        return Path(self.CROPS_DIR).resolve()

    @computed_field
    @property
    def temp_path(self) -> Path:
        return Path(self.TEMP_DIR).resolve()

    @computed_field
    @property
    def log_path(self) -> Path:
        return Path(self.LOG_DIR).resolve()

    @computed_field
    @property
    def model_cache_path(self) -> Path:
        return Path(self.MODEL_CACHE_DIR).resolve()

    @computed_field
    @property
    def hf_home_path(self) -> Path:
        if self.HF_HOME_OVERRIDE:
            return Path(self.HF_HOME_OVERRIDE).resolve()
        return self.model_cache_path / "huggingface"

    @computed_field
    @property
    def paddle_model_path(self) -> Path:
        if self.PADDLE_MODEL_DIR:
            return Path(self.PADDLE_MODEL_DIR).resolve()
        return self.model_cache_path / "paddleocr"

    def ensure_storage_directories(self) -> None:
        """Create all required storage directories if they don't exist."""
        for path in [
            self.storage_path,
            self.results_path,
            self.thumbnails_path,
            self.pages_path,
            self.crops_path,
            self.temp_path,
            self.log_path,
            self.hf_home_path,
            self.paddle_model_path,
        ]:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    Import this function everywhere — do not instantiate Settings directly.
    """
    return Settings()


settings = get_settings()

# Propagate the model-cache location to the third-party libraries that read
# it from the process environment (HuggingFace's hub/transformers, which
# surya-ocr sits on top of). This MUST happen here, immediately after
# `settings` exists, and before any other app module runs — config.py is
# imported before almost everything else, and surya's own import is lazy
# (inside a function, app/ocr/surya_layout_engine.py), so this is early
# enough. setdefault() never overrides a value the shell/deployment already
# exported explicitly.
os.environ.setdefault("HF_HOME", str(settings.hf_home_path))
os.environ.setdefault("TRANSFORMERS_CACHE", str(settings.hf_home_path))
