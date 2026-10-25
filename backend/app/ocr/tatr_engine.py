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
            
            # NOTE: do not pass `timeout=` here. transformers forwards unknown
            # kwargs to the model constructor, which fails with
            # "TableTransformerForObjectDetection.__init__() got an unexpected
            # keyword argument 'timeout'" and silently drops every table onto
            # the weaker img2table fallback. Weights load from the local cache
            # (HF_HUB_OFFLINE=1 is set in app/core/config.py), so no network
            # timeout applies anyway.
            # use_pretrained_backbone=False: the checkpoint already contains the backbone weights. Left at its
            # default (True) timm first fetches resnet50 weights from the hub, which fails offline and made the
            # whole model fall back to img2table.
            self._processor = DetrImageProcessor.from_pretrained(
                "microsoft/table-transformer-structure-recognition",
            )
            self._structure_model = TableTransformerForObjectDetection.from_pretrained(
                "microsoft/table-transformer-structure-recognition",
                use_pretrained_backbone=False,
            )
            self._structure_model.to(device)
            self._device = device
            
            self._is_loaded = True
            logger.info("tatr_models_loaded")
        except Exception as e:
            logger.warning("tatr_load_failed_using_img2table_fallback", error=str(e))
            self._is_loaded = True  # Avoid retrying continuously
            self._structure_model = None

    def extract_tables(self, region_image: Image.Image, table_bbox: dict | None = None) -> list[OCRTable]:
        if not self._is_loaded:
            self.load_model()

        if not self._structure_model:
            from app.ocr.img2table_engine import extract_tables_from_image
            buf = io.BytesIO()
            region_image.save(buf, format="PNG")
            return extract_tables_from_image(buf.getvalue())

        try:
            if region_image.mode != "RGB":
                region_image = region_image.convert("RGB")

            encoding = self._processor(region_image, return_tensors="pt")
            encoding = {k: v.to(self._device) for k, v in encoding.items()}

            outputs = self._structure_model(**encoding)

            target_sizes = [region_image.size[::-1]]
            results = self._processor.post_process_object_detection(outputs, threshold=0.5, target_sizes=target_sizes)[0]

            import pytesseract
            from app.ocr.preprocessing import preprocess_for_ocr
            if settings.TESSERACT_CMD:
                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

            rows = []
            cols = []
            header_boxes = []

            for score, label, box in zip(results['scores'], results['labels'], results['boxes']):
                label_name = self._structure_model.config.id2label[label.item()]
                box_coords = [round(i, 2) for i in box.tolist()]
                if label_name == 'table row':
                    rows.append(box_coords)
                elif label_name == 'table column':
                    cols.append(box_coords)
                elif 'header' in label_name.lower():
                    header_boxes.append(box_coords)

            if not rows or not cols:
                from app.ocr.img2table_engine import extract_tables_from_image
                buf = io.BytesIO()
                region_image.save(buf, format="PNG")
                return extract_tables_from_image(buf.getvalue())

            rows = sorted(rows, key=lambda x: x[1])
            cols = sorted(cols, key=lambda x: x[0])

            computed_table_bbox = table_bbox or {
                "x1": min(c[0] for c in cols),
                "y1": min(r[1] for r in rows),
                "x2": max(c[2] for c in cols),
                "y2": max(r[3] for r in rows),
            }

            # Fast single-pass OCR on table region image, then map words to cells
            table_words = []
            try:
                prep_table = preprocess_for_ocr(region_image, deskew=False, binarize=True)
                t_data = pytesseract.image_to_data(prep_table, output_type=pytesseract.Output.DICT, config='--psm 6')
                for idx in range(len(t_data.get('text', []))):
                    w_txt = t_data['text'][idx].strip()
                    if w_txt:
                        table_words.append({
                            "text": w_txt,
                            "x1": t_data['left'][idx],
                            "y1": t_data['top'][idx],
                            "x2": t_data['left'][idx] + t_data['width'][idx],
                            "y2": t_data['top'][idx] + t_data['height'][idx],
                            "conf": float(t_data['conf'][idx]) if float(t_data['conf'][idx]) > 0 else 0.0,
                        })
            except Exception as e:
                logger.warning("tatr_table_words_ocr_failed", error=str(e))

            table_data = []
            all_cells = []

            for r_idx, r in enumerate(rows):
                row_data = []
                r_mid_y = (r[1] + r[3]) / 2.0
                is_header_row = (r_idx == 0) or any(hb[1] <= r_mid_y <= hb[3] for hb in header_boxes)

                for c_idx, c in enumerate(cols):
                    x1 = max(r[0], c[0])
                    y1 = max(r[1], c[1])
                    x2 = min(r[2], c[2])
                    y2 = min(r[3], c[3])

                    cell_words = [
                        w for w in table_words
                        if w["x1"] >= x1 - 5 and w["x2"] <= x2 + 5
                        and w["y1"] >= y1 - 5 and w["y2"] <= y2 + 5
                    ]
                    cell_words.sort(key=lambda w: (w["y1"] // 10, w["x1"]))
                    cell_text = " ".join(w["text"] for w in cell_words)
                    confs = [w["conf"] for w in cell_words if w["conf"] > 0]
                    cell_conf = round(sum(confs) / len(confs) / 100.0, 3) if confs else (0.85 if cell_text else 0.0)

                    row_data.append(cell_text)
                    all_cells.append({
                        "row": r_idx,
                        "col": c_idx,
                        "text": cell_text,
                        "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                        "is_header": bool(is_header_row),
                        "confidence": cell_conf,
                    })

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

            return [OCRTable(
                headers=headers,
                rows=body,
                markdown=markdown,
                bbox=computed_table_bbox,
                cells=all_cells,
            )]

        except Exception as e:
            logger.error("tatr_extract_failed", error=str(e))
            try:
                from app.ocr.img2table_engine import extract_tables_from_image
                buf = io.BytesIO()
                region_image.save(buf, format="PNG")
                return extract_tables_from_image(buf.getvalue())
            except Exception:
                return []

