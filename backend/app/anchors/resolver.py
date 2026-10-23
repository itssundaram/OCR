"""
DOCINT - anchor resolver (pure functions, no I/O, no OCR calls).

An *anchor* is what a human confirmed on the review page: "this field's value
is inside this box on this page".  The resolver reads that value back out of
words that were ALREADY recognised (one full-page OCR pass), so using a
verified anchor costs no extra OCR.

Stored anchor (v2, every key optional except ``bbox``)::

    {
      "bbox":        {"x1","y1","x2","y2"},   # page pixels at save time
      "rel_bbox":    {"x1","y1","x2","y2"},   # same box as 0..1 fractions of the page
      "page_number": 1,
      "page_width": 1654, "page_height": 2338,
      "label_text":  "engine no",            # printed label found next to the box
      "label_rel_bbox": {...},               # where that label sat (0..1)
      "value":       "WD4 - 299332",         # the value the human confirmed
      "shape":       "AA9 - 999999",         # coarse character-class shape of value
    }

Resolution order inside ``resolve_anchor``:
  1. page-relative box (so a different DPI / page size still lines up);
  2. landmark alignment - if the printed label is found near where it used to be,
     shift the box by the same offset (handles shifted scans);
  3. read the words whose centres fall in the (slightly padded) box;
  4. validate against the field's regex / blacklist / learned shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

Box = dict  # {"x1","y1","x2","y2"}

_LEADING_JUNK = re.compile(r"^[\s:\-\|\~\+\.\*_=;,]+")
_TRAILING_JUNK = re.compile(r"[\s\|\~_=;,:]+$")


# ── geometry helpers ──────────────────────────────────────────────────────────
def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def box_ok(b: Any) -> bool:
    try:
        return (
            isinstance(b, dict)
            and float(b["x2"]) > float(b["x1"])
            and float(b["y2"]) > float(b["y1"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def to_relative(b: Box, page_w: float, page_h: float) -> Box:
    page_w = float(page_w or 1) or 1.0
    page_h = float(page_h or 1) or 1.0
    return {
        "x1": round(float(b["x1"]) / page_w, 5),
        "y1": round(float(b["y1"]) / page_h, 5),
        "x2": round(float(b["x2"]) / page_w, 5),
        "y2": round(float(b["y2"]) / page_h, 5),
    }


def to_absolute(rel: Box, page_w: float, page_h: float) -> Box:
    return {
        "x1": float(rel["x1"]) * page_w,
        "y1": float(rel["y1"]) * page_h,
        "x2": float(rel["x2"]) * page_w,
        "y2": float(rel["y2"]) * page_h,
    }


def _center(w: Box) -> tuple[float, float]:
    return (float(w["x1"]) + float(w["x2"])) / 2.0, (float(w["y1"]) + float(w["y2"])) / 2.0


def _union(ws: Iterable[Box]) -> Box:
    ws = list(ws)
    return {
        "x1": min(float(w["x1"]) for w in ws),
        "y1": min(float(w["y1"]) for w in ws),
        "x2": max(float(w["x2"]) for w in ws),
        "y2": max(float(w["y2"]) for w in ws),
    }


# ── word sources ──────────────────────────────────────────────────────────────
def words_from_lines(lines: list) -> list[dict]:
    """OCRLine objects (app.ocr.base) -> flat word dicts."""
    out: list[dict] = []
    for ln in lines or []:
        for w in getattr(ln, "words", None) or []:
            bb = getattr(w, "bbox", None)
            txt = (getattr(w, "text", "") or "").strip()
            if bb is None or not txt:
                continue
            out.append({
                "text": txt,
                "x1": float(bb.x1), "y1": float(bb.y1), "x2": float(bb.x2), "y2": float(bb.y2),
                "conf": float(getattr(w, "confidence", None) or 0.0),
            })
    return out


def words_from_detections(dets: list[dict] | None, n_pages: int = 1) -> list[list[dict]]:
    """Persisted ``all_detections`` entries -> per-page word lists."""
    pages: list[list[dict]] = [[] for _ in range(max(1, n_pages))]
    for d in dets or []:
        bb = d.get("bbox") or {}
        txt = str(d.get("text") or "").strip()
        if not txt or not box_ok(bb):
            continue
        p = int(d.get("page_number") or 1)
        while len(pages) < p:
            pages.append([])
        pages[p - 1].append({
            "text": txt,
            "x1": float(bb["x1"]), "y1": float(bb["y1"]),
            "x2": float(bb["x2"]), "y2": float(bb["y2"]),
            "conf": float(d.get("confidence") or 0.0),
        })
    return pages


# ── value shape ───────────────────────────────────────────────────────────────
def value_shape(value: str) -> str:
    """'WD4 - 299332' -> 'A9-9' : letters->A, digits->9, runs collapsed, spaces dropped."""
    out: list[str] = []
    for ch in value or "":
        if ch.isspace():
            continue
        c = "A" if ch.isalpha() else "9" if ch.isdigit() else ch
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


def shape_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 1.0  # nothing learned -> do not penalise
    return SequenceMatcher(None, a, b).ratio()


# ── label near a box (used at save time and for landmark alignment) ──────────
def find_label_near(words: list[dict], bbox: Box, page_w: float | None = None) -> dict | None:
    """Printed label sitting left of (same line) or just above the value box."""
    if not words or not box_ok(bbox):
        return None
    bx1, by1, bx2, by2 = (float(bbox[k]) for k in ("x1", "y1", "x2", "y2"))
    bh = max(1.0, by2 - by1)
    bcy = (by1 + by2) / 2.0
    max_left = (float(page_w) * 0.40) if page_w else 700.0

    def _ok(text: str) -> bool:
        letters = sum(c.isalpha() for c in text)
        return letters >= 2 and letters / max(1, len(text.replace(" ", ""))) >= 0.5

    # 1. same line, to the left
    left = [
        w for w in words
        if float(w["x2"]) <= bx1 + 6
        and re.search(r"[A-Za-z0-9]", w["text"])          # ':' '|' '-' between label and value are not the label
        and bx1 - float(w["x2"]) <= max_left
        and abs(_center(w)[1] - bcy) <= max(bh, float(w["y2"]) - float(w["y1"])) * 0.8
    ]
    if left:
        left.sort(key=lambda w: -float(w["x2"]))
        run = [left[0]]
        for w in left[1:]:
            h = max(1.0, float(w["y2"]) - float(w["y1"]))
            if float(run[-1]["x1"]) - float(w["x2"]) <= max(40.0, 3.0 * h):
                run.append(w)
            else:
                break
        run.sort(key=lambda w: float(w["x1"]))
        text = " ".join(w["text"] for w in run).strip()
        text = re.sub(r"[\s:\-\.]+$", "", text)
        if _ok(text):
            return {"text": text, "bbox": _union(run)}

    # 2. directly above, overlapping horizontally
    above = [
        w for w in words
        if float(w["y2"]) <= by1 + 4
        and by1 - float(w["y2"]) <= bh * 3.0
        and float(w["x2"]) >= bx1 - 10 and float(w["x1"]) <= bx2 + 10
    ]
    if above:
        nearest_y = max(float(w["y2"]) for w in above)
        row = [w for w in above if nearest_y - float(w["y2"]) <= bh * 0.8]
        row.sort(key=lambda w: float(w["x1"]))
        text = re.sub(r"[\s:\-\.]+$", "", " ".join(w["text"] for w in row).strip())
        if _ok(text):
            return {"text": text, "bbox": _union(row)}
    return None


def _locate_label(words: list[dict], label_text: str, expect_cx: float, expect_cy: float,
                  page_w: float, page_h: float) -> Box | None:
    """Find the printed label on this page; the candidate nearest to where it was wins."""
    target = _norm_text(label_text)
    if len(target) < 3 or not words:
        return None
    n_tok = max(1, len(label_text.split()))
    best: tuple[float, Box] | None = None
    for k in (n_tok,):   # same token count only: a sub/super-window has a different centre and would fake a shift
        for i in range(0, len(words) - k + 1):
            win = words[i:i + k]
            cand = _norm_text(" ".join(w["text"] for w in win))
            if not cand:
                continue
            score = 1.0 if cand == target else SequenceMatcher(None, target, cand).ratio()
            if score < 0.85:
                continue
            ub = _union(win)
            cx, cy = _center(ub)
            dx, dy = abs(cx - expect_cx), abs(cy - expect_cy)
            if dy > page_h * 0.15 or dx > page_w * 0.25:
                continue  # a different occurrence far away - not our landmark
            dist = dx / page_w + dy / page_h
            rank = dist - score * 0.05
            if best is None or rank < best[0]:
                best = (rank, ub)
    return best[1] if best else None


# ── the resolver ──────────────────────────────────────────────────────────────
@dataclass
class AnchorReading:
    value: str
    confidence: float
    bbox: Box
    page_number: int
    valid: bool = True               # passed regex / blacklist / shape checks
    aligned: bool = False            # landmark alignment shifted the box
    dx: float = 0.0
    dy: float = 0.0
    n_words: int = 0
    reason: str = ""
    extra: dict = field(default_factory=dict)


def expected_box(anchor: dict, page_w: float, page_h: float, orig_size: tuple[float, float] | None = None) -> Box | None:
    """Anchor's value box in the CURRENT page's pixel space."""
    rel = anchor.get("rel_bbox")
    if box_ok(rel):
        return to_absolute(rel, page_w, page_h)
    bb = anchor.get("bbox")
    if not box_ok(bb):
        return None
    ow = float(anchor.get("page_width") or (orig_size[0] if orig_size else 0) or page_w)
    oh = float(anchor.get("page_height") or (orig_size[1] if orig_size else 0) or page_h)
    return to_absolute(to_relative(bb, ow, oh), page_w, page_h)


def resolve_anchor(
    anchor: dict,
    words_by_page: list[list[dict]],
    page_sizes: list[tuple[float, float]],
    *,
    orig_size: tuple[float, float] | None = None,
    spec: dict | None = None,
    shift: tuple[float, float] | None = None,
) -> AnchorReading | None:
    """Read one field's value from its verified anchor. None = nothing readable there."""
    if not isinstance(anchor, dict) or not words_by_page or not page_sizes:
        return None
    p_idx = min(max(0, int(anchor.get("page_number") or 1) - 1), len(words_by_page) - 1)
    words = words_by_page[p_idx]
    page_w, page_h = page_sizes[min(p_idx, len(page_sizes) - 1)]
    box = expected_box(anchor, page_w, page_h, orig_size)
    if box is None or not words:
        return None

    # landmark alignment
    dx = dy = 0.0
    aligned = False
    landmark = False
    label = (anchor.get("label_text") or "").strip()
    if label:
        lrel = anchor.get("label_rel_bbox")
        if box_ok(lrel):
            lb = to_absolute(lrel, page_w, page_h)
            ecx, ecy = _center(lb)
        else:  # label assumed immediately left of the box on the same line
            ecx, ecy = box["x1"] - 60.0, (box["y1"] + box["y2"]) / 2.0
        found = _locate_label(words, label, ecx, ecy, page_w, page_h)
        if found is not None:
            landmark = True                       # the printed label really is where the anchor expects it
            fcx, fcy = _center(found)
            dx, dy = fcx - ecx, fcy - ecy
            # only trust a believable shift (<= 8% of the page)
            if abs(dx) <= page_w * 0.08 and abs(dy) <= page_h * 0.08 and (abs(dx) > 1.5 or abs(dy) > 1.5):
                box = {"x1": box["x1"] + dx, "y1": box["y1"] + dy, "x2": box["x2"] + dx, "y2": box["y2"] + dy}
                aligned = True
            else:
                dx = dy = 0.0

    if not aligned and shift is not None:
        # this field's own label was not found; apply the page shift measured from other fields' labels
        dx, dy = float(shift[0]), float(shift[1])
        box = {"x1": box["x1"] + dx, "y1": box["y1"] + dy, "x2": box["x2"] + dx, "y2": box["y2"] + dy}
        aligned = True

    bw, bh = box["x2"] - box["x1"], box["y2"] - box["y1"]
    pad_x = max(8.0, bw * 0.10)
    pad_y = max(6.0, bh * 0.25)
    ex1, ey1, ex2, ey2 = box["x1"] - pad_x, box["y1"] - pad_y, box["x2"] + pad_x, box["y2"] + pad_y

    inside = []
    for w in words:
        cx, cy = _center(w)
        if ex1 <= cx <= ex2 and ey1 <= cy <= ey2:
            inside.append(w)
    if not inside:
        return None

    # drop the printed label if the box overlaps it
    if label:
        lab_tokens = {_norm_text(t) for t in label.split() if _norm_text(t)}
        trimmed = [w for w in inside if _norm_text(w["text"]) not in lab_tokens]
        if trimmed:
            inside = trimmed

    # reading order: cluster by line, then left->right
    inside.sort(key=lambda w: (_center(w)[1], float(w["x1"])))
    med_h = sorted(float(w["y2"]) - float(w["y1"]) for w in inside)[len(inside) // 2] or 10.0
    lines: list[list[dict]] = []
    for w in inside:
        if lines and abs(_center(w)[1] - _center(lines[-1][0])[1]) <= med_h * 0.6:
            lines[-1].append(w)
        else:
            lines.append([w])
    ordered = [w for ln in lines for w in sorted(ln, key=lambda x: float(x["x1"]))]
    # edge tokens that are only punctuation (the ':' after a label) are not part of the value
    while ordered and not re.search(r"[A-Za-z0-9]", ordered[0]["text"]):
        ordered = ordered[1:]
    while ordered and not re.search(r"[A-Za-z0-9]", ordered[-1]["text"]):
        ordered = ordered[:-1]
    if not ordered:
        return None
    raw = " ".join(w["text"] for w in ordered).strip()
    value = _TRAILING_JUNK.sub("", _LEADING_JUNK.sub("", raw)).strip()
    if not value:
        return None

    confs = [float(w.get("conf") or 0.0) for w in ordered if float(w.get("conf") or 0.0) > 0]
    conf = (sum(confs) / len(confs)) if confs else 0.6
    conf = min(1.0, max(0.0, conf))
    used = ordered
    valid, reason = True, ""

    # validation
    # A human-confirmed value carries its own learned shape; the generic per-field regex
    # (which cannot match spaced values like 'WD4 - 299332' whole) must not truncate it.
    rx = (spec or {}).get("regex") if not anchor.get("shape") else None
    if rx:
        m = re.search(rx, value, re.IGNORECASE)
        if m:
            value = m.group(0).strip()
            toks = [t for t in value.split() if t]
            sel = [w for w in ordered if any(t in w["text"] for t in toks)]
            used = sel or ordered
        else:
            valid, reason = False, "regex_mismatch"
    for bad in (spec or {}).get("blacklist") or []:
        if bad and bad.lower() in value.lower():
            valid, reason = False, "blacklisted"
            break
    sim = shape_similarity(anchor.get("shape") or "", value_shape(value))
    if sim < 0.5:
        valid, reason = False, reason or "shape_mismatch"
    if valid:
        conf = max(conf, 0.5) * (0.85 + 0.15 * sim)
    else:
        conf = min(conf, 0.3)

    tight = _union(used)
    return AnchorReading(
        value=value,
        confidence=round(conf, 3),
        bbox={k: int(round(v)) for k, v in tight.items()},
        page_number=p_idx + 1,
        valid=valid,
        aligned=aligned,
        dx=round(dx, 1),
        dy=round(dy, 1),
        n_words=len(used),
        reason=reason,
        extra={"shape_similarity": round(sim, 3), "lines": len(lines), "landmark": landmark, "has_label": bool(label)},
    )
