"""
DOCINT — TATR (Table Transformer) Table Extraction Engine
Extracts tables using microsoft/table-transformer models from HuggingFace.
"""
from __future__ import annotations

import io
from PIL import Image

from app.ocr.base import OCRTable
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

class TATRTableEngine:
    def __init__(self):
        self._is_loaded = False
        self._processor = None
        self._structure_model = None
        self._device = None

    def load_model(self):
        if self._is_loaded:
            return
            
        logger.info("loading_tatr_models")
        try:
            from transformers import TableTransformerForObjectDetection, DetrImageProcessor
            import torch
            
            device = "cuda" if settings.GPU_ENABLED and torch.cuda.is_available() else "cpu"
            
            self._processor = DetrImageProcessor.from_pretrained("microsoft/table-transformer-structure-recognition")
            self._structure_model = TableTransformerForObjectDetection.from_pretrained("microsoft/table-transformer-structure-recognition")
            self._structure_model.to(device)
            self._device = device
            
            self._is_loaded = True
            logger.info("tatr_models_loaded")
        except Exception as e:
            logger.error("tatr_load_failed", error=str(e))
            raise RuntimeError(f"Failed to load TATR models: {e}")

    def extract_tables(self, region_image: Image.Image) -> list[OCRTable]:
        if not self._is_loaded:
            self.load_model()
            
        try:
            if region_image.mode != "RGB":
                region_image = region_image.convert("RGB")
                
            encoding = self._processor(region_image, return_tensors="pt")
            encoding = {k: v.to(self._device) for k, v in encoding.items()}
            
            outputs = self._structure_model(**encoding)
            
            target_sizes = [region_image.size[::-1]]
            results = self._processor.post_process_object_detection(outputs, threshold=0.5, target_sizes=target_sizes)[0]
            
            import pytesseract
            
            rows = []
            cols = []
            for score, label, box in zip(results['scores'], results['labels'], results['boxes']):
                label_name = self._structure_model.config.id2label[label.item()]
                box = [round(i, 2) for i in box.tolist()]
                if label_name == 'table row':
                    rows.append(box)
                elif label_name == 'table column':
                    cols.append(box)
                    
            if not rows or not cols:
                from app.ocr.img2table_engine import extract_tables_from_image
                buf = io.BytesIO()
                region_image.save(buf, format="PNG")
                return extract_tables_from_image(buf.getvalue())
                
            rows = sorted(rows, key=lambda x: x[1])
            cols = sorted(cols, key=lambda x: x[0])
            
            table_data = []
            for r in rows:
                row_data = []
                for c in cols:
                    x1 = max(r[0], c[0])
                    y1 = max(r[1], c[1])
                    x2 = min(r[2], c[2])
                    y2 = min(r[3], c[3])
                    
                    if x2 > x1 and y2 > y1:
                        cell_crop = region_image.crop((x1, y1, x2, y2))
                        # Tesseract CLI directly if needed, or via pytesseract (easier for simple cells)
                        cell_text = pytesseract.image_to_string(cell_crop, config='--psm 6').strip()
                        row_data.append(cell_text)
                    else:
                        row_data.append("")
                table_data.append(row_data)
                
            if not table_data:
                return []
                
            headers = table_data[0] if table_data else []
            body = table_data[1:] if len(table_data) > 1 else []
            
            sep = " | ".join(["---"] * len(headers))
            markdown = f"| {' | '.join(headers)} |\n| {sep} |\n"
            for row in body:
                safe = [str(col).replace("|", "\\|").replace("\n", " ") for col in row]
                markdown += f"| {' | '.join(safe)} |\n"
                
            return [OCRTable(headers=headers, rows=body, markdown=markdown)]
            
        except Exception as e:
            logger.error("tatr_extract_failed", error=str(e))
            try:
                from app.ocr.img2table_engine import extract_tables_from_image
                buf = io.BytesIO()
                region_image.save(buf, format="PNG")
                return extract_tables_from_image(buf.getvalue())
            except Exception as inner_e:
                return []
