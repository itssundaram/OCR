"""
DOCINT — Asset Service
Local-filesystem backend for the evidence asset abstraction (Phase 3 of the
implementation plan). Saves rendered page images, field/table crops, and
thumbnails, each under its own configured directory (app.core.config.settings).

This is intentionally the ONLY storage backend for now — do not add S3/MinIO/NFS
support here speculatively; swap this module out when there's a real deployment
need for one (see the plan's Phase 3 notes on storage abstraction).
"""
import os
from PIL import Image

from app.core.config import settings


class AssetService:
    def __init__(
        self,
        pages_dir: str | None = None,
        crops_dir: str | None = None,
        thumbnails_dir: str | None = None,
    ):
        self.pages_dir = pages_dir or str(settings.pages_path)
        self.crops_dir = crops_dir or str(settings.crops_path)
        self.thumbnails_dir = thumbnails_dir or str(settings.thumbnails_path)

        # Ensure dirs exist (settings.ensure_storage_directories() also creates
        # these at app startup — this guards direct/standalone use too, e.g. tests).
        os.makedirs(self.pages_dir, exist_ok=True)
        os.makedirs(self.crops_dir, exist_ok=True)
        os.makedirs(self.thumbnails_dir, exist_ok=True)

    def save_page_image(self, doc_id: str, page_n: int, image: Image.Image) -> str:
        doc_dir = os.path.join(self.pages_dir, str(doc_id))
        os.makedirs(doc_dir, exist_ok=True)
        path = os.path.join(doc_dir, f"page_{page_n}.png")
        image.save(path, format="PNG")
        return path

    def save_crop(self, job_id: str, field_name: str, page_n: int, image: Image.Image) -> str:
        job_dir = os.path.join(self.crops_dir, str(job_id))
        os.makedirs(job_dir, exist_ok=True)
        # Sanitize field name for filesystem
        safe_field_name = "".join([c for c in field_name if c.isalpha() or c.isdigit() or c == '_']).rstrip()
        path = os.path.join(job_dir, f"{safe_field_name}_p{page_n}.png")
        image.save(path, format="PNG")
        return path

    def save_thumbnail(self, doc_id: str, page_n: int, image: Image.Image) -> str:
        doc_dir = os.path.join(self.thumbnails_dir, str(doc_id))
        os.makedirs(doc_dir, exist_ok=True)
        path = os.path.join(doc_dir, f"thumb_{page_n}.jpg")
        # Resize to thumbnail size if needed before save
        thumb = image.copy()
        thumb.thumbnail((256, 256))
        if thumb.mode in ("RGBA", "P"):
            thumb = thumb.convert("RGB")
        thumb.save(path, format="JPEG", quality=85)
        return path


_asset_service: AssetService | None = None


def get_asset_service() -> AssetService:
    """Singleton accessor — matches the `_get_surya_engines()` pattern already
    used elsewhere in the codebase, so a worker process builds its directories
    (and, later, any client connection a different backend might need) once."""
    global _asset_service
    if _asset_service is None:
        _asset_service = AssetService()
    return _asset_service
