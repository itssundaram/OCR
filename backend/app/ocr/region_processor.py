"""
DOCINT — Region Processor
Routes each layout region to the correct OCR sub-pipeline.
"""
from __future__ import annotations
import io
from dataclasses import dataclass
from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.ocr.base import OCRLine, OCRTable, BoundingBox
from app.ocr.surya_layout_engine import LayoutRegion

logger = get_logger(__name__)

@dataclass
class OCRRegionResult:
    region_type: str
    lines: list[OCRLine]
    confidence: float
    table: OCRTable | None = None


def process_region(
    region: LayoutRegion, 
    surya_engine, 
    handwriting_engine,
    llm_engine=None  # for llm cleaning
) -> OCRRegionResult:
    """Routes region processing based on its type."""
    
    match region.region_type:
        case "Text" | "Title" | "Caption" | "Footnote" | "List-item" | "Page-header" | "Page-footer":
            return _process_text_region(region, surya_engine, handwriting_engine, llm_engine)
            
        case "Table":
            return _process_table_region(region, surya_engine)
            
        case "Form":
            return _process_form_region(region, surya_engine, handwriting_engine)
            
        case "Checkbox":
            return _process_checkbox_region(region)
            
        case "Figure" | "Signature" | "Math":
            return _process_figure_region(region)
            
        case _:
            # default fallback
            return _process_text_region(region, surya_engine, handwriting_engine, llm_engine)


def _llm_clean_text(text: str, llm_engine) -> str:
    """Uses LLM to clean up OCR text if it's very messy."""
    if not llm_engine or not text.strip():
        return text
    
    try:
        # We can implement a fast call to Ollama to clean text. 
        # For simplicity in this implementation plan, we'll return original text if we don't have direct access
        # to a simple text-to-text generation function from the existing ai engine here.
        # But ideally:
        # prompt = f"Fix spelling and OCR errors in this text. Output only the corrected text:\n{text}"
        # corrected_text = llm_engine.generate(prompt)
        # return corrected_text
        return text
    except Exception as e:
        logger.warning("llm_cleaning_failed", error=str(e))
        return text


def _process_text_region(
    region: LayoutRegion, 
    surya_engine, 
    handwriting_engine,
    llm_engine
) -> OCRRegionResult:
    lines, confidence = surya_engine.process_region(region.cropped_image)
    
    # Check for handwriting fallback
    if confidence < settings.HANDWRITING_FALLBACK_THRESHOLD and settings.HANDWRITING_FALLBACK_ENABLED:
        logger.info("triggering_handwriting_fallback", region=region.region_type, conf=confidence)
        hw_text, hw_conf = handwriting_engine.process_region(region.cropped_image)
        
        if hw_conf > confidence and hw_text:
            logger.info("handwriting_fallback_used", hw_conf=hw_conf, original_conf=confidence)
            confidence = hw_conf
            # Replace lines with single handwriting line
            lines = [OCRLine(text=hw_text, bbox=region.bbox, confidence=hw_conf)]
            
    # Check for LLM cleaning fallback
    if confidence < settings.LLM_CLEANING_THRESHOLD and settings.LLM_CLEANING_ENABLED and llm_engine:
        logger.info("triggering_llm_cleaning", conf=confidence)
        for i, line in enumerate(lines):
            cleaned = _llm_clean_text(line.text, llm_engine)
            lines[i].text = cleaned
            
    # Adjust line bboxes relative to the original image
    # Note: Surya OCR on region might return bbox relative to region, so we should translate back
    for line in lines:
        if line.bbox:
            line.bbox.x1 += region.bbox.x1
            line.bbox.y1 += region.bbox.y1
            line.bbox.x2 += region.bbox.x1
            line.bbox.y2 += region.bbox.y1

    return OCRRegionResult(region_type=region.region_type, lines=lines, confidence=confidence)


def _process_table_region(region: LayoutRegion, surya_engine) -> OCRRegionResult:
    """Extracts tables. Uses img2table on the region to find structure."""
    try:
        from app.ocr.img2table_engine import extract_tables_from_image
        buf = io.BytesIO()
        region.cropped_image.save(buf, format="PNG")
        tables = extract_tables_from_image(buf.getvalue())
        
        table_obj = tables[0] if tables else None
        
        # We can also extract all text natively with surya for fallback
        lines, confidence = surya_engine.process_region(region.cropped_image)
        
        for line in lines:
            if line.bbox:
                line.bbox.x1 += region.bbox.x1
                line.bbox.y1 += region.bbox.y1
                line.bbox.x2 += region.bbox.x1
                line.bbox.y2 += region.bbox.y1
                
        return OCRRegionResult(
            region_type="Table", 
            lines=lines, 
            confidence=confidence, 
            table=table_obj
        )
    except Exception as e:
        logger.error("table_region_failed", error=str(e))
        return OCRRegionResult("Table", [], 0.0)


def _process_form_region(region: LayoutRegion, surya_engine, handwriting_engine) -> OCRRegionResult:
    # Form behaves similarly to text for OCR purposes, LLM handles key-value
    return _process_text_region(region, surya_engine, handwriting_engine, None)


def _process_checkbox_region(region: LayoutRegion) -> OCRRegionResult:
    # Vision based check. For now, just annotate.
    lines = [OCRLine(text="[CHECKBOX]", bbox=region.bbox, confidence=1.0)]
    return OCRRegionResult("Checkbox", lines, 1.0)


def _process_figure_region(region: LayoutRegion) -> OCRRegionResult:
    # Do not OCR images/signatures, just annotate them.
    lines = [OCRLine(text=f"[{region.region_type.upper()}]", bbox=region.bbox, confidence=1.0)]
    return OCRRegionResult(region.region_type, lines, 1.0)
