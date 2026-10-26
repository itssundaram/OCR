"""Turn the library's "could not reach huggingface.co" into what an offline operator needs to hear."""
from __future__ import annotations

from app.core.config import settings

_OFFLINE_MARKERS = (
    "huggingface.co", "couldn't find it in the cached", "LocalEntryNotFound", "offline mode",
    "is not a local folder", "does not appear to have a file named", "Offline guard",
)


def weights_error(exc: Exception, what: str) -> Exception:
    """A clearer exception when `exc` looks like missing model weights; `exc` itself otherwise."""
    text = f"{type(exc).__name__} {exc}"
    if any(m.lower() in text.lower() for m in _OFFLINE_MARKERS):
        return RuntimeError(
            f"{what}: model weights are not in {settings.hf_home_path}. "
            "On a PC with internet run `python scripts/download_models.py`, then copy backend/data/models to this machine. "
            "(The fast_ocr pipeline needs no weights.) "
            f"Original error: {type(exc).__name__}: {str(exc)[:200]}"
        )
    return exc
