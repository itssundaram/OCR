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
from app.ai.orchestrator import ai_orchestrator

logger = get_logger(__name__)

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
        """Return (crop, offset_x, offset_y) for every table Surya sees on the page.

        The table-structure model is trained on tight table crops; handing it a
        whole page makes it miss rows or merge text above/below the table.
        When layout detection finds no table (or fails) the whole page is used,
        which is what this pipeline did before.
        """
        regions: list[tuple[Image.Image, int, int]] = []
        try:
            from app.core.model_registry import get_surya_layout_engine
            pad = 5  # same padding SuryaLayoutEngine applies to its crops
            for region in get_surya_layout_engine().detect_layout(page):
                if str(region.region_type).strip().lower() != "table":
                    continue
                ox = max(0, int(region.bbox.x1) - pad)
                oy = max(0, int(region.bbox.y1) - pad)
                if region.cropped_image is not None:
                    regions.append((region.cropped_image, ox, oy))
        except Exception as exc:
            logger.warning("learned_anchor_table_layout_failed", error=str(exc))
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
        """Extract structured tables (cells with geometry) from every table region.

        Table regions come from Surya layout; each crop goes to the singleton
        TATR engine (which itself falls back to img2table), and boxes are
        mapped back to full-page coordinates so evidence crops line up.
        """
        results: list[TableResult] = []
        try:
            from app.core.model_registry import get_tatr_engine
            tatr = get_tatr_engine()
            table_idx = 1
            for p_idx, page in enumerate(pages):
                for crop, ox, oy in self._table_regions(page):
                    for t in tatr.extract_tables(crop):
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

    def _attach_discovered_pairs(
        self,
        result: PipelineResult,
        pages: list[Image.Image],
        ocr_lines_by_page: list | None = None,
    ) -> None:
        """Append every "Label: value" pair found on the page to result.fields.

        Template fields stay exactly as extracted; discovered pairs are added
        after them (source="discovered") and are never written into the
        learned anchors, so this cannot change what the template learns.
        """
        if not settings.KV_DISCOVERY_ENABLED or result is None:
            return
        try:
            if ocr_lines_by_page is None:
                ocr_res = self._get_ocr_engine().process_images(pages)
                ocr_lines_by_page = [p.lines for p in ocr_res.pages]

            def _norm(text: str) -> str:
                return " ".join((text or "").lower().split())

            existing_values = {f.field_name: _norm(f.value) for f in result.fields}
            taken = set(existing_values)

            pairs = []
            for pair in discover_key_values(ocr_lines_by_page):
                key = slugify_label(pair.label)
                # Already extracted as a template field with the same value: skip the duplicate.
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
            logger.info("learned_anchor_kv_discovery_done", job_pairs=added)
        except Exception as exc:
            logger.warning("learned_anchor_kv_discovery_failed", error=str(exc), exc_info=True)

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
        **kwargs,
    ) -> PipelineResult:
        t_start = time.perf_counter()

        # Step 1: Check if learned anchors exist in schema or template
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
                )
                if warm_result is not None:
                    self._attach_discovered_pairs(warm_result, pages)
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
        allow_persist = (not has_valid_anchors) or force_relearn
        logger.info(
            "learned_anchor_executing_cold_path",
            template_id=template_id,
            allow_persist=allow_persist,
            force_relearn=force_relearn,
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

    def _normalize_key(self, k: str) -> str:
        k_str = str(k).lower().strip()
        k_clean = re.sub(r"[^a-z0-9]", "", k_str)
        if "engine" in k_clean:
            return "engine_no"
        if "chasi" in k_clean or "chass" in k_clean:
            return "chasis_no"
        if re.search(r"\bba\b", k_str) or k_clean in ("bano", "ba", "banumber"):
            return "ba_no"
        return k_clean

    FIELD_SPECS = {
        "engine_no": {
            "patterns": ["engine no:", "engine no.", "engine no", "engine number:", "engine#"],
            "regex": r"[A-Z0-9/\-]{6,16}",
            "blacklist": ["log book", "adequate", "carried out", "serviceable", "inadequate", "tyres", "correct", "steering", "brakes"],
        },
        "chasis_no": {
            "patterns": ["chassis no:", "chasis no:", "chassis no.", "chasis no.", "chassis:", "chasis:"],
            "regex": r"(STLN[\s\w\-]+|MA5[\s\w\-]+|LPTA[\s\w\-]+|[A-Z0-9\-]{8,24})",
            "blacklist": ["carried out", "tyres", "adequate", "correct", "inadequate", "rotary", "pump", "bar type"],
        },
        "ba_no": {
            "patterns": ["ba no :", "ba no:", "ba number", "ba no.", "b.a. number", "ba:"],
            "regex": r"\d{2}[A-Z]\s*\d{5,7}[A-Z]?",
            "blacklist": ["run:", "km", "hours", "distance", "maintained", "wksp", "workshop", "nature", "service", "bar type"],
        },
    }

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
                    tj["learned_anchors"] = learned_anchors
                    tmpl.template_json = tj
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
        if "no" in clean:
            base = clean.replace("no", "").strip()
            candidates.extend([f"{base} no:", f"{base} no.", f"{base} number:", f"{base} number", f"{base}#"])
        else:
            candidates.extend([f"{clean} no:", f"{clean} no.", f"{clean} number:", f"{clean} number"])

        for a in (aliases or []):
            al = a.lower().strip()
            candidates.extend([f"{al}:", f"{al}.", al])

        if "engine" in clean:
            candidates.extend(["engine no:", "engine no.", "engine no", "engine number:", "engine#"])
        elif "chasis" in clean or "chassis" in clean:
            candidates.extend(["chassis no:", "chasis no:", "chassis no.", "chasis no.", "chassis:", "chasis:"])
        elif re.search(r"\bba\b", clean) or clean in ("ba no", "bano", "ba"):
            candidates.extend(["ba no :", "ba no:", "ba number", "ba no.", "b.a. number", "ba:"])

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

        # Fast Preprocessing on all pages for label detection and universal detections
        words_by_page = []
        all_detections = []
        for p_idx, page in enumerate(pages):
            prep_page = self._preprocess_image(page)
            p_words = []
            try:
                t_data = pytesseract.image_to_data(prep_page, output_type=pytesseract.Output.DICT)
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
                            "x1": box["x1"],
                            "y1": box["y1"],
                            "x2": box["x2"],
                            "y2": box["y2"],
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

            norm_key = self._normalize_key(key)
            spec = self.FIELD_SPECS.get(norm_key, self.FIELD_SPECS.get(key, {
                "patterns": [key.replace("_", " ") + ":", key.replace("_", " ")],
                "regex": None,
                "blacklist": [],
            }))
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
                    "confidence": 0.95,
                    "bbox": orig_bbox,
                    "crop_image": crop_img,
                    "page_number": task["page_number"],
                }

            try:
                prep_crop = self._preprocess_image(crop_img)
                data = pytesseract.image_to_data(prep_crop, output_type=pytesseract.Output.DICT, config="--psm 6")
                
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
                        final_conf = max(avg_c, 0.95)
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
    ) -> PipelineResult:
        """Cold path: detects full page OCR geometry + runs VLM extraction, then (if allowed) persists learned anchors."""
        if event_callback:
            event_callback("COLD_LEARNED_FALLBACK_STARTED", {"reason": "learning_template_geometry"})

        # Step A: Run OCR for page line geometry
        ocr_engine = self._get_ocr_engine()
        ocr_res = ocr_engine.process_images(pages)
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
                    t_data = pytesseract.image_to_data(page_img, output_type=pytesseract.Output.DICT)
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
        if target_fields and words_by_page:
            for f in target_fields:
                fn = f.get("name", "")
                fa = f.get("aliases", [])
                spec = self.FIELD_SPECS.get(self._normalize_key(fn))
                lbl_m = self._find_field_bbox_by_label(
                    fn, fa, words_by_page[0], pages[0].width, pages[0].height, spec=spec
                )
                if lbl_m:
                    val = lbl_m.get("direct_value")
                    val_box = lbl_m.get("val_bbox")
                    if not val and val_box:
                        c_img = self._safe_crop(pages[0], val_box["x1"], val_box["y1"], val_box["x2"], val_box["y2"])
                        if c_img:
                            try:
                                prep = self._preprocess_image(c_img)
                                val_raw = pytesseract.image_to_string(prep, config="--psm 6").strip()
                                val_raw = re.sub(r"^[\s:\-\|\~\+\.\*]+", "", val_raw).strip()
                                val = re.sub(r"[\_\~\|\s]+$", "", val_raw).strip()
                                if spec and spec.get("regex"):
                                    m_spec = re.search(spec["regex"], val, re.IGNORECASE)
                                    val = m_spec.group(0).strip() if m_spec else ""
                                if spec and spec.get("blacklist") and any(b in val.lower() for b in spec["blacklist"]):
                                    val = ""
                            except Exception:
                                val = ""
                    if val:
                        direct_matched_fields[fn] = {
                            "value": val,
                            "confidence": 0.95,
                            "bbox": val_box,
                        }

        # If at least 70% of scalar fields are found via deterministic layout, bypass Ollama
        if target_fields and len(direct_matched_fields) >= max(1, int(len(target_fields) * 0.7)):
            logger.info("cold_path_bypassing_vlm_via_direct_match", matched=len(direct_matched_fields), total=len(target_fields))
            raw_fields = direct_matched_fields
            conf_by_field = {k: 0.95 for k in direct_matched_fields}
            ai_result_engine = "learned_anchor_layout"
            ai_result_model = "deterministic_geometry"
        else:
            # Fall back to Qwen 2.5-VL / LLM semantic extraction
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
                                val_str = pytesseract.image_to_string(crop_val, config="--psm 6").strip() if crop_val else ""
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
                    val_str = pytesseract.image_to_string(crop_val, config="--psm 6").strip() if crop_val else ""
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
                reason="existing_anchors_preserved_after_automatic_fallback",
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
        self._attach_discovered_pairs(cold_pipeline_result, pages, ocr_lines_by_page)
        return cold_pipeline_result

