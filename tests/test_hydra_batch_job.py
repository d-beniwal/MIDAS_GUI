"""The Hydra page's background-job path: argv planning and the combine CLI.

Hydra Batch Integrate has two run paths that share no code. ``_run_all``
builds ``BatchRunCoordinator``s in-process; ``_run_as_job`` serialises
everything to argvs and hands them to a detached ``screen`` session running
``python -m midas_gui.batch_cli`` once per panel, then
``python -m midas_gui.hydra_combine_cli`` to merge that scan's archives.

That split is exactly the shape that produced the omega bug on the
single-detector tab (see tests/test_batch_cli_omega.py): a setting wired into
the in-process path only is silently absent from the background one, and no
error can be raised because an unpassed flag and a genuine default are
indistinguishable from inside the worker. Hydra multiplies the exposure —
each panel has its OWN calibration and its own azimuthal wedge, and ``eta_min``/
``eta_max`` had no CLI flag at all until this feature, so a background job
that forgot them would integrate the full circle on every panel and look
plausible.

So the load-bearing test here is
``test_the_launched_argvs_carry_each_panels_own_caking_and_calibration``: it
drives the real argv builder and feeds every argv it produces back through
the real CLI parsers, closing the loop rather than asserting each half in
isolation.

Qt import discipline per STATE.md: Qt and the GUI modules are imported inside
fixtures, never at module scope, and this file builds exactly ONE
``HydraBatchPage`` (9 pyqtgraph widgets — see tests/test_hydra_batch_ui.py's
header for the teardown-crash history).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "test_data" / "gui_synthetic" / "hydra"

pytestmark = pytest.mark.forked


# ══ the CLI half: argv -> spec / worker kwargs (pure logic, no Qt) ══

def _parse(extra):
    from midas_gui import batch_cli
    base = ["--calib-file", "c.json", "--out-dir", "o",
            "--source-type", "hdf5", "--source-path", "scan.h5"]
    return batch_cli._build_arg_parser().parse_args(base + extra)


def test_eta_bounds_default_to_none_so_the_full_circle_is_still_the_default():
    """The single-detector tab never passes these and must keep integrating
    all 360°, so "unset" has to reach the spec builder as None rather than as
    some numeric stand-in."""
    a = _parse([])
    assert a.eta_min is None and a.eta_max is None


def test_eta_bounds_survive_the_branch_cut_ge1_actually_sits_on():
    """ge1 at 1-ID-E is caked across the ±180 discontinuity, so the parser
    must carry a low edge that is numerically greater than the high one
    without normalising or reordering it — the wrap is the backend's job
    (see eta_wrap.plan_eta), not the CLI's."""
    a = _parse(["--eta-min", "170", "--eta-max", "-170"])
    assert (a.eta_min, a.eta_max) == (170.0, -170.0)


def test_frame_range_is_a_separate_control_from_the_file_number_filter():
    """--frame-start/--frame-end subset the SOURCE by file number before it
    is opened; --frame-range-* subset the output frames it then yields. They
    are different ranges with different meanings and must not alias."""
    a = _parse(["--frame-start", "100", "--frame-end", "200",
                "--frame-range-start", "2", "--frame-range-end", "9",
                "--frame-range-stride", "3"])
    assert (a.frame_start, a.frame_end) == (100, 200)
    assert (a.frame_range_start, a.frame_range_end, a.frame_range_stride) == (2, 9, 3)


def test_an_unset_frame_range_leaves_the_worker_on_its_own_default():
    """Not merely "equivalent to (0, None, 1)" — actually None, so
    BatchWorker keeps whatever its own default is and this flag cannot
    change existing single-detector behaviour by being added."""
    from midas_gui import batch_cli
    captured = {}

    def _fake_worker(*args, **kw):
        captured.update(kw)
        raise SystemExit(0)       # stop before any real work

    import midas_gui.workers as workers_mod
    orig = workers_mod.BatchWorker
    workers_mod.BatchWorker = _fake_worker
    try:
        with pytest.raises(SystemExit):
            batch_cli.main(["--calib-file", str(FIXTURE_DIR / "ge1" / "ps_ge1.txt"),
                            "--out-dir", "/tmp", "--source-type", "hdf5",
                            "--source-path", "scan.h5"])
    finally:
        workers_mod.BatchWorker = orig
    assert captured.get("frame_range") is None


# ══ the combine CLI ══

