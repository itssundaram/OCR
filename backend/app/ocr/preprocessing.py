"""
DOCINT — Image Preprocessing
Implements orientation correction, blank space cropping, and deskewing for the OCR pipeline.
"""
import io
import math
import numpy as np
from PIL import Image, ImageOps
import cv2

from app.core.logging import get_logger
from app.core.config import settings

logger = get_logger(__name__)


def correct_orientation(img: Image.Image) -> Image.Image:
    """
    Detect and fix page rotation using Surya's orientation model if available,
    or fallback to PIL ImageOps.EXIF.
    For simplicity, here we do standard EXIF orientation handling.
    """
    return ImageOps.exif_transpose(img)


def deskew_image(img: Image.Image) -> Image.Image:
    """
    Fix minor skew angles using OpenCV.
    """
    # Convert PIL Image to OpenCV format (numpy array)
    cv_img = np.array(img.convert('L'))  # grayscale

    # Threshold the image
    # Use inverted thresholding: text becomes white, background becomes black
    _, thresh = cv2.threshold(cv_img, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    # Get coordinates of all non-zero pixels
    coords = np.column_stack(np.where(thresh > 0))

    if len(coords) == 0:
        return img

    # Calculate minimum area bounding box
    angle = cv2.minAreaRect(coords)[-1]

    # Adjust angle to be between -45 and 45 degrees
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # If angle is very small, no need to deskew
    if abs(angle) < 0.5:
        return img

    # Rotate the image
    (h, w) = cv_img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    
    # Use white background for rotation (255, 255, 255)
    cv_img_color = np.array(img.convert("RGB"))
    rotated = cv2.warpAffine(
        cv_img_color, M, (w, h), 
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255)
    )

    return Image.fromarray(rotated)


def crop_blank_borders(img: Image.Image, threshold: int = 10) -> Image.Image:
    """
    Remove large white/blank margins from scanned pages.
    """
    # Convert to grayscale
    gray = img.convert('L')
    
    # Invert so text is bright, background is dark
    invert = ImageOps.invert(gray)
    
    # Get bounding box of non-zero pixels
    bbox = invert.getbbox()
    
    if bbox:
        # Add a small padding back to avoid cropping too tight
        pad = 20
        w, h = img.size
        x1 = max(0, bbox[0] - pad)
        y1 = max(0, bbox[1] - pad)
        x2 = min(w, bbox[2] + pad)
        y2 = min(h, bbox[3] + pad)
        return img.crop((x1, y1, x2, y2))
    
    return img


def _resolve_preprocess_config(config: dict | None) -> dict:
    """Settings-driven defaults (Phase 5), overridable per-call via `config`."""
    cfg = {
        "orientation": settings.PREPROCESS_ORIENTATION_ENABLED,
        "deskew": settings.PREPROCESS_DESKEW_ENABLED,
        "crop_borders": settings.PREPROCESS_CROP_BORDERS_ENABLED,
    }
    if config:
        cfg.update(config)
    return cfg


def preprocess_page_with_metadata(img: Image.Image, config: dict | None = None) -> tuple[Image.Image, dict]:
    """
    Run the configurable preprocessing pipeline: orient -> deskew -> crop.

    Each stage can be individually enabled/disabled via settings
    (PREPROCESS_*_ENABLED) or by passing an explicit `config` dict for this
    call only (e.g. {"deskew": False}). Returns (processed_image, metadata)
    where metadata records which operations actually ran, so a caller can
    persist it (Phase 2/14 — PROCESSING_EVENTS, DOCUMENT_PAGES.extra data).

    The caller's original image is never mutated — a new Image is returned at
    each stage — so "always preserve the original" (master prompt section 5)
    holds as long as the caller keeps its own reference to what it passed in,
    which is exactly what app/services/extractor.py already does.
    """
    cfg = _resolve_preprocess_config(config)
    applied: dict[str, bool] = {}
    try:
        if cfg.get("orientation", True):
            img = correct_orientation(img)
            applied["orientation"] = True
        if cfg.get("deskew", True):
            img = deskew_image(img)
            applied["deskew"] = True
        if cfg.get("crop_borders", True):
            img = crop_blank_borders(img)
            applied["crop_borders"] = True
            
        logger.info("preprocessing_completed", operations_applied=applied)
        return img, {"operations_applied": applied, "config": cfg}
    except Exception as e:
        logger.warning("preprocessing_failed", error=str(e), operations_applied=applied)
        return img, {"operations_applied": applied, "config": cfg, "error": str(e)}


def preprocess_page(img: Image.Image, config: dict | None = None) -> Image.Image:
    """Backward-compatible wrapper: same call signature existing callers already
    use, now additionally configurable via settings or an explicit `config` dict."""
    processed, _metadata = preprocess_page_with_metadata(img, config)
    return processed
