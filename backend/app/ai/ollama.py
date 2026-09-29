"""
DOCINT — Ollama AI Engine
Implements the AIExtractionEngine using the official Python Ollama client.
"""
from __future__ import annotations

import json
from typing import Any
import httpx
from ollama import Client, ResponseError
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.ai.base import AIExtractionEngine, ExtractionResult
from app.ocr.base import OCRResult

logger = get_logger(__name__)


class OllamaExtractionEngine(AIExtractionEngine):
    def __init__(self) -> None:
        self.base_url = settings.OLLAMA_BASE_URL
        self.model_name = settings.AI_MODEL
        
        # Initialize client
        self.client = Client(host=self.base_url)
        
    @property
    def engine_name(self) -> str:
        return "ollama"
        
    def is_available(self) -> bool:
        """Ping the Ollama server to verify availability."""
        try:
            # A simple HTTP GET to the base URL usually returns 'Ollama is running'
            response = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            return response.status_code == 200
        except Exception:
            return False

    def extract(
        self,
        ocr_result: OCRResult,
        schema: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        images: list[str] | None = None
    ) -> ExtractionResult:
        """
        Executes the extraction using Ollama.
        """
        logger.info("ollama_extraction_start", model=self.model_name)
        
        user_message = {"role": "user", "content": user_prompt}
        if images:
            user_message["images"] = images

        messages = [
            {"role": "system", "content": system_prompt},
            user_message
        ]
        
        # Determine if we should use structured outputs (JSON schema format).
        # Ollama supports a 'format' parameter for JSON schema since recently.
        # However, fallback to native JSON parsing if it doesn't align perfectly.
        # The user requested native JSON where possible.
        options = {
            "temperature": 0.0,
            "num_predict": settings.OLLAMA_NUM_PREDICT,
            "num_ctx": settings.OLLAMA_NUM_CTX,
        }

        max_retries = 3
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                # We explicitly specify format='json' which forces the model to output valid JSON
                response = self.client.chat(
                    model=self.model_name,
                    messages=messages,
                    format="json",
                    options=options
                )
                
                raw_output = response.message.content
                
                # Ollama returns token metrics
                prompt_eval_count = getattr(response, "prompt_eval_count", None)
                eval_count = getattr(response, "eval_count", None)
                total_tokens = None
                if prompt_eval_count and eval_count:
                    total_tokens = prompt_eval_count + eval_count

                # Safely parse the JSON
                try:
                    parsed_data = json.loads(raw_output)
                except json.JSONDecodeError:
                    # In rare cases where format='json' still breaks or includes trailing text
                    # We can attempt a fallback extraction of the JSON block
                    import re
                    json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
                    if json_match:
                        try:
                            parsed_data = json.loads(json_match.group(0))
                        except json.JSONDecodeError as e:
                            logger.error("ollama_json_parse_fallback_failed", attempt=attempt, error=str(e), raw=raw_output)
                            raise ValueError(f"Failed to parse LLM output as JSON: {str(e)}")
                    else:
                        logger.error("ollama_json_parse_failed", attempt=attempt, raw=raw_output)
                        raise ValueError("LLM output did not contain valid JSON.")

                logger.info("ollama_extraction_complete", attempt=attempt, tokens=total_tokens)

                return ExtractionResult(
                    raw_json=raw_output,
                    parsed_data=parsed_data,
                    engine_name=self.engine_name,
                    model_name=self.model_name,
                    total_tokens=total_tokens,
                    prompt_tokens=prompt_eval_count,
                    completion_tokens=eval_count
                )

            except (ValueError, ResponseError) as e:
                last_error = e
                logger.warning("ollama_api_error_retry", attempt=attempt, error=str(e))
                if attempt < max_retries:
                    time.sleep(2 * attempt)
            except Exception as e:
                logger.error("ollama_extraction_error", attempt=attempt, error=str(e))
                raise
                
        # If we exhausted retries
        logger.error("ollama_extraction_failed_after_retries", max_retries=max_retries, error=str(last_error))
        raise RuntimeError(f"Ollama API Error after {max_retries} attempts: {str(last_error)}")
