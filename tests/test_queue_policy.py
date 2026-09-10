"""Batch Queue scheduling: the concurrency cap and the one-geometry-build-per-
calibration gate.

Pure logic, no Qt — the point of keeping ``QueuePolicy`` out of the ``QObject``
is that this state machine can be driven step by step in a test instead of
being inferred from signal traces.

Two failure modes these pin, both of which would otherwise show up only as
"the overnight run was slower than expected" or "the run wedged":

* every sample of a calibration starting at once and each rebuilding the same
  detector map;
* a pilot dying before it reports a map, leaving its siblings waiting on a
  context that will never arrive.
"""
from midas_gui.queue_policy import CANCELLED, DONE, FAILED, QueuePolicy


def _p(items, max_concurrent=2):
    return QueuePolicy(items, max_concurrent=max_concurrent)


ONE_CAL = [("a", "cal0"), ("b", "cal0"), ("c", "cal0")]
TWO_CAL = [("a", "cal0"), ("b", "cal0"), ("c", "cal1"), ("d", "cal1")]


# ── the pilot gate ───────────────────────────────────────────────────

def test_only_one_sample_of_a_calibration_starts_before_its_map_exists():
    """The whole reason the gate exists: b and c would each rebuild cal0's map."""
    p = _p(ONE_CAL, max_concurrent=3)
    assert p.take_next() == ["a"]
    assert p.take_next() == []          # b and c held behind the pilot


def test_siblings_are_released_once_the_map_arrives():
    p = _p(ONE_CAL, max_concurrent=3)
    p.take_next()
    p.mark_context_ready("cal0")
    assert p.take_next() == ["b", "c"]
    assert p.in_flight == ["a", "b", "c"]


def test_a_pilot_per_calibration_runs_concurrently():
    """Distinct calibrations do not gate each other — the queue stays busy
    while each one's pilot warms up."""
    p = _p(TWO_CAL, max_concurrent=4)
    assert p.take_next() == ["a", "c"]
    assert p.take_next() == []


def test_one_calibration_being_ready_does_not_release_another():
    p = _p(TWO_CAL, max_concurrent=4)
    p.take_next()
    p.mark_context_ready("cal0")
    assert p.take_next() == ["b"]
    p.mark_context_ready("cal1")
    assert p.take_next() == ["d"]


def test_a_ready_calibration_starts_freely_up_to_the_cap():
    p = _p([("a", "c0"), ("b", "c0"), ("c", "c0"), ("d", "c0")], max_concurrent=2)
    assert p.take_next() == ["a"]
    p.mark_context_ready("c0")
    assert p.take_next() == ["b"]       # cap of 2 reached
    p.mark_finished("a")
    assert p.take_next() == ["c"]


# ── the concurrency cap ──────────────────────────────────────────────

def test_the_cap_is_never_exceeded():
    items = [(f"s{i}", f"cal{i}") for i in range(6)]
    p = _p(items, max_concurrent=3)
    assert len(p.take_next()) == 3
    assert p.take_next() == []
    p.mark_finished("s0")
    assert len(p.take_next()) == 1


def test_a_cap_below_one_is_clamped():
    p = _p(ONE_CAL, max_concurrent=0)
    assert p.max_concurrent == 1
    assert len(p.take_next()) == 1


def test_queue_order_is_respected():
    p = _p([("a", "c0"), ("b", "c1"), ("c", "c2")], max_concurrent=1)
    assert p.take_next() == ["a"]
    p.mark_finished("a")
    assert p.take_next() == ["b"]


# ── failure does not stall the rest ──────────────────────────────────

def test_a_failed_pilot_lets_the_next_sibling_pilot():
    """Otherwise the group's remaining samples wait forever on a context that
    is never coming."""
    p = _p(ONE_CAL, max_concurrent=3)
    p.take_next()
    p.mark_finished("a", FAILED)        # died before reporting a context
    assert p.take_next() == ["b"]
    assert p.take_next() == []          # b is the new pilot
    p.mark_context_ready("cal0")
    assert p.take_next() == ["c"]


def test_a_failure_does_not_stop_other_calibrations():
    p = _p(TWO_CAL, max_concurrent=4)
    p.take_next()
    p.mark_finished("a", FAILED)
    p.mark_context_ready("cal1")
    assert set(p.take_next()) == {"b", "d"}


def test_results_record_each_terminal_state():
    p = _p(TWO_CAL, max_concurrent=4)
    p.take_next()
    p.mark_finished("a", DONE)
    p.mark_finished("c", FAILED)
    assert p.results == {"a": DONE, "c": FAILED}


def test_completion_needs_both_queues_empty():
    p = _p([("a", "c0")], max_concurrent=1)
    assert not p.is_complete()
    p.take_next()
    assert not p.is_complete()          # running, not finished
    p.mark_finished("a")
    assert p.is_complete()


def test_counts_track_progress():
    p = _p(TWO_CAL, max_concurrent=2)
    assert p.counts() == {"total": 4, "done": 0, "failed": 0, "cancelled": 0,
                          "running": 0, "pending": 4}
    p.take_next()
    p.mark_finished("a", DONE)
    p.mark_finished("c", FAILED)
    c = p.counts()
    assert (c["done"], c["failed"], c["running"], c["total"]) == (1, 1, 0, 4)


# ── cancellation ─────────────────────────────────────────────────────

def test_cancel_drops_the_pending_and_starts_nothing_more():
    p = _p(TWO_CAL, max_concurrent=2)
    running = p.take_next()
    dropped = p.cancel()
    assert set(dropped) == {"b", "d"}
    assert p.take_next() == []
    assert all(p.results[k] == CANCELLED for k in dropped)
    assert p.cancelled and set(p.in_flight) == set(running)


def test_cancel_completes_once_the_running_samples_report_back():
    """In-flight work is the scheduler's to interrupt; the policy just stops
    issuing more and waits for them."""
    p = _p(TWO_CAL, max_concurrent=2)
    started = p.take_next()
    p.cancel()
    assert not p.is_complete()
    for key in started:
        p.mark_finished(key, CANCELLED)
    assert p.is_complete()


# ── bookkeeping ──────────────────────────────────────────────────────

def test_an_empty_queue_is_immediately_complete():
    p = _p([])
    assert p.take_next() == [] and p.is_complete()
    assert p.counts()["total"] == 0


def test_group_lookup():
    p = _p(TWO_CAL)
    assert p.group_of("a") == "cal0" and p.group_of("d") == "cal1"
    assert p.group_of("nope") is None


def test_context_readiness_is_reported():
    p = _p(ONE_CAL)
    assert not p.context_is_ready("cal0")
    p.mark_context_ready("cal0")
    assert p.context_is_ready("cal0")


def test_a_sample_is_never_started_twice():
    p = _p(ONE_CAL, max_concurrent=3)
    first = p.take_next()
    p.mark_context_ready("cal0")
    second = p.take_next()
    assert not set(first) & set(second)
    assert sorted(first + second) == ["a", "b", "c"]
    assert p.take_next() == []
