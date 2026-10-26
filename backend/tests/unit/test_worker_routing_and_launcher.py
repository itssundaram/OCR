"""One worker per pipeline: routing to the pipeline's queue, fallback to the general queue, the launcher's plan,
and the OS-dependent worker class."""
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
redis = pytest.importorskip("redis")
needs_redis = pytest.mark.skipif(not shutil.which("redis-server"), reason="needs redis-server")
PORT = 6393


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
def _clean(request):
    if "conn" in request.fixturenames:
        request.getfixturevalue("conn").flushall()


# ── queue names and the choice between a pipeline's own queue and the general one ──────────────────────────────

def test_every_pipeline_has_its_own_queue_and_fast_keeps_its_old_name():
    from app.core.config import settings
    from app.workers import routing
    names = {p: routing.queue_name(p) for p in routing.KNOWN_PIPELINES}
    assert names["fast_ocr"] == settings.FAST_QUEUE_NAME
    assert len(set(names.values())) == len(names)
    assert routing.general_queue() not in names.values()


@needs_redis
def test_a_job_goes_to_its_pipelines_queue_only_while_that_worker_is_alive(conn):
    from app.workers import routing
    assert routing.pick_queue(conn, "surya") == routing.general_queue()
    routing.publish_alive(conn, [routing.queue_name("surya")])
    assert routing.pick_queue(conn, "surya") == routing.queue_name("surya")
    assert routing.pick_queue(conn, "tesseract") == routing.general_queue()      # another pipeline is unaffected
    assert routing.pick_queue(conn, "something_else") == routing.general_queue() # unknown -> general
    assert routing.pick_queue(None, "surya") == routing.general_queue()          # no Redis -> general


@needs_redis
def test_a_dead_workers_mark_expires_and_only_the_owner_can_clear_it(conn):
    from app.workers import routing
    q = routing.queue_name("tesseract")
    routing.publish_alive(conn, [q])
    assert routing.queue_alive(conn, q)
    conn.set(routing.alive_key(q), "99999|other", ex=30)                         # another process's mark
    routing.clear_alive(conn, [q])
    assert routing.queue_alive(conn, q)                                          # not ours: left alone
    conn.pexpire(routing.alive_key(q), 60)
    time.sleep(0.15)
    assert not routing.queue_alive(conn, q)


@needs_redis
def test_upload_dispatch_uses_the_pipeline_queue_then_falls_back(conn, monkeypatch):
    from rq import Queue
    import app.workers.queue as wq
    from app.api.routes import extract
    from app.core.config import settings
    from app.workers import routing
    general = Queue(settings.REDIS_QUEUE_NAME, connection=conn)
    monkeypatch.setattr(wq, "redis_conn", conn)
    monkeypatch.setattr(wq, "q", general)

    class _BG:
        def add_task(self, *a, **k): raise AssertionError("must not fall back to a background task")

    task = "app.workers.tasks.run_single_pipeline_job"
    routing.publish_alive(conn, [routing.queue_name("surya")])
    assert extract._dispatch_job(task, "job-a", ("surya", False), _BG(), None) == "redis"
    assert Queue(routing.queue_name("surya"), connection=conn).job_ids == ["job-a"] and general.job_ids == []

    assert extract._dispatch_job(task, "job-b", ("tesseract", False), _BG(), None) == "redis"   # no tesseract worker
    assert general.job_ids == ["job-b"]


@needs_redis
def test_waiting_jobs_of_a_dead_pipeline_move_to_the_general_queue(conn, monkeypatch):
    from rq import Queue
    import app.workers.queue as wq
    import app.workers.reaper as reaper
    from app.workers import routing
    monkeypatch.setattr(wq, "redis_conn", conn)
    monkeypatch.setattr(reaper, "RESCUE_AFTER_S", 0)
    for pipe, jid in (("surya", "s1"), ("learned_anchor", "l1")):
        Queue(routing.queue_name(pipe), connection=conn).enqueue("os.getpid", job_id=jid)
    routing.publish_alive(conn, [routing.queue_name("surya")])                   # surya's worker is alive, the other's is not
    assert reaper.rescue_queued_fast_jobs() == 1
    assert Queue(routing.general_queue(), connection=conn).job_ids == ["l1"]
    assert Queue(routing.queue_name("surya"), connection=conn).job_ids == ["s1"]


# ── the launcher's plan ────────────────────────────────────────────────────────────────────────────────────────

def test_launcher_plan_lists_installed_pipelines_and_always_a_general_worker():
    from app.workers.launcher import plan
    names, _ = plan("auto")
    assert names[-1] == "general" and "fast_ocr" in names and "tesseract" in names


def test_launcher_plan_honours_a_list_skips_unknowns_and_missing_packages(monkeypatch):
    import app.workers.launcher as la
    names, skipped = la.plan("fast_ocr,nonsense,fast_docling")
    assert names[0] == "fast_ocr" and names[-1] == "general"
    reasons = dict(skipped)
    assert reasons["nonsense"] == "unknown pipeline"
    if "fast_docling" in reasons:
        assert "not installed" in reasons["fast_docling"]


def test_general_worker_can_be_turned_off_only_when_something_else_runs(monkeypatch):
    import app.workers.launcher as la
    monkeypatch.setattr(la.settings, "WORKER_GENERAL", False)
    assert "general" not in la.plan("fast_ocr")[0]
    assert la.plan("nonsense")[0] == ["general"]                                 # never an empty launcher


# ── the worker class follows the operating system ──────────────────────────────────────────────────────────────

