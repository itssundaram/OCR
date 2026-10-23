"""
Cell refinement (pure logic, no DB / Redis): decide which cells to re-read, read
them again from a tight crop, and judge the new reading with the same column
rules the first pass used.

Tiers, cheapest first
  t1   Tesseract on the single cell, upscaled, single-line mode, characters
       limited to what the column holds (digits for a serial, ...)
  vlm  the vision model on the same crop (only for what t1 could not fix)

A new reading replaces the old one only when it passes the column's checks
(or, for a column the printed-text reader cannot read at all, is marked as a
vision-model reading that still needs a look). User-edited cells are never touched.
"""
from __future__ import annotations

import io
import re
from difflib import SequenceMatcher
import subprocess
from dataclasses import dataclass
from statistics import median

from PIL import Image, ImageOps

from app.core.config import settings
from app.core.logging import get_logger
from app.fast.tableschema import ACCEPT_AT, _reason, strip_edges, validate_cell

logger = get_logger(__name__)

_WHITELIST = {
    "serial": "0123456789",
    "number": "0123456789.,-",
    "date": "0123456789-/.",
    "id": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-",
    "enum": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ",
    "name": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz .'-",
}
_VLM_KINDS = {"nonlatin", "name", "text", "id", "date", "enum"}


@dataclass
class Target:
    table_pos: int
    table_index: int
    row: int
    col: int
    page: int
    tier: str                # "t1" | "vlm"
    kind: str
    priority: tuple


@dataclass
class Change:
    table_index: int
    row: int
    col: int
    base_rev: int
    text: str
    state: str
    confidence: float
    reasons: list
    suggestion: str | None
    tier: str
    raw_text: str | None = None
    rev: int = 0                 # the cell's revision after the change was applied


# ── choosing what to refine ──────────────────────────────────────────────────
def _cell_map(table: dict) -> dict[tuple[int, int], dict]:
    return {(c["row"], c["col"]): c for c in table.get("cells") or []}


def _spec_for(table: dict, col: int) -> dict:
    for s in (table.get("meta") or {}).get("columns") or []:
        if s.get("index") == col:
            return s
    return {"kind": "text", "index": col}


def pending_targets(tables: list[dict], focus: dict | None, vlm_left: int) -> list[Target]:
    """Flagged, un-edited cells that still have an untried tier, most urgent first.
    `focus` is what the viewer is looking at: {"table_index", "row_from", "row_to"}."""
    out: list[Target] = []
    for pos, t in enumerate(tables):
        cells = t.get("cells") or []
        if not cells:
            continue
        f_rows = None
        if focus and focus.get("table_index") == t.get("table_index"):
            f_rows = (focus.get("row_from", 0), focus.get("row_to", 10 ** 6))
        for c in cells:
            if c.get("state") != "flagged" or c.get("edited") or not c.get("bbox"):
                continue
            spec = _spec_for(t, c["col"]) if not c.get("is_header") else {"kind": "header"}
            kind = spec.get("kind", "text")
            tried = set(c.get("tried") or [])
            tier = None
            if "t1" not in tried and kind in _WHITELIST | {"text": ""} | {"header": ""} and kind != "nonlatin":
                tier = "t1"
            elif "vlm" not in tried and settings.FAST_VLM_FALLBACK_ENABLED and vlm_left > 0 and \
                    (kind in _VLM_KINDS or c.get("is_header")):
                tier = "vlm"
            if tier is None:
                continue
            in_focus = f_rows is not None and f_rows[0] <= c["row"] <= f_rows[1]
            dist = 0 if in_focus else (abs(c["row"] - f_rows[0]) if f_rows else c["row"])
            # focus first, then cheap tier before the model, then top-to-bottom reading order
            out.append(Target(pos, t.get("table_index", pos + 1), c["row"], c["col"], c.get("page", t.get("page_number", 1)),
                              tier, kind, (0 if in_focus else 1, 0 if tier == "t1" else 1, dist, c["row"], c["col"])))
    out.sort(key=lambda x: x.priority)
    return out


