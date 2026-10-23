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

import time
from dataclasses import dataclass, field as dc_field

from app.core.config import settings
from app.core.logging import get_logger
from app.fast.events import EventBus
from app.fast.types import PageData
from app.fast.validate import assess
from app.fast.vlm import FieldVlm, supported_by_ocr
from app.ocr.base import OCRLine
from app.ocr.kv_discovery import DiscoveredPair, assign_unique_keys, discover_key_values
from app.ocr.kv_match import _candidates, _score, _tokens, match_fields
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
        self._ocr_engine = ocr_engine
        self._vlm = vlm
        self.meta: dict[str, FieldMeta] = {}
        self.timings: dict[str, int] = {}

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
                self.bus.emit(
                    "field_extracted", page=m.pair.page_number, field=name, label=m.pair.label,
                    value=m.pair.value, confidence=round(m.pair.confidence, 3), state="extracted",
                    provisional=True, source="kv_pair", bbox=m.pair.bbox,
                )
        self.timings["kv_ms"] = self.timings.get("kv_ms", 0) + int((time.perf_counter() - t0) * 1000)

    # ── label proximity (labels printed without a colon) ───────────────────────
    def _proximity(self, fdef: dict):
        """Find the field's label without needing a colon. Returns
        (page, label_box, value_words) for the best-scoring label occurrence
        (exact beats typo-tolerant; a hit that has a value beats one without)."""
        cands = _candidates(fdef)
        if not cands:
            return None
        best, best_key = None, None
        for page in self.pages.values():
            for ln in page.lines:
                words = ln.words
                for cand in cands:
                    k = len(cand)
                    for i in range(0, len(words) - k + 1):
                        seg = words[i:i + k]
                        if any(w.bbox is None for w in seg):
                            continue
                        score = _score(cand, [t for w in seg for t in _tokens(w.text)])
                        if score < 0.9:
                            continue
                        label_box = _union(seg)
                        value_words = [w for w in words[i + k:] if w.text.strip(" :-–—=>;")]
                        if not value_words:
                            value_words = self._line_below(page, ln, label_box)
                        key = (score, bool(value_words), -page.page_number, -label_box["y1"])
                        if best_key is None or key > best_key:
                            best, best_key = (page, label_box, value_words), key
        return best

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

    # ── all pages are in ──────────────────────────────────────────────────────────
    def finalize(self) -> list[FieldResult]:
        t0 = time.perf_counter()
        matches = match_fields(self.fields, self.pairs)
        results: list[FieldResult] = []
        vlm_budget = settings.FAST_VLM_MAX_FIELDS if settings.FAST_VLM_FALLBACK_ENABLED else 0

        for fdef in self.fields:
            name = fdef["name"]
            if self.bus.cancel_requested():
                break
            m = matches.get(name)
            fr: FieldResult | None = None
            meta = FieldMeta()
            label_hit = None

            rd = self._read_anchor(name)
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
                    if vwords:
                        fr = FieldResult(
                            field_name=name, value=" ".join(w.text for w in vwords),
                            confidence=round(_avg_conf(vwords) * 0.9, 3), page_number=page.page_number,
                            bbox=_union(vwords), extraction_method="label_proximity",
                            label=str(fdef.get("label") or name),
                        )
                        meta.source = "label_proximity"

            a = assess(fdef, fr.value, fr.confidence) if fr else None

            # VLM only when a label was located but there is no usable value
            if (fr is None or (a and a.status != "validated")) and vlm_budget > 0:
                hit = label_hit
                if hit is None and fr is not None and fr.bbox:
                    page = self.pages.get(fr.page_number)
                    hit = (page, fr.bbox, []) if page else None
                if hit:
                    page, label_box, _ = hit
                    self.bus.emit("fallback_started", page=page.page_number, field=name,
                                  reason=(a.reason if a else "value_missing"), engine="vlm")
                    vlm_budget -= 1
                    try:
                        got = self._vlm_read(page, label_box, str(fdef.get("label") or name))
                    except Exception as exc:
                        logger.warning("fast_vlm_failed", field=name, error=str(exc))
                        got = None
                    if got:
                        value, ok, crop_box = got
                        fr = FieldResult(
                            field_name=name, value=value, confidence=0.7 if ok else 0.4,
                            page_number=page.page_number,
                            bbox={"x1": crop_box[0], "y1": crop_box[1], "x2": crop_box[2], "y2": crop_box[3]},
                            extraction_method="vlm_fallback", label=str(fdef.get("label") or name),
                        )
                        meta.source = "vlm_fallback"
                        a = assess(fdef, value, 0.99 if ok else 0.0, supported=ok)

            if fr is None:
                meta.state, meta.reason = "failed", "not_found"
                fr = FieldResult(field_name=name, value="", confidence=0.0, page_number=1,
                                 extraction_method="not_found", label=str(fdef.get("label") or ""))
                self.bus.emit("field_failed", field=name, reason="not_found")
            else:
                meta.state, meta.checks, meta.reason = a.status, a.checks, a.reason
            self.meta[name] = meta
            results.append(fr)

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
