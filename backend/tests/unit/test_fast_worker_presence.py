"""A dead fast worker stops counting within seconds (all operating systems), is replaced cleanly, and its queued
uploads are handed to the default worker instead of sitting at QUEUED."""
import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

redis = pytest.importorskip("redis")
pytestmark = pytest.mark.skipif(not shutil.which("redis-server"), reason="needs redis-server")

PORT = 6392


@pytest.fixture(scope="module")
def conn():
    proc = subprocess.Popen(["redis-server", "--port", str(PORT), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c = redis.Redis(port=PORT)
    for _ in range(50):
        try:
            c.ping()
            break
        except Exception:
            time.sleep(0.1)
    yield c
    proc.terminate()


@pytest.fixture(autouse=True)
def _clean(conn):
    conn.flushall()


def test_dead_worker_stops_counting_after_its_mark_expires(conn):
    import app.fast.worker_supervisor as ws
    assert ws.worker_listening(conn) is False
    ws.publish_alive(conn)
    assert ws.worker_listening(conn) is True
    conn.pexpire(ws.ALIVE_KEY, 80)
    time.sleep(0.2)
    assert ws.worker_listening(conn) is False


def test_a_ghost_in_rqs_registry_does_not_count(conn):
    """The Windows failure: a killed worker stays registered with RQ for minutes."""
    import app.fast.worker_supervisor as ws
    from rq import Queue, Worker
    from app.core.config import settings
    q = Queue(settings.FAST_QUEUE_NAME, connection=conn)
    ghost = Worker([q], connection=conn)
    ghost.register_birth()
    assert Worker.count(connection=conn, queue=q) == 1      # RQ thinks a worker is there
    assert ws.worker_listening(conn) is False                # we do not


def test_retire_asks_the_old_build_to_leave_and_lets_a_new_one_start(conn):
    import app.fast.worker_supervisor as ws
    from app.core.build_info import build_id
    ws.publish_alive(conn)
    conn.set(ws.BUILD_KEY, "oldbuild")
    assert ws._retire_stale_workers(conn) is True
    assert conn.get(ws.STOP_KEY).decode() == build_id()
    assert ws.worker_listening(conn) is False                # a replacement may start now
    assert ws.should_retire(conn, "oldbuild") is True        # the old one leaves when idle...
    assert ws.should_retire(conn, build_id()) is False       # ...the replacement stays


def test_presence_thread_keeps_the_mark_fresh_and_a_stale_build_exits_when_idle(conn, monkeypatch):
    import app.fast.worker_supervisor as ws
    import run_fast_worker as rfw

    class FakeWorker:
        state = "idle"
        def get_state(self): return self.state
        def register_death(self): pass

    exits = []
    monkeypatch.setattr(rfw.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(rfw, "PRESENCE_EVERY_S", 0.05)
    w = FakeWorker()
    stop = rfw._start_presence(w, conn)
    assert ws.worker_listening(conn) is True
    conn.set(ws.STOP_KEY, "some-other-build")
    time.sleep(0.4)
    stop.set()
    assert exits == [0] or exits[0] == 0                     # idle + asked to leave -> leaves
    assert ws.worker_listening(conn) is False                # and removes its mark


def test_a_busy_worker_finishes_its_job_before_leaving(conn, monkeypatch):
    import app.fast.worker_supervisor as ws
    import run_fast_worker as rfw

    class Busy:
        def get_state(self): return "busy"
        def register_death(self): pass

    exits = []
    monkeypatch.setattr(rfw.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(rfw, "PRESENCE_EVERY_S", 0.05)
    conn.set(ws.STOP_KEY, "some-other-build")
    stop = rfw._start_presence(Busy(), conn)
    time.sleep(0.3)
    stop.set()
    assert exits == []


def _enqueue_fast(conn, job_id="job-1"):
    from rq import Queue
    from app.core.config import settings
    Queue(settings.FAST_QUEUE_NAME, connection=conn).enqueue(
        "app.fast.runner.run_fast_job", job_id, False, job_id=job_id, job_timeout=600)


def test_orphaned_fast_job_moves_to_the_default_queue(conn, monkeypatch):
    import app.workers.queue as q
    import app.workers.reaper as reaper
    from rq import Queue
    from app.core.config import settings
    monkeypatch.setattr(q, "redis_conn", conn)
    monkeypatch.setattr(reaper, "RESCUE_AFTER_S", 0)
    _enqueue_fast(conn)
    assert reaper.rescue_queued_fast_jobs() == 1
    fast = Queue(settings.FAST_QUEUE_NAME, connection=conn)
    default = Queue(settings.REDIS_QUEUE_NAME, connection=conn)
    assert fast.job_ids == [] and default.job_ids == ["job-1"]
    moved = default.fetch_job("job-1")
    assert moved.func_name == "app.fast.runner.run_fast_job" and tuple(moved.args) == ("job-1", False)


def test_nothing_is_moved_while_a_fast_worker_is_alive_or_the_job_is_young(conn, monkeypatch):
    import app.fast.worker_supervisor as ws
    import app.workers.queue as q
    import app.workers.reaper as reaper
    monkeypatch.setattr(q, "redis_conn", conn)
    _enqueue_fast(conn)
    monkeypatch.setattr(reaper, "RESCUE_AFTER_S", 0)
    ws.publish_alive(conn)
    assert reaper.rescue_queued_fast_jobs() == 0             # a worker is alive: leave it queued
    conn.delete(ws.ALIVE_KEY)
    monkeypatch.setattr(reaper, "RESCUE_AFTER_S", 3600)
    assert reaper.rescue_queued_fast_jobs() == 0             # nobody alive but only just queued: give a start-up time
