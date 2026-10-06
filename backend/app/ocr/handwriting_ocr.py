"""
DOCINT — Handwriting OCR Fallback
Uses microsoft/trocr-large-handwritten for regions with low standard OCR confidence.
"""
from __future__ import annotations

from PIL import Image
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

class HandwritingOCREngine:
    """
    Uses TrOCR via HuggingFace transformers. Loaded lazily.
    """
    MODEL_ID = "microsoft/trocr-large-handwritten"
    
    def __init__(self):
        self._is_loaded = False
        self._processor = None
        self._model = None
        
    def load_model(self) -> None:
        if self._is_loaded:
            return
            
        logger.info("loading_handwriting_ocr_model")
        try:
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            
            self._processor = TrOCRProcessor.from_pretrained(self.MODEL_ID)
            self._model = VisionEncoderDecoderModel.from_pretrained(self.MODEL_ID)
            
            if settings.SURYA_DEVICE == "cuda":
                self._model.to("cuda")
                
            self._is_loaded = True
            logger.info("handwriting_ocr_loaded")
        except Exception as e:
            logger.error("handwriting_ocr_load_failed", error=str(e))
            raise RuntimeError(f"Failed to load handwriting model: {e}")

    def process_region(self, region_image: Image.Image) -> tuple[str, float]:
        """
        Processes a single line/region image. Returns (text, confidence).
        Note: TrOCR is optimized for single-line text, so bounding boxes from layout are helpful.
        """
        if not self._is_loaded:
            self.load_model()
            
        try:
            # Ensure RGB
            if region_image.mode != "RGB":
                region_image = region_image.convert("RGB")
                
            pixel_values = self._processor(images=region_image, return_tensors="pt").pixel_values
            
            if settings.SURYA_DEVICE == "cuda":
                pixel_values = pixel_values.to("cuda")
                
            # TrOCR does not naturally return probabilities for the whole sequence easily without 
            # some manipulation. For now, we return generated text and a fallback confidence.
            # A more advanced implementation would compute average token probability.
            generated_ids = self._model.generate(
                pixel_values, 
                max_new_tokens=50,
                output_scores=True,
                return_dict_in_generate=True
            )
            
            generated_text = self._processor.batch_decode(generated_ids.sequences, skip_special_tokens=True)[0]
            
            # Simple confidence calculation based on output scores
            if generated_ids.sequences.shape[1] > 1 and generated_ids.scores:
                import torch
                probs = torch.stack(generated_ids.scores, dim=1).softmax(-1)
                # Max probability per token
                token_probs, _ = probs.max(dim=-1)
                avg_conf = token_probs.mean().item()
            else:
                avg_conf = 0.85 # default fallback
                
            return generated_text.strip(), avg_conf
            
        except Exception as e:
            logger.error("handwriting_region_failed", error=str(e))
            return "", 0.0
