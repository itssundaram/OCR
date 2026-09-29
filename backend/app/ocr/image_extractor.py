"""
DOCINT — Image Extractor
Converts PDFs and standard image formats into a sequence of PIL Images for OCR.
Also provides scanned-page detection to route pages to the correct OCR engine.
"""
from __future__ import annotations

import io
from pathlib import Path
from PIL import Image, ImageSequence
import fitz  # PyMuPDF

from app.core.config import settings
from app.core.exceptions import ProcessingError


def _is_page_scanned(fitz_page: fitz.Page, text_threshold: int = 50) -> bool:
    """
    Heuristic: if a PDF page has fewer than `text_threshold` extractable characters,
    it is almost certainly a scanned/image-only page that needs OCR rather than
    native text extraction.
    """
    text = fitz_page.get_text("text").strip()
    return len(text) < text_threshold


def extract_pages_as_images(file_path: str | Path, mime_type: str) -> list[Image.Image]:
    """
    Reads a document from disk and converts it into a list of PIL Images (one per page).
    Preserves full backward compatibility — callers that don't need scan metadata
    continue to use this function unchanged.
    """
    images, _, _ = extract_pages_as_images_with_metadata(file_path, mime_type)
    return images


def extract_pages_as_images_with_metadata(
    file_path: str | Path,
    mime_type: str,
    scanned_text_threshold: int = 50,
    dpi_native: float | None = None,
    dpi_scanned: float | None = None,
) -> tuple[list[Image.Image], list[bool], list[str]]:
    """
    Reads a document from disk and returns:
      - images: list of PIL Images (one per page)
      - is_scanned: list of booleans (True = page needs OCR, False = text-native)
      - native_texts: list of str (empty string for scanned pages)

    Adaptive DPI rendering:
      - Native-text pages: dpi_native (default: settings.OCR_DPI_NATIVE = 1.5x ≈ 108 DPI)
        These pages don't need high-res for Tesseract — only the LLM vision thumbnail.
      - Scanned/photographed pages: dpi_scanned (default: settings.OCR_DPI_SCANNED = 2.0x ≈ 144 DPI)
        Tesseract works best with at least 150 DPI; 2x gives ~144 DPI which is sufficient
        while being 33% faster to render than the previous 3x (216 DPI).

    Args:
        file_path: Path to the document on disk.
        mime_type: MIME type string (e.g. "application/pdf").
        scanned_text_threshold: Pages with fewer extractable chars than this are treated as scanned.
        dpi_native: DPI multiplier override for native-text pages.
        dpi_scanned: DPI multiplier override for scanned pages.

    Returns:
        A (images, is_scanned, native_texts) tuple.
    """
    path = Path(file_path)
    if not path.exists():
        raise ProcessingError("IMAGE_EXTRACTION", f"File not found: {path}")

    # Resolve DPI multipliers: caller override → settings → hardcoded safe defaults
    _dpi_native = dpi_native if dpi_native is not None else settings.OCR_DPI_NATIVE
    _dpi_scanned = dpi_scanned if dpi_scanned is not None else settings.OCR_DPI_SCANNED

    images: list[Image.Image] = []
    is_scanned: list[bool] = []
    native_texts: list[str] = []

    try:
        if mime_type == "application/pdf":
            doc = fitz.open(path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)

                # ── Scanned detection & Native Text ───────────────────────────
                text = page.get_text("text").strip()
                scanned = len(text) < scanned_text_threshold
                is_scanned.append(scanned)
                native_texts.append(text)

                # ── Adaptive DPI rendering ────────────────────────────────────
                # Use lower DPI for native-text pages (LLM vision doesn't need
                # extremely high resolution). Use higher DPI for scanned pages
                # so Tesseract has enough pixel density to read text accurately.
                dpi_mult = _dpi_scanned if scanned else _dpi_native
                mat = fitz.Matrix(dpi_mult, dpi_mult)
                pix = page.get_pixmap(matrix=mat, alpha=False)

                img_data = pix.tobytes("png")
                pil_img = Image.open(io.BytesIO(img_data)).convert("RGB")
                images.append(pil_img)

            doc.close()

        elif mime_type in ["image/tiff", "image/webp", "image/bmp", "image/jpeg", "image/png"]:
            # PIL can handle multi-page TIFFs and standard single-page images natively.
            # Image files have no embedded text layer → always considered scanned.
            img = Image.open(path)
            for frame in ImageSequence.Iterator(img):
                rgb_img = frame.convert("RGB")
                images.append(rgb_img)
                is_scanned.append(True)
                native_texts.append("")

        else:
            raise ProcessingError(
                "IMAGE_EXTRACTION",
                f"Unsupported MIME type for extraction: {mime_type}",
            )

    except ProcessingError:
        raise
    except Exception as e:
        raise ProcessingError(
            "IMAGE_EXTRACTION",
            f"Failed to extract images from {mime_type}: {str(e)}",
        )

    if not images:
        raise ProcessingError("IMAGE_EXTRACTION", "No pages/images could be extracted.")

    return images, is_scanned, native_texts

