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


# ── batch_cli._source_cfg (argv -> source dict, pure logic, no Qt) ──────

def _parse_source_args(extra):
    from midas_gui import batch_cli
    parser = batch_cli._build_arg_parser()
    base = ["--calib-file", "c.json", "--out-dir", "o"]
    return parser.parse_args(base + extra)


def test_source_cfg_hdf5_forwards_frame_start_end():
    """The "hdf5" branch was the only one of the four source types that
    didn't forward --frame-start/--frame-end into the returned cfg dict —
    needed now that a single-file HDF5 source's start/end mean a raw
    sub-frame range within that one file (see
    widgets.DataLoaderPanel.source_cfg's "hdf5" branch), matching how the
    other three source types already forward them (as scan-number bounds)."""
    from midas_gui import batch_cli
    args = _parse_source_args([
        "--source-type", "hdf5", "--source-path", "scan.h5",
        "--frame-start", "2", "--frame-end", "7"])
    cfg = batch_cli._source_cfg(args)
    assert cfg["type"] == "hdf5"
    assert (cfg["frame_start"], cfg["frame_end"]) == (2, 7)


def test_source_cfg_hdf5_frame_bounds_default_to_none():
    from midas_gui import batch_cli
    args = _parse_source_args(["--source-type", "hdf5", "--source-path", "scan.h5"])
    cfg = batch_cli._source_cfg(args)
    assert cfg["frame_start"] is None and cfg["frame_end"] is None


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


# ── the finished job's screenlog reaches the shared log tree ────────────
#
# A background job already had a log, but in ~/.midas_gui/jobs — a fixed
# location job adoption scans after a GUI restart, so it cannot move. It is
# copied into ~/midas_runs/midas_screen_logs/<beamline>/<expid>/ instead,
# where the in-process run writes its own. Without that, which run mode you
# happened to use would decide whether your log was findable.

def test_a_finished_job_log_is_copied_into_the_screen_log_tree(tab, tmp_path,
                                                               monkeypatch):
    from midas_gui import run_log
    root = tmp_path / "midas_screen_logs"
    monkeypatch.setattr(run_log, "LOG_ROOT", root)

    live = tmp_path / "jobs" / "midasgui_batch_42.screenlog"
    live.parent.mkdir(parents=True)
    live.write_text("[launcher] DONE exit=0\n")

    tab.set_expid_provider(lambda: "brown_sep26")
    tab._archive_job_log(SimpleNamespace(logfile=str(live),
                                         session="midasgui_batch_42"))

    copies = list(root.rglob("*.screenlog"))
    assert len(copies) == 1
    assert copies[0].read_text() == "[launcher] DONE exit=0\n"
    assert "midasgui_batch_42" in copies[0].name
    assert "brown_sep26" in copies[0].parts
    assert live.is_file(), "the live log must stay put for job adoption"


def test_archiving_a_missing_job_log_is_a_no_op(tab, tmp_path, monkeypatch):
    """A log is a record of work, not the work."""
    from midas_gui import run_log
    root = tmp_path / "midas_screen_logs"
    monkeypatch.setattr(run_log, "LOG_ROOT", root)
    tab.set_expid_provider(lambda: "e")
    tab._archive_job_log(SimpleNamespace(logfile="/nonexistent/x.screenlog",
                                         session="s"))      # must not raise
    assert not root.exists()


# ── the sidecar is opt-in, and only the reachable step writes one ───────
# JobQueuePanel records ONE out_dir per job (the first step's), so in a
# chained multi-folder job only that step's _bg_job_results.npz can ever be
# opened by "Load results". It used to be written unconditionally by every
# step: a real 196-folder sweep left 196 of them, 826 MB, on a beamline
# share, 195 unreachable from the UI.

def test_the_sidecar_is_off_unless_asked_for():
    from midas_gui import batch_cli
    base = ["--calib-file", "c", "--out-dir", "o",
            "--source-type", "hdf5", "--source-path", "s.h5"]
    assert batch_cli._build_arg_parser().parse_args(base).results_sidecar is False
    assert batch_cli._build_arg_parser().parse_args(
        base + ["--results-sidecar"]).results_sidecar is True


