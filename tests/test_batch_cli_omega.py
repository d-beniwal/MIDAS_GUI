"""Omega survives the background-job path, not just the in-process one.

Batch Integrate has two run paths that share no code: ``BatchTab._run``
constructs a ``BatchRunCoordinator`` in-process, while ``BatchTab._run_as_job``
serialises everything to an argv and hands it to a detached
``python -m midas_gui.batch_cli``. When the omega feature shipped it was wired
into the first only, so a background job silently wrote ω=0 on every frame
however the Cake parameters were set — the settings were simply not on the
command line, and no error could be raised because an unpassed config and a
genuine 0°/0° are indistinguishable from inside the worker.

That is the regression these tests exist to prevent, so the important one is
``test_the_launched_argv_carries_the_tabs_own_omega_settings``: it drives the
real argv builder and feeds what comes out to the real CLI parser, closing
the loop rather than asserting each half in isolation.

Qt import discipline per STATE.md: import inside fixtures, never at module
scope.
"""
import pytest


# ── the CLI half: argv -> omega_cfg (pure logic, no Qt) ─────────────────

def _parse(extra):
    from midas_gui import batch_cli
    base = ["--calib-file", "c.json", "--out-dir", "o",
            "--source-type", "hdf5", "--source-path", "scan.h5"]
    return batch_cli._build_arg_parser().parse_args(base + extra)


def test_omega_cfg_defaults_to_a_real_zero_not_a_missing_key():
    """0/0 is a genuine 0° on every frame (a stationary sample), not a
    sentinel — so the key is always present and always a float, and a run
    with no omega flags is well-defined rather than unconfigured."""
    from midas_gui import batch_cli
    cfg = batch_cli._omega_cfg(_parse([]))
    assert cfg == {"start": 0.0, "step": 0.0, "channel": "", "collapse": False}


def test_omega_cfg_reads_start_and_step():
    from midas_gui import batch_cli
    cfg = batch_cli._omega_cfg(_parse(["--ome-start", "5.0", "--ome-step", "0.25"]))
    assert (cfg["start"], cfg["step"]) == (5.0, 0.25)


def test_omega_cfg_reads_the_measured_channel_and_strips_it():
    from midas_gui import batch_cli
    cfg = batch_cli._omega_cfg(_parse(["--ome-channel", "  /SMS/D/HR/samRy  "]))
    assert cfg["channel"] == "/SMS/D/HR/samRy"


def test_omega_cfg_reads_the_averaged_summed_override():
    from midas_gui import batch_cli
    assert batch_cli._omega_cfg(_parse(["--ome-collapse"]))["collapse"] is True


def test_a_negative_step_survives_argv():
    """A reverse scan is a real acquisition, and argparse is perfectly happy
    to read a leading '-' as another flag if the type is wrong."""
    from midas_gui import batch_cli
    assert batch_cli._omega_cfg(_parse(["--ome-step", "-0.5"]))["step"] == -0.5


def test_the_cli_hands_the_omega_cfg_to_the_worker():
    """The flags existing is not the point — reaching ``BatchWorker`` is.
    ``main`` is too heavy to call here (it opens the source and integrates),
    so this pins the construction site by reading it."""
    import inspect
    from midas_gui import batch_cli
    src = inspect.getsource(batch_cli.main)
    assert "omega_cfg=_omega_cfg(args)" in src


# ── the GUI half: the tab's settings -> argv -> back to a cfg ───────────

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt5.QtWidgets")
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(qapp):
    from midas_gui.tab_batch import BatchTab
    return BatchTab()


def _launched_argv(tab, tmp_path, monkeypatch):
    """Drive the real ``_run_as_job`` far enough to capture the argv it would
    hand to ``screen``, stubbing only what needs a real calibration, real
    frames or a real subprocess."""
    calib = tmp_path / "calib.json"
    calib.write_text("{}")
    tab._use_tab2_btn.setChecked(False)
    tab._json_ed.setText(str(calib))
    tab._out_ed.setText(str(tmp_path / "out"))

    class _Spec:
        TransOpt = ()
    monkeypatch.setattr(tab, "_build_spec", lambda: _Spec())
    monkeypatch.setattr(tab._loader, "source_cfg",
                        lambda: {"type": "hdf5", "path": str(tmp_path / "scan.h5"),
                                 "dataset": "exchange/data", "chunk_size": 25,
                                 "combine_op": "mean",
                                 "frame_start": 0, "frame_end": 1441})
    monkeypatch.setattr(tab._loader, "has_pending_fields", lambda: [])
    monkeypatch.setattr(tab._fmt, "checked_keys", lambda: ["zarr"])

    seen = {}

    def _capture(argv, **_kw):
        seen["argv"] = argv
        return None
    monkeypatch.setattr(tab._job_queue, "launch", _capture)
    tab._run_as_job()
    assert "argv" in seen, "_run_as_job bailed out before launching"
    return seen["argv"]


def test_the_launched_argv_carries_the_tabs_own_omega_settings(tab, tmp_path, monkeypatch):
    """The whole bug in one assertion: what the tab would run in-process and
    what it sends to the background job must be the same omega config."""
    from midas_gui import batch_cli
    tab._ome_start.setValue(5.0)
    tab._ome_step.setValue(0.25)
    tab._ome_channel.setEditText("")
    tab._ome_collapse.setChecked(False)

    argv = _launched_argv(tab, tmp_path, monkeypatch)
    parsed = batch_cli._build_arg_parser().parse_known_args(argv[3:])[0]
    assert batch_cli._omega_cfg(parsed) == tab._omega_cfg()


def test_the_argv_round_trips_the_channel_and_the_collapse_override(tab, tmp_path, monkeypatch):
    from midas_gui import batch_cli
    tab._ome_start.setValue(-1.5)
    tab._ome_step.setValue(1.0)
    tab._ome_channel.setEditText("/SMS/D/HR/samRy")
    tab._ome_collapse.setChecked(True)

    argv = _launched_argv(tab, tmp_path, monkeypatch)
    parsed = batch_cli._build_arg_parser().parse_known_args(argv[3:])[0]
    assert batch_cli._omega_cfg(parsed) == tab._omega_cfg()


def test_start_and_step_are_always_on_the_command_line(tab, tmp_path, monkeypatch):
    """Even at the 0/0 default. The launched command line is echoed into the
    Logs tab, and it is the only place a user can see what angles a detached
    job will record — an absent flag is exactly what made this bug invisible."""
    argv = _launched_argv(tab, tmp_path, monkeypatch)
    assert "--ome-start" in argv and "--ome-step" in argv
