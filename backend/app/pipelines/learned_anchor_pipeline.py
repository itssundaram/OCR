"""
DOCINT — Learned Geometric Anchor Pipeline (Sub-second extraction for learned templates)

Architectural Strategy:
1. Warm Path: If the template has learned geometric anchors:
   - Scale & anchor offset alignment via key visual/text anchor
   - Parallel multi-threaded micro-region OCR directly on field bounding boxes
   - Ultra-fast execution (< 300ms) with zero heavy VLM/layout latency
2. Cold Path / Adaptive Learning Fallback:
   - If no learned geometry exists or warm confidence < threshold:
   - Full OCR line detection + Qwen 2.5-VL structured semantic extraction
   - Auto-detect bounding boxes and anchor points
   - Automatically persist learned geometry into Template.template_json
   - Next run on any similar document executes instantly via Warm Path!
"""
from __future__ import annotations

import datetime
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Any
from PIL import Image, ImageOps
from sqlalchemy.orm.attributes import flag_modified

import pytesseract

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import Template
from app.pipelines.base import (
    FieldResult,
    OCRPipeline,
    PipelineCapabilities,
    PipelineResult,
    TableResult,
)
from app.pipelines.registry import register_pipeline
from app.ocr.base import OCRPage, OCRResult, OCRLine, BoundingBox
from app.ocr.tesseract import TesseractOCREngine
from app.ocr.kv_discovery import assign_unique_keys, discover_key_values, slugify_label
from app.ocr.kv_match import match_fields
from app.ai.orchestrator import ai_orchestrator
from app.anchors import resolve_anchor, words_from_lines

logger = get_logger(__name__)

# extraction methods that are label-geometry GUESSES (not a confirmed anchor): a better-evidenced pair may replace them
_GUESS_METHODS = frozenset({"learned_anchor_cold", "label_search"})

# every Tesseract call ends after this long (a hung subprocess used to block the whole job)
_TESS_TIMEOUT = max(60, int(settings.TESSERACT_TIMEOUT) * 2)

# Ensure pytesseract points to the configured binary
if settings.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD


