"""The fast worker picks its job-timeout mechanism by operating system (Windows has no SIGALRM)."""
import os
import signal
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import run_fast_worker as rfw  # noqa: E402
from rq import SimpleWorker  # noqa: E402
from rq.timeouts import TimerDeathPenalty  # noqa: E402


def _mode(monkeypatch, value):
    monkeypatch.setattr(rfw.settings, "FAST_WORKER_TIMEOUT", value, raising=False)


def test_forced_timer_uses_thread_timer(monkeypatch):
    _mode(monkeypatch, "timer")
    cls = rfw._worker_class()
    assert issubclass(cls, SimpleWorker) and cls.death_penalty_class is TimerDeathPenalty


def test_forced_signal_uses_plain_simple_worker(monkeypatch):
    _mode(monkeypatch, "signal")
    assert rfw._worker_class() is SimpleWorker


def test_auto_follows_the_platform(monkeypatch):
    _mode(monkeypatch, "auto")
    has_alarm = hasattr(signal, "SIGALRM") and os.name != "nt"
    assert (rfw._worker_class() is SimpleWorker) == has_alarm


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="needs a platform that has SIGALRM")
def test_auto_picks_timer_when_sigalrm_is_missing(monkeypatch):
    _mode(monkeypatch, "auto")
    monkeypatch.delattr(signal, "SIGALRM")
    assert rfw._worker_class().death_penalty_class is TimerDeathPenalty
