"""
DOCINT — Pipeline Listing API (Phase 13)

Powers both new frontend pages' pipeline pickers: the OCR page's single-
select dropdown and the Orchestration page's multi-select. Reads directly
from the Phase 4/6 pipeline registry, so a newly registered pipeline shows
up here with no route change needed.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.common import APIResponse
from app.pipelines.registry import list_pipelines, get_pipeline
from app.core.config import settings
from app.workers.queue import redis_conn

router = APIRouter(tags=["Pipelines"])


@router.get("/pipelines")
def list_available_pipelines():
    """List every registered pipeline with its capabilities, for the OCR
    page's pipeline dropdown and the Orchestration page's multi-select."""
    pipelines = []
    for name in list_pipelines():
        pipeline = get_pipeline(name)
        caps = pipeline.capabilities
        
        desc = ""
        stack = []
        if name == "tesseract":
            desc = "Tesseract text extraction with Surya layout detection (reads the whole page as one region when the layout weights are missing), img2table tables and a vision-model fallback."
            stack = ["Surya Layout", "Tesseract", "img2table", "LLM Fallback"]
        elif name == "surya":
            desc = "GPU-accelerated pipeline utilizing Surya's models for document understanding."
            stack = ["Surya Layout", "Surya OCR", "TrOCR", "TATR", "LLM Fallback"]
        elif name == "learned_anchor":
            desc = "Autonomous Geometric Anchor Pipeline. Instant <300ms warm extraction for known templates with adaptive VLM learning fallback."
            stack = ["Geometric Anchors", "Micro-Region OCR", "Qwen 2.5-VL Cold Learning", "Template Memory"]
        elif name == "fast_ocr":
            desc = "Fast layout-first pipeline: PDF text layer first, one cached OCR pass for scans, ruled tables from geometry, live field-by-field results, VLM only to re-read a single field."
            stack = ["PyMuPDF text layer", "Tesseract (once per page)", "OpenCV tables", "Key:Value matching", "VLM single-field repair"]
        pipelines.append({
            "name": pipeline.name,
            "description": desc,
            "stack": stack,
            "supports_layout_detection": caps.supports_layout_detection,
            "supports_tables": caps.supports_tables,
            "supports_handwriting": caps.supports_handwriting,
            "supports_vision": caps.supports_vision,
            "requires_gpu": caps.requires_gpu,
            "model_name": caps.model_name,
        })
    return APIResponse(data={"pipelines": pipelines})

@router.get("/workers/health")
def get_worker_health():
    """Phase 11 (scoped down, per the plan): a lightweight worker/GPU status
    report instead of a full multi-worker fleet registry — this is a single-box
    dev/production deployment today, so there's nothing to schedule across yet.
    Reports whether the job queue (Redis) is reachable and, best-effort,
    whether a CUDA GPU is visible to this process — never hardcodes a device
    name or assumes a specific GPU, per the plan's "no cuda:0, no RTX 5060/L40S
    in application logic" rule."""
    redis_ok = False
    if redis_conn is not None:
        try:
            redis_conn.ping()
            redis_ok = True
        except Exception:
            redis_ok = False

    gpu_info = {"gpu_enabled_setting": settings.GPU_ENABLED, "cuda_available": False, "devices": []}
    try:
        import torch
        gpu_info["cuda_available"] = torch.cuda.is_available()
        if gpu_info["cuda_available"]:
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                gpu_info["devices"].append({
                    "index": i,
                    "name": props.name,
                    "total_memory_mb": int(props.total_memory / (1024 * 1024)),
                })
    except Exception:
        gpu_info["torch_import_error"] = True

    return APIResponse(data={
        "redis_reachable": redis_ok,
        "queue_name": settings.REDIS_QUEUE_NAME,
        "registered_pipelines": list_pipelines(),
        "gpu": gpu_info,
    })