# ── reading a crop ───────────────────────────────────────────────────────────
def crop_cell(page_image: Image.Image, bbox: dict, inset: int = 3, scale: int = 2) -> Image.Image:
    x1, y1, x2, y2 = bbox["x1"] + inset, bbox["y1"] + inset, bbox["x2"] - inset, bbox["y2"] - inset
    if x2 - x1 < 6 or y2 - y1 < 6:
        x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
    crop = page_image.convert("L").crop((max(0, x1), max(0, y1), x2, y2))
    crop = ImageOps.autocontrast(crop, cutoff=1)
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    return ImageOps.expand(crop, border=12, fill=255)


class CropReader:
    """Tesseract on one small image. Reuses the worker's resolved binary."""

    def __init__(self, cmd: str | None = None):
        self._cmd = cmd

    def _binary(self) -> str:
        if self._cmd is None:
            from app.core.model_registry import get_tesseract_engine
            eng = get_tesseract_engine()
            if not getattr(eng, "_is_loaded", True):
                eng.load_model()
            self._cmd = eng._cmd
        return self._cmd

    def read(self, img: Image.Image, psm: int = 7, whitelist: str | None = None) -> tuple[str, float | None]:
        buf = io.BytesIO()
        img.convert("L").save(buf, format="PNG")
        cmd = [self._binary(), "stdin", "stdout", "--psm", str(psm), "--oem", "3"]
        if whitelist:
            cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
        cmd.append("tsv")
        try:
            res = subprocess.run(cmd, input=buf.getvalue(), capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            return "", None
        if res.returncode != 0:
            return "", None
        words, confs = [], []
        for ln in res.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
            p = ln.split("\t")
            if len(p) >= 12 and p[0] == "5" and p[11].strip():
                words.append(p[11].strip())
                try:
                    c = float(p[10])
                    if c >= 0:
                        confs.append(c / 100.0)
                except ValueError:
                    pass
        return " ".join(words), (sum(confs) / len(confs) if confs else None)


# ── judging a new reading ────────────────────────────────────────────────────
def serial_ctx(table: dict, row: int, col: int) -> dict:
    cm = _cell_map(table)

    def val(r: int) -> int | None:
        c = cm.get((r, col))
        if not c or c.get("state") == "flagged":
            return None
        txt = str(c.get("text") or "").strip()
        return int(txt) if txt.isdigit() and len(txt) <= 4 else None

    prev = val(row - 1) if row > 1 else None
    nxt = val(row + 1)
    return {"expected": (prev + 1) if prev is not None else (1 if row == 1 else None), "next": nxt}


def _single_line(table: dict, bbox: dict) -> bool:
    heights = [c["bbox"]["y2"] - c["bbox"]["y1"] for c in table.get("cells") or [] if c.get("bbox") and not c.get("is_header")]
    return not heights or (bbox["y2"] - bbox["y1"]) <= 1.6 * median(heights)


def refine_with_tesseract(table: dict, target: Target, page_image: Image.Image, reader: CropReader) -> Change | None:
    cell = _cell_map(table)[(target.row, target.col)]
    kind = target.kind
    crop = crop_cell(page_image, cell["bbox"])
    psm = 7 if _single_line(table, cell["bbox"]) else 6
    text, conf = reader.read(crop, psm=psm, whitelist=_WHITELIST.get(kind))
    text = text.strip()
    if not text:
        return None
    if cell.get("is_header"):
        spec = {"kind": "text"}
    else:
        spec = _spec_for(table, target.col)
    ctx = serial_ctx(table, target.row, target.col) if kind == "serial" else None
    v = validate_cell(spec, text, conf, ctx)
    if v.state != "accepted":
        return None
    same = v.text.strip() == (cell.get("text") or "").strip()
    if kind in ("name", "text") and not cell.get("is_header") and (cell.get("text") or "").strip():
        # a re-read that drops or invents a lot of letters is a different reading, not a correction
        a, b = re.sub(r"[^a-z]", "", cell["text"].lower()), re.sub(r"[^a-z]", "", v.text.lower())
        if a and b and SequenceMatcher(None, a, b).ratio() < 0.8:
            return None
    note = "Confirmed by a second read of the cell" if same else "Re-read from a close-up of the cell"
    reasons = [r for r in v.reasons if r["code"] != "cleaned"] + [_reason("re_read", note, "info")]
    return Change(target.table_index, target.row, target.col, cell.get("rev", 0), v.text, "accepted", v.confidence,
                  reasons, None, "t1", raw_text=text)


_VLM_PROMPT = {
    "nonlatin": "Transcribe the handwriting or printed text in this image (it may be Hindi/Devanagari). Copy it exactly.",
    "date": "Transcribe the date in this image exactly as written (digits and separators only).",
    "id": "Transcribe the identifier in this image exactly (letters, digits, hyphen).",
    "serial": "Transcribe the number in this image.",
    "default": "Transcribe the text in this image exactly as written. Do not guess or complete it.",
}


def refine_with_vlm(table: dict, target: Target, page_image: Image.Image, vlm, t1_text: str = "") -> Change | None:
    cell = _cell_map(table)[(target.row, target.col)]
    kind = "text" if cell.get("is_header") else target.kind
    crop = crop_cell(page_image, cell["bbox"], inset=2, scale=3)
    header = table.get("headers", [""] * (target.col + 1))[target.col] if not cell.get("is_header") else ""
    text = vlm.read_cell(crop, _VLM_PROMPT.get(kind, _VLM_PROMPT["default"]), header).strip()
    if not text:
        return None
    base = Change(target.table_index, target.row, target.col, cell.get("rev", 0), text, "flagged", 0.55, [], None, "vlm",
                  raw_text=text)
    if kind == "nonlatin" or cell.get("is_header"):
        # nothing in the document can confirm a Hindi / handwritten reading: show it, keep it marked
        base.reasons = [_reason("vlm_read", "Read by the vision model; please verify", "warn")]
        return base
    spec = _spec_for(table, target.col)
    ctx = serial_ctx(table, target.row, target.col) if kind == "serial" else None
    v = validate_cell(spec, text, 0.6, ctx)
    if v.state == "accepted" and v.text.strip() != (cell.get("text") or "").strip():
        base.text, base.state, base.confidence = v.text, "accepted", min(0.8, v.confidence)
        base.reasons = [_reason("vlm_read", "Read by the vision model; fits the column", "info")]
        return base
    return None


# ── applying changes to a stored table ───────────────────────────────────────
def apply_changes(tables: list[dict], changes: list[Change]) -> list[Change]:
    """Write accepted changes into the stored tables (in place). A change whose
    cell was edited by a person, or changed since it was read, is dropped.
    Returns the changes that were applied."""
    from app.fast.tableschema import recount_quality
    applied: list[Change] = []
    touched: set[int] = set()
    for ch in changes:
        pos = next((i for i, t in enumerate(tables) if t.get("table_index") == ch.table_index), None)
        if pos is None:
            continue
        cell = _cell_map(tables[pos]).get((ch.row, ch.col))
        if cell is None or cell.get("edited") or cell.get("rev", 0) != ch.base_rev:
            continue
        if "raw_text" not in cell and cell.get("text") != ch.text:
            cell["raw_text"] = cell.get("text", "")
        cell["text"], cell["state"], cell["confidence"] = ch.text, ch.state, ch.confidence
        cell["reasons"], cell["rev"] = ch.reasons, ch.base_rev + 1
        cell["tried"] = sorted(set(cell.get("tried") or []) | {ch.tier})
        cell.pop("suggestion", None)
        if ch.suggestion:
            cell["suggestion"] = ch.suggestion
        ch.rev = cell["rev"]
        applied.append(ch)
        touched.add(pos)
    for pos in touched:
        t = tables[pos]
        cells = t["cells"]
        n_cols = len(t.get("headers") or [])
        for c in cells:
            if c["row"] == 0 and c["col"] < n_cols:
                t["headers"][c["col"]] = c["text"]
            elif c["row"] >= 1 and c["col"] < n_cols and c["row"] - 1 < len(t["rows"]):
                t["rows"][c["row"] - 1][c["col"]] = c["text"]
        from app.services.table_edit import _markdown
        t["markdown"] = _markdown(t["headers"], t["rows"])
        recount_quality(t)
    return applied


def mark_tried(tables: list[dict], target: Target) -> None:
    for t in tables:
        if t.get("table_index") == target.table_index:
            cell = _cell_map(t).get((target.row, target.col))
            if cell is not None:
                cell["tried"] = sorted(set(cell.get("tried") or []) | {target.tier})
