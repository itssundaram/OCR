"""
Phase 3 / Phase 25 — tests for AssetService (the local-filesystem evidence
asset backend). Uses a real temp directory (tmp_path), not a mock — consistent
with this project's "no mocks" testing convention, just scoped to the
filesystem instead of Oracle.
"""
import os
from PIL import Image

from app.services.asset_service import AssetService


def _make_service(tmp_path):
    return AssetService(
        pages_dir=str(tmp_path / "pages"),
        crops_dir=str(tmp_path / "crops"),
        thumbnails_dir=str(tmp_path / "thumbnails"),
    )


def _make_image(mode="RGB", size=(40, 30), color=(10, 20, 30)):
    return Image.new(mode, size, color)


def test_save_page_image_writes_png_under_doc_id(tmp_path):
    svc = _make_service(tmp_path)
    path = svc.save_page_image("doc-123", 1, _make_image())

    assert path.endswith(os.path.join("doc-123", "page_1.png"))
    assert os.path.exists(path)
    assert Image.open(path).size == (40, 30)


def test_save_crop_sanitizes_field_name_for_filesystem(tmp_path):
    svc = _make_service(tmp_path)
    # A field name with spaces/punctuation must not break the filesystem path.
    path = svc.save_crop("job-9", "invoice #, total!", 2, _make_image())

    assert os.path.exists(path)
    assert "#" not in os.path.basename(path)
    assert "," not in os.path.basename(path)
    assert os.path.basename(path) == "invoicetotal_p2.png"


def test_save_thumbnail_resizes_and_handles_rgba_without_crashing(tmp_path):
    svc = _make_service(tmp_path)
    # RGBA previously crashed JPEG saving before this phase's fix.
    rgba_image = _make_image(mode="RGBA", size=(1000, 800))

    path = svc.save_thumbnail("doc-456", 1, rgba_image)

    assert os.path.exists(path)
    saved = Image.open(path)
    assert saved.mode == "RGB"
    assert max(saved.size) <= 256


def test_each_doc_or_job_gets_its_own_subdirectory(tmp_path):
    svc = _make_service(tmp_path)
    p1 = svc.save_page_image("doc-a", 1, _make_image())
    p2 = svc.save_page_image("doc-b", 1, _make_image())

    assert os.path.dirname(p1) != os.path.dirname(p2)
