"""
DOCINT — File Security & Validation
Prevents path traversal, enforces file type/size, generates safe stored filenames.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import FileTypeInvalidError, FileTooLargeError

ALLOWED_MIME_TYPES: set[str] = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
    "image/bmp",
}

MAGIC_SIGNATURES: dict[bytes, str] = {
    b"%PDF":         "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG":      "image/png",
    b"II*\x00":      "image/tiff",
    b"MM\x00*":      "image/tiff",
    b"RIFF":         "image/webp",
    b"BM":           "image/bmp",
}

ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".bmp":  "image/bmp",
}

EXT_MAP: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg":      ".jpg",
    "image/png":       ".png",
    "image/tiff":      ".tif",
    "image/webp":      ".webp",
    "image/bmp":       ".bmp",
}


def detect_mime_type(header_bytes: bytes) -> str | None:
    for signature, mime in MAGIC_SIGNATURES.items():
        if header_bytes.startswith(signature):
            if mime == "image/webp" and len(header_bytes) >= 12:
                return "image/webp" if header_bytes[8:12] == b"WEBP" else None
            return mime
    return None


def validate_file(file_bytes_header: bytes, filename: str, file_size: int) -> str:
    """
    Validate uploaded file by magic bytes, extension, and size.
    Returns detected MIME type string.
    Raises FileTypeInvalidError or FileTooLargeError.
    """
    if file_size > settings.max_upload_bytes:
        raise FileTooLargeError(file_size, settings.max_upload_bytes)

    detected_mime = detect_mime_type(file_bytes_header)
    
    if not detected_mime or detected_mime not in ALLOWED_MIME_TYPES:
        raise FileTypeInvalidError(detected_mime or "unknown")
        
    extension_mime = ALLOWED_EXTENSIONS.get(Path(filename).suffix.lower())
    if extension_mime and extension_mime != detected_mime:
        # Prevent extension spoofing
        raise FileTypeInvalidError(detected_mime)

    return detected_mime


def generate_stored_filename(mime_type: str) -> str:
    """Generate a UUID-based stored filename. Original filename is never used for FS ops."""
    extension = EXT_MAP.get(mime_type, ".bin")
    return f"{uuid.uuid4()}{extension}"


def safe_file_path(base_dir: Path, filename: str) -> Path:
    """
    Construct a safe absolute file path within base_dir.
    Raises ValueError on path traversal attempt.
    """
    base = base_dir.resolve()
    safe_name = Path(filename).name
    target = (base / safe_name).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Path traversal detected: '{filename}'")
    return target


def sanitize_original_filename(filename: str) -> str:
    """Sanitize filename for display/logging only — never for FS operations."""
    name = Path(filename).name
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)
    return safe[:255]
