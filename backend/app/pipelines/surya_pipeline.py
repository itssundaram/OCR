"""
DOCINT — Surya Pipeline V2
Uses Surya Layout detection, Surya OCR for text, TrOCR for fallback, and TATR for tables.
Replaces the old stub.
"""
from __future__ import annotations

import io
from PIL import Image

from app.pipelines.base import OCRPipeline, PipelineCapabilities, PipelineResult, FieldResult, TableResult
from app.pipelines.registry import register_pipeline
from app.anchors.apply import read_verified_anchor
from app.ocr.preprocessing import preprocess_page
from app.ocr.surya_ocr_engine import SuryaOCREngine
from app.ocr.surya_layout_engine import SuryaLayoutEngine
from app.ocr.tatr_engine import TATRTableEngine
from app.ocr.handwriting_ocr import HandwritingOCREngine
from app.ai.orchestrator import ai_orchestrator
from app.ocr.base import OCRPage, OCRResult, OCRLine
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

@register_pipeline("surya")
class SuryaPipeline(OCRPipeline):
    @property
    def name(self) -> str:
        return "surya"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities(
            supports_layout_detection=True,
            supports_tables=True,
            supports_handwriting=True,
            supports_vision=False,
            requires_gpu=settings.GPU_ENABLED,
            model_name="surya+tatr",
        )
        
    def __init__(self):
        self._surya_ocr = None
        self._layout_engine = None
        self._tatr = None
        self.trocr = None

    @property
    def surya_ocr(self):
        from app.core.model_registry import get_surya_ocr_engine
        return get_surya_ocr_engine()

    @property
    def layout_engine(self):
        from app.core.model_registry import get_surya_layout_engine
        return get_surya_layout_engine()

    @property
    def tatr(self):
        from app.core.model_registry import get_tatr_engine
        return get_tatr_engine()


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
        event_callback=None,
        **kwargs,
    ) -> PipelineResult:
        if event_callback:
            event_callback("PREPROCESS_STARTED")
        
        preprocessed_pages = []
        for img in pages:
            from app.ocr.preprocessing import preprocess_for_ocr
            preprocessed_pages.append(preprocess_for_ocr(img, deskew=True, apply_clahe=True, denoise=True, binarize=False))
            
        if event_callback:
            event_callback("PREPROCESS_DONE")
            event_callback("LAYOUT_STARTED")
            
        regions_by_page = []
        region_count = 0
        for p_idx, img in enumerate(preprocessed_pages):
            regions = self.layout_engine.detect_layout(img)
            regions_by_page.append(regions)
            region_count += len(regions)
            
        if event_callback:
            event_callback("LAYOUT_DONE", {"region_count": region_count})
            event_callback("OCR_STARTED")
            
        fields_out = []
        total_conf = 0.0
        ocr_lines_by_page = []
        
        for p_idx, img in enumerate(preprocessed_pages):
            regions = regions_by_page[p_idx]
            page_ocr_lines = []
            
            text_regions = [r for r in regions if r.region_type in ("Text", "Title", "List-item", "Caption", "Table")]
            for r in text_regions:
                lines, conf = self.surya_ocr.process_region(r.cropped_image)
                
                # TrOCR fallback for handwriting
                if conf < settings.HANDWRITING_FALLBACK_THRESHOLD and settings.HANDWRITING_FALLBACK_ENABLED:
                    if not self.trocr:
                        self.trocr = HandwritingOCREngine()
                    hw_text, hw_conf = self.trocr.process_region(r.cropped_image)
                    if hw_conf > conf and hw_text:
                        lines = [OCRLine(text=hw_text, bbox=r.bbox, confidence=hw_conf)]
                
                for line in lines:
                    if line.bbox:
                        line.bbox.x1 += r.bbox.x1
                        line.bbox.y1 += r.bbox.y1
                        line.bbox.x2 += r.bbox.x1
                        line.bbox.y2 += r.bbox.y1
                    page_ocr_lines.append(line)
                    
            ocr_lines_by_page.append(page_ocr_lines)
            
        if event_callback:
            event_callback("OCR_DONE", {"fields_found": 0, "avg_confidence": 0.0})
            event_callback("LLM_FALLBACK_STARTED", {"reason": "always_use_llm_for_now"})
            
        ocr_pages_obj = []
        for i, lines in enumerate(ocr_lines_by_page):
            ocr_pages_obj.append(OCRPage(
                page_number=i+1,
                width=preprocessed_pages[i].width,
                height=preprocessed_pages[i].height,
                lines=lines
            ))
            
        ocr_result = OCRResult(pages=ocr_pages_obj, engine_name="surya", engine_version="2", ocr_method="layout+surya")
        
        ai_result = ai_orchestrator.run_smart_extraction(
            ocr_result,
            schema or {},
            doc_id=job_id,
            department=department,
            doc_type=doc_type,
            template_version=template_version,
            extraction_instructions=extraction_instructions,
            page_images=pages,
        )
        
        if event_callback:
            event_callback("LLM_FALLBACK_DONE")
            
        conf_by_field = {c.field_name: c.confidence_score for c in ai_result.confidence_scores}
        envelope = ai_result.parsed_data or {}
        raw_fields = envelope.get("fields", envelope)
        
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
            
            # Boost confidence for native PDF text matches
            full_native_text = " ".join(native_texts or []).lower()
            if field_val and isinstance(field_val, str) and field_val.strip():
                if field_val.lower() in full_native_text:
                    conf = 1.0
            
            total_conf += conf
            
            bbox = None
            crop_image = None
            if field_val and isinstance(field_val, str):
                import re
                from difflib import SequenceMatcher
                norm_field = re.sub(r'[^a-z0-9]', '', field_val.lower())
                if len(norm_field) >= 2:
                    best_match_score = 0.0
                    best_line = None
                    best_p_idx = -1
                    
                    for p_idx, lines in enumerate(ocr_lines_by_page):
                        for line in lines:
                            norm_line = re.sub(r'[^a-z0-9]', '', line.text.lower())
                            
                            score = SequenceMatcher(None, norm_field, norm_line).ratio()
                            if len(norm_line) > len(norm_field):
                                for i in range(len(norm_line) - len(norm_field) + 1):
                                    sub_score = SequenceMatcher(None, norm_field, norm_line[i:i+len(norm_field)]).ratio()
                                    score = max(score, sub_score)
                            
                            if norm_field in norm_line:
                                score = 1.0
                                        
                            if score > best_match_score:
                                best_match_score = score
                                best_line = line
                                best_p_idx = p_idx
                                
                    if best_line and best_match_score > 0.6:
                        bbox = {"x1": best_line.bbox.x1, "y1": best_line.bbox.y1, "x2": best_line.bbox.x2, "y2": best_line.bbox.y2}
                        pad = 10
                        c_x1 = max(0, int(bbox["x1"]) - pad)
                        c_y1 = max(0, int(bbox["y1"]) - pad)
                        c_x2 = min(preprocessed_pages[best_p_idx].width, int(bbox["x2"]) + pad)
                        c_y2 = min(preprocessed_pages[best_p_idx].height, int(bbox["y2"]) + pad)
                        crop_image = preprocessed_pages[best_p_idx].crop((c_x1, c_y1, c_x2, c_y2))
                        if crop_image and crop_image.height > crop_image.width * 1.5:
                            crop_image = crop_image.rotate(270, expand=True)

            # Human-verified box: read the value FROM the box (it wins over the LLM's guess).
            learned_anchors = (schema or {}).get("learned_anchors", {})
            anchor_method = None
            _rd = read_verified_anchor(learned_anchors, field_name, ocr_lines_by_page, preprocessed_pages)
            if _rd is not None:
                if field_val and str(field_val).strip() != _rd.value:
                    logger.info("anchor_overrides_llm_value", field=field_name, llm=str(field_val)[:40], anchor=_rd.value[:40])
                field_val, bbox, best_p_idx, anchor_method = _rd.value, _rd.bbox, _rd.page_number - 1, "anchor_verified"
                total_conf += _rd.confidence - conf
                conf = _rd.confidence
                _pg = preprocessed_pages[best_p_idx]
                crop_image = _pg.crop((max(0, int(bbox["x1"]) - 8), max(0, int(bbox["y1"]) - 8),
                                       min(_pg.width, int(bbox["x2"]) + 8), min(_pg.height, int(bbox["y2"]) + 8)))
            # Ground truth anchor integration (box only - older anchors / unreadable boxes)
            anchor_fields = learned_anchors.get("fields", {}) if isinstance(learned_anchors, dict) else {}
            if not bbox and field_name in anchor_fields:
                la_data = anchor_fields[field_name]
                if la_data.get("bbox"):
                    bbox = la_data["bbox"]
                    best_p_idx = 0
                    p_img = preprocessed_pages[0] if preprocessed_pages else None
                    if p_img:
                        pad = 8
                        c_x1 = max(0, int(bbox["x1"]) - pad)
                        c_y1 = max(0, int(bbox["y1"]) - pad)
                        c_x2 = min(p_img.width, int(bbox["x2"]) + pad)
                        c_y2 = min(p_img.height, int(bbox["y2"]) + pad)
                        if c_x2 > c_x1 and c_y2 > c_y1:
                            crop_image = p_img.crop((c_x1, c_y1, c_x2, c_y2))
                            if crop_image.height > crop_image.width * 1.5:
                                crop_image = crop_image.rotate(270, expand=True)
                    if learned_anchors.get("is_verified"):
                        conf = max(conf, 0.95)

            fields_out.append(FieldResult(
                field_name=field_name,
                value="" if field_val is None else str(field_val),
                confidence=conf,
                page_number=best_p_idx + 1 if bbox else 1,
                bbox=bbox,
                crop_image=crop_image,
                extraction_method=anchor_method or "surya+llm",
                fallback_method="llm"
            ))
            
        overall_conf = total_conf / len(fields_out) if fields_out else 0.0
        
        return PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=fields_out,
            overall_confidence=overall_conf,
            metadata={
                "ai_engine": ai_result.engine_name,
                "ai_model": ai_result.model_name,
                "ocr_metadata": ocr_result.model_dump(),
                "learned_anchors": learned_anchors if learned_anchors else None,
                "anchor_matched": bool(learned_anchors),
            },
        )

    def extract_tables(self, pages: list[Image.Image], job_id: str) -> list[TableResult]:
        tables = []
        for i, img in enumerate(pages):
            regions = self.layout_engine.detect_layout(img)
            table_regions = [r for r in regions if r.region_type == "Table"]
            for r in table_regions:
                page_tables = self.tatr.extract_tables(r.cropped_image)
                
                for t in page_tables:
                    tables.append(TableResult(
                        table_index=len(tables) + 1,
                        page_number=i + 1,
                        rows=t.rows,
                        headers=t.headers,
                        markdown=t.markdown,
                        cells=getattr(t, "cells", []),
                        bbox=getattr(t, "bbox", None),
                        extraction_method="tatr",
                    ))
        return tables

