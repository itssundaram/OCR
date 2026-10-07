from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from PIL import Image

@dataclass
class PipelineCapabilities:
    supports_layout_detection: bool = False
    supports_tables: bool = False
    supports_handwriting: bool = False
    supports_vision: bool = False
    requires_gpu: bool = False
    model_name: str = ""

@dataclass
class FieldResult:
    field_name: str
    value: str
    confidence: float
    page_number: int
    bbox: dict | None = None          # {x1,y1,x2,y2}
    crop_image: Image.Image | None = None
    crop_asset_id: str | None = None
    extraction_method: str = ""
    fallback_method: str = ""
    raw_text: str = ""

@dataclass
class TableResult:
    table_index: int
    page_number: int
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    confidence: float = 0.0
    bbox: dict | None = None
    crop_image: Image.Image | None = None
    extraction_method: str = ""

@dataclass
class PipelineResult:
    pipeline_name: str
    status: str                       # 'completed','failed','partial'
    fields: list[FieldResult] = field(default_factory=list)
    tables: list[TableResult] = field(default_factory=list)
    raw_text_per_page: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    processing_time_ms: int = 0
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    step_events: list[dict] = field(default_factory=list)

class OCRPipeline(ABC):
    """Plugin interface — all pipelines implement this."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> PipelineCapabilities: ...

    def preprocess(self, image: Image.Image, config: dict) -> Image.Image:
        return image

    def detect_regions(self, image: Image.Image) -> list[dict]:
        return []

    @abstractmethod
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
        """native_texts: per-page text PyMuPDF already extracted (empty string for a
        scanned page with none) — pipelines that can skip OCR on a native-text page
        (the legacy hybrid pipeline) need this; a vision-only pipeline can ignore it."""
        ...

    @abstractmethod
    def extract_tables(
        self,
        pages: list[Image.Image],
        job_id: str,
    ) -> list[TableResult]: ...

    def run(
        self,
        pages: list[Image.Image],
        template: dict,
        job_id: str,
        event_callback=None,
    ) -> PipelineResult:
        """Default run implementation: extract_fields, then extract_tables, timed
        and with failures of either step turned into a PipelineResult rather than
        an uncaught exception — callers (the orchestrator, Phase 13) only ever
        get a PipelineResult back, never a raised error, from a registered pipeline."""
        import time
        start = time.perf_counter()
        template_fields = template.get("fields", []) if isinstance(template, dict) else []

        step_events = []
        
        def _internal_event_callback(step_name: str, data: dict = None):
            event = {"step_name": step_name, "data": data or {}}
            step_events.append(event)
            if event_callback:
                event_callback(step_name, data)

        _internal_event_callback("PIPELINE_STARTED", {"pipeline": self.name})

        try:
            result = self.extract_fields(
                pages,
                template_fields,
                job_id,
                native_texts=template.get("native_texts") if isinstance(template, dict) else None,
                schema=template.get("schema") if isinstance(template, dict) else None,
                department=template.get("department", "GENERAL") if isinstance(template, dict) else "GENERAL",
                doc_type=template.get("doc_type", "UNKNOWN") if isinstance(template, dict) else "UNKNOWN",
                template_version=template.get("template_version", 1) if isinstance(template, dict) else 1,
                extraction_instructions=template.get("extraction_instructions") if isinstance(template, dict) else None,
                event_callback=_internal_event_callback,
            )
            result.step_events = step_events
        except Exception as exc:
            _internal_event_callback("PIPELINE_FAILED", {"pipeline": self.name, "error": str(exc)})
            return PipelineResult(
                pipeline_name=self.name,
                status="failed",
                error_message=str(exc),
                processing_time_ms=int((time.perf_counter() - start) * 1000),
                step_events=step_events,
            )

        try:
            result.tables = self.extract_tables(pages, job_id)
        except Exception as table_exc:
            result.status = "partial"
            result.metadata.setdefault("warnings", []).append(f"extract_tables failed: {table_exc}")

        result.processing_time_ms = int((time.perf_counter() - start) * 1000)
        _internal_event_callback("PIPELINE_COMPLETED", {"pipeline": self.name, "status": result.status, "overall_confidence": result.overall_confidence})
        return result
