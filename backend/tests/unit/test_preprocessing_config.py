"""
Phase 5 — configurable preprocessing tests.

Pure-logic tests (no DB, no real scanned documents): small synthetic PIL
images, verifying that each stage can be disabled independently via the
`config` dict (per-call override) and that `operations_applied` accurately
reflects what actually ran. Consistent with this project's "no mocks, no
SQLite" convention (backend/tests/conftest.py) — nothing here touches a
database or an external service, so it needs no such fixture.
"""
from PIL import Image

from app.ocr.preprocessing import (
    _resolve_preprocess_config,
    preprocess_page,
    preprocess_page_with_metadata,
)


def _blank_page(size=(200, 300), color=(255, 255, 255)) -> Image.Image:
    """A plain white RGB page — enough to exercise crop/deskew/orientation
    code paths without needing a real scanned document."""
    return Image.new("RGB", size, color)


def test_resolve_preprocess_config_defaults_from_settings():
    cfg = _resolve_preprocess_config(None)
    assert set(cfg.keys()) == {"orientation", "deskew", "crop_borders"}
    assert all(isinstance(v, bool) for v in cfg.values())


def test_resolve_preprocess_config_per_call_override_wins():
    cfg = _resolve_preprocess_config({"deskew": False})
    assert cfg["deskew"] is False
    # Untouched keys still come from settings, not silently dropped.
    assert "orientation" in cfg
    assert "crop_borders" in cfg


def test_all_stages_enabled_records_all_three_as_applied():
    img = _blank_page()
    _, meta = preprocess_page_with_metadata(
        img, config={"orientation": True, "deskew": True, "crop_borders": True}
    )
    assert meta["operations_applied"] == {
        "orientation": True,
        "deskew": True,
        "crop_borders": True,
    }
    assert "error" not in meta


def test_disabling_a_stage_skips_it_and_metadata_reflects_that():
    img = _blank_page()
    _, meta = preprocess_page_with_metadata(
        img, config={"orientation": True, "deskew": False, "crop_borders": False}
    )
    assert meta["operations_applied"] == {"orientation": True}
    assert "deskew" not in meta["operations_applied"]
    assert "crop_borders" not in meta["operations_applied"]


def test_all_stages_disabled_returns_image_unchanged_with_empty_applied():
    img = _blank_page()
    out, meta = preprocess_page_with_metadata(
        img, config={"orientation": False, "deskew": False, "crop_borders": False}
    )
    assert meta["operations_applied"] == {}
    assert out.size == img.size


def test_preprocess_page_backward_compatible_signature_returns_image_only():
    img = _blank_page()
    out = preprocess_page(img, config={"deskew": False, "crop_borders": False})
    assert isinstance(out, Image.Image)


def test_preprocess_page_still_works_with_no_config_arg_at_all():
    # Existing callers invoke preprocess_page(img) with no config kwarg —
    # must keep working exactly as before.
    img = _blank_page()
    out = preprocess_page(img)
    assert isinstance(out, Image.Image)
