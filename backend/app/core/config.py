"""
DOCINT — Centralized Application Configuration
All environment access is funnelled through this module.
No other module should call os.environ directly.
"""
from __future__ import annotations

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
    LOG_DIR: str = "./data/logs"

    # ── OCR ───────────────────────────────────────────────────────────────────
    OCR_STRATEGY: Literal["tesseract_only"] = "tesseract_only"
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
    def temp_path(self) -> Path:
        return Path(self.TEMP_DIR).resolve()

    @computed_field
    @property
    def log_path(self) -> Path:
        return Path(self.LOG_DIR).resolve()

    def ensure_storage_directories(self) -> None:
        """Create all required storage directories if they don't exist."""
        for path in [
            self.storage_path,
            self.results_path,
            self.thumbnails_path,
            self.temp_path,
            self.log_path,
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