@register_pipeline("learned_anchor")
class LearnedAnchorPipeline(OCRPipeline):
    """High-speed geometric anchor pipeline with autonomous layout learning."""

    @property
    def name(self) -> str:
        return "learned_anchor"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities(
            supports_layout_detection=True,
            supports_tables=True,
            supports_handwriting=True,
            supports_vision=True,
            requires_gpu=False,  # Runs blazing fast on CPU or GPU
            model_name="learned_anchors+qwen2.5vl",
        )

    def __init__(self) -> None:
        pass

    def _get_ocr_engine(self) -> TesseractOCREngine:
        from app.core.model_registry import get_tesseract_engine
        return get_tesseract_engine()

    @staticmethod
    def _table_regions(page: Image.Image) -> list[tuple[Image.Image, int, int]]:
        """Return (crop, offset_x, offset_y) for every table found on the page.

        The table-structure model is trained on tight table crops; handing it a
        whole page makes it miss rows or merge text above/below the table.
        settings.TABLE_REGION_DETECTOR picks how regions are found:
          "lines" (default) - OpenCV ruled-line finder, a few ms per page
          "surya"           - Surya layout model, accurate but slow on CPU
          "none"            - skip cropping
        When nothing is found (or the detector fails) the whole page is used,
        which is what this pipeline did originally.
        """
        mode = str(getattr(settings, "TABLE_REGION_DETECTOR", "lines") or "lines").strip().lower()
        regions: list[tuple[Image.Image, int, int]] = []
        t0 = time.perf_counter()
        try:
            if mode == "surya":
                from app.core.model_registry import get_surya_layout_engine
                pad = 5  # same padding SuryaLayoutEngine applies to its crops
                for region in get_surya_layout_engine().detect_layout(page):
                    if str(region.region_type).strip().lower() != "table":
                        continue
                    ox = max(0, int(region.bbox.x1) - pad)
                    oy = max(0, int(region.bbox.y1) - pad)
                    if region.cropped_image is not None:
                        regions.append((region.cropped_image, ox, oy))
            elif mode != "none":
                from app.ocr.table_regions import find_table_regions
                for x1, y1, x2, y2 in find_table_regions(page):
                    regions.append((page.crop((x1, y1, x2, y2)), x1, y1))
        except Exception as exc:
            logger.warning("learned_anchor_table_layout_failed", detector=mode, error=str(exc))
        logger.info(
            "learned_anchor_table_regions",
            detector=mode,
            regions=len(regions),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        if not regions:
            regions.append((page, 0, 0))
        return regions

    @staticmethod
    def _shift_box(box: dict | None, dx: int, dy: int) -> dict | None:
        """Move a {x1,y1,x2,y2} box from crop coordinates to page coordinates."""
        if not box or (dx == 0 and dy == 0):
            return box
        try:
            return {
                "x1": box["x1"] + dx, "y1": box["y1"] + dy,
                "x2": box["x2"] + dx, "y2": box["y2"] + dy,
            }
        except (KeyError, TypeError):
            return box

    def extract_tables(self, pages: list[Image.Image], job_id: str) -> list[TableResult]:
        """Tables by geometry first (shared with fast_ocr); the TATR path below only for pages with none."""
        from app.pipelines.geometry_tables import geometry_first_tables
        return geometry_first_tables(pages, job_id, lambda idx: self._model_tables(pages, idx))

    def _model_tables(self, pages: list[Image.Image], indexes: list[int]) -> list[TableResult]:
        """Extract structured tables (cells with geometry) from every table region.

        Table regions come from the line finder (or Surya layout); each crop goes to the singleton
        TATR engine (which itself falls back to img2table), and boxes are
        mapped back to full-page coordinates so evidence crops line up.
        """
        results: list[TableResult] = []
        try:
            from app.core.model_registry import get_tatr_engine
            tatr = get_tatr_engine()
            table_idx = 1
            for p_idx in indexes:
                page = pages[p_idx]
                for crop, ox, oy in self._table_regions(page):
                    t_read = time.perf_counter()
                    read = tatr.extract_tables(crop)
                    logger.info(
                        "learned_anchor_table_read",
                        page=p_idx + 1,
                        crop=f"{crop.width}x{crop.height}",
                        tables=len(read),
                        ms=int((time.perf_counter() - t_read) * 1000),
                    )
                    for t in read:
                        cells = []
                        for c in getattr(t, "cells", None) or []:
                            c = dict(c)
                            c["bbox"] = self._shift_box(c.get("bbox"), ox, oy)
                            cells.append(c)
                        has_geometry = any(c.get("bbox") for c in cells)
                        results.append(TableResult(
                            table_index=table_idx,
                            page_number=p_idx + 1,
                            headers=t.headers,
                            rows=t.rows,
                            confidence=0.90 if has_geometry else 0.70,
                            bbox=self._shift_box(t.bbox, ox, oy),
                            cells=cells,
                            markdown=t.markdown,
                            extraction_method="tatr" if has_geometry else "img2table",
                        ))
                        table_idx += 1
        except Exception as exc:
            logger.warning("learned_anchor_extract_tables_failed", error=str(exc))
        return results

    # ── key : value discovery ("pairs first") ──────────────────────────────────
    def _kv_scan(self, pages: list[Image.Image], need_words: bool = False) -> dict | None:
        """One full-page OCR pass + pair discovery. Returns
        {"ocr_res", "lines", "pairs"} or None when disabled / failed.

        need_words=True keeps the pass even when pair discovery is switched off:
        stored anchors are read from these words, so the page is OCR-ed exactly
        once whichever path ends up answering."""
        if not settings.KV_DISCOVERY_ENABLED and not need_words:
            return None
        try:
            t0 = time.perf_counter()
            ocr_res = self._get_ocr_engine().process_images(pages)
            t1 = time.perf_counter()
            lines = [p.lines for p in ocr_res.pages]
            pairs = discover_key_values(lines) if settings.KV_DISCOVERY_ENABLED else []
            logger.info(
                "learned_anchor_kv_scan",
                pages=len(pages),
                pairs=len(pairs),
                ocr_ms=int((t1 - t0) * 1000),
                discovery_ms=int((time.perf_counter() - t1) * 1000),
            )
            return {"ocr_res": ocr_res, "lines": lines, "pairs": pairs}
        except Exception as exc:
            logger.warning("learned_anchor_kv_scan_failed", error=str(exc), exc_info=True)
            return None

    @staticmethod
    def _wanted_fields(template_fields: list[dict]) -> list[dict]:
        return [
            f for f in (template_fields or [])
            if f.get("name") and str(f.get("type") or "").lower() != "table"
        ]

    def _kv_first_result(
        self,
        kv: dict,
        template_fields: list[dict],
        pages: list[Image.Image],
    ) -> PipelineResult | None:
        """Answer from the discovered pairs alone, or None when they do not cover
        enough of the template (the caller then runs the normal path)."""
        wanted = self._wanted_fields(template_fields)
        if not wanted:
            return None
        matches = match_fields(wanted, kv["pairs"])
        good = {n: m for n, m in matches.items() if m.pair.confidence >= settings.KV_FIRST_MIN_CONFIDENCE}
        ratio = len(good) / len(wanted)
        if ratio < settings.KV_FIRST_MIN_MATCH_RATIO or ratio == 0:
            logger.info(
                "learned_anchor_kv_first_miss",
                matched=len(matches), confident=len(good), wanted=len(wanted),
                missing=[f["name"] for f in wanted if f["name"] not in good],
            )
            return None

        fields: list[FieldResult] = []
        for fdef in wanted:
            m = good.get(fdef["name"])
            if m is None:
                continue
            fields.append(FieldResult(
                field_name=fdef["name"],
                value=m.pair.value,
                confidence=m.pair.confidence,
                page_number=m.pair.page_number,
                bbox=m.pair.bbox,
                extraction_method="kv_match",
                label=m.pair.label,
            ))
        overall = sum(f.confidence for f in fields) / len(fields) if fields else 0.0

        detections = []
        for p_idx, lines in enumerate(kv["lines"]):
            for ln in lines:
                for w_idx, w in enumerate(ln.words or []):
                    if w.bbox is None or not (w.text or "").strip():
                        continue
                    detections.append({
                        "id": f"det_p{p_idx + 1}_{len(detections)}",
                        "text": w.text,
                        "bbox": {"x1": w.bbox.x1, "y1": w.bbox.y1, "x2": w.bbox.x2, "y2": w.bbox.y2},
                        "confidence": round(w.confidence or 0.0, 3),
                        "page_number": p_idx + 1,
                    })

        result = PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=fields,
            overall_confidence=round(overall, 4),
            all_detections=detections,
            metadata={
                "mode": "KV_FIRST",
                "anchor_learned": False,
                "all_detections": detections,
                "doc_page_width": pages[0].width if pages else 0,
                "doc_page_height": pages[0].height if pages else 0,
                "ocr_metadata": kv["ocr_res"].model_dump(),
            },
        )
        self._attach_discovered_pairs(result, kv, template_fields)
        return result

    @staticmethod
    def _fill_from_kv(result: PipelineResult, matches: dict) -> None:
        """Use a matched pair for any template field the main path left empty."""
        by_name = {f.field_name: i for i, f in enumerate(result.fields)}
        filled = 0
        replaced = 0
        for name, m in matches.items():
            idx = by_name.get(name)
            if idx is not None and (result.fields[idx].value or "").strip():
                cur = result.fields[idx]
                # The main path has a value: keep it, unless it is only a label-geometry guess (cold path) that the
                # pair parser reads with MORE confidence. A confirmed reading (verified / stored anchor) is never replaced.
                if not (cur.extraction_method in _GUESS_METHODS and m.pair.confidence > (cur.confidence or 0.0)):
                    continue
                replaced += 1
            fr = FieldResult(
                field_name=name,
                value=m.pair.value,
                confidence=m.pair.confidence,
                page_number=m.pair.page_number,
                bbox=m.pair.bbox,
                extraction_method="kv_match",
                label=m.pair.label,
            )
            if idx is None:
                result.fields.append(fr)
            else:
                result.fields[idx] = fr
            filled += 1
        if filled:
            result.metadata["kv_filled_fields"] = filled
        if replaced:
            result.metadata["kv_replaced_guesses"] = replaced

    def _attach_discovered_pairs(
        self,
        result: PipelineResult,
        kv: dict | None,
        template_fields: list[dict] | None = None,
    ) -> None:
        """Add every remaining "Label: value" pair to result.fields.

        1. template fields the main path left empty are filled from matching pairs;
        2. all other pairs are appended (source="discovered").
        Discovered pairs are never written into the learned anchors, so this
        cannot change what a template learns.
        """
        if kv is None or result is None:
            return
        t0 = time.perf_counter()
        try:
            matches = match_fields(self._wanted_fields(template_fields or []), kv["pairs"])
            self._fill_from_kv(result, matches)
            matched_ids = {id(m.pair) for m in matches.values()}

            def _norm(text: str) -> str:
                return " ".join((text or "").lower().split())

            existing_values = {f.field_name: _norm(f.value) for f in result.fields}
            taken = set(existing_values)

            pairs = []
            for pair in kv["pairs"]:
                if id(pair) in matched_ids:
                    continue  # already shown as a template field
                key = slugify_label(pair.label)
                if key in existing_values and existing_values[key] == _norm(pair.value):
                    continue
                pairs.append(pair)

            added = 0
            for key, pair in assign_unique_keys(pairs, taken):
                result.fields.append(FieldResult(
                    field_name=key,
                    value=pair.value,
                    confidence=pair.confidence,
                    page_number=pair.page_number,
                    bbox=pair.bbox,
                    extraction_method="kv_discovery",
                    label=pair.label,
                    source="discovered",
                ))
                added += 1
            result.metadata["discovered_pairs"] = added
            logger.info(
                "learned_anchor_kv_attach_done",
                discovered=added,
                template_matched=len(matches),
                ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:
            logger.warning("learned_anchor_kv_discovery_failed", error=str(exc), exc_info=True)

    # ── verified anchors first ─────────────────────────────────────────────────
    @staticmethod
    def _anchor_trusted(la: dict, anchor: dict | None) -> bool:
        """A human-confirmed box: the whole set is locked, or this field was saved by a user."""
        return bool(la.get("is_verified")) or str((anchor or {}).get("source") or "").startswith("user")

    @staticmethod
    def _detections_from_words(words_by_page: list[list[dict]]) -> list[dict]:
        dets: list[dict] = []
        for p_idx, ws in enumerate(words_by_page):
            for w in ws:
                dets.append({
                    "id": f"det_p{p_idx + 1}_{len(dets)}",
                    "text": w["text"],
                    "bbox": {"x1": w["x1"], "y1": w["y1"], "x2": w["x2"], "y2": w["y2"]},
                    "confidence": round(float(w.get("conf") or 0.0), 3),
                    "page_number": p_idx + 1,
                })
        return dets

    def _read_via_label(self, name: str, aliases: list[str], words_by_page: list[list[dict]],
                        sizes: list[tuple[int, int]], spec: dict | None):
        """Label search on EVERY page (not just page 1), reading the words right of the label."""
        best = None
        for p_idx, words in enumerate(words_by_page):
            if not words:
                continue
            w, h = sizes[min(p_idx, len(sizes) - 1)]
            m = self._find_field_bbox_by_label(name, aliases, words, w, h, spec)
            if m and (best is None or m["score"] > best[0]["score"]):
                best = (m, p_idx, w, h)
        if best is None:
            return None
        m, p_idx, w, h = best
        vb = m["val_bbox"]
        rd = resolve_anchor(
            {"bbox": vb, "page_number": p_idx + 1, "page_width": w, "page_height": h},
            words_by_page, sizes, spec=spec,
        )
        if rd is None and m.get("direct_value"):
            return {"value": m["direct_value"], "confidence": 0.9, "bbox": vb, "page_number": p_idx + 1, "valid": True}
        if rd is None or not rd.valid:
            return None
        return {"value": rd.value, "confidence": min(rd.confidence, 0.92), "bbox": rd.bbox,
                "page_number": rd.page_number, "valid": True}

    def _anchor_first_result(self, kv: dict | None, pages: list[Image.Image], template_fields: list[dict],
                             la: dict, event_callback=None) -> PipelineResult | None:
        """Answer from human-verified anchors using the words already read.

        Per field: verified box (landmark-aligned) -> label search on every page ->
        discovered key:value pair -> a low-confidence reading of the stored box.
        Returns None (caller falls through to the older paths) when no verified box
        could be read or fewer than 30% of the template's fields were resolved.
        """
        if not kv or not la or not la.get("fields"):
            return None
        anchored: dict = la["fields"]
        if not any(self._anchor_trusted(la, a) for a in anchored.values()):
            return None
        wanted = self._wanted_fields(template_fields) or [{"name": n} for n in anchored]
        if not wanted:
            return None

        t0 = time.perf_counter()
        if event_callback:
            event_callback("ANCHOR_VERIFIED_STARTED", {"anchors": len(anchored)})
        words_by_page = [words_from_lines(lines) for lines in kv["lines"]]
        while len(words_by_page) < len(pages):
            words_by_page.append([])
        sizes = [(p.width, p.height) for p in pages]
        orig = (la.get("page_width"), la.get("page_height"))
        matches = match_fields(wanted, kv["pairs"]) if kv.get("pairs") else {}

        # Pass 0: fields whose own printed label was found tell us how far this scan is shifted;
        # fields whose label was not found (OCR split it, etc.) reuse the median shift.
        shifts: list[tuple[float, float]] = []
        for fdef in wanted:
            a0 = anchored.get(fdef["name"])
            if a0 and self._anchor_trusted(la, a0):
                r0 = resolve_anchor(a0, words_by_page, sizes, orig_size=orig,
                                    spec=self._field_spec(fdef["name"], fdef))
                if r0 is not None and r0.valid and r0.aligned:
                    shifts.append((r0.dx, r0.dy))
        page_shift = None
        if shifts:
            xs, ys = sorted(s[0] for s in shifts), sorted(s[1] for s in shifts)
            page_shift = (xs[len(xs) // 2], ys[len(ys) // 2])

        fields_out: list[FieldResult] = []
        trace: dict[str, dict] = {}
        n_resolved = n_verified = 0
        for fdef in wanted:
            name = fdef["name"]
            anchor = anchored.get(name)
            trusted = bool(anchor) and self._anchor_trusted(la, anchor)
            spec = self._field_spec(name, fdef)
            aliases = list(fdef.get("aliases", [])) + list((spec or {}).get("patterns", []))

            rd = (resolve_anchor(anchor, words_by_page, sizes, orig_size=orig, spec=spec, shift=page_shift)
                  if anchor else None)
            anchor_ok = rd is not None and rd.valid and rd.confidence >= 0.35
            chosen = None  # (value, conf, bbox, page, method)

            if trusted and anchor_ok:
                chosen = (rd.value, rd.confidence, rd.bbox, rd.page_number, "anchor_verified")
                n_verified += 1
            if chosen is None:
                lab = self._read_via_label(name, aliases, words_by_page, sizes, spec)
                if lab:
                    chosen = (lab["value"], lab["confidence"], lab["bbox"], lab["page_number"], "label_search")
            if chosen is None and anchor_ok:  # untrusted stored box ranks below label search
                chosen = (rd.value, rd.confidence, rd.bbox, rd.page_number, "anchor_stored")
            if chosen is None and name in matches:
                mp = matches[name].pair
                chosen = (mp.value, mp.confidence, mp.bbox, mp.page_number, "kv_match")
            if chosen is None and rd is not None and rd.value:
                chosen = (rd.value, rd.confidence, rd.bbox, rd.page_number, "anchor_low_confidence")

            trace[name] = {
                "method": chosen[4] if chosen else "not_found",
                "aligned": bool(rd and rd.aligned),
                "dx": rd.dx if rd else 0.0, "dy": rd.dy if rd else 0.0,
                "anchor_valid": bool(rd and rd.valid),
                "reason": rd.reason if rd else ("no_anchor" if not anchor else "nothing_in_box"),
            }
            if chosen is None:
                fields_out.append(FieldResult(field_name=name, value="", confidence=0.0, page_number=1,
                                              bbox=None, crop_image=None, extraction_method="not_found"))
                continue
            value, conf, bbox, pg, method = chosen
            if method != "anchor_low_confidence":
                n_resolved += 1
            crop = None
            if bbox and 1 <= pg <= len(pages):
                crop = self._safe_crop(pages[pg - 1], bbox["x1"] - 6, bbox["y1"] - 6, bbox["x2"] + 6, bbox["y2"] + 6)
            fields_out.append(FieldResult(field_name=name, value=value, confidence=round(float(conf), 3),
                                          page_number=pg, bbox=bbox, crop_image=crop, extraction_method=method))

        if n_verified == 0 or n_resolved < max(1, len(wanted) * 0.3):
            logger.info("anchor_first_insufficient", verified=n_verified, resolved=n_resolved, wanted=len(wanted))
            return None

        overall = sum(f.confidence for f in fields_out) / len(fields_out) if fields_out else 0.0
        dets = self._detections_from_words(words_by_page)
        ms = int((time.perf_counter() - t0) * 1000)
        logger.info("anchor_first_hit", verified=n_verified, resolved=n_resolved, wanted=len(wanted), ms=ms)
        if event_callback:
            event_callback("ANCHOR_VERIFIED_DONE", {"verified": n_verified, "resolved": n_resolved})
        return PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=fields_out,
            overall_confidence=round(overall, 4),
            all_detections=dets,
            metadata={
                "mode": "ANCHOR_VERIFIED",
                "speed": "single_ocr_pass",
                "anchor_matched": True,
                "anchor_learned": False,
                "anchor_trace": trace,
                "learned_anchors": {k: v for k, v in la.items() if k != "history"},
                "all_detections": dets,
                "doc_page_width": pages[0].width if pages else 0,
                "doc_page_height": pages[0].height if pages else 0,
                "ocr_metadata": kv["ocr_res"].model_dump(),
            },
        )

    def extract_fields(
        self,
        pages: list[Image.Image],
        template_fields: list[dict],
        job_id: str,
        *,
        native_texts: list[str] | None = None,
        schema: dict | None = None,
        department: str = "GENERAL",
        doc_type: str = "UNKNOWN",
        template_version: int = 1,
        extraction_instructions: str | None = None,
        template_id: int | None = None,
        event_callback=None,
        force_relearn: bool = False,
        read_only: bool = False,
        **kwargs,
    ) -> PipelineResult:
        t_start = time.perf_counter()

        # Stored anchors are loaded FIRST: a human-verified box must beat "pairs first".
        learned_anchors = None
        if schema and isinstance(schema, dict):
            learned_anchors = schema.get("learned_anchors")

        if not learned_anchors and template_id:
            try:
                with SessionLocal() as db:
                    tmpl = db.get(Template, template_id)
                    if tmpl and tmpl.template_json:
                        learned_anchors = tmpl.template_json.get("learned_anchors")
            except Exception as e:
                logger.warning("failed_fetching_template_for_anchors", error=str(e))

        # One full-page OCR pass serves every path below (anchors, pairs, cold path).
        kv = self._kv_scan(pages, need_words=bool(learned_anchors and learned_anchors.get("fields")))

        # Step 0: verified anchors - read the confirmed boxes from the words we already have.
        if kv is not None and not force_relearn and isinstance(learned_anchors, dict):
            try:
                av = self._anchor_first_result(kv, pages, template_fields, learned_anchors, event_callback)
                if av is not None:
                    self._attach_discovered_pairs(av, kv, template_fields)
                    av.processing_time_ms = int((time.perf_counter() - t_start) * 1000)
                    return av
            except Exception as exc:
                logger.warning("anchor_first_failed_falling_back", error=str(exc), exc_info=True)

        # Step 0b: "pairs first" - find every "Label: value" pair and, if they cover the
        # template, answer from them without anchors or a VLM.
        if kv is not None and settings.KV_FIRST_ENABLED and not force_relearn:
            kv_result = self._kv_first_result(kv, template_fields, pages)
            if kv_result is not None:
                kv_result.processing_time_ms = int((time.perf_counter() - t_start) * 1000)
                logger.info("learned_anchor_kv_first_hit", job_id=job_id, ms=kv_result.processing_time_ms)
                return kv_result

        # Step 1: stored anchors (loaded above) - validity check
        # Check if learned_anchors has valid field definitions
        has_valid_anchors = (
            learned_anchors
            and isinstance(learned_anchors, dict)
            and learned_anchors.get("fields")
            and len(learned_anchors["fields"]) > 0
        )

        if has_valid_anchors and force_relearn:
            logger.info("learned_anchor_force_relearn_requested", template_id=template_id)

        if has_valid_anchors and not force_relearn:
            logger.info("learned_anchor_warm_candidate_found", template_id=template_id, field_count=len(learned_anchors["fields"]))
            try:
                warm_result = self._execute_warm_anchor_path(
                    pages=pages,
                    template_fields=template_fields,
                    learned_anchors=learned_anchors,
                    job_id=job_id,
                    template_id=template_id,
                    event_callback=event_callback,
                    kv=kv,
                )
                if warm_result is not None:
                    self._attach_discovered_pairs(warm_result, kv, template_fields)
                    warm_result.processing_time_ms = int((time.perf_counter() - t_start) * 1000)
                    return warm_result
            except Exception as exc:
                logger.warning("warm_anchor_path_failed_falling_back", error=str(exc), exc_info=True)

        # Step 2: Fall back to Cold Learning Path (VLM + Line OCR + Auto-anchor learning).
        #
        # Whether this cold run is allowed to OVERWRITE a template's stored
        # pattern depends on why we got here:
        #   - no anchors existed yet              -> genuine first learn, persist
        #   - user explicitly asked for a relearn -> persist
        #   - anchors existed but warm path failed -> automatic fallback; still
        #     extract this document correctly, but don't silently clobber a
        #     pattern that may simply not have matched THIS one odd document.
        #   - read_only (orchestration comparison run) -> never persist: the run is
        #     being compared, not reviewed; the learned anchors still come back in
        #     the result metadata, and a single-pipeline run can persist them.
        allow_persist = ((not has_valid_anchors) or force_relearn) and not read_only
        logger.info(
            "learned_anchor_executing_cold_path",
            template_id=template_id,
            allow_persist=allow_persist,
            force_relearn=force_relearn,
            read_only=read_only,
            had_existing_anchors=bool(has_valid_anchors),
        )
        cold_result = self._execute_cold_learned_path(
            pages=pages,
            template_fields=template_fields,
            job_id=job_id,
            native_texts=native_texts,
            schema=schema,
            department=department,
            doc_type=doc_type,
            template_version=template_version,
            extraction_instructions=extraction_instructions,
            template_id=template_id,
            event_callback=event_callback,
            allow_persist=allow_persist,
            kv=kv,
        )
        cold_result.processing_time_ms = int((time.perf_counter() - t_start) * 1000)
        return cold_result

    @staticmethod
    def _preprocess_image(img: Image.Image) -> Image.Image:
        """Grayscale & contrast enhancement without destructive binarization."""
        try:
            from app.ocr.preprocessing import preprocess_for_ocr
            return preprocess_for_ocr(img, deskew=False, binarize=False)
        except Exception:
            return img

    @staticmethod
    def _safe_crop_box(
        x1: float, y1: float, x2: float, y2: float, max_w: int, max_h: int, min_size: int = 2
    ) -> tuple[int, int, int, int]:
        """
        Normalizes a computed (x1, y1, x2, y2) box into one PIL.Image.crop() can
        never reject. Every bbox in this pipeline is a heuristic guess (word
        matching, label-proximity search, scaled/padded template memory), so
        inversion (x2 < x1 or y2 < y1) is an expected outcome of those
        heuristics near page edges or on bad matches — not something to let
        crash the whole extraction. Swaps inverted pairs, clamps into the
        image bounds, and guarantees a non-zero-area box.
        """
        ix1, iy1, ix2, iy2 = int(x1), int(y1), int(x2), int(y2)
        if ix2 < ix1:
            ix1, ix2 = ix2, ix1
        if iy2 < iy1:
            iy1, iy2 = iy2, iy1

        ix1 = max(0, min(ix1, max_w))
        ix2 = max(0, min(ix2, max_w))
        iy1 = max(0, min(iy1, max_h))
        iy2 = max(0, min(iy2, max_h))

        if ix2 - ix1 < min_size:
            ix2 = min(max_w, ix1 + min_size)
            ix1 = max(0, ix2 - min_size)
        if iy2 - iy1 < min_size:
            iy2 = min(max_h, iy1 + min_size)
            iy1 = max(0, iy2 - min_size)

        return ix1, iy1, ix2, iy2

    @classmethod
    def _safe_crop(
        cls, img: Image.Image, x1: float, y1: float, x2: float, y2: float, min_size: int = 2
    ) -> Image.Image | None:
        """Crops img to a bbox normalized by _safe_crop_box. Never raises."""
        try:
            bx1, by1, bx2, by2 = cls._safe_crop_box(x1, y1, x2, y2, img.width, img.height, min_size)
            return img.crop((bx1, by1, bx2, by2))
        except Exception as e:
            logger.warning("safe_crop_failed", error=str(e))
            return None

    @staticmethod
    def _field_spec(name: str, fdef: dict | None = None) -> dict | None:
        """Value-extraction hints for one template field, or None when nothing is known about it.

        Nothing about any form is written in this file. The hints come from DATA: the template field itself
        (its own `aliases`, `regex`, `blacklist`) plus the knowledge layer's entry for a field of that name
        (app/knowledge/data/fields.json: printed `labels`, `extract` regex, `blacklist`), which an administrator
        can edit or extend without touching code. A field the knowledge layer has never heard of simply gets
        no spec and is found by its own name / aliases alone."""
        from app.knowledge.kb import known_field

        fd = dict(fdef or {})
        fd.setdefault("name", name)
        kb = known_field(fd) or {}
        regex = fd.get("regex") or kb.get("extract")
        blacklist = [str(b) for b in list(fd.get("blacklist") or []) + list(kb.get("blacklist") or [])]
        patterns = [str(x) for x in list(fd.get("aliases") or []) + list(kb.get("labels") or [])]
        if not (regex or blacklist or patterns):
            return None
        return {"patterns": patterns, "regex": regex, "blacklist": blacklist}

    @staticmethod
    def _evidence_confidence(label_score: float | None, value_words: list[dict] | None, cap: float = 0.95) -> float:
        """Confidence of a value found by label geometry: how well the label matched times how well the
        OCR read the value's words. A constant here would make a mis-parsed value as "certain" as a clean one."""
        confs = [float(w.get("conf") or 0.0) / 100.0 for w in (value_words or []) if (w.get("conf") or 0) > 0]
        ocr = sum(confs) / len(confs) if confs else 0.5
        return round(max(0.0, min(cap, (label_score if label_score is not None else 0.5) * ocr)), 3)

    def _persist_learned_anchors(self, template_id: int, learned_anchors: dict) -> None:
        """Helper to safely persist updated learned geometric anchors to DB."""
        if not template_id:
            return
        try:
            with SessionLocal() as db:
                tmpl = db.get(Template, template_id)
                if tmpl:
                    tj = dict(tmpl.template_json or {})
                    existing_la = tj.get("learned_anchors", {})
                    # If human ground truth has verified and locked this template's anchors,
                    # do NOT automatically overwrite with cold path guesses!
                    if existing_la and existing_la.get("is_verified"):
                        logger.info("learned_anchors_persist_skipped_already_verified", template_id=template_id)
                        return
                    # Merge, never replace: boxes a human saved (source "user*") survive an
                    # automatic relearn; only machine-learned boxes are refreshed.
                    from app.anchors import merge_anchor_fields
                    base = dict(existing_la or {})
                    base["fields"] = {
                        n: f for n, f in (existing_la or {}).get("fields", {}).items()
                        if str((f or {}).get("source") or "").startswith("user")
                    }
                    incoming = {
                        n: f for n, f in (learned_anchors.get("fields") or {}).items()
                        if n not in base["fields"]
                    }
                    merged, _rep = merge_anchor_fields(
                        base, incoming,
                        page_w=learned_anchors.get("page_width") or 1654,
                        page_h=learned_anchors.get("page_height") or 2338,
                        verified=None, source="auto",
                    )
                    for k in ("anchor_text", "anchor_bbox", "learned_at", "sample_job_id"):
                        if k in learned_anchors:
                            merged[k] = learned_anchors[k]
                    tj["learned_anchors"] = merged
                    tmpl.template_json = tj
                    flag_modified(tmpl, "template_json")
                    db.commit()
                    logger.info("learned_anchors_auto_persisted", template_id=template_id, field_count=len(learned_anchors.get("fields", {})))
        except Exception as exc:
            logger.error("failed_saving_learned_anchors", template_id=template_id, error=str(exc))

    @classmethod
    def _find_field_bbox_by_label(
        cls,
        field_name: str,
        aliases: list[str],
        words: list[dict],
        page_width: int,
        page_height: int,
        spec: dict | None = None,
    ) -> dict | None:
        """
        Locates the label for field_name on the page and returns the bounding box
        of the value area immediately to the right of the label.
        """
        clean = field_name.replace("_", " ").lower().strip()
        candidates = [f"{clean}:", f"{clean}.", clean]
        toks = clean.split()
        if toks and toks[-1] in ("no", "number", "num", "nr"):
            base = " ".join(toks[:-1]).strip()          # "ba no" -> "ba": also try the printed spellings of "no"
            if base:
                candidates.extend([f"{base} no:", f"{base} no.", f"{base} number:", f"{base} number", f"{base}#"])
        else:
            candidates.extend([f"{clean} no:", f"{clean} no.", f"{clean} number:", f"{clean} number"])

        # Other printed spellings of the label come in as `aliases` (the template's own, plus the
        # knowledge layer's labels for a known field) - nothing field-specific is listed here.
        for a in (aliases or []):
            al = a.lower().strip()
            candidates.extend([f"{al}:", f"{al}.", al])

        best_match = None
        best_score = 0.0

        for pat in candidates:
            tokens = pat.split()
            k = len(tokens)
            for i in range(len(words) - k + 1):
                window = words[i:i + k]
                if window[0]["y1"] < 100 and len(words) > 10:
                    continue

                win_txt = " ".join(w["text"].lower() for w in window)
                w_norm = re.sub(r"[^a-z0-9]", "", win_txt)
                p_norm = re.sub(r"[^a-z0-9]", "", pat)

                if len(p_norm) <= 3:
                    score = 1.0 if p_norm == w_norm else 0.0
                else:
                    score = SequenceMatcher(None, p_norm, w_norm).ratio()
                    if p_norm == w_norm:
                        score = 1.0
                    elif len(p_norm) >= 5 and p_norm in w_norm:
                        score = max(score, 0.92)

                if score > best_score and score >= 0.82:
                    lx1 = min(w["x1"] for w in window)
                    ly1 = min(w["y1"] for w in window)
                    lx2 = max(w["x2"] for w in window)
                    ly2 = max(w["y2"] for w in window)
                    ly_center = (ly1 + ly2) / 2
                    lh = max(18, ly2 - ly1)

                    # Look for value words to the right on same line
                    line_words = [
                        w for w in words
                        if w["x1"] >= lx2 - 4
                        and abs((w["y1"] + w["y2"]) / 2 - ly_center) <= lh * 0.95
                        and w["text"] not in [":", "-", "|", "~", "=", "/", "\\", ";"]
                    ]

                    direct_value = None
                    direct_bbox = None
                    direct_words: list[dict] = []
                    val_regex = (spec or {}).get("regex")
                    if val_regex and line_words:
                        line_str = " ".join(w["text"] for w in line_words)
                        m = re.search(val_regex, line_str, re.IGNORECASE)
                        if m:
                            cand_val = m.group(0).strip()
                            # Blacklist check
                            if spec and spec.get("blacklist") and any(b in cand_val.lower() for b in spec["blacklist"]):
                                cand_val = None
                            if cand_val:
                                direct_value = cand_val
                                m_words = [w for w in line_words if any(part in w["text"] for part in direct_value.split())]
                                direct_words = m_words
                                if m_words:
                                    pad = 4
                                    direct_bbox = {
                                        "x1": max(0, min(w["x1"] for w in m_words) - pad),
                                        "y1": max(0, min(w["y1"] for w in m_words) - pad),
                                        "x2": min(page_width, max(w["x2"] for w in m_words) + pad),
                                        "y2": min(page_height, max(w["y2"] for w in m_words) + pad),
                                    }

                    if direct_bbox:
                        vx1, vy1, vx2, vy2 = direct_bbox["x1"], direct_bbox["y1"], direct_bbox["x2"], direct_bbox["y2"]
                    else:
                        vx1 = lx2 + 2
                        vy1 = max(0, ly1 - 8)
                        # The label can legitimately sit right at (or past) the
                        # page's right edge — e.g. a value box that wraps to
                        # the next line, or a noisy word-detection box that
                        # overruns the page. min(page_width - 5, ...) alone
                        # can then land below vx1, producing an inverted box
                        # that crashes PIL's Image.crop(). Widen the lower
                        # bound along with the upper one instead of just
                        # clamping, so there's always room for a value box.
                        vx2 = max(vx1 + 20, min(page_width - 5, lx2 + 400))
                        vy2 = max(vy1 + 10, min(page_height, ly2 + 12))

                    best_score = score
                    best_match = {
                        "matched_label": win_txt,
                        "score": score,
                        "label_bbox": {"x1": lx1, "y1": ly1, "x2": lx2, "y2": ly2},
                        "val_bbox": {"x1": max(0, vx1), "y1": max(0, vy1), "x2": min(page_width, max(vx2, vx1 + 1)), "y2": min(page_height, max(vy2, vy1 + 1))},
                        "direct_value": direct_value,
                        "direct_conf": cls._evidence_confidence(score, direct_words) if direct_value else None,
                    }

        return best_match

    def _execute_warm_anchor_path(
        self,
        pages: list[Image.Image],
        template_fields: list[dict],
        learned_anchors: dict,
        job_id: str,
        template_id: int | None = None,
        event_callback=None,
        kv: dict | None = None,
    ) -> PipelineResult | None:
        """Dynamic label-anchored micro-OCR with strict value isolation and bounding box snapping."""
        if event_callback:
            event_callback("WARM_ANCHOR_MATCH_STARTED", {"mode": "WARM_HIT", "anchors_count": len(learned_anchors.get("fields", {}))})

        page0 = pages[0]
        cur_w, cur_h = page0.size
        orig_w = learned_anchors.get("page_width", cur_w) or cur_w
        orig_h = learned_anchors.get("page_height", cur_h) or cur_h

        scale_x = cur_w / float(orig_w)
        scale_y = cur_h / float(orig_h)

        dx, dy = 0, 0
        anchor_matched = False

        # Words for label detection: reuse the page-wide OCR pass when the caller has one
        # (no second full-page OCR); otherwise read the pages here.
        words_by_page = []
        all_detections = []
        if kv is not None:
            words_by_page = [words_from_lines(lines) for lines in kv["lines"]]
            while len(words_by_page) < len(pages):
                words_by_page.append([])
            all_detections = self._detections_from_words(words_by_page)
        else:
            for p_idx, page in enumerate(pages):
                prep_page = self._preprocess_image(page)
                p_words = []
                try:
                    t_data = pytesseract.image_to_data(prep_page, output_type=pytesseract.Output.DICT, timeout=_TESS_TIMEOUT)
                    for idx in range(len(t_data.get("text", []))):
                        w_txt = t_data["text"][idx].strip()
                        if w_txt:
                            box = {
                                "x1": t_data["left"][idx],
                                "y1": t_data["top"][idx],
                                "x2": t_data["left"][idx] + t_data["width"][idx],
                                "y2": t_data["top"][idx] + t_data["height"][idx],
                            }
                            c_val = float(t_data["conf"][idx]) / 100.0 if float(t_data["conf"][idx]) > 0 else 0.8
                            p_words.append({
                                "text": w_txt,
                                "x1": box["x1"], "y1": box["y1"], "x2": box["x2"], "y2": box["y2"],
                                "conf": c_val,
                            })
                            all_detections.append({
                                "id": f"det_p{p_idx+1}_{idx}",
                                "text": w_txt,
                                "bbox": box,
                                "confidence": round(c_val, 3),
                                "page_number": p_idx + 1,
                            })
                except Exception as e:
                    logger.warning("warm_words_extraction_failed", page=p_idx, error=str(e))
                words_by_page.append(p_words)

        p0_words = words_by_page[0] if words_by_page else []

        anchored_fields = dict(learned_anchors.get("fields", {}))
        target_keys = [f["name"] for f in template_fields] if template_fields else list(anchored_fields.keys())
        field_defs = {f["name"]: f for f in template_fields} if template_fields else {}

        # Prepare field crop tasks
        crop_tasks = []
        for key in target_keys:
            fdef = field_defs.get(key, {})
            if fdef.get("type") == "table":
                continue

            spec = self._field_spec(key, fdef) or {
                "patterns": [key.replace("_", " ") + ":", key.replace("_", " ")],
                "regex": None,
                "blacklist": [],
            }
            aliases = list(fdef.get("aliases", [])) + spec.get("patterns", [])

            # 1. Try Dynamic Label Anchor on THIS page first
            lbl_match = self._find_field_bbox_by_label(
                field_name=key,
                aliases=aliases,
                words=p0_words,
                page_width=page0.width,
                page_height=page0.height,
                spec=spec,
            )

            if lbl_match:
                vb = lbl_match["val_bbox"]
                x1 = max(0, int(vb["x1"]))
                y1 = max(0, int(vb["y1"]))
                x2 = min(page0.width, int(vb["x2"]))
                y2 = min(page0.height, int(vb["y2"]))
                p_idx = 0
                target_page = page0
            else:
                # 2. Fall back to template memory, loosely: field values routinely sit
                # a bit off from where they were first learned (slightly different scan,
                # a shifted scan/crop, different page DPI), so the search padding is
                # proportional to page size rather than a fixed pixel count, and we
                # sanity-check the scale ratio before trusting the stored geometry at all.
                f_info = anchored_fields.get(key)
                if not f_info or "bbox" not in f_info:
                    continue
                if not (0.5 <= scale_x <= 2.0 and 0.5 <= scale_y <= 2.0):
                    # This document's page dimensions are too different from what the
                    # template was learned on for scaled stored coordinates to be
                    # trustworthy -- skip the memory fallback for this field rather than
                    # guess at a box that's very likely wrong.
                    continue
                box = f_info["bbox"]
                p_num = f_info.get("page_number", 1)
                p_idx = min(max(0, p_num - 1), len(pages) - 1)
                target_page = pages[p_idx]
                pad_x = max(35, int(target_page.width * 0.025))
                pad_y = max(15, int(target_page.height * 0.015))
                x1 = max(0, int(box["x1"] * scale_x + dx - pad_x))
                y1 = max(0, int(box["y1"] * scale_y + dy - pad_y))
                x2 = min(target_page.width, int(box["x2"] * scale_x + dx + pad_x))
                y2 = min(target_page.height, int(box["y2"] * scale_y + dy + pad_y))

            if x2 > x1 and y2 > y1:
                crop = target_page.crop((x1, y1, x2, y2))
                if crop.height > crop.width * 1.5:
                    crop = crop.rotate(270, expand=True)

                crop_tasks.append({
                    "field_name": key,
                    "crop": crop,
                    "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    "page_number": p_idx + 1,
                    "spec": spec,
                    "target_page": target_page,
                    "direct_value": lbl_match.get("direct_value") if lbl_match else None,
                    "direct_conf": lbl_match.get("direct_conf") if lbl_match else None,
                })

        if not crop_tasks:
            return None

        # Execute micro-OCR in parallel with preprocessing, regex isolation & tight snapping
        def _ocr_crop(task: dict) -> dict:
            crop_img = task["crop"]
            spec = task["spec"]
            field_name = task["field_name"]
            orig_bbox = task["bbox"]
            target_page = task["target_page"]

            if task.get("direct_value"):
                return {
                    "field_name": field_name,
                    "value": task["direct_value"],
                    "confidence": task.get("direct_conf") if task.get("direct_conf") is not None else 0.5,
                    "bbox": orig_bbox,
                    "crop_image": crop_img,
                    "page_number": task["page_number"],
                }

            try:
                prep_crop = self._preprocess_image(crop_img)
                data = pytesseract.image_to_data(prep_crop, output_type=pytesseract.Output.DICT, config="--psm 6", timeout=_TESS_TIMEOUT)
                
                c_words = []
                for idx in range(len(data.get("text", []))):
                    w_txt = data["text"][idx].strip()
                    if w_txt:
                        c_words.append({
                            "text": w_txt,
                            "x1": data["left"][idx],
                            "y1": data["top"][idx],
                            "x2": data["left"][idx] + data["width"][idx],
                            "y2": data["top"][idx] + data["height"][idx],
                            "conf": float(data["conf"][idx]) if float(data["conf"][idx]) > 0 else 0.0,
                        })

                confs = [w["conf"] for w in c_words if w["conf"] > 0]
                avg_c = (sum(confs) / len(confs) / 100.0) if confs else 0.0
                raw_text = " ".join(w["text"] for w in c_words).strip()

                final_val = raw_text
                tight_bbox = orig_bbox
                tight_crop = crop_img
                final_conf = avg_c

                # Strict regex pattern isolation & bounding box snapping
                if spec and spec.get("regex"):
                    m = re.search(spec["regex"], raw_text, re.IGNORECASE)
                    if m:
                        final_val = m.group(0).strip()
                        # the expected shape was found: worth a floor, not certainty
                        final_conf = min(0.95, max(avg_c, 0.6))
                        # Snap bounding box to the matched word(s) only
                        matched_ws = [cw for cw in c_words if any(part in cw["text"] for part in final_val.split())]
                        if matched_ws:
                            pad = 4
                            snap_x1 = max(0, orig_bbox["x1"] + min(w["x1"] for w in matched_ws) - pad)
                            snap_y1 = max(0, orig_bbox["y1"] + min(w["y1"] for w in matched_ws) - pad)
                            snap_x2 = min(target_page.width, orig_bbox["x1"] + max(w["x2"] for w in matched_ws) + pad)
                            snap_y2 = min(target_page.height, orig_bbox["y1"] + max(w["y2"] for w in matched_ws) + pad)
                            tight_bbox = {"x1": snap_x1, "y1": snap_y1, "x2": snap_x2, "y2": snap_y2}
                            tight_crop = target_page.crop((snap_x1, snap_y1, snap_x2, snap_y2))
                    else:
                        # Regex validation failed! The crop text doesn't match expected field format
                        final_conf = min(0.25, avg_c * 0.3)
                        final_val = re.sub(r"^[\s:\-\|\~\+\.\*]+", "", final_val).strip()
                        final_val = re.sub(r"[\_\~\|\s]+$", "", final_val).strip()
                else:
                    final_val = re.sub(r"^[\s:\-\|\~\+\.\*]+", "", final_val).strip()
                    final_val = re.sub(r"[\_\~\|\s]+$", "", final_val).strip()

                # Blacklist noise rejection
                if spec and spec.get("blacklist"):
                    if any(bw.lower() in final_val.lower() for bw in spec["blacklist"]):
                        final_val = ""
                        final_conf = 0.0

                return {
                    "field_name": field_name,
                    "value": final_val,
                    "confidence": round(final_conf, 3),
                    "bbox": tight_bbox,
                    "crop_image": tight_crop,
                    "page_number": task["page_number"],
                }
            except Exception as e:
                logger.warning("micro_ocr_task_failed", field=field_name, error=str(e))
                return {
                    "field_name": field_name,
                    "value": "",
                    "confidence": 0.0,
                    "bbox": orig_bbox,
                    "crop_image": crop_img,
                    "page_number": task["page_number"],
                }

        worker_count = min(len(crop_tasks), 8)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            micro_results = list(executor.map(_ocr_crop, crop_tasks))

        # Check quality of warm hit
        valid_fields = [r for r in micro_results if r["value"].strip() and r["confidence"] >= 0.40]
        if len(valid_fields) < max(1, len(crop_tasks) * 0.3):
            logger.info("warm_hit_insufficient_fields", valid=len(valid_fields), total=len(crop_tasks))
            return None  # Fall through to cold path

        fields_out = []
        total_conf = 0.0
        seen_keys = set()
        for r in micro_results:
            total_conf += r["confidence"]
            seen_keys.add(r["field_name"])
            fields_out.append(FieldResult(
                field_name=r["field_name"],
                value=r["value"],
                confidence=r["confidence"],
                page_number=r["page_number"],
                bbox=r["bbox"],
                crop_image=r["crop_image"],
                extraction_method="learned_anchor_warm",
            ))

        # Ensure all scalar template fields are present in the output
        for key in target_keys:
            if key not in seen_keys:
                fdef = field_defs.get(key, {})
                if fdef.get("type") == "table":
                    continue
                fields_out.append(FieldResult(
                    field_name=key,
                    value="",
                    confidence=0.0,
                    page_number=1,
                    bbox=None,
                    crop_image=None,
                    extraction_method="not_found",
                ))

        avg_conf = total_conf / len(fields_out) if fields_out else 0.0

        if event_callback:
            event_callback("WARM_ANCHOR_MATCH_DONE", {
                "mode": "WARM_HIT",
                "matched_fields": len(fields_out),
                "avg_confidence": round(avg_conf, 3),
            })

        return PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=fields_out,
            overall_confidence=round(avg_conf, 4),
            all_detections=all_detections,
            metadata={
                "mode": "WARM_ANCHOR_HIT",
                "speed": "ultra_fast",
                "anchor_matched": anchor_matched,
                "learned_anchors": learned_anchors,
                "fields_extracted": len(fields_out),
                "all_detections": all_detections,
                "doc_page_width": cur_w,
                "doc_page_height": cur_h,
            },
        )

    def _execute_cold_learned_path(
        self,
        pages: list[Image.Image],
        template_fields: list[dict],
        job_id: str,
        *,
        native_texts: list[str] | None = None,
        schema: dict | None = None,
        department: str = "GENERAL",
        doc_type: str = "UNKNOWN",
        template_version: int = 1,
        extraction_instructions: str | None = None,
        template_id: int | None = None,
        event_callback=None,
        allow_persist: bool = True,
        kv: dict | None = None,
    ) -> PipelineResult:
        """Cold path: detects full page OCR geometry + runs VLM extraction, then (if allowed) persists learned anchors."""
        if event_callback:
            event_callback("COLD_LEARNED_FALLBACK_STARTED", {"reason": "learning_template_geometry"})

        # Step A: Run OCR for page line geometry
        ocr_engine = self._get_ocr_engine()
        # reuse the pass already made for pair discovery instead of OCR-ing twice
        ocr_res = kv["ocr_res"] if kv else ocr_engine.process_images(pages)
        ocr_lines_by_page = [p.lines for p in ocr_res.pages]

        if event_callback:
            event_callback("OCR_GEOMETRY_DETECTED", {"pages": len(pages)})

        # Extract word-level bboxes for tight bounding box localization and direct matching.
        words_by_page = []
        for p_idx, page_img in enumerate(pages):
            p_words = []
            try:
                if p_idx < len(ocr_lines_by_page):
                    for ln in ocr_lines_by_page[p_idx]:
                        for w in (ln.words or []):
                            txt = (w.text or "").strip()
                            if txt:
                                p_words.append({
                                    "text": txt,
                                    "x1": w.bbox.x1,
                                    "y1": w.bbox.y1,
                                    "x2": w.bbox.x2,
                                    "y2": w.bbox.y2,
                                    "conf": w.confidence or 0.0,
                                })
            except Exception as e:
                logger.warning("word_reuse_failed_for_page", page=p_idx, error=str(e))
                p_words = []

            if not p_words:
                try:
                    t_data = pytesseract.image_to_data(page_img, output_type=pytesseract.Output.DICT, timeout=_TESS_TIMEOUT)
                    for idx in range(len(t_data.get("text", []))):
                        w_txt = t_data["text"][idx].strip()
                        if w_txt:
                            p_words.append({
                                "text": w_txt,
                                "x1": t_data["left"][idx],
                                "y1": t_data["top"][idx],
                                "x2": t_data["left"][idx] + t_data["width"][idx],
                                "y2": t_data["top"][idx] + t_data["height"][idx],
                                "conf": float(t_data["conf"][idx]) if float(t_data["conf"][idx]) > 0 else 0.0
                            })
                except Exception as e:
                    logger.warning("word_extraction_failed_for_page", page=p_idx, error=str(e))

            words_by_page.append(p_words)

        # Step B: Fast deterministic layout matching against template fields
        direct_matched_fields = {}
        target_fields = [f for f in template_fields if isinstance(f, dict) and f.get("type") != "table"]
        # Fields the pair parser (kv_discovery + kv_match) already matched are left to it: it splits a printed
        # label from its value properly, where this label-then-everything-to-the-right scan can take the rest of a
        # longer label ("Date of Final Insp ...") for a value. They are filled from the pairs afterwards.
        kv_matched: set[str] = set()
        if kv and kv.get("pairs"):
            try:
                kv_matched = set(match_fields(self._wanted_fields(template_fields), kv["pairs"]))
            except Exception:
                kv_matched = set()
        if target_fields and words_by_page:
            for f in target_fields:
                fn = f.get("name", "")
                if fn in kv_matched:
                    continue
                fa = f.get("aliases", [])
                spec = self._field_spec(fn, f)
                lbl_m = self._find_field_bbox_by_label(
                    fn, fa, words_by_page[0], pages[0].width, pages[0].height, spec=spec
                )
                if lbl_m:
                    val = lbl_m.get("direct_value")
                    val_box = lbl_m.get("val_bbox")
                    val_conf = lbl_m.get("direct_conf")
                    if not val and val_box:
                        c_img = self._safe_crop(pages[0], val_box["x1"], val_box["y1"], val_box["x2"], val_box["y2"])
                        if c_img:
                            try:
                                prep = self._preprocess_image(c_img)
                                val_raw = pytesseract.image_to_string(prep, config="--psm 6", timeout=_TESS_TIMEOUT).strip()
                                val_raw = re.sub(r"^[\s:\-\|\~\+\.\*]+", "", val_raw).strip()
                                val = re.sub(r"[\_\~\|\s]+$", "", val_raw).strip()
                                if spec and spec.get("regex"):
                                    m_spec = re.search(spec["regex"], val, re.IGNORECASE)
                                    val = m_spec.group(0).strip() if m_spec else ""
                                if spec and spec.get("blacklist") and any(b in val.lower() for b in spec["blacklist"]):
                                    val = ""
                                # words inside the value box carry the OCR confidence of what was read
                                vw = [w for w in words_by_page[0]
                                      if val_box["x1"] <= (w["x1"] + w["x2"]) / 2 <= val_box["x2"]
                                      and val_box["y1"] <= (w["y1"] + w["y2"]) / 2 <= val_box["y2"]]
                                val_conf = self._evidence_confidence(lbl_m.get("score"), vw)
                            except Exception:
                                val = ""
                    if val:
                        direct_matched_fields[fn] = {
                            "value": val,
                            "confidence": val_conf if val_conf is not None else 0.5,
                            "bbox": val_box,
                        }

        # If at least 70% of scalar fields are found via deterministic layout, bypass Ollama
        if target_fields and len(direct_matched_fields) >= max(1, int(len(target_fields) * 0.7)):
            logger.info("cold_path_bypassing_vlm_via_direct_match", matched=len(direct_matched_fields), total=len(target_fields))
            raw_fields = direct_matched_fields
            conf_by_field = {k: v["confidence"] for k, v in direct_matched_fields.items()}
            ai_result_engine = "learned_anchor_layout"
            ai_result_model = "deterministic_geometry"
        else:
            # Fall back to Qwen 2.5-VL / LLM semantic extraction. A stalled or unreachable model must not hang
            # or fail the job: keep what the deterministic layout pass already found and say so.
            try:
                ai_result = ai_orchestrator.run_smart_extraction(
                    ocr_res,
                    schema or {},
                    doc_id=job_id,
                    department=department,
                    doc_type=doc_type,
                    template_version=template_version,
                    extraction_instructions=extraction_instructions,
                    page_images=pages,
                )
            except Exception as exc:
                logger.error("cold_path_vlm_failed_degrading", error=str(exc), direct_fields=len(direct_matched_fields))
                if event_callback:
                    event_callback("VLM_EXTRACTION_SKIPPED", {"reason": str(exc)[:300]})
                ai_result = None

            if ai_result is None:
                raw_fields = dict(direct_matched_fields)
                conf_by_field = {k: v["confidence"] for k, v in direct_matched_fields.items()}
                ai_result_engine = "learned_anchor_layout"
                ai_result_model = "deterministic_geometry(vlm_unavailable)"
            else:
                ai_result_engine = ai_result.engine_name
                ai_result_model = ai_result.model_name
                if event_callback:
                    event_callback("VLM_EXTRACTION_DONE", {"model": ai_result.model_name})

                conf_by_field = {c.field_name: c.confidence_score for c in ai_result.confidence_scores}
                envelope = ai_result.parsed_data or {}
                raw_fields = envelope.get("fields", envelope)
                # Merge in direct matched fields for any missing
                for k, v in direct_matched_fields.items():
                    if k not in raw_fields or not raw_fields[k]:
                        raw_fields[k] = v

        fields_out = []
        fields_learned = {}
        total_conf = 0.0

        for field_name, field_data in raw_fields.items():
            if field_name == "tables":
                continue
            if isinstance(field_data, dict) and "value" in field_data:
                field_val = field_data.get("value")
                field_conf = field_data.get("confidence")
            else:
                field_val = field_data
                field_conf = conf_by_field.get(field_name)

            conf = field_conf or 0.0
            total_conf += conf

            # Find matching bounding box on the page
            bbox = None
            crop_image = None
            best_p_idx = 0

            if field_val and isinstance(field_val, str) and field_val.strip():
                clean_target = field_val.strip()
                val_tokens = [tok for tok in re.split(r"\s+", clean_target) if tok]

                # Step 1: Precise Word-Level Sequence Matching (tight bbox)
                best_word_score = 0.0
                best_word_bbox = None
                k = len(val_tokens)

                for p_idx, p_words in enumerate(words_by_page):
                    if not p_words:
                        continue
                    # Sliding window of token length
                    for i in range(len(p_words) - k + 1):
                        window = p_words[i:i + k]
                        win_txt = " ".join(w["text"] for w in window)
                        sc = SequenceMatcher(None, clean_target.lower(), win_txt.lower()).ratio()
                        if sc > best_word_score:
                            best_word_score = sc
                            best_word_bbox = {
                                "x1": min(w["x1"] for w in window),
                                "y1": min(w["y1"] for w in window),
                                "x2": max(w["x2"] for w in window),
                                "y2": max(w["y2"] for w in window),
                            }
                            best_p_idx = p_idx

                    # Individual token fallback
                    for w in p_words:
                        sc = SequenceMatcher(None, clean_target.lower(), w["text"].lower()).ratio()
                        if sc > best_word_score:
                            best_word_score = sc
                            best_word_bbox = {
                                "x1": w["x1"],
                                "y1": w["y1"],
                                "x2": w["x2"],
                                "y2": w["y2"],
                            }
                            best_p_idx = p_idx

                if best_word_bbox and best_word_score >= 0.60:
                    bbox = best_word_bbox
                else:
                    # Step 2: Line-Level Fallback Matching
                    norm_field = re.sub(r"[^a-z0-9]", "", clean_target.lower())
                    if len(norm_field) >= 2:
                        best_match_score = 0.0
                        best_line = None

                        for p_idx, lines in enumerate(ocr_lines_by_page):
                            for line in lines:
                                norm_line = re.sub(r"[^a-z0-9]", "", line.text.lower())
                                score = SequenceMatcher(None, norm_field, norm_line).ratio()

                                if len(norm_line) > len(norm_field):
                                    for i in range(len(norm_line) - len(norm_field) + 1):
                                        sub_score = SequenceMatcher(None, norm_field, norm_line[i:i + len(norm_field)]).ratio()
                                        score = max(score, sub_score)

                                if norm_field in norm_line:
                                    score = 1.0

                                if score > best_match_score:
                                    best_match_score = score
                                    best_line = line
                                    best_p_idx = p_idx

                        if best_line and best_match_score > 0.55:
                            bbox = {
                                "x1": best_line.bbox.x1,
                                "y1": best_line.bbox.y1,
                                "x2": best_line.bbox.x2,
                                "y2": best_line.bbox.y2,
                            }

                # Step 3: Label-Proximity Fallback Locator
                if bbox is None:
                    lbl_match = self._find_field_bbox_by_label(
                        field_name=field_name,
                        aliases=[],
                        words=words_by_page[0] if words_by_page else [],
                        page_width=pages[0].width,
                        page_height=pages[0].height,
                    )
                    if lbl_match:
                        bbox = lbl_match["val_bbox"]
                        best_p_idx = 0
                        if not field_val or not str(field_val).strip():
                            crop_val = self._safe_crop(
                                pages[best_p_idx], bbox["x1"] - 4, bbox["y1"] - 4, bbox["x2"] + 4, bbox["y2"] + 4
                            )
                            try:
                                val_str = pytesseract.image_to_string(crop_val, config="--psm 6", timeout=_TESS_TIMEOUT).strip() if crop_val else ""
                                clean_str = re.sub(r"^[\s:\-\|\~\+\.\*]+", "", val_str).strip()
                                clean_str = re.sub(r"[\_\~\|\s]+$", "", clean_str).strip()
                                if clean_str:
                                    field_val = clean_str
                                    conf = max(conf, 0.88)
                            except Exception:
                                pass

                if bbox:
                    pad = 8
                    crop_image = self._safe_crop(
                        pages[best_p_idx], bbox["x1"] - pad, bbox["y1"] - pad, bbox["x2"] + pad, bbox["y2"] + pad
                    )
                    if crop_image and crop_image.height > crop_image.width * 1.5:
                        crop_image = crop_image.rotate(270, expand=True)

                    fields_learned[field_name] = {
                        "bbox": bbox,
                        "page_number": best_p_idx + 1,
                        "value": str(field_val),
                    }

            fields_out.append(FieldResult(
                field_name=field_name,
                value="" if field_val is None else str(field_val),
                confidence=conf,
                page_number=best_p_idx + 1 if bbox else 1,
                bbox=bbox,
                crop_image=crop_image,
                extraction_method="learned_anchor_cold",
            ))

        # Step 4: Recover any non-table template fields completely omitted in raw_fields
        field_defs_all = {f.get("name"): f for f in template_fields} if template_fields else {}
        for fn, fdef in field_defs_all.items():
            if not fn or fn in fields_learned or fdef.get("type") == "table":
                continue
            lbl_match = self._find_field_bbox_by_label(
                field_name=fn,
                aliases=fdef.get("aliases", []),
                words=words_by_page[0] if words_by_page else [],
                page_width=pages[0].width,
                page_height=pages[0].height,
            )
            if lbl_match:
                bbox = lbl_match["val_bbox"]
                crop_val = self._safe_crop(pages[0], bbox["x1"] - 6, bbox["y1"] - 6, bbox["x2"] + 6, bbox["y2"] + 6)
                if crop_val is not None and crop_val.height > crop_val.width * 1.5:
                    crop_val = crop_val.rotate(270, expand=True)
                val_txt = ""
                try:
                    val_str = pytesseract.image_to_string(crop_val, config="--psm 6", timeout=_TESS_TIMEOUT).strip() if crop_val else ""
                    val_txt = re.sub(r"^[\s:\-\|\~\+\.\*]+", "", val_str).strip()
                    val_txt = re.sub(r"[\_\~\|\s]+$", "", val_txt).strip()
                except Exception:
                    pass
                fields_learned[fn] = {
                    "bbox": bbox,
                    "page_number": 1,
                    "value": val_txt,
                }
                fields_out.append(FieldResult(
                    field_name=fn,
                    value=val_txt,
                    confidence=0.88 if val_txt else 0.5,
                    page_number=1,
                    bbox=bbox,
                    crop_image=crop_val,
                    extraction_method="learned_anchor_cold",
                ))

        # Identify prominent anchor line on page 1
        anchor_text = ""
        anchor_bbox = None
        if ocr_lines_by_page and ocr_lines_by_page[0]:
            top_lines = [
                ln for ln in ocr_lines_by_page[0]
                if ln.bbox.y1 < pages[0].height * 0.35 and len(ln.text.strip()) >= 4
            ]
            if top_lines:
                # Select highest confidence or longest header line
                top_line = max(top_lines, key=lambda l: (l.confidence, len(l.text)))
                anchor_text = top_line.text.strip()
                anchor_bbox = {
                    "x1": top_line.bbox.x1,
                    "y1": top_line.bbox.y1,
                    "x2": top_line.bbox.x2,
                    "y2": top_line.bbox.y2,
                }

        new_learned_anchors = {
            "page_width": pages[0].width,
            "page_height": pages[0].height,
            "anchor_text": anchor_text,
            "anchor_bbox": anchor_bbox,
            "fields": fields_learned,
            "learned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sample_job_id": job_id,
        }

        # Automatically persist learned anchors to Template in Oracle DB -- but
        # only when allowed to (see allow_persist's docstring in extract_fields):
        # a genuine first learn or an explicit force_relearn always persists;
        # an automatic fallback from a degraded warm hit extracts this document
        # correctly without silently overwriting a pattern that is still good
        # for other documents of the same template.
        anchors_persisted = False
        if template_id and fields_learned and allow_persist:
            self._persist_learned_anchors(template_id, new_learned_anchors)
            anchors_persisted = True
        elif template_id and fields_learned and not allow_persist:
            logger.info(
                "learned_anchor_persist_skipped_fallback_run",
                template_id=template_id,
                reason="read_only_run_or_existing_anchors_preserved_after_automatic_fallback",
            )

        overall_conf = total_conf / len(fields_out) if fields_out else 0.0

        if event_callback:
            event_callback("COLD_LEARNED_FALLBACK_DONE", {
                "mode": "COLD_LEARNED_CACHE",
                "learned_fields": len(fields_learned),
            })

        all_detections = []
        for p_idx, p_words in enumerate(words_by_page):
            for w_idx, w in enumerate(p_words):
                all_detections.append({
                    "id": f"det_p{p_idx+1}_{w_idx}",
                    "text": w["text"],
                    "bbox": {"x1": w["x1"], "y1": w["y1"], "x2": w["x2"], "y2": w["y2"]},
                    "confidence": round(w.get("conf", 0.0), 3),
                    "page_number": p_idx + 1,
                })

        cold_pipeline_result = PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=fields_out,
            overall_confidence=round(overall_conf, 4),
            all_detections=all_detections,
            metadata={
                "mode": "COLD_LEARNED_CACHE",
                "anchor_learned": bool(fields_learned),
                "anchors_persisted": anchors_persisted,
                "learned_anchors": new_learned_anchors,
                "all_detections": all_detections,
                "doc_page_width": pages[0].width if pages else 0,
                "doc_page_height": pages[0].height if pages else 0,
                "ai_engine": ai_result_engine,
                "ai_model": ai_result_model,
                "ocr_metadata": ocr_res.model_dump(),
            },
        )
        self._attach_discovered_pairs(cold_pipeline_result, kv, template_fields)
        return cold_pipeline_result

