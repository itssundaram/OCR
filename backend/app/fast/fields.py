"""
Field extraction on top of the words every page already has.

  1. discover every "Label : value" pair on the page (kv_discovery)
  2. match the template's fields to those pairs by label (kv_match)
  3. fields still empty: find the label without a colon and read to its right
     or just below it (label proximity)
  4. a field whose label was found but whose value is missing or fails its
     checks: re-read ONE crop with the VLM; the answer must be backed by OCR
  5. validate (checks + confidence) and give every field a state

Fields from step 2 are announced as soon as their page is read
(`field_extracted`, provisional); step 3-5 and the final states happen once all
pages are in, because a better pair for a field can still turn up on a later page.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from dataclasses import dataclass, field as dc_field

from app.core.config import settings
from app.core.logging import get_logger
from app.fast.events import EventBus
from app.fast.types import PageData
from app.fast.validate import assess
from app.fast.vlm import FieldVlm, supported_by_ocr
from app.ocr.base import OCRLine
from app.ocr import label_kb
from app.ocr.kv_discovery import DiscoveredPair, assign_unique_keys, discover_key_values
from app.ocr.kv_match import _candidates, _score, _side_hint, _tokens, _value_ok, match_fields
from app.anchors import resolve_anchor, words_from_lines
from app.anchors.apply import anchor_is_trusted
from app.pipelines.base import FieldResult

logger = get_logger(__name__)


@dataclass
class FieldMeta:
    state: str = "failed"             # validated | review_required | extracted | failed
    source: str = ""                  # kv_pair | label_proximity | vlm_fallback | discovered
    checks: list = dc_field(default_factory=list)
    reason: str = ""


def _box(b) -> dict | None:
    return None if b is None else {"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2}


def _union(words) -> dict | None:
    ws = [w for w in words if w.bbox]
    if not ws:
        return None
    return {"x1": min(w.bbox.x1 for w in ws), "y1": min(w.bbox.y1 for w in ws),
            "x2": max(w.bbox.x2 for w in ws), "y2": max(w.bbox.y2 for w in ws)}


_LABEL_CONTINUERS = {"of", "to", "for", "from", "in", "on", "by", "and", "no", "no.", "number", "type", "name"}


def _overlaps(a: dict | None, b: dict | None, frac: float = 0.5) -> bool:
    """True when the two boxes share more than `frac` of the smaller one's area."""
    if not a or not b:
        return False
    ix = min(a["x2"], b["x2"]) - max(a["x1"], b["x1"])
    iy = min(a["y2"], b["y2"]) - max(a["y1"], b["y1"])
    if ix <= 0 or iy <= 0:
        return False
    small = min((a["x2"] - a["x1"]) * (a["y2"] - a["y1"]), (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]))
    return small > 0 and ix * iy / small > frac


def _avg_conf(words) -> float:
    cs = [w.confidence for w in words if w.confidence is not None]
    return sum(cs) / len(cs) if cs else 0.0


