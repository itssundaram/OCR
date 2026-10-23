"""
Triage: open the document, decide per page whether the PDF already contains
its text, and render the page image.

A digital PDF page carries its words and their positions; reading them takes
milliseconds and needs no OCR model. Only pages without a usable text layer
(scans, photos, broken encodings) go to OCR.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from app.core.config import settings
from app.core.exceptions import ProcessingError
from app.core.logging import get_logger
from app.ocr.base import BoundingBox, OCRLine, OCRWord

NATIVE_CONFIDENCE = 0.99


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_layer_ok(text: str, n_words: int) -> bool:
    """True when the page's text layer is real, readable text (not empty, not
    a few stray characters, not broken font encoding)."""
    t = (text or "").strip()
    if len(t) < settings.SCANNED_PAGE_TEXT_THRESHOLD or n_words < settings.FAST_NATIVE_MIN_WORDS:
        return False
    if "(cid:" in t:
        return False
    visible = sum(1 for c in t if not c.isspace())
    if visible == 0:
        return False
    if t.count("�") / visible > 0.05:
        return False
    alnum = sum(1 for c in t if c.isalnum())
    return alnum / visible >= 0.5


def image_cover(page: "fitz.Page") -> float:
    """Share of the page area covered by embedded images (0..1)."""
    try:
        area = float(page.rect.width * page.rect.height) or 1.0
        covered = 0.0
        for info in page.get_image_info():
            r = fitz.Rect(info["bbox"]) & page.rect
            if not r.is_empty:
                covered += r.width * r.height
        return min(1.0, covered / area)
    except Exception:
        return 0.0


def is_digital_page(page: "fitz.Page", raw_words: list) -> bool:
    """A page is "digital" (use the text layer, skip OCR) only when it has a
    readable text layer AND is not just a picture of a page. Scans often carry a
    few watermark words or a poor embedded OCR layer; trusting those would drop
    most of the real content."""
    if not text_layer_ok(" ".join(w[4] for w in raw_words), len(raw_words)):
        return False
    if image_cover(page) >= settings.FAST_IMAGE_PAGE_COVER:
        return bool(settings.FAST_TRUST_EMBEDDED_OCR and len(raw_words) >= settings.FAST_EMBEDDED_OCR_MIN_WORDS)
    return True


def words_to_lines(words: list[tuple[float, float, float, float, str]], conf: float = NATIVE_CONFIDENCE) -> list[OCRLine]:
    """Group (x1, y1, x2, y2, text) words into visual lines by vertical overlap.

    PyMuPDF's own line numbers follow the PDF's drawing order, which splits a
    form row such as "Name :  John" into separate lines when label and value
    were drawn as separate blocks. Grouping by position gives the same lines a
    reader sees (and the same shape Tesseract returns)."""
    items = [w for w in words if w[4] and w[4].strip()]
    items.sort(key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
    groups: list[list[tuple]] = []
    cy_of: list[float] = []
    h_of: list[float] = []
    for w in items:
        cy = (w[1] + w[3]) / 2.0
        h = max(1.0, w[3] - w[1])
        placed = False
        if groups:
            gi = len(groups) - 1
            if abs(cy - cy_of[gi]) <= 0.5 * max(h, h_of[gi]):
                groups[gi].append(w)
                n = len(groups[gi])
                cy_of[gi] += (cy - cy_of[gi]) / n
                h_of[gi] = max(h_of[gi], h)
                placed = True
        if not placed:
            groups.append([w])
            cy_of.append(cy)
            h_of.append(h)

    lines: list[OCRLine] = []
    for g in groups:
        g.sort(key=lambda w: w[0])
        ocr_words = [
            OCRWord(text=w[4].strip(), bbox=BoundingBox(x1=w[0], y1=w[1], x2=w[2], y2=w[3]), confidence=conf)
            for w in g
        ]
        lines.append(OCRLine(
            text=" ".join(w.text for w in ocr_words),
            words=ocr_words,
            bbox=BoundingBox(
                x1=min(w[0] for w in g), y1=min(w[1] for w in g),
                x2=max(w[2] for w in g), y2=max(w[3] for w in g),
            ),
            confidence=conf,
        ))
    lines.sort(key=lambda ln: (ln.bbox.y1, ln.bbox.x1))
    return lines


logger = get_logger(__name__)

# Hindi typed in a legacy "font hack" (Kruti Dev & co) is stored as Latin-looking letters ("lsuk lfpo" for the Hindi
# for "Army Secretary"). The text layer is therefore pure noise for key:value matching, so words set in those
# fonts are dropped from the native text. The font name is the reliable marker; the letters themselves look like
# ordinary words.
_LEGACY_FONT_RE = re.compile(r"kruti|devlys|dev\s?lys|chanakya|walkman|shivaji|agra\s?hindi|aps[-_ ]?dv|kiran", re.I)


def legacy_font_rects(page) -> list:
    """Bounding boxes (page coordinates) of text spans set in a legacy Hindi font. Cheap when the page has none."""
    try:
        if not any(_LEGACY_FONT_RE.search(str(f[3])) for f in page.get_fonts()):
            return []
        rects = []
        for block in page.get_text("dict").get("blocks", []):
            for ln in block.get("lines", []):
                for sp in ln.get("spans", []):
                    if _LEGACY_FONT_RE.search(str(sp.get("font", ""))):
                        rects.append(fitz.Rect(sp["bbox"]))
        return rects
    except Exception as exc:                                # never fail a page over this
        logger.warning("legacy_font_scan_failed", error=str(exc)[:160])
        return []


def drop_legacy_words(raw: list, rects: list) -> list:
    """Remove words whose centre lies inside a legacy-font span."""
    if not rects:
        return raw
    keep = []
    for w in raw:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if not any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rects):
            keep.append(w)
    return keep


class DocumentSource:
    """One uploaded file. `load_page` opens the file itself, so pages can be
    loaded from several threads (PyMuPDF documents are not thread-safe)."""

    def __init__(self, path: str | Path, mime_type: str):
        self.path = Path(path)
        self.mime_type = mime_type
        if not self.path.exists():
            raise ProcessingError("IMAGE_EXTRACTION", f"File not found: {self.path}")
        self.sha256 = file_sha256(self.path)
        self.is_pdf = mime_type == "application/pdf"
        try:
            if self.is_pdf:
                with fitz.open(self.path) as doc:
                    count = len(doc)
            elif mime_type in ("image/tiff", "image/webp", "image/bmp", "image/jpeg", "image/png"):
                with Image.open(self.path) as img:
                    count = getattr(img, "n_frames", 1)
            else:
                raise ProcessingError("IMAGE_EXTRACTION", f"Unsupported MIME type: {mime_type}")
        except ProcessingError:
            raise
        except Exception as exc:
            raise ProcessingError("IMAGE_EXTRACTION", f"Cannot open document: {exc}")
        cap = settings.MAX_PAGES_TO_PROCESS
        self.page_count = min(count, cap) if cap and cap > 0 else count
        if self.page_count <= 0:
            raise ProcessingError("IMAGE_EXTRACTION", "No pages could be extracted.")

    def load_page(self, index: int) -> tuple[Image.Image, list[OCRLine] | None, float]:
        """Return (page image, native lines or None, render scale).
        `native lines` are in page-image pixels; None means the page needs OCR."""
        if self.is_pdf:
            return self._load_pdf_page(index)
        with Image.open(self.path) as img:
            img.seek(index)
            return img.convert("RGB"), None, 1.0

    def _load_pdf_page(self, index: int) -> tuple[Image.Image, list[OCRLine] | None, float]:
        with fitz.open(self.path) as doc:
            page = doc.load_page(index)
            raw = page.get_text("words")
            native = is_digital_page(page, raw)
            if native:
                legacy = legacy_font_rects(page)
                if legacy:
                    before = len(raw)
                    raw = drop_legacy_words(raw, legacy)
                    logger.info("fast_native_legacy_font_dropped", page=index + 1, words=before - len(raw))
            scale = float(settings.OCR_DPI_NATIVE if native else settings.OCR_DPI_SCANNED)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if not native:
                return image, None, scale
            rot = page.rotation_matrix if page.rotation else None
            words = []
            for w in raw:
                r = fitz.Rect(w[0], w[1], w[2], w[3])
                if rot is not None:
                    r = r * rot
                    r.normalize()
                words.append((r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale, w[4]))
            return image, words_to_lines(words), scale
