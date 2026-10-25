"""Scan-noise "tables" are dropped; a fast worker is started when none is listening; results carry their build."""
import re

import pytest

from app.fast.tableschema import looks_like_noise_table
from app.pipelines.base import TableResult


def _t(headers, rows, method="grid_lines"):
    return TableResult(table_index=1, page_number=1, headers=headers, rows=rows, extraction_method=method)


def test_a_label_value_fragment_is_not_a_table():
    junk = _t(["Grade Box Gear rs (s) Grade... Rear Grade. (s) Front", "Box Gradey,.."], [["} } Tie In | . 'i. 1 \"ec eae I!", "f | h | 4 3) yj Ti ;"]])
    assert looks_like_noise_table(junk)


def test_a_header_and_one_row_in_a_few_columns_needs_a_clear_header():
    assert looks_like_noise_table(_t(["BA", "vet", "Column 3", "HT FE"], [["1", "2", "3", "4"]]))
    assert not looks_like_noise_table(_t(["S No", "Nomenclature", "Qty", "Remarks"], [["1", "Spanner", "2", ""]]))


def test_a_single_row_across_many_columns_stays():
    cols = ["Ser No", "Veh BA No", "Engine No", "Chassis No", "Make", "Mileage"]
    assert not looks_like_noise_table(_t(cols, [["1", "10D-1", "E1", "C1", "Tata", "1200"]]))


def test_a_wide_scanned_table_full_of_rule_specks_stays():
    headers = [f"Column {i}" for i in range(1, 14)]
    rows = [["|", "1", "", "2", "_", "3", "|", "", "4", "", "5", "", "!"] for _ in range(20)]
    assert not looks_like_noise_table(_t(headers, rows))


def test_a_narrow_grid_of_specks_is_noise():
    rows = [["| .", "'i. ,", "~"], ["f |", "h |", "; ."], ["} }", "I!", "= ."], ["| |", ". ,", "- ~"]]
    assert looks_like_noise_table(_t(["a", "|", "."], rows))


def test_a_table_found_only_by_text_alignment_needs_a_body():
    assert looks_like_noise_table(_t(["Name", "Unit", "Qty", "Total"], [["x", "1", "2", "3"], ["y", "4", "5", "6"]], method="grid_text"))
    body = [[f"item {i}", "1", "2", "3"] for i in range(4)]
    assert not looks_like_noise_table(_t(["Name", "Unit", "Qty", "Total"], body, method="grid_text"))


def test_build_id_is_a_short_stable_hash():
    from app.core.build_info import build_id
    assert re.fullmatch(r"[0-9a-f]{8}", build_id()) and build_id() == build_id()


class _FakeConn:
    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture()
def sup(monkeypatch):
    import app.fast.worker_supervisor as ws
    monkeypatch.setattr(ws, "_child", None)
    return ws


def test_a_fast_worker_is_started_once_when_none_is_listening(sup, monkeypatch):
    spawned = []

    class _Proc:
        pid, returncode = 4242, None

        def poll(self):
            return None

    monkeypatch.setattr(sup, "_spawn", lambda: spawned.append(1) or _Proc())
    monkeypatch.setattr(sup, "worker_listening", lambda conn, q=None: bool(spawned))   # registers right after the start
    monkeypatch.setattr(sup.settings, "FAST_AUTOSTART_WORKER", True)
    conn = _FakeConn()
    assert sup.ensure_fast_worker(conn, wait_s=2.0) is True
    assert sup.ensure_fast_worker(conn, wait_s=0.0) is True          # already listening: nothing more is started
    assert len(spawned) == 1


def test_nothing_is_started_when_autostart_is_off_or_redis_is_missing(sup, monkeypatch):
    monkeypatch.setattr(sup, "_spawn", lambda: pytest.fail("must not start a worker"))
    monkeypatch.setattr(sup, "worker_listening", lambda conn, q=None: False)
    monkeypatch.setattr(sup.settings, "FAST_AUTOSTART_WORKER", False)
    assert sup.ensure_fast_worker(_FakeConn()) is False
    monkeypatch.setattr(sup.settings, "FAST_AUTOSTART_WORKER", True)
    assert sup.ensure_fast_worker(None) is False


def test_a_second_api_process_waits_for_the_worker_the_first_one_started(sup, monkeypatch):
    monkeypatch.setattr(sup, "_spawn", lambda: pytest.fail("the lock is held by another process"))
    monkeypatch.setattr(sup, "worker_listening", lambda conn, q=None: False)
    monkeypatch.setattr(sup.settings, "FAST_AUTOSTART_WORKER", True)
    conn = _FakeConn()
    conn.store[sup._LOCK_KEY] = "other-process"
    assert sup.ensure_fast_worker(conn, wait_s=0.6) is False