class _FakeCombine:
    """Stand-in for the ``hydra_zarr_combine`` module surface the CLI uses."""

    def __init__(self, sets, fail_on=()):
        self.sets = sets
        self.fail_on = set(fail_on)
        self.combined = []

    def find_panel_zarr_sets(self, out_dir, panels):
        self.panels_asked = list(panels)
        return list(self.sets)

    def default_combined_path(self, group, out_dir=None):
        return Path(out_dir or ".") / (Path(group[sorted(group)[0]]).name + ".merged")

    def combine_panel_zarrs(self, group, out_path=None, warn=None):
        key = Path(group[sorted(group)[0]]).name
        if key in self.fail_on:
            raise RuntimeError("omega mismatch")
        self.combined.append(key)
        return out_path


def _run_combine(monkeypatch, fake, argv):
    # The CLI does `from midas_gui import hydra_zarr_combine` inside main(),
    # which reads the attribute off the package at call time — so patching
    # the package attribute is enough, and keeps the real module untouched.
    import midas_gui
    from midas_gui import hydra_combine_cli
    monkeypatch.setattr(midas_gui, "hydra_zarr_combine", fake)
    return hydra_combine_cli.main(argv)


def test_combine_cli_merges_every_complete_set(monkeypatch, tmp_path, capsys):
    fake = _FakeCombine([{1: str(tmp_path / "f0.ge1.zip"), 2: str(tmp_path / "f0.ge2.zip")},
                         {1: str(tmp_path / "f1.ge1.zip"), 2: str(tmp_path / "f1.ge2.zip")}])
    rc = _run_combine(monkeypatch, fake,
                      ["--out-dir", str(tmp_path), "--panels", "ge1", "ge2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert fake.combined == ["f0.ge1.zip", "f1.ge1.zip"]
    assert fake.panels_asked == ["ge1", "ge2"]
    # Progress lines in the shape JobQueuePanel already parses, so a combine
    # step moves the same bar the integration steps do.
    assert "[combine] PROGRESS 1/2" in out and "[combine] PROGRESS 2/2" in out


def test_combine_cli_treats_no_complete_set_as_success_not_failure(
        monkeypatch, tmp_path, capsys):
    """An aborted or partial scan legitimately has no frame with every panel,
    and its per-panel output is still good. Exiting non-zero would mark the
    whole chained job failed for something that is not an error — the
    in-process path says exactly this and carries on."""
    rc = _run_combine(monkeypatch, _FakeCombine([]),
                      ["--out-dir", str(tmp_path)])
    assert rc == 0
    assert "nothing to combine" in capsys.readouterr().out


def test_one_bad_frame_does_not_cost_the_others_but_is_still_reported(
        monkeypatch, tmp_path, capsys):
    fake = _FakeCombine(
        [{1: str(tmp_path / "f0.ge1.zip")}, {1: str(tmp_path / "f1.ge1.zip")},
         {1: str(tmp_path / "f2.ge1.zip")}],
        fail_on=["f1.ge1.zip"])
    rc = _run_combine(monkeypatch, fake, ["--out-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert fake.combined == ["f0.ge1.zip", "f2.ge1.zip"], "a bad frame stopped the rest"
    assert rc == 1, "a failed merge must still mark the step failed"
    assert "omega mismatch" in out


def test_panels_may_be_spelled_with_commas_like_the_mpe_workflows_combine(
        monkeypatch, tmp_path):
    """mpe_wf_saxs_waxs/combine_hydra_zarr.py takes --panels ge1,ge2,ge3,ge4.
    Typing it that way here must not look for one panel literally named
    "ge1,ge2,ge3,ge4" and then report "nothing to combine"."""
    fake = _FakeCombine([])
    _run_combine(monkeypatch, fake,
                 ["--out-dir", str(tmp_path), "--panels", "ge1,ge2,ge3,ge4"])
    assert fake.panels_asked == ["ge1", "ge2", "ge3", "ge4"]


def test_combine_cli_rejects_a_missing_output_directory(tmp_path, capsys):
    from midas_gui import hydra_combine_cli
    rc = hydra_combine_cli.main(["--out-dir", str(tmp_path / "nope")])
    assert rc == 1 and "ERROR" in capsys.readouterr().out


# ══ the GUI half: one page, the whole argv plan ══

_QT_LOADED = False
QtWidgets = None
hydra_batch_widgets_mod = HydraBatchPage = None


def _load_qt():
    """See tests/test_hydra_batch_ui.py — importing PyQt5 in the collecting
    parent process and then forking is what this defers around."""
    global _QT_LOADED
    if _QT_LOADED:
        return
    from PyQt5 import QtWidgets
    import midas_gui.hydra_batch_widgets as hydra_batch_widgets_mod
    from midas_gui.hydra_batch_page import HydraBatchPage
    _QT_LOADED = True
    globals().update(QtWidgets=QtWidgets,
                     hydra_batch_widgets_mod=hydra_batch_widgets_mod,
                     HydraBatchPage=HydraBatchPage)


@pytest.fixture(scope="module")
def app():
    _load_qt()
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def dialogs(app, monkeypatch):
    """Record modal dialogs instead of showing them.

    Not a convenience: offscreen Qt still runs a real modal event loop, so an
    unstubbed ``QMessageBox.critical`` inside ``_run_as_job`` does not fail
    the test — it hangs the forked child forever at 0% CPU, with pytest
    printing nothing at all. Every test here therefore asserts on what WOULD
    have been shown rather than letting one open.
    """
    seen = []
    for kind in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QtWidgets.QMessageBox, kind,
            staticmethod(lambda *a, _k=kind, **kw: seen.append((_k, a[1], a[2]))))
    return seen


@pytest.fixture(autouse=True)
def _stub_spec_builders(app, monkeypatch):
    """Spec building goes through the real geometry math, which is not what
    this file tests — same choice as tests/test_hydra_batch_ui.py."""
    spec = SimpleNamespace(Lsd=200_000.0, pxY=200.0, Wavelength=0.1729)
    monkeypatch.setattr(hydra_batch_widgets_mod, "spec_from_geometry_file",
                        lambda path, r_bin, e_bin, **kw: spec)
    monkeypatch.setattr(hydra_batch_widgets_mod, "_build_spec",
                        lambda result, r_bin, e_bin, **kw: spec)


def test_the_launched_argvs_carry_each_panels_own_caking_and_calibration(
        app, monkeypatch, tmp_path, dialogs):
    """One page, the whole plan.

    Asserts the step ORDER (panels of a scan, then that scan's combine), that
    each panel's argv carries ITS OWN calibration file and ITS OWN six caking
    values rather than a shared set, and — the part that closes the loop —
    that every argv produced actually parses with the real CLI parser and
    yields back the values that went in.
    """
    if not FIXTURE_DIR.exists():
        pytest.skip("test_data/gui_synthetic/hydra/ fixture not present")
    from midas_gui import batch_cli, hydra_combine_cli
    import midas_gui.job_queue as job_queue_mod

    captured = {}

    def _fake_launch(self, argv, *, name, total_frames, out_dir="", extra_argvs=None):
        captured.update(argv=argv, name=name, total_frames=total_frames,
                        out_dir=out_dir, extra=list(extra_argvs or []))
        return SimpleNamespace(session="midasgui_test_session")

    monkeypatch.setattr(job_queue_mod.JobQueuePanel, "launch", _fake_launch)
    # Redirect the run-log tree. Without this the test writes a real
    # screenlog into the user's ~/midas_runs/ every time it runs — a test
    # that litters the operator's own log tree is worse than no test.
    from midas_gui import run_log
    monkeypatch.setattr(run_log, "LOG_ROOT", tmp_path / "runlogs")

    page = HydraBatchPage()
    page._loader.set_path(str(FIXTURE_DIR / "ge1" / "panel.ge1.h5"))

    # Two panels, each "From file" with its own calibration and its own
    # wedge — ge1 deliberately straddling the ±180 cut, as it does at 1-ID-E.
    caking = {1: dict(R_MIN=10.0, R_MAX=900.0, R_STEP=1.0,
                      ETA_MIN=170.0, ETA_MAX=-170.0, ETA_STEP=5.0),
              2: dict(R_MIN=20.0, R_MAX=800.0, R_STEP=2.0,
                      ETA_MIN=-80.0, ETA_MAX=-10.0, ETA_STEP=10.0)}
    for n in (1, 2):
        page._cards[n]._json_ed.setText(str(FIXTURE_DIR / f"ge{n}" / f"ps_ge{n}.txt"))
        page._cards[n].set_caking(caking[n])
    page._out_ed.setText(str(tmp_path))
    for _k, _cb in page._fmt._checks.items():
        _cb.setChecked(_k == "zarr")
    if getattr(page, "_combine_zarr_chk", None) is not None:
        page._combine_zarr_chk.setChecked(True)

    page._run_as_job()
    assert captured, "no job was launched"
    # Loading ge1's file pulls in all four siblings, so ge3/ge4 are present
    # but uncalibrated. The in-process run skips such a panel and integrates
    # the rest (_skip_panel); the background path must agree rather than
    # refusing the whole job.
    assert not dialogs, f"a modal dialog aborted the job: {dialogs}"

    steps = [captured["argv"]] + captured["extra"]
    kinds = [s[2] for s in steps]      # [exe, "-m", module, ...]
    assert kinds[:2] == ["midas_gui.batch_cli"] * 2, kinds
    assert kinds[2] == "midas_gui.hydra_combine_cli", (
        "the combine must run after the panels of its scan, not before")

    # Every argv parses with the real parser — the loop this file exists for.
    parsed = {}
    for s in steps[:2]:
        a = batch_cli._build_arg_parser().parse_args(s[3:])
        parsed[Path(a.out_dir).name] = a
    assert set(parsed) == {"ge1", "ge2"}, parsed.keys()

    for n in (1, 2):
        a = parsed[f"ge{n}"]
        c = caking[n]
        assert a.calib_file.endswith(f"ps_ge{n}.txt"), "panels share a calibration"
        assert (a.eta_min, a.eta_max) == (c["ETA_MIN"], c["ETA_MAX"])
        assert (a.r_bin, a.eta_bin) == (c["R_STEP"], c["ETA_STEP"])
        assert (a.r_min, a.r_max) == (c["R_MIN"], c["R_MAX"])
        assert a.fmts == "zarr"

    # ge1's wedge survives intact rather than being silently normalised —
    # the whole reason eta needed its own flags.
    assert parsed["ge1"].eta_min > parsed["ge1"].eta_max

    # The radial bounds the job sends are the ones resolved_spec would use
    # in-process, including the rule that a falsy bound is OMITTED rather
    # than sent as a literal 0 (0.0 means "let the backend pick the farthest
    # corner"; --r-max 0 would integrate out to zero radius). Asserted
    # against the live caking rather than a hardcoded number because the
    # card re-derives Rmax from each panel's own calibration.
    for n in (1, 2):
        live = page._cards[n].caking()
        a = parsed[f"ge{n}"]
        assert a.r_max == (live["R_MAX"] or None)
        assert a.r_min == (live["R_MIN"] or None)

    # Every command that will run is recorded beside the data, in the logs/
    # folder the MPE SAXS/WAXS workflow established. ~/.midas_gui/jobs/ has
    # the same chain, but it is keyed by session name in a directory nobody
    # browses — this is the copy you can find from the dataset alone.
    beside = sorted((tmp_path / "logs").glob("*.commands.log"))
    assert len(beside) == 1, f"no command log beside the data: {list(tmp_path.iterdir())}"
    recorded = beside[0].read_text()
    assert "midasgui_test_session" in recorded
    assert recorded.count("midas_gui.batch_cli") == 2
    assert "midas_gui.hydra_combine_cli" in recorded
    # Skipped panels are named, so the log answers "why only two panels?"
    assert "ge3" in recorded and "ge4" in recorded

    # The same record is filed in the run-log tree, by experiment rather
    # than by session name.
    filed = list((tmp_path / "runlogs").rglob("*hydra_integrate*.screenlog"))
    assert len(filed) == 1, f"run-log tree entry missing: {filed}"
    assert "midas_gui.hydra_combine_cli" in filed[0].read_text()

    # Bin type reaches the job as the self-describing rebin trio. 2θ is the
    # point: this page had a Q-only checkbox until 2026-10-12 and could not
    # express 2θ-uniform output at all, while the single-detector tab could.
    page._bin_type.setCurrentIndex(2)          # 2θ
    assert page._rebin_unit() == "2th"
    captured.clear()
    page._run_as_job()
    a2 = batch_cli._build_arg_parser().parse_args(captured["argv"][3:])
    assert a2.rebin_unit == "2th"
    assert (a2.rebin_min, a2.rebin_max, a2.rebin_step) == (
        page._tth_min.value(), page._tth_max.value(), page._tth_bin.value())
    # Radial means no rebin at all, not a rebin with default numbers.
    page._bin_type.setCurrentIndex(0)
    captured.clear()
    page._run_as_job()
    assert "--rebin-unit" not in captured["argv"]

    # A project saved before Bin type existed carried a Q-only checkbox;
    # dropping it would silently reopen a Q run as Radial.
    page.set_state({"fields": {"q_check": True}})
    assert page._rebin_unit() == "Q", "legacy q_check did not migrate"
    assert page._q_min.isEnabled() and not page._tth_min.isEnabled(), (
        "restoring state must also re-grey the unused spin trio")
    page.set_state({"fields": {"q_check": False}})
    assert page._rebin_unit() is None
    # An explicit bin_type always wins over a stale legacy key.
    page.set_state({"fields": {"q_check": True, "bin_type": "2θ"}})
    assert page._rebin_unit() == "2th"

    # Multi-azimuth and the ion-CSV extras were absent from this page
    # entirely, so they could not be asked for on a Hydra run at all.
    page._bin_type.setCurrentIndex(0)            # Radial (see the guard below)
    page._multi_azimuth_chk.setChecked(True)
    page._ion_env_chk.setChecked(True)
    page._ion_motors_chk.setChecked(True)
    captured.clear()
    page._run_as_job()
    a3 = batch_cli._build_arg_parser().parse_args(captured["argv"][3:])
    assert a3.multi_azimuth is True
    assert a3.ion_csv_extras == "env,motors"

    # ...and the same incompatibility the single-detector tab enforces, so
    # it is caught before launch rather than in a detached job's log.
    page._bin_type.setCurrentIndex(1)            # Q
    captured.clear()
    dialogs.clear()
    page._run_as_job()
    assert not captured, "multi-azimuth + Q-uniform should not have launched"
    assert any("Incompatible" in str(d[1]) for d in dialogs), dialogs

    # "Preview: sum first N" — a display aid, and the last toolbar control
    # the single-detector tab had and this page lacked. Exercised against a
    # real multi-frame stack: the bundled fixture holds exactly ONE frame,
    # so it can only ever prove the overrun path.
    import h5py
    stack = tmp_path / "stack.h5"
    with h5py.File(stack, "w") as f:
        f.create_dataset("exchange/data",
                         data=np.ones((3, 4, 4), dtype=np.float32))
    page._preview_sum_n.setValue(1)
    assert float(np.sum(page._preview_frame(str(stack)))) == 16.0
    page._preview_sum_n.setValue(3)
    assert float(np.sum(page._preview_frame(str(stack)))) == 48.0
    # Asking for more frames than exist sums what is there rather than
    # raising -- _load_image raises IndexError past the end of the stack.
    page._preview_sum_n.setValue(999)
    assert float(np.sum(page._preview_frame(str(stack)))) == 48.0
    page._preview_sum_n.setValue(1)

    combine = hydra_combine_cli._build_arg_parser().parse_args(steps[2][3:])
    assert combine.panels == ["ge1", "ge2"], (
        "the combine must name only the panels that actually ran — naming a "
        "skipped one makes every frame look incomplete and merges nothing")
    assert Path(combine.out_dir) == Path(parsed["ge1"].out_dir).parent, (
        "the combine must run at scan level, beside the ge{n}/ folders")


def test_the_merged_view_tabs_exist_and_fill_from_a_merged_archive(app, tmp_path):
    """The Hydra page showed four per-panel results and had no view of the
    archive a Hydra run actually produces. These three tabs are that view;
    Per-panel results stays, because one panel at a time is still how you
    check a single panel's calibration.

    One page, per this file's header and the pyqtgraph teardown history.
    """
    import zarr
    from midas_gui import hydra_zarr_combine as hz

    page = HydraBatchPage()
    assert [page._top_tabs.tabText(i) for i in range(page._top_tabs.count())] == [
        "Per-panel results", "Detector view",
        "Waterfall", "Stacked profiles", "Eta-R cakes"]

    merged = tmp_path / "s.ge1234.ave.zarr.zip"
    store = zarr.ZipStore(str(merged), mode="w")
    z = zarr.group(store)
    osf = z.create_group("OmegaSumFrame")
    for i in range(3):
        osf[f"LastFrameNumber_{i}"] = np.full((6, 4), float(i + 1))
    rmap = np.zeros((5, 6, 4))
    rmap[0] = np.arange(6)[:, None] * 10.0
    rmap[2] = np.linspace(-175, 175, 4)[None, :]
    z["REtaMap"] = rmap
    store.close()

    page._populate_merged_views(str(merged))
    assert page._merged_shown == str(merged)
    log = page._log.toPlainText()
    assert "s.ge1234.ave.zarr.zip" in log

    # No calibration is loaded, so the R axis cannot be converted -- and the
    # note must say THAT, not blame panel distances, which is a different
    # situation that looks identical from the viewer.
    ctx, why = page._merged_axis_context()
    assert ctx is None and "no panel calibration" in why
    assert "no panel calibration" in log

    # A bad path is a log line, never an exception out of the combine step.
    page._populate_merged_views(str(tmp_path / "nope.zarr.zip"))
    assert "could not read back" in page._log.toPlainText()
