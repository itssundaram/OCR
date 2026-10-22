"""
fast_ocr as a registered pipeline.

The job runner (app/fast/runner.py) is the full path with live events and the
two-phase save. This class is the same engine behind the standard pipeline
interface, so `fast_ocr` also works anywhere a pipeline name is accepted (for
example the comparison page); there it only has rendered images to work with,
so every page is OCR'd (cached by pixel hash) instead of using a text layer.
"""
from __future__ import annotations

import time

from PIL import Image

from app.core.config import settings
from app.pipelines.base import OCRPipeline, PipelineCapabilities, PipelineResult, TableResult
from app.pipelines.registry import register_pipeline


@register_pipeline("fast_ocr")
class FastOCRPipeline(OCRPipeline):
    def __init__(self):
        self._tables: dict[str, list[TableResult]] = {}

    @property
    def name(self) -> str:
        return "fast_ocr"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities(
            supports_layout_detection=True,
            supports_tables=True,
            supports_handwriting=False,
            supports_vision=settings.FAST_VLM_FALLBACK_ENABLED,
            requires_gpu=False,
            model_name="pymupdf+tesseract+opencv",
        )

    def extract_fields(
        self,
        pages: list[Image.Image],
        template_fields: list[dict],
        job_id: str,
        *,
        native_texts: list[str] | None = None,
        event_callback=None,
        **kwargs,
    ) -> PipelineResult:
        # imported here: app.pipelines imports this module while it is being
        # initialised, and the engine imports app.pipelines.base
        from app.fast.engine import FastEngine
        from app.fast.events import EventBus
        from app.fast.ocr_cache import default_cache
        from app.fast.reader import PageReader

        t0 = time.perf_counter()
        bus = EventBus(job_id, coarse_callback=event_callback)
        engine = FastEngine(template_fields, bus, want_tables=True)
        reader = PageReader(None, default_cache())
        bus.emit("job_started", pages_total=len(pages), mode="images")
        engine.run_pages(len(pages), lambda i: reader.from_image(pages[i], i + 1))
        fields = engine.finalize_fields()
        engine.extractor.emit_final_events(fields)
        self._tables[job_id] = engine.collect_tables()
        result = PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=fields,
            raw_text_per_page=[p.text for p in engine.sorted_pages()],
            overall_confidence=engine.overall_confidence(fields),
            processing_time_ms=int((time.perf_counter() - t0) * 1000),
            all_detections=engine.all_detections(),
        )
        result.metadata.update({
            "mode": "fast_ocr_images",
            "speed": engine.timings,
            "blocks": engine.blocks,
            "warnings": engine.warnings,
            "doc_page_width": pages[0].width if pages else None,
            "doc_page_height": pages[0].height if pages else None,
        })
        bus.emit("job_completed", counts={"fields": len(fields), "tables": len(self._tables[job_id])})
        return result

    def extract_tables(self, pages: list[Image.Image], job_id: str) -> list[TableResult]:
        return self._tables.pop(job_id, [])
