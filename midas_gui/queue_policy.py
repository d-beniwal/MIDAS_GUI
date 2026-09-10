"""Which Batch Queue samples may start right now — the scheduling decision,
with no Qt and no workers attached.

Samples run in parallel, several at a time. Two constraints shape which ones
may be in flight together, and both are easy to get subtly wrong in the middle
of signal-handling code, which is why they live here as plain state machine
rather than inside the ``QObject``:

**A concurrency cap.** Each in-flight sample holds its own frames and results;
without a ceiling a hundred-sample queue is an out-of-memory crash hours in.

**One geometry build per calibration.** The detector map is the expensive part
of a run and depends only on the calibration, kernel, mask, correction flags
and weighting (``workers.build_integration_context`` takes exactly those and
never sees the data source) — so every sample under one calibration node can
share a single built map. But the map only exists *after* a run has built it,
and if K samples of the same calibration all start at once they each build
their own, K-1 of them wasted. So the first sample of a calibration goes out
alone as a **pilot**; its siblings are held until its context arrives, then
released to run with it.

The policy never blocks the whole queue on one pilot: samples belonging to
*other* calibrations keep starting normally, so a queue spanning several
calibrations stays saturated while each one's pilot warms up.

``queue_runner.SampleRunScheduler`` is the thin Qt layer that owns the workers
and calls into this.
"""
from __future__ import annotations

from typing import Iterable, Optional

#: Terminal states a sample can end in.
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


class QueuePolicy:
    """Ordering and gating for one queue run.

    ``items`` is an ordered iterable of ``(key, group)``: ``key`` identifies a
    sample uniquely, ``group`` identifies the calibration node whose detector
    map it would share. Both are opaque here — the scheduler supplies strings.
    """

    def __init__(self, items: Iterable, max_concurrent: int = 1):
        pairs = [(str(k), str(g)) for k, g in items]
        self._order = [k for k, _g in pairs]
        self._group = dict(pairs)
        self.max_concurrent = max(1, int(max_concurrent))
        self._pending = list(self._order)
        self._in_flight: set = set()
        self._ready: set = set()      # groups whose context has been built
        self._piloting: set = set()   # groups with a pilot in flight, no context yet
        self._results: dict = {}      # key -> DONE | FAILED | CANCELLED
        self._cancelled = False

    # ── queries ──────────────────────────────────────────────────

    @property
    def pending(self) -> list:
        return list(self._pending)

    @property
    def in_flight(self) -> list:
        return sorted(self._in_flight)

    @property
    def results(self) -> dict:
        return dict(self._results)

    def group_of(self, key: str) -> Optional[str]:
        return self._group.get(str(key))

    def context_is_ready(self, group: str) -> bool:
        return str(group) in self._ready

    def is_complete(self) -> bool:
        """Nothing running and nothing left that could run."""
        return not self._in_flight and not self._pending

    def counts(self) -> dict:
        """``{"total", "done", "failed", "cancelled", "running", "pending"}`` —
        what the tab's progress line reports."""
        vals = list(self._results.values())
        return {"total": len(self._order),
                "done": vals.count(DONE), "failed": vals.count(FAILED),
                "cancelled": vals.count(CANCELLED),
                "running": len(self._in_flight), "pending": len(self._pending)}

    # ── transitions ──────────────────────────────────────────────

    def take_next(self) -> list:
        """Claim every sample that may start now, in queue order.

        Marks them in flight, so a caller starts exactly what it is handed and
        never double-starts. Returns ``[]`` when the cap is reached, when the
        only pending samples are held behind a pilot, or after cancellation."""
        if self._cancelled:
            return []
        started = []
        while len(self._in_flight) < self.max_concurrent:
            pick = next((k for k in self._pending if self._may_start(k)), None)
            if pick is None:
                break
            self._pending.remove(pick)
            self._in_flight.add(pick)
            group = self._group[pick]
            if group not in self._ready:
                self._piloting.add(group)   # this one is the pilot
            started.append(pick)
        return started

    def _may_start(self, key: str) -> bool:
        group = self._group[key]
        # Free to go once the group's map exists; otherwise only if no sibling
        # is already building it.
        return group in self._ready or group not in self._piloting

    def mark_context_ready(self, group: str) -> None:
        """A pilot reported its detector map — release the group's siblings."""
        group = str(group)
        self._ready.add(group)
        self._piloting.discard(group)

    def mark_finished(self, key: str, state: str = DONE) -> None:
        """Record a terminal state and free the slot.

        If a pilot ends without ever reporting a context — it failed, or was
        cancelled before the build finished — the group is un-piloted so the
        next sibling can pilot instead. Without that the group's remaining
        samples would wait forever on a context that is never coming."""
        key = str(key)
        self._in_flight.discard(key)
        self._pending = [k for k in self._pending if k != key]
        self._results[key] = state
        group = self._group.get(key)
        if group is not None and group not in self._ready:
            if not any(self._group.get(k) == group for k in self._in_flight):
                self._piloting.discard(group)

    def cancel(self) -> list:
        """Drop everything not yet started; return the keys dropped.

        In-flight samples are the caller's to interrupt — the policy only stops
        handing out new ones, and ``is_complete`` goes true once the running
        ones report back."""
        self._cancelled = True
        dropped, self._pending = self._pending, []
        for key in dropped:
            self._results[key] = CANCELLED
        return dropped

    @property
    def cancelled(self) -> bool:
        return self._cancelled
