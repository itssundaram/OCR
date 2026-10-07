"""
DOCINT — PaddleOCR Pipeline
Uses PPStructure for layout detection, PaddleOCR for text, TrOCR for fallback, and TATR for tables.
"""
from __future__ import annotations

import io
from PIL import Image

from app.pipelines.base import OCRPipeline, PipelineCapabilities, PipelineResult, FieldResult, TableResult
from app.pipelines.registry import register_pipeline
from app.ocr.preprocessing import preprocess_page
from app.ocr.paddle_ocr_engine import PaddleOCREngine
from app.ocr.ppstructure_layout_engine import PPStructureLayoutEngine
from app.ocr.tatr_engine import TATRTableEngine
from app.ocr.handwriting_ocr import HandwritingOCREngine
from app.ai.orchestrator import ai_orchestrator
from app.ocr.base import OCRPage, OCRResult, OCRLine
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

@register_pipeline("paddle")
class PaddlePipeline(OCRPipeline):
    @property
    def name(self) -> str:
        return "paddle"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities(
            supports_layout_detection=True,
            supports_tables=True,
            supports_handwriting=True,
            supports_vision=False,
            requires_gpu=settings.GPU_ENABLED,
            model_name="paddleocr+tatr",
        )
        
    def __init__(self):
        self.paddle = PaddleOCREngine()
        self.layout_engine = PPStructureLayoutEngine()
        self.tatr = TATRTableEngine()
        self.trocr = None

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
    ) -> PipelineResult:
        if event_callback:
            event_callback("PREPROCESS_STARTED")
        
        preprocessed_pages = []
        for img in pages:
            preprocessed_pages.append(preprocess_page(img, {"greyscale": True}))
            
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
            page_ocr_lines = []
            
            # Run PaddleOCR on the full page image directly.
            # Paddle's detection model fails on tight crops, so we don't use regions here.
            text_res = self.paddle.process_images([img])
            
            if text_res.pages and text_res.pages[0].lines:
                page_ocr_lines = text_res.pages[0].lines
                
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
            
        ocr_result = OCRResult(pages=ocr_pages_obj, engine_name="paddle", engine_version="2", ocr_method="layout+paddle")
        
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
                norm_field = re.sub(r'[^a-z0-9]', '', field_val.lower())
                if len(norm_field) >= 2:
                    for p_idx, lines in enumerate(ocr_lines_by_page):
                        for line in lines:
                            norm_line = re.sub(r'[^a-z0-9]', '', line.text.lower())
                            if norm_field in norm_line or (len(norm_line) >= 4 and norm_line in norm_field):
                                bbox = {"x1": line.bbox.x1, "y1": line.bbox.y1, "x2": line.bbox.x2, "y2": line.bbox.y2}
                                pad = 10
                                c_x1 = max(0, int(bbox["x1"]) - pad)
                                c_y1 = max(0, int(bbox["y1"]) - pad)
                                c_x2 = min(preprocessed_pages[p_idx].width, int(bbox["x2"]) + pad)
                                c_y2 = min(preprocessed_pages[p_idx].height, int(bbox["y2"]) + pad)
                                crop_image = preprocessed_pages[p_idx].crop((c_x1, c_y1, c_x2, c_y2))
                                break
                        if bbox:
                            break
                        
            fields_out.append(FieldResult(
                field_name=field_name,
                value="" if field_val is None else str(field_val),
                confidence=conf,
                page_number=p_idx + 1 if bbox else 1,
                bbox=bbox,
                crop_image=crop_image,
                extraction_method="paddle+llm",
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
                        extraction_method="tatr"
                    ))
        return tables