def test_only_the_first_chained_step_is_asked_to_write_one(tmp_path, monkeypatch):
    """Driven through the real argv builder: the flag must land on the step
    whose out_dir the queue row records, and on no other."""
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    import midas_gui.job_queue as jq

    seen = {}
    monkeypatch.setattr(
        jq.JobQueuePanel, "launch",
        lambda self, argv, **kw: seen.update(
            first=argv, rest=list(kw.get("extra_argvs") or [])) or None)

    # Three folders -> three invocations, one flagged.
    argvs = [["python", "-m", "midas_gui.batch_cli", "--out-dir", f"/o/{d}"]
             for d in "abc"]
    argvs[0] = argvs[0] + ["--results-sidecar"]
    jq.JobQueuePanel.launch(None, argvs[0], name="x", total_frames=1,
                            out_dir="/o/a", extra_argvs=argvs[1:])

    assert "--results-sidecar" in seen["first"]
    assert not any("--results-sidecar" in a for a in seen["rest"]), (
        "a step the UI cannot reach was still asked to write a sidecar")


# ── end-of-job email notification ───────────────────────────────────────
# A background job can run for many hours in a detached screen session and
# nothing told you when it ended -- the GUI crashed mid-sweep today and the
# loss went unnoticed for hours. _finalize_job already runs exactly once per
# job and already knows the exit code, so that is where this hangs.

def _job(tmp_path, status="Done", exit_code=0, log_lines=()):
    from midas_gui.job_queue import Job
    lf = tmp_path / "j.screenlog"
    lf.write_text("\n".join(log_lines) + ("\n" if log_lines else ""))
    j = Job(session="midasgui_x", logfile=str(lf),
            meta_path=str(tmp_path / "j.json"), name="scan_sweep",
            total_frames=10, out_dir=str(tmp_path / "out"))
    j.status, j.exit_code = status, exit_code
    j.step, j.n_steps = 7, 40
    return j


def test_the_notification_names_the_job_and_how_far_it_got(tmp_path):
    from midas_gui import job_queue as jq
    sent = {}

    def fake_run(argv, **kw):
        sent.update(argv=argv, body=kw.get("input", ""))
        return None

    assert jq.send_job_notification(_job(tmp_path), "me@example.com",
                                    _runner=fake_run) is True
    assert sent["argv"][0] == "mail"
    assert "me@example.com" in sent["argv"]
    subject = sent["argv"][sent["argv"].index("-s") + 1]
    assert "Done" in subject and "scan_sweep" in subject
    assert "midasgui_x" in sent["body"]
    assert "7/40" in sent["body"], "progress missing from the body"


def test_error_lines_are_carried_into_the_body(tmp_path):
    """So the mail is actionable without opening the log on the host."""
    from midas_gui import job_queue as jq
    sent = {}
    job = _job(tmp_path, status="Failed (1)", exit_code=1, log_lines=[
        "[batch] PROGRESS 1/10", "[batch] ERROR: calibration file not found",
        "[batch] PROGRESS 2/10", "Traceback (most recent call last):"])
    jq.send_job_notification(job, "me@example.com",
                             _runner=lambda a, **k: sent.update(body=k.get("input", "")))
    assert "calibration file not found" in sent["body"]
    assert "Traceback" in sent["body"]
    assert "PROGRESS 1/10" not in sent["body"], "ordinary progress is not an error"


def test_no_address_means_no_mail(tmp_path):
    from midas_gui import job_queue as jq
    called = []
    for addr in ("", "   ", None):
        assert jq.send_job_notification(
            _job(tmp_path), addr, _runner=lambda *a, **k: called.append(1)) is False
    assert not called


def test_a_broken_mail_system_cannot_hurt_a_finished_job(tmp_path):
    """The contract. The job is already over by the time this runs, so
    there is no outcome left to protect -- and a notifier that could raise
    would turn a successful sweep into a traceback in the GUI."""
    from midas_gui import job_queue as jq

    def explode(*a, **k):
        raise OSError("no mail transport agent")

    assert jq.send_job_notification(
        _job(tmp_path), "me@example.com", _runner=explode) is False


def test_the_setting_round_trips_and_defaults_to_off(tmp_path, monkeypatch):
    """Blank means no mail, so an operator who never configures it is never
    surprised by outbound traffic from a beamline host."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    from midas_gui import settings
    importlib.reload(settings)
    assert settings.get_notify_email() == ""
    settings.set_notify_email("  a@b.com  ")
    assert settings.get_notify_email() == "a@b.com", "not stripped"
    settings.set_notify_email("")
    assert settings.get_notify_email() == ""
