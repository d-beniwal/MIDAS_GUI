""""Run as background job" results reaching the Batch Integrate tab.

A background job is a detached `screen` session running `batch_cli.py` in a
separate process — no Qt signals from it reach the GUI that launched it. Two
things close that gap:

* `batch_cli._write_results_sidecar` persists the same r_axis_px/profiles/
  frame_ids/eta_axis arrays a live run's `finished` payload carries, as
  `_bg_job_results.npz` in the job's `--out-dir` — regardless of which
  `--fmts` the user picked (plain csv doesn't round-trip that shape).
* `job_queue.JobQueuePanel`'s `on_job_done` callback fires once a job is
  detected `Done` (and again from a "Load results" click), and
  `BatchTab._on_job_done` reads that sidecar back through the same replay
  path `_populate_plots_from_attempt` already uses for a restored project
  attempt.

Builds pyqtgraph widgets via BatchTab/JobQueuePanel, hence forked, and defers
every Qt / midas_gui GUI import into fixtures — see STATE.md's rule for new
Qt test files (``tests/test_set_raw_frame.py`` is the reference).
"""
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.forked

N_ETA, N_R = 6, 8


def _axes():
    return (np.arange(N_R, dtype=float), np.linspace(-180.0, 180.0, N_ETA))


def _1d_payload(n_frames=3):
    r_axis, _ = _axes()
    return {"n": n_frames, "out_paths": ["x"], "aborted": False,
            "r_axis_px": r_axis,
            "profiles": np.random.default_rng(0).random((n_frames, N_R)).astype(np.float32),
            "frame_ids": list(range(n_frames))}


def _cake_payload(n_frames=4):
    r_axis, eta_axis = _axes()
    return {"n": n_frames, "out_paths": ["x"], "aborted": False,
            "r_axis_px": r_axis, "eta_axis": eta_axis,
            "profiles": np.random.default_rng(1).random((n_frames, N_ETA, N_R)).astype(np.float32),
            "frame_ids": [f"f{i}" for i in range(n_frames)]}


# ── batch_cli._write_results_sidecar (pure logic, no Qt) ────────────────

def test_write_results_sidecar_round_trips_1d(tmp_path):
    from midas_gui import batch_cli
    batch_cli._write_results_sidecar(str(tmp_path), _1d_payload())
    with np.load(tmp_path / "_bg_job_results.npz") as npz:
        assert npz["profiles"].shape == (3, N_R)
        assert list(npz["frame_ids"]) == ["0", "1", "2"]
        assert "eta_axis_deg" not in npz.files


def test_write_results_sidecar_round_trips_cakes(tmp_path):
    from midas_gui import batch_cli
    batch_cli._write_results_sidecar(str(tmp_path), _cake_payload())
    with np.load(tmp_path / "_bg_job_results.npz") as npz:
        assert npz["profiles"].shape == (4, N_ETA, N_R)
        assert npz["eta_axis_deg"] == pytest.approx(_axes()[1])
        assert list(npz["frame_ids"]) == ["f0", "f1", "f2", "f3"]


def test_write_results_sidecar_is_a_noop_with_nothing_to_plot(tmp_path):
    from midas_gui import batch_cli
    batch_cli._write_results_sidecar(str(tmp_path), {"n": 0, "aborted": False})
    assert not (tmp_path / "_bg_job_results.npz").exists()


# ── job_queue.JobQueuePanel ───────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def job_queue(app):
    from midas_gui import job_queue
    return job_queue


@pytest.fixture
def log_widget(app):
    from midas_gui.widgets import LogPanel
    return LogPanel()


