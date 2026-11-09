"""Reporting files that were skipped for want of a valid dark.

A skip is the DELIBERATE outcome when no dark can be resolved —
correcting a file with another scan's dark produces a plausible and wrong
answer, which is the error ``dark_policy`` exists to prevent. That is only
defensible if the skip is loud, so these tests are about visibility rather
than about arithmetic.

``mail`` itself is never invoked: the sender takes a ``_runner`` so the
command can be captured instead of sent, the same seam
``job_queue.send_job_notification`` has always used.
"""
from __future__ import annotations

import pytest

from midas_gui.mail import dark_skip_report, send_mail


class _Runner:
    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    def __call__(self, argv, **kw):
        if self.boom:
            raise OSError("no mail command here")
        self.calls.append((argv, kw.get("input", "")))
        return None


def test_a_blank_address_is_not_an_error():
    """It is how a user says "no mail". Every caller falls back to the
    log, so this must not raise or look like a failure to send."""
    r = _Runner()
    assert send_mail("s", "b", "", _runner=r) is False
    assert send_mail("s", "b", "   ", _runner=r) is False
    assert r.calls == []


def test_a_missing_mail_command_costs_a_return_value_and_nothing_else():
    """Best effort by contract: the work being reported has already
    finished, so a notifier that could raise would be worse than none."""
    assert send_mail("s", "b", "me@example.com", _runner=_Runner(boom=True)) \
        is False


def test_the_body_may_be_lines_or_a_string():
    r = _Runner()
    send_mail("s", ["a", "b"], "me@example.com", _runner=r)
    send_mail("s", "a\nb", "me@example.com", _runner=r)
    assert r.calls[0][1] == r.calls[1][1] == "a\nb\n"


def test_the_report_names_every_file_and_its_reason():
    """"No dark found" on its own sends someone to go and look. The
    reason is the part that saves the trip."""
    subject, body = dark_skip_report(
        [("/d/a.h5", "exposure 0.1s != 10.0s"),
         ("/d/b.h5", "no dark-like file anywhere under the selected folder")],
        context="Batch Integrate")
    text = "\n".join(body)
    assert "2 file(s) skipped" in subject and "Batch Integrate" in subject
    assert "/d/a.h5" in text and "exposure 0.1s != 10.0s" in text
    assert "/d/b.h5" in text


def test_the_report_says_why_skipping_was_the_right_answer():
    """So nobody reads it as a malfunction and 'fixes' it by pointing
    everything at whatever dark happens to be nearest."""
    _subject, body = dark_skip_report([("/d/a.h5", "why")])
    text = "\n".join(body).lower()
    assert "another scan's dark" in text


def test_a_long_list_is_truncated_rather_than_mailed_whole():
    subject, body = dark_skip_report([(f"/d/{i}.h5", "why") for i in range(400)])
    text = "\n".join(body)
    assert "400 file(s)" in subject
    assert "and 350 more" in text
    assert len(text) < 20000


# ── the coordinators aggregate what their workers skipped ───────────────

@pytest.mark.forked
def test_both_coordinators_expose_what_their_workers_skipped():
    """A skip recorded only on a worker is a silent skip — the tabs talk
    to coordinators, not to workers."""
    pytest.importorskip("PyQt5.QtWidgets")
    from midas_gui import workers as W
    for cls in (W.BatchCorrectionCoordinator, W.BatchRunCoordinator):
        assert isinstance(getattr(cls, "dark_skipped", None), property), \
            f"{cls.__name__} does not aggregate dark_skipped"
