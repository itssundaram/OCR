"""
Which code is running?

`build_id()` is a short hash of the backend's own source files (not of anything configurable). Every extraction result
carries the build that produced it, the fast worker publishes the build it was started with, and the review page shows
both. After a `git pull` a result - or a worker that was not restarted - from an older build is therefore visible
instead of looking like "the fix does not work".
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1]
_SKIP = {"__pycache__"}


@lru_cache(maxsize=1)
def build_id() -> str:
    h = hashlib.sha1()
    try:
        for p in sorted(_APP_DIR.rglob("*.py")):
            if _SKIP & set(p.parts):
                continue
            h.update(str(p.relative_to(_APP_DIR)).encode())
            h.update(p.read_bytes())
        for p in sorted((_APP_DIR / "knowledge" / "data").glob("*.json")):
            h.update(p.read_bytes())
    except OSError:
        return "unknown"
    return h.hexdigest()[:8]
