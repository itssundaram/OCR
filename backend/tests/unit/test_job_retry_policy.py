"""
Phase 10 — Redis/RQ job hardening: the transient-vs-deterministic error
classifier that decides whether a failed job is worth a bounded retry.

Pure-logic tests only: no Redis, no RQ worker, no DB. `_rq_retries_remaining`
itself calls rq.get_current_job(), which is RQ/worker-context-dependent and
therefore exercised only implicitly (outside a worker it always returns
False) — that part isn't unit-tested here for the same reason the project
doesn't mock Oracle: there's nothing meaningful to assert without a real
RQ worker process, and faking one would test the mock, not the code.
"""
from app.services.extractor import _is_transient_error, _rq_retries_remaining


def test_connection_error_is_transient():
    assert _is_transient_error(ConnectionError("db gone")) is True


def test_timeout_error_is_transient():
    assert _is_transient_error(TimeoutError("redis timeout")) is True


def test_os_error_is_transient():
    assert _is_transient_error(OSError("disk hiccup")) is True


def test_value_error_is_not_transient():
    # A bad template / malformed document: retrying changes nothing.
    assert _is_transient_error(ValueError("Template 42 not found.")) is False


def test_exception_named_like_a_driver_connectivity_error_is_transient():
    # Simulates oracledb/sqlalchemy/redis-py exception classes without
    # importing every driver — matched by class name (see
    # _TRANSIENT_EXCEPTION_NAMES in app/services/extractor.py).
    class OperationalError(Exception):
        pass

    assert _is_transient_error(OperationalError("ORA-03135: connection lost")) is True


def test_arbitrary_runtime_error_is_not_transient_by_default():
    assert _is_transient_error(RuntimeError("schema validation failed")) is False


def test_rq_retries_remaining_is_false_outside_a_worker_context():
    # No RQ worker is running in this test process, so get_current_job()
    # returns None — must degrade to False, never raise.
    assert _rq_retries_remaining() is False
