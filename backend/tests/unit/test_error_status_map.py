"""Every error code a route raises must have an HTTP status; unmapped ones used to come back as 500."""
import glob
import re

from app.main import _STATUS_MAP


def test_every_raised_error_code_has_a_status():
    codes = set()
    for f in glob.glob("app/**/*.py", recursive=True):
        src = open(f, encoding="utf-8").read()
        codes |= set(re.findall(r'DocIntError\([^)]*?,\s*"([A-Z_]+)"', src, re.S))
    # codes that really are server faults may stay unmapped (500); everything else needs an entry
    faults = {"ANCHOR_STORE_ERROR"}
    missing = sorted(c for c in codes if c not in _STATUS_MAP and c not in faults)
    assert not missing, f"unmapped error codes (would be 500): {missing}"


def test_not_found_codes_are_404():
    for c in ("CELL_NOT_FOUND", "PIPELINE_NOT_FOUND", "URL_NOT_FOUND", "FILE_NOT_FOUND"):
        assert _STATUS_MAP[c] == 404