class FieldExtractor:
    def __init__(self, template_fields: list[dict], bus: EventBus, ocr_engine=None, vlm: FieldVlm | None = None,
                 anchors: dict | None = None):
        self.anchors = anchors if isinstance(anchors, dict) else None
        self.fields = [f for f in (template_fields or [])
                       if f.get("name") and str(f.get("type") or "").lower() != "table"]
        self.bus = bus
        self.pairs: list[DiscoveredPair] = []
        self.pages: dict[int, PageData] = {}
        self._announced: dict[str, int] = {}
        self._announced_vals: dict[str, str] = {}
        self._ocr_engine = ocr_engine
        self._vlm = vlm
        self.meta: dict[str, FieldMeta] = {}
        self.timings: dict[str, int] = {}
        self._claimed: list[tuple[int, dict]] = []     # (page, value box) already given to a field

    # ── per page (called as each page is read) ────────────────────────────────
    def add_page(self, page: PageData) -> None:
        t0 = time.perf_counter()
        self.pages[page.page_number] = page
        if settings.KV_DISCOVERY_ENABLED or self.fields:
            for pair in discover_key_values([page.lines]):
                pair.page_number = page.page_number
                self.pairs.append(pair)
        for name, m in match_fields(self.fields, self.pairs).items():
            if self._announced.get(name) != id(m.pair):
                self._announced[name] = id(m.pair)
                self._announced_vals[name] = m.pair.value
                self.bus.emit(
                    "field_extracted", page=m.pair.page_number, field=name, label=m.pair.label,
                    value=m.pair.value, confidence=round(m.pair.confidence, 3), state="extracted",
                    provisional=True, source="kv_pair", bbox=m.pair.bbox,
                )
        self.timings["kv_ms"] = self.timings.get("kv_ms", 0) + int((time.perf_counter() - t0) * 1000)

    # ── label proximity (labels printed without a colon) ───────────────────────
    def _label_tokens_all(self) -> list[list[str]]:
        """Every template field's label spellings (and their synonyms): words that start ANOTHER field's value."""
        cached = getattr(self, "_all_label_tokens", None)
        if cached is None:
            cached = []
            for f in self.fields:
                for c in _candidates(f):
                    cached.append(c)
                    cached.extend(label_kb.synonyms_of(c))
            self._all_label_tokens = cached
        return cached

    def _cut_value(self, words: list, label_h: float) -> list:
        """The value is what sits right after its label on the SAME stretch of the row: it ends at a wide gap
        (the next column) or where another field's label begins. Stops "Dated : 2 Jun 2025   Dated : ..." and
        "IV No : 20/4/MT/02   RV No : ..." from becoming one long value (and one long box)."""
        out: list = []
        limit = max(3.2 * label_h, 45.0)
        others = self._label_tokens_all()
        for n, w in enumerate(words):
            if out and w.bbox and out[-1].bbox and w.bbox.x1 - out[-1].bbox.x2 > limit:
                break
            if out:
                raw = w.text.strip()
                if raw.endswith(":") and len(raw) > 2 and raw[0].isalpha():
                    break                                           # "Name:" starts the next field
                nxt = words[n + 1].text.strip() if n + 1 < len(words) else ""
                if nxt in (":", "：") and raw[:1].isalpha():
                    break
                if raw[:1].isupper() and any(
                    len("".join(o)) >= 3 and len(o) <= 3
                    and _score(o, [t for x in words[n:n + len(o)] for t in _tokens(x.text)]) >= 0.9
                    for o in others
                ):
                    break                                           # another field's label begins ("Unit", "RV No")
            out.append(w)
        return out

    def _continue_value(self, page: PageData, ln: OCRLine, value_words: list, label_box: dict) -> list:
        """A value that runs to the right edge of the page wraps onto the next line ("Auth : ... dt 30 Jan 2025 and
        23 Inf DOU" / "disposal order No ..."): take that next line too, unless it is another field's row."""
        if not value_words or not page.width:
            return value_words
        vbox = _union(value_words)
        if not vbox or vbox["x2"] < 0.78 * page.width or (ln.words and value_words[-1] is not ln.words[-1]):
            return value_words                       # not a full-width line, or the row went on past a cut: no wrap
        lh = max(8.0, label_box["y2"] - label_box["y1"])
        best = None
        for other in page.lines:
            if other is ln or other.bbox is None:
                continue
            gap = other.bbox.y1 - ln.bbox.y2
            if gap < -0.3 * lh or gap > 1.1 * lh:
                continue
            if not (label_box["x1"] - lh <= other.bbox.x1 <= vbox["x1"] + 2 * lh):
                continue
            text = " ".join(w.text for w in other.words)
            if any(w.text.endswith(":") or w.text in (":", "\uff1a") for w in other.words) or re.match(r"^\W*\d+\W*$", text):
                continue
            if best is None or gap < best[0]:
                best = (gap, other)
        if best is None:
            return value_words
        extra = self._cut_value([w for w in best[1].words if any(ch.isalnum() for ch in w.text)], lh)
        return list(value_words) + extra

    def _proximity(self, fdef: dict, pages=None):
        """Find the field's label without needing a colon. Returns
        (page, label_box, value_words) for the best label occurrence: exact beats typo-tolerant; a label that
        opens its stretch of the row or is followed by ":" / "-" beats the same words inside a sentence; a hit
        that has a value beats one without."""
        cands = _candidates(fdef)
        if not cands:
            return None
        # other names for the same thing ("From" for a field called unit) are tried too, ranked below exact names
        weighted = [(c, 1.0) for c in cands]
        known = {tuple(c) for c in cands}
        for c in cands:
            for syn in label_kb.synonyms_of(c):
                if tuple(syn) not in known:
                    known.add(tuple(syn))
                    weighted.append((syn, 0.93))
        best, best_key = None, None
        hint = _side_hint(fdef)
        for page in (pages if pages is not None else self.pages.values()):
            for ln in page.lines:
                words = ln.words
                for cand, weight in weighted:
                    # OCR may glue or split label words ("BA No" -> "BAN"): try windows one word shorter / longer too
                    for k in sorted({len(cand), max(1, len(cand) - 1), len(cand) + 1}):
                        for i in range(0, len(words) - k + 1):
                            seg = words[i:i + k]
                            if any(w.bbox is None for w in seg):
                                continue
                            score = _score(cand, [t for w in seg for t in _tokens(w.text)])
                            if score < 0.9:
                                continue
                            label_box = _union(seg)
                            lh = max(8.0, label_box["y2"] - label_box["y1"])
                            gap = max(2.2 * lh, 30.0)
                            opens = i == 0 or (words[i - 1].bbox is not None and label_box["x1"] - words[i - 1].bbox.x2 > gap) \
                                or (i == 1 and not any(ch.isalpha() for ch in words[0].text))      # "1. | Veh BA No"
                            sep = (i + k < len(words) and words[i + k].text.strip() in (":", "-", "=", "\u2013", "\u2014", ">", ";", "\uff1a")) \
                                or seg[-1].text.rstrip().endswith(":")
                            tail = [w for w in words[i + k:] if any(ch.isalnum() for ch in w.text)]
                            value_words = self._continue_value(page, ln, self._cut_value(tail, lh), label_box)
                            vbox = _union(value_words)
                            if vbox and any(pn == page.page_number and _overlaps(vbox, cb) for pn, cb in self._claimed):
                                continue                      # that value already belongs to another field
                            side = -label_box["x1"] if hint == "left" else label_box["x1"] if hint == "right" else 0
                            nxt = words[i + k].text.strip().lower() if i + k < len(words) else ""
                            longer = nxt in _LABEL_CONTINUERS                 # "Date of Final Insp": a longer label, not "Date"
                            plausible = bool(value_words) and _value_ok(fdef, " ".join(w.text for w in value_words))
                            key = (round(score * weight, 2), not longer, plausible, side, int(opens) + int(sep), bool(value_words),
                                   round(_avg_conf(value_words) * 5) / 5, -page.page_number, -label_box["y1"])
                            if best_key is None or key > best_key:
                                best, best_key = (page, label_box, value_words), key

        return best

    def _occurrences(self, fdef: dict, primary: FieldResult) -> list[dict]:
        """The same field read on the OTHER pages (a voucher printed twice, a photo of its copy): shown on those
        pages in the viewer and offered as an alternative, never merged into the primary value."""
        if len(self.pages) < 2 or not primary.value:
            return []
        name, out = fdef["name"], []
        claimed, self._claimed = self._claimed, []            # each page is searched on its own
        try:
            for n, page in sorted(self.pages.items()):
                if n == primary.page_number:
                    continue
                pairs = [p for p in self.pairs if p.page_number == n]
                m = match_fields([fdef], pairs).get(name)
                if m:
                    value, conf, bbox, src = m.pair.value, m.pair.confidence, m.pair.bbox, "kv_pair"
                else:
                    hit = self._proximity(fdef, pages=[page])
                    if not hit or not hit[2]:
                        continue
                    value = " ".join(w.text for w in hit[2])
                    conf, bbox, src = round(_avg_conf(hit[2]) * 0.9, 3), _union(hit[2]), "label_proximity"
                if sum(ch.isalnum() for ch in value) < 2 or not _value_ok(fdef, value):
                    continue
                out.append({"page_number": n, "value": value, "confidence": round(float(conf), 3),
                            "bbox": bbox, "source": src})
        except Exception as exc:
            logger.warning("fast_occurrences_failed", field=name, error=str(exc)[:160])
        finally:
            self._claimed = claimed
        return out

    def _reread_value(self, page: PageData, label_box: dict):
        """The label was found but nothing was read next to it. Re-read just that spot, as one line, with ruling
        lines taken out and the crop enlarged: cheap (one small Tesseract call) and it recovers values that sit
        in a shaded or ruled box or in small print that the whole-page pass dropped. Returns (text, conf, bbox)."""
        if not settings.FAST_LABEL_REREAD_ENABLED or page.image is None:
            return None
        from app.fast.gapfill import _prepare_crop
        h = max(8.0, label_box["y2"] - label_box["y1"])
        box = (int(min(page.width - 2, label_box["x2"] + 4)), int(max(0, label_box["y1"] - 0.4 * h)),
               int(min(page.width, label_box["x2"] + 0.5 * page.width)), int(min(page.height, label_box["y2"] + 0.6 * h)))
        # stop at the next thing already read on this row (the next column's label), so it is never pulled into the value
        band_lo, band_hi = label_box["y1"], label_box["y2"]
        right_edges = [w.bbox.x1 for w in page.words()
                       if w.bbox and w.bbox.x1 > label_box["x2"] + 6 and band_lo - 0.3 * h <= (w.bbox.y1 + w.bbox.y2) / 2 <= band_hi + 0.3 * h]
        if right_edges:
            box = (box[0], box[1], int(min(box[2], min(right_edges) - 4)), box[3])
        if box[2] - box[0] < 20 or box[3] - box[1] < 10:
            return None
        self._ensure_ocr_engine()
        best = None
        for flatten in (True, False):              # shading removed first (the usual reason a value was dropped), then as is
            crop, scale = _prepare_crop(page.image, box, flatten=flatten)
            for psm in (7, 6):
                lines = self._ocr_engine._ocr_image(crop, 0, psm=psm)
                words = [w for ln in lines for w in ln.words if w.text.strip() and (w.confidence or 0) >= 0.5 and w.bbox]
                if words and sum(ch.isalnum() for w in words for ch in w.text) >= 2:
                    best = words
                    break
            if best:
                break
        if not best:
            return None
        # box borders and stray marks at either end ("lo\u201d 20D-210124N 7 |"): trim short, doubtful edge tokens
        best = sorted(best, key=lambda w: w.bbox.x1)
        def _stray(w) -> bool:
            core = "".join(ch for ch in w.text if ch.isalnum())
            return len(core) <= 2 and (w.confidence or 0) < 0.85
        while len(best) > 1 and _stray(best[0]):
            best = best[1:]
        while len(best) > 1 and _stray(best[-1]):
            best = best[:-1]
        ox, oy = max(0, box[0] - 8), max(0, box[1] - 8)
        bb = {"x1": min((w.bbox.x1 - 12) / scale + ox for w in best), "y1": min((w.bbox.y1 - 12) / scale + oy for w in best),
              "x2": max((w.bbox.x2 - 12) / scale + ox for w in best), "y2": max((w.bbox.y2 - 12) / scale + oy for w in best)}
        text = " ".join(w.text for w in sorted(best, key=lambda w: w.bbox.x1))
        return text, sum(w.confidence for w in best) / len(best), bb

    def _value_line_below(self, page: PageData, label_box: dict) -> list:
        """The line under the label, unless it is itself a "Label - value" row of another field."""
        cy = (label_box["y1"] + label_box["y2"]) / 2
        ln = next((l for l in page.lines if l.bbox and l.bbox.y1 <= cy <= l.bbox.y2
                   and l.bbox.x1 <= label_box["x1"] + 2 and l.bbox.x2 >= label_box["x2"] - 2), None)
        if ln is None:
            return []
        words = self._line_below(page, ln, label_box)
        if words and re.match(r"^\W*[A-Za-z][A-Za-z ./&]{1,25}\s*[:=\-\u2013\u2014]\s", " ".join(w.text for w in words)):
            return []                                   # "Chassis No - STLN ...": a different field's row, not this value
        return words

    def _ensure_ocr_engine(self) -> None:
        if self._ocr_engine is None:
            from app.core.model_registry import get_tesseract_engine
            self._ocr_engine = get_tesseract_engine()
            if not getattr(self._ocr_engine, "_is_loaded", True):
                self._ocr_engine.load_model()

    @staticmethod
    def _line_below(page: PageData, ln: OCRLine, label_box: dict) -> list:
        lh = max(1.0, ln.bbox.y2 - ln.bbox.y1)
        best, best_gap = None, None
        for other in page.lines:
            if other is ln or other.bbox is None:
                continue
            gap = other.bbox.y1 - ln.bbox.y2
            if gap < -0.3 * lh or gap > 2.5 * lh:
                continue
            # the value sits under its label: it starts near the label's left edge
            if abs(other.bbox.x1 - label_box["x1"]) > 4 * lh and not (
                label_box["x1"] - lh <= other.bbox.x1 <= label_box["x2"]
            ):
                continue
            if any(w.text in (":", "：") or w.text.endswith(":") for w in other.words):
                continue                      # that line is itself a "Label : value" row
            if best_gap is None or gap < best_gap:
                best, best_gap = other, gap
        return list(best.words) if best else []

    # ── VLM on one crop ─────────────────────────────────────────────────────────
    def _vlm_read(self, page: PageData, label_box: dict, label: str):
        if page.image is None:
            return None
        h = max(8.0, label_box["y2"] - label_box["y1"])
        box = (
            int(max(0, label_box["x1"] - 20)), int(max(0, label_box["y1"] - 0.8 * h)),
            int(min(page.width, label_box["x2"] + 0.55 * page.width)), int(min(page.height, label_box["y2"] + 2.2 * h)),
        )
        crop = page.image.crop(box)
        if crop.width < 20 or crop.height < 10:
            return None
        if self._vlm is None:
            self._vlm = FieldVlm()
        value = self._vlm.read_field(crop, label)
        if not value:
            return None
        if self._ocr_engine is None:
            from app.core.model_registry import get_tesseract_engine
            self._ocr_engine = get_tesseract_engine()
            if not getattr(self._ocr_engine, "_is_loaded", True):
                self._ocr_engine.load_model()
        crop_text = " ".join(ln.text for ln in self._ocr_engine._ocr_image(crop, 0))
        return value, supported_by_ocr(value, crop_text), box

    # ── human-verified anchors ────────────────────────────────────────────────
    def _read_anchor(self, name: str):
        """Value inside the field's confirmed box, read from the words already on the page."""
        la = self.anchors
        anchor = ((la or {}).get("fields") or {}).get(name)
        if not anchor or not anchor_is_trusted(la, anchor) or not self.pages:
            return None
        try:
            nums = sorted(self.pages)
            words = [words_from_lines(self.pages[n].lines) for n in range(1, max(nums) + 1) if n in self.pages]
            sizes = [(self.pages[n].width, self.pages[n].height) for n in range(1, max(nums) + 1) if n in self.pages]
            rd = resolve_anchor(anchor, words, sizes, orig_size=(la.get("page_width"), la.get("page_height")))
        except Exception as exc:
            logger.warning("fast_anchor_read_failed", field=name, error=str(exc))
            return None
        return rd if (rd is not None and rd.valid and rd.confidence >= 0.35) else None

    def _vet_anchor(self, fdef: dict, rd, m):
        """A confirmed box is trusted first, but not blindly: a box that swallowed a whole block of the page, or
        whose printed label is nowhere near where it was confirmed while the label search finds something else,
        is read from the label instead. Returns (reading or None, note for the reviewer)."""
        extra = getattr(rd, "extra", None) or {}
        if int(extra.get("lines") or 1) > 6:
            return None, "anchor_box_too_large"
        if extra.get("has_label") and not extra.get("landmark") and m is not None:
            from difflib import SequenceMatcher
            norm = lambda t: re.sub(r"[^a-z0-9]", "", (t or "").lower())
            if SequenceMatcher(None, norm(rd.value), norm(m.pair.value)).ratio() < 0.5:
                return None, "anchor_label_not_found"
        return rd, ""

    # ── all pages are in ──────────────────────────────────────────────────────────
    def _ensure_vlm_tools(self) -> None:
        """Create the VLM client and the OCR engine once, in the calling thread (the parallel VLM workers share them)."""
        if self._vlm is None:
            self._vlm = FieldVlm()
        if self._ocr_engine is None:
            from app.core.model_registry import get_tesseract_engine
            self._ocr_engine = get_tesseract_engine()
            if not getattr(self._ocr_engine, "_is_loaded", True):
                self._ocr_engine.load_model()

    def _settle(self, slot: dict) -> FieldResult:
        """The field is decided: set its state, announce it. Runs in the job thread only."""
        name, fdef, meta, fr, a = slot["name"], slot["fdef"], slot["meta"], slot["fr"], slot["a"]
        if fr is None:
            meta.state, meta.reason = "failed", slot.get("skip") or "not_found"
            fr = FieldResult(field_name=name, value="", confidence=0.0, page_number=1,
                             extraction_method="not_found", label=str(fdef.get("label") or ""))
            self.bus.emit("field_failed", field=name, reason=meta.reason)
        else:
            meta.state, meta.checks, meta.reason = a.status, a.checks, a.reason
            if slot.get("skip") and meta.state != "validated":
                meta.reason = meta.reason or slot["skip"]
            # fields found only now (anchor / label proximity / VLM) stream the moment they are
            # resolved, not after the whole job is saved; same-value repeats are skipped
            if fr.value and self._announced_vals.get(name) != fr.value:
                self._announced_vals[name] = fr.value
                self.bus.emit(
                    "field_extracted", page=fr.page_number, field=name, label=fr.label or name,
                    value=fr.value, confidence=round(fr.confidence, 3), state="extracted",
                    provisional=True, source=meta.source or fr.extraction_method, bbox=fr.bbox,
                )
        self.meta[name] = meta
        slot["result"] = fr
        return fr

    def _vlm_phase(self, pending: list[dict], max_calls: int) -> None:
        """Repair the fields the cheap steps could not settle with the vision model.

        Bounded three ways so a new template never waits minutes on it: at most `max_calls` reads, at most
        FAST_VLM_BUDGET_S seconds in total, and the phase stops early when the model keeps failing (Ollama
        down or stalled). Whatever is not repaired is flagged for review, with the reason.
        """
        t0 = time.monotonic()
        deadline = t0 + max(1, settings.FAST_VLM_BUDGET_S)
        todo, over_cap = pending[:max_calls], pending[max_calls:]
        for slot in over_cap:
            slot["skip"] = "vlm_cap_reached"
            self._settle(slot)
        if not todo:
            return
        self._ensure_vlm_tools()
        dead = threading.Event()                       # set after repeated failures: stop calling the model
        failures = [0]

        def _work(slot: dict):
            if dead.is_set() or time.monotonic() > deadline or self.bus.cancel_requested():
                return "skipped", None
            page, label_box, _ = slot["hit"]
            self.bus.emit("fallback_started", page=page.page_number, field=slot["name"],
                          reason=(slot["a"].reason if slot["a"] else "value_missing"), engine="vlm")
            try:
                return "ok", self._vlm_read(page, label_box, str(slot["fdef"].get("label") or slot["name"]))
            except Exception as exc:
                logger.warning("fast_vlm_failed", field=slot["name"], error=str(exc)[:200])
                failures[0] += 1
                if failures[0] >= 2:
                    dead.set()
                return "error", None

        done_n = 0
        with ThreadPoolExecutor(max_workers=max(1, settings.FAST_VLM_PARALLEL), thread_name_prefix="fast-vlm") as pool:
            futs = {pool.submit(_work, slot): slot for slot in todo}
            for fut in as_completed(futs):
                slot = futs[fut]
                outcome, got = fut.result()
                fdef, name, meta = slot["fdef"], slot["name"], slot["meta"]
                if outcome == "skipped":
                    slot["skip"] = "vlm_budget_spent"
                elif got:
                    value, ok, crop_box = got
                    page = slot["hit"][0]
                    slot["fr"] = FieldResult(
                        field_name=name, value=value, confidence=0.7 if ok else 0.4,
                        page_number=page.page_number,
                        bbox={"x1": crop_box[0], "y1": crop_box[1], "x2": crop_box[2], "y2": crop_box[3]},
                        extraction_method="vlm_fallback", label=str(fdef.get("label") or name),
                    )
                    meta.source = "vlm_fallback"
                    slot["a"] = assess(fdef, value, 0.99 if ok else 0.0, supported=ok)
                    done_n += 1
                self._settle(slot)
        self.timings["vlm_ms"] = int((time.monotonic() - t0) * 1000)
        self.timings["vlm_repaired"] = done_n

    # ── all pages are in ──────────────────────────────────────────────────────────
    def finalize(self) -> list[FieldResult]:
        t0 = time.perf_counter()
        matches = match_fields(self.fields, self.pairs)
        vlm_cap = settings.FAST_VLM_MAX_FIELDS if settings.FAST_VLM_FALLBACK_ENABLED else 0
        slots: list[dict] = []
        pending: list[dict] = []
        # values the key:value step already gave to a field are not offered again to label proximity
        self._claimed = [(m.pair.page_number, m.pair.bbox) for m in matches.values() if m.pair.bbox]

        # 1. every field the cheap steps can settle is settled and streamed first
        for fdef in self.fields:
            name = fdef["name"]
            if self.bus.cancel_requested():
                break
            m = matches.get(name)
            fr: FieldResult | None = None
            meta = FieldMeta()
            label_hit = None

            rd = self._read_anchor(name)
            anchor_note = ""
            if rd is not None:
                rd, anchor_note = self._vet_anchor(fdef, rd, m)
            if rd is not None:
                fr = FieldResult(
                    field_name=name, value=rd.value, confidence=rd.confidence,
                    page_number=rd.page_number, bbox=rd.bbox,
                    extraction_method="anchor_verified", label=str(fdef.get("label") or name),
                )
                meta.source = "anchor_verified"
            elif m:
                fr = FieldResult(
                    field_name=name, value=m.pair.value, confidence=m.pair.confidence,
                    page_number=m.pair.page_number, bbox=m.pair.bbox,
                    extraction_method="kv_match", label=m.pair.label,
                )
                meta.source = "kv_pair"
            else:
                label_hit = self._proximity(fdef)
                if label_hit:
                    page, label_box, vwords = label_hit
                    rr = None
                    if not vwords:                      # label found, nothing read beside it: look at that spot again
                        try:
                            rr = self._reread_value(page, label_box)
                        except Exception as exc:
                            logger.warning("fast_label_reread_failed", field=name, error=str(exc)[:160])
                            rr = None
                        if rr is None:                  # value printed UNDER its label (only if that line is not another label)
                            vwords = self._value_line_below(page, label_box)
                            label_hit = (page, label_box, vwords)
                        if rr:
                            text, conf, bb = rr
                            fr = FieldResult(
                                field_name=name, value=text, confidence=round(conf * 0.85, 3), page_number=page.page_number,
                                bbox=bb, extraction_method="label_reread", label=str(fdef.get("label") or name),
                            )
                            meta.source = "label_reread"
                    if vwords:
                        fr = FieldResult(
                            field_name=name, value=" ".join(w.text for w in vwords),
                            confidence=round(_avg_conf(vwords) * 0.9, 3), page_number=page.page_number,
                            bbox=_union(vwords), extraction_method="label_proximity",
                            label=str(fdef.get("label") or name),
                        )
                        meta.source = "label_proximity"

            if fr is not None and fr.bbox:
                self._claimed.append((fr.page_number, fr.bbox))
            a = assess(fdef, fr.value, fr.confidence) if fr else None
            slot = {"name": name, "fdef": fdef, "fr": fr, "meta": meta, "a": a, "hit": None}
            if anchor_note:
                slot["skip"] = anchor_note
            slots.append(slot)

            # VLM only when a label was located but there is no usable value
            if (fr is None or (a and a.status != "validated")) and vlm_cap > 0:
                hit = label_hit
                if hit is None and fr is not None and fr.bbox:
                    page = self.pages.get(fr.page_number)
                    hit = (page, fr.bbox, []) if page else None
                if hit:
                    slot["hit"] = hit
                    pending.append(slot)
                    continue
            self._settle(slot)

        # 2. the vision model repairs what is left, in parallel and inside a time budget
        if pending:
            self._vlm_phase(pending, vlm_cap)
        results: list[FieldResult] = [s["result"] for s in slots if "result" in s]
        for slot in slots:
            fr = slot.get("result")
            if fr is not None and fr.value:
                fr.occurrences = self._occurrences(slot["fdef"], fr)

        # discovered pairs that no template field used
        if settings.KV_DISCOVERY_ENABLED:
            used = {id(m.pair) for m in matches.values()}
            leftovers = [p for p in self.pairs if id(p) not in used]
            for key, pair in assign_unique_keys(leftovers, {f.field_name for f in results}):
                results.append(FieldResult(
                    field_name=key, value=pair.value, confidence=pair.confidence,
                    page_number=pair.page_number, bbox=pair.bbox, extraction_method="kv_discovery",
                    label=pair.label, source="discovered",
                ))
                self.meta[key] = FieldMeta(state="extracted", source="discovered")
        self.timings["finalize_ms"] = int((time.perf_counter() - t0) * 1000)
        return results

    def emit_final_events(self, results: list[FieldResult]) -> None:
        """Call AFTER the results are persisted: `validated` means saved."""
        for fr in results:
            meta = self.meta.get(fr.field_name)
            if not meta or meta.state in ("failed", "extracted"):
                continue
            self.bus.emit(
                "field_validated", page=fr.page_number, field=fr.field_name, label=fr.label,
                value=fr.value, confidence=round(fr.confidence, 3), state=meta.state,
                provisional=False, source=meta.source, checks=meta.checks, reason=meta.reason, bbox=fr.bbox,
            )
