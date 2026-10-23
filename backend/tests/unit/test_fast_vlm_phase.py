"""The VLM repair phase must stay bounded: parallel, capped, time-boxed, and it gives up when the model is down."""
import time
from types import SimpleNamespace

from app.core.config import settings
from app.fast.events import EventBus
from app.fast.fields import FieldExtractor, FieldMeta


def _extractor(names):
    bus = EventBus("j", redis_conn=None)
    bus._redis = None
    ex = FieldExtractor([{"name": n} for n in names], bus)
    ex._ensure_vlm_tools = lambda: None
    return ex, bus


def _slots(ex, names):
    page = SimpleNamespace(page_number=1)
    return [{"name": n, "fdef": {"name": n}, "fr": None, "meta": FieldMeta(), "a": None,
             "hit": (page, {"x1": 0, "y1": 0, "x2": 10, "y2": 10}, [])} for n in names]


def test_repairs_run_in_parallel_and_each_field_is_settled(monkeypatch):
    monkeypatch.setattr(settings, "FAST_VLM_PARALLEL", 4)
    names = [f"f{i}" for i in range(4)]
    ex, bus = _extractor(names)
    ex._vlm_read = lambda page, box, label: (time.sleep(0.3) or ("VALUE " + label, True, (0, 0, 10, 10)))
    slots = _slots(ex, names)
    t0 = time.perf_counter()
    ex._vlm_phase(slots, max_calls=8)
    assert time.perf_counter() - t0 < 0.9            # 4 x 0.3 s sequentially would be 1.2 s
    assert all(s["result"].value.startswith("VALUE") for s in slots)
    assert ex.timings["vlm_repaired"] == 4


def test_cap_flags_the_rest_with_a_reason():
    names = ["a", "b", "c"]
    ex, bus = _extractor(names)
    ex._vlm_read = lambda *a: ("x", True, (0, 0, 1, 1))
    slots = _slots(ex, names)
    ex._vlm_phase(slots, max_calls=1)
    skipped = [s for s in slots if s["result"].extraction_method == "not_found"]
    assert len(skipped) == 2 and all(ex.meta[s["name"]].reason == "vlm_cap_reached" for s in skipped)


def test_a_dead_model_stops_the_phase_early(monkeypatch):
    monkeypatch.setattr(settings, "FAST_VLM_PARALLEL", 1)
    names = [f"f{i}" for i in range(6)]
    ex, bus = _extractor(names)
    calls = []

    def boom(*a):
        calls.append(1)
        raise RuntimeError("ollama down")
    ex._vlm_read = boom
    slots = _slots(ex, names)
    ex._vlm_phase(slots, max_calls=8)
    assert len(calls) == 2                         # gave up after two failures, did not try all six
    assert all("result" in s for s in slots)       # every field still got an answer (failed / review)


def test_time_budget_ends_the_phase(monkeypatch):
    monkeypatch.setattr(settings, "FAST_VLM_PARALLEL", 1)
    monkeypatch.setattr(settings, "FAST_VLM_BUDGET_S", 1)
    names = [f"f{i}" for i in range(5)]
    ex, bus = _extractor(names)
    ex._vlm_read = lambda *a: (time.sleep(0.6) or ("v", True, (0, 0, 1, 1)))
    slots = _slots(ex, names)
    t0 = time.perf_counter()
    ex._vlm_phase(slots, max_calls=8)
    assert time.perf_counter() - t0 < 2.5          # would be 3 s without the budget
    assert any(ex.meta[s["name"]].reason == "vlm_budget_spent" for s in slots)
