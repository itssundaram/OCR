"""Glue for pipelines that get their words from their own OCR pass (surya / tesseract):
read a human-verified anchor box out of those words."""
from __future__ import annotations

from app.anchors.resolver import AnchorReading, resolve_anchor, words_from_lines


def anchor_is_trusted(la: dict | None, anchor: dict | None) -> bool:
    la = la or {}
    return bool(la.get("is_verified")) or str((anchor or {}).get("source") or "").startswith("user")


def read_verified_anchor(
    learned_anchors: dict | None,
    field_name: str,
    ocr_lines_by_page: list,
    pages: list,
    spec: dict | None = None,
) -> AnchorReading | None:
    """The value inside the field's human-confirmed box, or None when there is no
    trusted anchor for the field or nothing valid could be read there."""
    if not isinstance(learned_anchors, dict):
        return None
    anchor = (learned_anchors.get("fields") or {}).get(field_name)
    if not anchor or not anchor_is_trusted(learned_anchors, anchor):
        return None
    try:
        words_by_page = [words_from_lines(lines) for lines in ocr_lines_by_page]
        while len(words_by_page) < len(pages):
            words_by_page.append([])
        sizes = [(p.width, p.height) for p in pages]
        orig = (learned_anchors.get("page_width"), learned_anchors.get("page_height"))
        rd = resolve_anchor(anchor, words_by_page, sizes, orig_size=orig, spec=spec)
    except Exception:
        return None
    return rd if (rd is not None and rd.valid and rd.confidence >= 0.35) else None
