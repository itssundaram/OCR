"""Missing weights on an offline machine give an actionable message, other errors pass through unchanged."""
from app.ocr.model_errors import weights_error


def test_hub_connection_error_becomes_a_download_instruction():
    exc = OSError("We couldn't connect to 'https://huggingface.co' to load this file, couldn't find it in the cached files")
    out = weights_error(exc, "Surya layout")
    assert out is not exc
    assert "download_models.py" in str(out) and "Surya layout" in str(out)


def test_other_errors_are_not_rewritten():
    exc = ValueError("shape mismatch")
    assert weights_error(exc, "Surya layout") is exc
