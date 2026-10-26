"""
VLM repair for ONE field.

Used only after the cheap steps failed validation, and only on a crop around
the field's label. The model's answer is accepted as "supported" only when
the OCR text of that same crop contains it, so the VLM can read a faint
character but cannot invent a value.
"""
from __future__ import annotations

import base64
import io
import json
import re

from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.ocr.kv_match import _edit_distance

logger = get_logger(__name__)


def _toks(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def supported_by_ocr(value: str, ocr_text: str) -> bool:
    """Every token of `value` appears in the crop's OCR text (one wrong character
    allowed on tokens of 5+ characters)."""
    want, have = _toks(value), _toks(ocr_text)
    if not want or not have:
        return False
    hv = set(have)
    for t in want:
        if t in hv:
            continue
        if len(t) >= 5 and any(abs(len(t) - len(h)) <= 1 and _edit_distance(t, h, 1) <= 1 for h in hv):
            continue
        return False
    return True


class FieldVlm:
    def __init__(self):
        self._client = None

    def _get(self, timeout: float | None = None):
        """The shared client; with `timeout` (seconds left in the job's VLM budget) a short-lived one that gives up then."""
        from ollama import Client
        if timeout is not None and timeout < settings.FAST_VLM_TIMEOUT_S:
            return Client(host=settings.OLLAMA_BASE_URL.rstrip("/"), timeout=max(1.0, float(timeout)))
        if self._client is None:
            self._client = Client(host=settings.OLLAMA_BASE_URL.rstrip("/"), timeout=settings.FAST_VLM_TIMEOUT_S)
        return self._client

    def read_field(self, crop: Image.Image, label: str, timeout: float | None = None) -> str:
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="PNG")
        prompt = (
            f'Read the value printed next to or under the label "{label}" in this image. '
            'Copy the text exactly as printed; do not guess or complete it. '
            'If the value is not visible, return an empty string. '
            'Reply with JSON only: {"value": "<text>"}'
        )
        resp = self._get(timeout).chat(
            model=settings.AI_MODEL,
            messages=[{"role": "user", "content": prompt, "images": [base64.b64encode(buf.getvalue()).decode()]}],
            format="json",
            options={"temperature": 0.0, "num_predict": 96, "num_ctx": 4096},
            keep_alive=settings.FAST_VLM_KEEP_ALIVE,
        )
        raw = resp.message.content or ""
        try:
            return str(json.loads(raw).get("value") or "").strip()
        except Exception:
            m = re.search(r'"value"\s*:\s*"([^"]*)"', raw)
            return m.group(1).strip() if m else ""

    def read_cell(self, crop: Image.Image, instruction: str, header: str = "") -> str:
        """Transcribe ONE table cell crop. Returns "" when nothing is readable."""
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="PNG")
        ctx = f' The column is titled "{header}".' if header else ""
        prompt = (f"{instruction}{ctx} If nothing is readable return an empty string. "
                  'Reply with JSON only: {"value": "<text>"}')
        resp = self._get().chat(
            model=settings.AI_MODEL,
            messages=[{"role": "user", "content": prompt, "images": [base64.b64encode(buf.getvalue()).decode()]}],
            format="json",
            options={"temperature": 0.0, "num_predict": 80, "num_ctx": 2048},
            keep_alive=settings.FAST_VLM_KEEP_ALIVE,
        )
        raw = resp.message.content or ""
        try:
            return str(json.loads(raw).get("value") or "").strip()
        except Exception:
            m = re.search(r'"value"\s*:\s*"([^"]*)"', raw)
            return m.group(1).strip() if m else ""