def test_worker_class_follows_the_platform(monkeypatch):
    from rq import SimpleWorker
    from rq.timeouts import TimerDeathPenalty
    import app.workers.worker_classes as wc
    monkeypatch.setattr(wc.settings, "FAST_WORKER_TIMEOUT", "auto")
    monkeypatch.setattr(wc.settings, "WORKER_MODE", "auto")
    monkeypatch.setattr(wc.os, "name", "nt")
    assert wc.worker_class().death_penalty_class is TimerDeathPenalty
    monkeypatch.setattr(wc.os, "name", "posix")
    if hasattr(signal, "SIGALRM"):
        assert wc.worker_class() is SimpleWorker
    monkeypatch.setattr(wc.settings, "FAST_WORKER_TIMEOUT", "timer")
    assert wc.worker_class().death_penalty_class is TimerDeathPenalty


# ── real processes: the launcher starts a worker per pipeline, keeps them alive, stops them ───────────────────

@needs_redis
@pytest.mark.skipif(os.environ.get("DOCINT_E2E") != "1", reason="slow end-to-end test; set DOCINT_E2E=1")
def test_launcher_end_to_end(tmp_path):
    from rq import Queue
    from rq.job import Job
    from app.workers import routing
    port = 6394
    srv = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c = redis.Redis(port=port)
    for _ in range(50):
        try:
            c.ping(); break
        except Exception:
            time.sleep(0.1)
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, REDIS_URL=f"redis://localhost:{port}/0", DATABASE_URL=f"sqlite:///{tmp_path}/t.db",
               STORAGE_DIR=str(tmp_path / "docs"), TESSERACT_CMD=shutil.which("tesseract") or "tesseract")
    launcher = subprocess.Popen([sys.executable, "run_worker.py", "--only", "fast_ocr,tesseract"], cwd=backend, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        keys = {n: routing.alive_key(routing.queue_name(n)) for n in ("fast_ocr", "tesseract")}
        deadline = time.time() + 120
        while time.time() < deadline and not all(c.exists(k) for k in keys.values()):
            time.sleep(1)
        assert all(c.exists(k) for k in keys.values()), "workers did not come up"
        assert c.exists(routing.LAUNCHER_KEY)

        # a job on tesseract's queue runs in tesseract's own process (not the launcher, not the fast worker)
        job = Queue(routing.queue_name("tesseract"), connection=c).enqueue("os.getpid", job_id="e2e-1")
        for _ in range(60):
            if Job.fetch("e2e-1", connection=c).is_finished:
                break
            time.sleep(0.5)
        pid = Job.fetch("e2e-1", connection=c).return_value()
        assert pid == int(c.get(keys["tesseract"]).decode().split("|")[0])
        assert pid != int(c.get(keys["fast_ocr"]).decode().split("|")[0]) and pid != launcher.pid

        # kill -9 the tesseract worker: the launcher notices and starts a new one
        os.kill(pid, signal.SIGKILL)
        c.delete(keys["tesseract"])
        deadline = time.time() + 90
        new = None
        while time.time() < deadline:
            v = c.get(keys["tesseract"])
            if v and int(v.decode().split("|")[0]) != pid:
                new = int(v.decode().split("|")[0]); break
            time.sleep(1)
        assert new, "the crashed worker was not restarted"

        # a second launcher refuses to start (one at a time)
        second = subprocess.run([sys.executable, "run_worker.py", "--only", "tesseract"], cwd=backend,
                                env=dict(env, DOCINT_LAUNCHER_WAIT_S="2"), capture_output=True, text=True, timeout=90)
        assert second.returncode == 3 and "already running" in second.stdout
    finally:
        launcher.send_signal(signal.SIGINT)
        try:
            launcher.wait(timeout=30)
        except Exception:
            launcher.kill()
        time.sleep(1)
        left = [k for k in keys.values() if c.exists(k)]
        srv.terminate()
        assert not left, "alive marks left behind after Ctrl+C"


# ── stopping: a plain kill of the launcher must not leave workers running ─────────────────────────────────

def test_launcher_turns_sigterm_into_a_normal_stop():
    from app.workers import launcher
    old = signal.getsignal(signal.SIGTERM)
    try:
        launcher._stop_on_signals()
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, old)


@pytest.mark.skipif(os.name == "nt", reason="uses kill -9 and re-parenting")
def test_a_worker_stops_itself_when_its_launcher_is_killed_hard(tmp_path):
    from app.workers import launcher
    assert launcher.parent_alive(os.getppid())
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    child_code = ("from app.workers.launcher import watch_launcher; import time; watch_launcher(0.2); time.sleep(60)")
    parent_code = (
        "import os, subprocess, sys, time\n"
        f"env = dict(os.environ, DOCINT_PARENT_PID=str(os.getpid()))\n"
        f"c = subprocess.Popen([sys.executable, '-c', {child_code!r}], cwd={backend!r}, env=env)\n"
        "print(c.pid, flush=True); time.sleep(60)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True)
    try:
        child_pid = int(parent.stdout.readline())
        time.sleep(1.0)
        os.kill(child_pid, 0)                                   # running while its launcher lives
        parent.kill()
        parent.wait()
        deadline = time.time() + 15
        gone = False
        while time.time() < deadline and not gone:
            try:
                os.kill(child_pid, 0)
                try:                                            # a zombie still answers kill(0); read its state
                    gone = open(f"/proc/{child_pid}/stat").read().split(")")[-1].split()[0] == "Z"
                except OSError:
                    gone = True
            except OSError:
                gone = True
            time.sleep(0.2)
        assert gone, "the worker kept running after its launcher died"
    finally:
        parent.kill()
