"""
DOCINT — img2table Table Extraction Engine

Extracts tables from page images using img2table + Tesseract.
Improvements over the original:
  - Image preprocessing (contrast + sharpness) to make faint lines visible
  - Multi-pass strategy: bordered first, then borderless fallback
  - implicit_rows=True to handle tables without explicit row separators
  - Reduced min_confidence for partially-legible scanned docs
  - Proper error logging instead of silent except
"""
from __future__ import annotations

import io
import os

from PIL import Image, ImageEnhance
from img2table.document import Image as Img2TableImage
from img2table.ocr import TesseractOCR

from app.ocr.base import OCRTable
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Tesseract command comes exclusively from settings — no hardcoded fallbacks.
_TESSERACT_CMD: str = settings.TESSERACT_CMD or ""
if not _TESSERACT_CMD:
    raise RuntimeError(
        "TESSERACT_CMD is not set in your .env file. "
        "Please add: TESSERACT_CMD=<full path to tesseract executable>"
    )

_TESS_DIR = os.path.dirname(_TESSERACT_CMD)
if _TESS_DIR and _TESS_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _TESS_DIR + os.pathsep + os.environ.get("PATH", "")


def _preprocess_for_table_detection(img_bytes: bytes) -> bytes:
    """
    Enhance a scanned/photographed image so that faint table borders
    become clearer before passing to img2table's line detector.

    Steps:
      1. Boost contrast  — makes grey cell borders stand out
      2. Boost sharpness — helps Tesseract OCR inside img2table
      3. Convert to RGB PNG (img2table expects standard RGB input)
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

    # Contrast: 1.0 = original, 2.0 = double contrast
    img = ImageEnhance.Contrast(img).enhance(1.8)
    # Sharpness: 1.0 = original, 2.0 = sharper
    img = ImageEnhance.Sharpness(img).enhance(2.0)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _build_table(table) -> OCRTable | None:
    """Convert a single img2table ExtractedTable to an OCRTable."""
    df = table.df
    if df is None or df.empty:
        return None

    df = df.fillna("").astype(str)

    headers = df.iloc[0].tolist()
    rows = df.iloc[1:].values.tolist() if len(df) > 1 else []

    # Generate markdown
    sep = " | ".join(["---"] * len(headers))
    markdown = f"| {' | '.join(headers)} |\n| {sep} |\n"
    for row in rows:
        safe = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        markdown += f"| {' | '.join(safe)} |\n"

    return OCRTable(headers=headers, rows=rows, markdown=markdown)


def extract_tables_from_image(image_bytes: bytes) -> list[OCRTable]:
    """
    Extracts tables from a single page image using img2table + Tesseract.

    Multi-pass strategy:
      Pass 1 — bordered tables, high confidence (handles clear printed docs)
      Pass 2 — bordered + implicit rows (handles tables without full row lines)
      Pass 3 — borderless tables (handles scanned/photographed docs where
                lines may be too faint to detect reliably)

    The image is preprocessed to enhance contrast & sharpness before each pass.

    Returns a list of OCRTable objects (may be empty if nothing is detected).
    """
    try:
        ocr = TesseractOCR(n_threads=1, lang="eng")

        # Preprocess to improve border visibility
        enhanced_bytes = _preprocess_for_table_detection(image_bytes)

        # All available passes in priority order:
        #   1 = bordered only (fastest, most precise)
        #   2 = bordered + implicit rows (handles tables without full row lines)
        #   3 = borderless (slowest, most permissive — for heavily scanned docs)
        all_passes = [
            # (implicit_rows, borderless, min_confidence, label)
            (False, False, 50, "bordered"),
            (True,  False, 40, "bordered+implicit"),
            (True,  True,  40, "borderless"),
        ]
        # Limit passes according to settings — avoids wasting time on docs
        # where early passes already succeed (e.g. clean digital PDFs).
        active_passes = all_passes[:settings.IMG2TABLE_MAX_PASSES]

        for implicit_rows, borderless, min_conf, label in active_passes:
            try:
                img_obj = Img2TableImage(enhanced_bytes)
                extracted = img_obj.extract_tables(
                    ocr=ocr,
                    implicit_rows=implicit_rows,
                    borderless_tables=borderless,
                    min_confidence=min_conf,
                )
                logger.info(
                    "img2table_pass",
                    label=label,
                    count=len(extracted),
                    implicit_rows=implicit_rows,
                    borderless=borderless,
                )
                if extracted:
                    results = [t for t in (_build_table(e) for e in extracted) if t]
                    if results:
                        logger.info("img2table_success", label=label, tables=len(results))
                        return results
            except Exception as pass_err:
                logger.warning("img2table_pass_failed", label=label, error=str(pass_err))
                continue

        logger.info("img2table_no_tables_found")
        return []

    except Exception as e:
        import traceback
        logger.error("img2table_fatal", error=str(e), traceback=traceback.format_exc())
        return []
