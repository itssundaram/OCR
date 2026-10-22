"""SSE stream over a Redis Stream (fakeredis, no real Redis/Oracle needed)."""
import asyncio
import json

import pytest

fakeredis = pytest.importorskip("fakeredis")

from app.api.routes import stream as stream_mod  # noqa: E402
from app.fast.events import EventBus, cancel_key  # noqa: E402


class _Req:
    async def is_disconnected(self):
        return False


def _setup(monkeypatch, status="RUNNING"):
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server)
    aio = fakeredis.FakeAsyncRedis(server=server)
    monkeypatch.setattr(stream_mod, "_aredis", aio)
    monkeypatch.setattr(stream_mod, "_job_status", lambda job_id: status)
    return sync


def _collect(job_id, **kw):
    async def run():
        resp = await stream_mod.stream_job(job_id, _Req(), **{"last_event_id": None, "after": None, **kw})
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk if isinstance(chunk, str) else chunk.decode())
        return out
    return asyncio.run(asyncio.wait_for(run(), 10))


def _types(frames):
    return [f.split("event: ")[1].split("\n")[0] for f in frames if "event: " in f]


def test_replay_in_order_and_closes_on_terminal(monkeypatch):
    sync = _setup(monkeypatch)
    bus = EventBus("j1", redis_conn=sync)
    bus.emit("job_started")
    bus.emit("page_completed", page=1)
    bus.emit("job_completed")
    frames = _collect("j1")
    assert _types(frames) == ["job_started", "page_completed", "job_completed"]
    assert frames[0].startswith("id: 1\n") and frames[2].startswith("id: 3\n")
    payload = json.loads(frames[1].split("data: ")[1])
    assert payload["v"] == 1 and payload["seq"] == 2 and payload["page"] == 1


def test_last_event_id_resumes(monkeypatch):
    sync = _setup(monkeypatch)
    bus = EventBus("j2", redis_conn=sync)
    for t in ("job_started", "page_started", "page_completed", "job_completed"):
        bus.emit(t)
    frames = _collect("j2", last_event_id="2")
    assert _types(frames) == ["page_completed", "job_completed"]


def test_trimmed_stream_requests_resync(monkeypatch):
    sync = _setup(monkeypatch)
    bus = EventBus("j3", redis_conn=sync)
    for t in ("job_started", "page_started", "page_completed", "job_completed"):
        bus.emit(t)
    sync.xtrim("docint:job:j3:events", maxlen=2, approximate=False)       # events 1-2 are gone
    frames = _collect("j3", last_event_id="1")
    assert _types(frames) == ["resync_required"]


def test_expired_stream_of_finished_job_requests_resync(monkeypatch):
    _setup(monkeypatch, status="COMPLETED")
    frames = _collect("gone")
    assert _types(frames) == ["resync_required"]
    assert "stream_expired" in frames[0]


def test_unknown_job_is_404(monkeypatch):
    _setup(monkeypatch, status=None)

    async def run():
        return await stream_mod.stream_job("nope", _Req(), last_event_id=None, after=None)
    resp = asyncio.run(run())
    assert resp.status_code == 404


def test_cancel_sets_key_and_bus_sees_it(monkeypatch):
    sync = _setup(monkeypatch)
    resp = asyncio.run(stream_mod.cancel_job("j4"))
    assert resp["data"]["requested"] is True
    assert sync.exists(cancel_key("j4"))
    assert EventBus("j4", redis_conn=sync).cancel_requested() is True


def test_cancel_on_finished_job_is_noop(monkeypatch):
    _setup(monkeypatch, status="COMPLETED")
    resp = asyncio.run(stream_mod.cancel_job("j5"))
    assert resp["data"]["requested"] is False
