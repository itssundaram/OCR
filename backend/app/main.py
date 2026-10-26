"""
DOCINT — FastAPI Application Factory
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import DocIntError
from app.core.logging import configure_logging, get_logger

# Phase 13: importing app.pipelines triggers every @register_pipeline
# decorator (surya, tesseract, learned_anchor) so the registry is populated before
# any request — the OCR/Orchestration page routes (app/api/routes/extract.py,
# pipelines.py) call get_pipeline()/list_pipelines() and need this to have
# already run. Previously only tests imported this package; it was harmless
# while nothing in the live app called into the registry at runtime.
import app.pipelines  # noqa: F401

configure_logging()
logger = get_logger(__name__)

# HTTP status code mapping for each exception code
_STATUS_MAP: dict[str, int] = {
    "DOC_TYPE_NOT_FOUND":       404,
    "DOCUMENT_NOT_FOUND":       404,
    "JOB_NOT_FOUND":            404,
    "TEMPLATE_NOT_FOUND":       404,
    "PAGE_NOT_FOUND":           404,
    "TABLE_NOT_FOUND":          404,
    "INVALID_CELL":             422,
    "DEPT_NOT_FOUND":           404,
    "DOC_TYPE_INACTIVE":        400,
    "TEMPLATE_NOT_ACTIVE":      400,
    "TEMPLATE_ALREADY_ACTIVE":  409,
    "TEMPLATE_VALIDATION_ERROR":422,
    "FILE_TYPE_INVALID":        400,
    "FILE_TOO_LARGE":           413,
    "FILE_STORAGE_ERROR":       500,
    "DOC_TYPE_ALREADY_EXISTS":  409,
    "DEPT_ALREADY_EXISTS":      409,
    "DATABASE_CONNECTION_ERROR":503,
    "OCR_ENGINE_UNAVAILABLE":   503,
    "AI_ENGINE_UNAVAILABLE":    503,
    "QUEUE_UNAVAILABLE":        503,
    "PREFETCH_GONE":            404,
    "RESULT_NOT_READY":         409,
    "FIELD_ALREADY_IN_TEMPLATE": 409,
    "LABEL_ALREADY_IN_TEMPLATE": 409,
    "INVALID_FIELD_NAME":       422,
    "NOT_A_FOUND_FIELD":        400,
    "PREFETCH_NOT_DISCARDABLE": 409,
    # codes the routes raise that used to fall through to 500
    "CELL_NOT_FOUND":           404,
    "DEPARTMENT_NOT_FOUND":     404,
    "URL_NOT_FOUND":            404,
    "FIELD_NOT_FOUND":          404,
    "FILE_NOT_FOUND":           404,
    "DOCUMENT_FILE_MISSING":    404,
    "EVIDENCE_IMAGE_NOT_FOUND": 404,
    "EVIDENCE_IMAGE_UNREADABLE":422,
    "PIPELINE_NOT_FOUND":       404,
    "INVALID_BBOX":             422,
    "INSUFFICIENT_PIPELINES":   400,
    "ANCHOR_ROLLBACK_UNAVAILABLE": 409,
    "ANCHOR_VERSION_CONFLICT":  409,
    "DB_ADMIN_DISABLED":        403,
    "DB_ADMIN_FORBIDDEN_HOST":  403,
    "DB_ADMIN_NO_CRYPTO":       501,
    "DB_SESSION_NOT_FOUND":     404,
    "DB_PROFILE_NOT_FOUND":     404,
    "DB_OBJECT_NOT_FOUND":      404,
    "DB_CONNECT_FAILED":        400,
    "DB_DRIVER_MISSING":        400,
    "DB_QUERY_FAILED":          400,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info(
        "docint_startup",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
        ai_model=settings.AI_MODEL,
    )
    settings.ensure_storage_directories()
    logger.info("storage_directories_ready")

    from app.db.database import check_oracle_connectivity, engine
    result = check_oracle_connectivity()
    if result["status"] == "ok":
        db_type = "oracle" if engine.dialect.name == "oracle" else "postgresql"
        logger.info(f"{db_type}_connected")
    else:
        logger.warning("database_unavailable", detail=result.get("message"))

    from app.system.runtime import configure_process
    configure_process("api")

    from app.core.startup import preload_models
    preload_models()

    import asyncio
    if settings.FAST_AUTOSTART_WORKER:
        # start the fast worker now (in a thread: it may wait for Redis), so the first upload already finds it
        def _warm_fast_worker():
            try:
                from app.workers.queue import redis_conn
                from app.fast.worker_supervisor import ensure_fast_worker
                ensure_fast_worker(redis_conn, wait_s=0, retire_stale=True)
            except Exception as exc:
                logger.warning("fast_worker_autostart_skipped", error=str(exc)[:160])
        asyncio.get_running_loop().run_in_executor(None, _warm_fast_worker)

    from app.workers.reaper import fast_rescue_loop, reaper_loop
    reaper_task = asyncio.create_task(reaper_loop())
    rescue_task = asyncio.create_task(fast_rescue_loop())

    yield
    reaper_task.cancel()
    rescue_task.cancel()
    logger.info("docint_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="DocInt — Document Intelligence Platform",
        description="Offline-first AI document extraction platform.",
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.APP_ENV != "production" else None,
        redoc_url="/redoc" if settings.APP_ENV != "production" else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DocIntError)
    async def docint_error_handler(request: Request, exc: DocIntError):
        status_code = _STATUS_MAP.get(exc.code, 500)
        logger.warning("api_error", code=exc.code, message=exc.message, status=status_code)
        return JSONResponse(
            status_code=status_code,
            content={
                "success": False,
                "error": {"code": exc.code, "message": exc.message, "details": exc.details or None},
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error("unhandled_exception", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": f"An unexpected error occurred: {str(exc)}", "details": None},
            },
        )

    app.include_router(api_router)
    return app


app = create_app()