def test_launch_records_out_dir_in_meta(job_queue, log_widget, tmp_path, monkeypatch):
    monkeypatch.setattr(job_queue, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(job_queue, "screen_available", lambda: True)
    monkeypatch.setattr(job_queue, "_screen_session_alive", lambda session: False)
    monkeypatch.setattr(job_queue.QtCore.QProcess, "execute", staticmethod(lambda *a, **k: 0))

    panel = job_queue.JobQueuePanel(log_widget)
    out_dir = str(tmp_path / "results")
    job = panel.launch(["true"], name="myjob", total_frames=5, out_dir=out_dir)
    assert job is not None
    assert job.out_dir == out_dir
    import json
    meta = json.loads(job_queue.Path(job.meta_path).read_text())
    assert meta["out_dir"] == out_dir


def test_adopt_existing_sessions_recovers_out_dir(job_queue, log_widget, tmp_path, monkeypatch):
    monkeypatch.setattr(job_queue, "JOBS_DIR", tmp_path / "jobs")
    (tmp_path / "jobs").mkdir()
    session = f"{job_queue._SESSION_PREFIX}old_job"
    meta_path = tmp_path / "jobs" / f"{session}.meta.json"
    import json
    meta_path.write_text(json.dumps({"name": "old_job", "total_frames": 2,
                                     "out_dir": str(tmp_path / "old_out")}))

    def fake_run(cmd, **kw):
        if cmd[:2] == ["screen", "-ls"]:
            return SimpleNamespace(stdout=f"\t123.{session}\t(Detached)\n")
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(job_queue.subprocess, "run", fake_run)
    monkeypatch.setattr(job_queue, "_screen_session_alive", lambda s: True)

    panel = job_queue.JobQueuePanel(log_widget)
    assert len(panel._jobs) == 1
    assert panel._jobs[0].out_dir == str(tmp_path / "old_out")


def test_finalize_job_done_calls_on_job_done_and_enables_button(job_queue, log_widget, tmp_path):
    calls = []
    panel = job_queue.JobQueuePanel(log_widget, on_job_done=calls.append)
    logfile = tmp_path / "x.screenlog"
    logfile.write_text("[launcher] DONE exit=0\n")
    job = job_queue.Job(session="s1", logfile=str(logfile), meta_path="",
                        name="job1", out_dir=str(tmp_path))
    panel._jobs.append(job)
    panel._add_job_row(job)
    assert job.load_results_btn.isEnabled() is False

    panel._finalize_job(job)

    assert job.status == "Done"
    assert job.load_results_btn.isEnabled() is True
    assert calls == [job]


def test_finalize_job_failed_does_not_call_on_job_done(job_queue, log_widget, tmp_path):
    calls = []
    panel = job_queue.JobQueuePanel(log_widget, on_job_done=calls.append)
    logfile = tmp_path / "x.screenlog"
    logfile.write_text("[launcher] DONE exit=1\n")
    job = job_queue.Job(session="s2", logfile=str(logfile), meta_path="",
                        name="job2", out_dir=str(tmp_path))
    panel._jobs.append(job)
    panel._add_job_row(job)

    panel._finalize_job(job)

    assert job.status == "Failed (1)"
    assert job.load_results_btn.isEnabled() is False
    assert calls == []


def test_load_results_button_click_invokes_callback_again(job_queue, log_widget, tmp_path):
    calls = []
    panel = job_queue.JobQueuePanel(log_widget, on_job_done=calls.append)
    job = job_queue.Job(session="s3", logfile="", meta_path="", name="job3",
                        out_dir=str(tmp_path), status="Done")
    panel._jobs.append(job)
    panel._add_job_row(job)

    job.load_results_btn.click()

    assert calls == [job]


# ── BatchTab._on_job_done ──────────────────────────────────────────────

@pytest.fixture
def tab(app):
    from midas_gui.tab_batch import BatchTab
    return BatchTab()


def test_on_job_done_populates_waterfall_from_1d_sidecar(tab, tmp_path):
    from midas_gui import batch_cli
    batch_cli._write_results_sidecar(str(tmp_path), _1d_payload())
    job = SimpleNamespace(session="sess-1d", out_dir=str(tmp_path))

    tab._on_job_done(job)

    assert tab._waterfall._nrows == 3
    assert tab._cake_stack_view.frame_count() == 0


def test_on_job_done_populates_cake_stack_from_multi_azimuth_sidecar(tab, tmp_path):
    from midas_gui import batch_cli
    batch_cli._write_results_sidecar(str(tmp_path), _cake_payload())
    job = SimpleNamespace(session="sess-cake", out_dir=str(tmp_path))

    tab._on_job_done(job)

    assert tab._cake_stack_view.frame_count() == 4
    assert tab._cake_stack_view._eta_axis == pytest.approx(_axes()[1])
    assert tab._cake_stack_view._frame_ids == ["f0", "f1", "f2", "f3"]
    # 1-D views got the η-collapse, not raw cakes.
    assert tab._waterfall._nrows == 4
    assert tab._waterfall._buf.shape[1] == N_R


def test_on_job_done_with_no_out_dir_logs_and_does_not_crash(tab):
    job = SimpleNamespace(session="sess-none", out_dir="")
    tab._on_job_done(job)
    assert "no recorded" in tab._log.toPlainText()


def test_on_job_done_with_missing_sidecar_logs_and_does_not_crash(tab, tmp_path):
    job = SimpleNamespace(session="sess-missing", out_dir=str(tmp_path))
    tab._on_job_done(job)
    assert "No results file found" in tab._log.toPlainText()


def test_job_queue_panel_wired_into_batch_tab_calls_populate(tab, tmp_path):
    """End-to-end wiring: BatchTab passes its own _on_job_done as the
    JobQueuePanel's on_job_done callback."""
    from midas_gui import batch_cli
    from midas_gui.job_queue import Job
    batch_cli._write_results_sidecar(str(tmp_path), _1d_payload())
    job = Job(session="sess-e2e", logfile="", meta_path="", name="j",
             out_dir=str(tmp_path))
    tab._job_queue._jobs.append(job)
    tab._job_queue._add_job_row(job)

    tab._job_queue._on_job_done(job)  # simulate what _finalize_job would call

    assert tab._waterfall._nrows == 3
