"""
Disk cache for page OCR results (words + boxes, never images).

Key = file SHA-256 + page + render scale + engine + engine version. Anything
that changes the pixels or the engine produces a different key, so a stale
entry can never be served. Re-running a file after editing its template, or a
retry, reads the words from here instead of running Tesseract again.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path

from app.core.logging import get_logger
from app.ocr.base import OCRLine

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def tesseract_version() -> str:
    try:
        from app.core.model_registry import get_tesseract_engine
        return str(get_tesseract_engine().engine_version)
    except Exception:
        return "unknown"


class OcrCache:
    def __init__(self, root: str | Path, enabled: bool = True, max_mb: int = 512, max_age_days: int = 14):
        self.root = Path(root)
        self.enabled = enabled
        self.max_bytes = int(max_mb) * 1024 * 1024
        self.max_age_s = int(max_age_days) * 86400
        if self.enabled:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("fast_ocr_cache_disabled", error=str(exc))
                self.enabled = False

    @staticmethod
    def make_key(sha256: str, page: int, scale: float, engine: str, version: str) -> str:
        raw = f"{sha256}|{page}|{scale:.3f}|{engine}|{version}|v1"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> list[OCRLine] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            os.utime(path, None)  # recently used
            return [OCRLine.model_validate(x) for x in data]
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("fast_ocr_cache_read_failed", error=str(exc))
            return None

    def put(self, key: str, lines: list[OCRLine]) -> None:
        if not self.enabled or not lines:
            return  # never cache an empty (timed-out / failed) read
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump([ln.model_dump() for ln in lines], fh)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("fast_ocr_cache_write_failed", error=str(exc))

    def prune(self) -> None:
        """Drop entries past the age limit, then the oldest until under the size cap."""
        if not self.enabled:
            return
        try:
            now = time.time()
            entries = []
            total = 0
            for p in self.root.rglob("*.json"):
                st = p.stat()
                if now - st.st_mtime > self.max_age_s:
                    p.unlink(missing_ok=True)
                    continue
                entries.append((st.st_mtime, st.st_size, p))
                total += st.st_size
            if total > self.max_bytes:
                for _, size, p in sorted(entries):
                    p.unlink(missing_ok=True)
                    total -= size
                    if total <= self.max_bytes:
                        break
        except Exception as exc:
            logger.warning("fast_ocr_cache_prune_failed", error=str(exc))


def default_cache() -> OcrCache:
    from app.core.config import settings
    return OcrCache(
        settings.FAST_OCR_CACHE_DIR,
        enabled=settings.FAST_OCR_CACHE_ENABLED,
        max_mb=settings.FAST_OCR_CACHE_MAX_MB,
        max_age_days=settings.FAST_OCR_CACHE_MAX_AGE_DAYS,
    )
