"""
DOCINT - live job events (SSE) for the fast_ocr pipeline.

  GET  /api/v1/stream/jobs/{job_id}          text/event-stream
  POST /api/v1/stream/jobs/{job_id}/cancel   ask the worker to stop

The stream is read from the job's Redis Stream, so any API process can serve
any job and a browser can reconnect with Last-Event-ID (= the event's seq).
If the missing part of the stream is gone (trimmed / expired) the server sends
`resync_required` and the client reloads the job result from the normal status
endpoint. Closing the browser never affects the job.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import ProcessingJob
from app.fast.events import SCHEMA_VERSION, TERMINAL_TYPES, cancel_key, stream_key

logger = get_logger(__name__)
router = APIRouter(prefix="/stream", tags=["Live"])

_TERMINAL_STATUS = {"COMPLETED", "WARNING", "FAILED", "CANCELLED"}
_aredis = None


def _async_redis():
    global _aredis
    if _aredis is None:
        try:
            import redis.asyncio as aioredis
            _aredis = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
        except Exception as exc:
            logger.warning("sse_redis_unavailable", error=str(exc))
            return None
    return _aredis


def _job_status(job_id: str) -> str | None:
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        return job.status if job else None


def _frame(seq: int | None, type_: str, payload: dict) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {type_}\ndata: {json.dumps(payload, separators=(',', ':'), default=str)}\n\n"


def _resync(job_id: str, reason: str) -> str:
    return _frame(None, "resync_required", {
        "v": SCHEMA_VERSION, "job_id": job_id, "type": "resync_required",
        "data": {"reason": reason, "snapshot": "use the job status / result endpoint"},
    })


@router.get("/jobs/{job_id}")
async def stream_job(
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int | None = Query(default=None, description="Resume after this seq (same as Last-Event-ID)"),
):
    try:
        cursor_seq = int(last_event_id) if last_event_id not in (None, "") else int(after or 0)
    except ValueError:
        cursor_seq = 0
    status = await run_in_threadpool(_job_status, job_id)
    if status is None:
        return JSONResponse(status_code=404, content={
            "success": False, "error": {"code": "JOB_NOT_FOUND", "message": f"Job {job_id} not found.", "details": None}})

    key = stream_key(job_id)
    heartbeat_ms = max(1, settings.FAST_SSE_HEARTBEAT_S) * 1000

    async def gen():
        r = _async_redis()
        if r is None:
            yield _resync(job_id, "event_stream_unavailable")
            return
        try:
            first = await r.xrange(key, count=1)
            if first:
                first_seq = int(first[0][0].decode().split("-")[0])
                if cursor_seq + 1 < first_seq:
                    yield _resync(job_id, "events_trimmed")
                    return
            elif status in _TERMINAL_STATUS:
                yield _resync(job_id, "stream_expired")
                return

            cursor, quiet = f"{cursor_seq}-0", 0
            while True:
                if await request.is_disconnected():
                    return
                res = await r.xread({key: cursor}, count=100, block=heartbeat_ms)
                if not res:
                    yield ": keep-alive\n\n"
                    quiet += 1
                    if quiet % 4 == 0:      # about once a minute: has the job ended without an event?
                        st = await run_in_threadpool(_job_status, job_id)
                        if st in _TERMINAL_STATUS:
                            yield _resync(job_id, "job_finished")
                            return
                    continue
                quiet = 0
                for _, entries in res:
                    for sid, fields in entries:
                        cursor = sid
                        env = json.loads(fields[b"e"])
                        yield _frame(env.get("seq"), env.get("type", "message"), env)
                        if env.get("type") in TERMINAL_TYPES:
                            return
        except Exception as exc:
            logger.warning("sse_stream_error", job_id=job_id, error=str(exc))
            yield _resync(job_id, "stream_error")

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no",
    })


@router.post("/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str):
    """Ask the worker to stop this job. It stops between steps (a call already
    running in the GPU/VLM finishes first)."""
    status = await run_in_threadpool(_job_status, job_id)
    if status is None:
        return JSONResponse(status_code=404, content={
            "success": False, "error": {"code": "JOB_NOT_FOUND", "message": f"Job {job_id} not found.", "details": None}})
    if status in _TERMINAL_STATUS:
        return {"success": True, "data": {"job_id": job_id, "requested": False, "reason": f"job already {status}"}}
    r = _async_redis()
    if r is None:
        return JSONResponse(status_code=503, content={
            "success": False, "error": {"code": "QUEUE_UNAVAILABLE", "message": "Cannot reach Redis.", "details": None}})
    await r.set(cancel_key(job_id), "1", ex=3600)
    return {"success": True, "data": {"job_id": job_id, "requested": True}}
