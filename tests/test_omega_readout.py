"""The two ω readouts: the cake summary's mapping line and the loader hint.

``start``/``end``/"Combine sub-frames" count raw sub-frames;
``OME_START``/``OME_STEP``/``OME_SUM`` assign degrees to those same
sub-frames. They are one axis in two coordinate systems, and the app used to
show each half in its own corner without ever relating them — so a user
reading "58 output frames" and "Δω 0.25°" had no way to see that consecutive
frames are 6.25° apart, and no way to notice that a loaded rotation had no
angles configured at all (the state that produced a run of all-zero
``/Omegas``).

These tests pin the correspondence itself, not the phrasing: each one asserts
the numbers that make the statement true. The angles are checked against
``cake_params.omega_for_window``'s own definition — the readout must not
become a second, independently-drifting implementation of the ω formula.

Qt import discipline per STATE.md: import inside fixtures, never at module
scope.
"""
import pytest

pytest.importorskip("PyQt5.QtWidgets")
h5py = pytest.importorskip("h5py")

DSET = "exchange/data"
N_RAW = 20
CHUNK = 5          # → 4 output frames per file


@pytest.fixture(scope="module")
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(qapp):
    from midas_gui.tab_batch import BatchTab
    return BatchTab()


@pytest.fixture
def stack(tmp_path):
    import numpy as np
    paths = []
    for stem in ("a", "b"):
        p = tmp_path / f"{stem}.h5"
        with h5py.File(p, "w") as h:
            h.create_dataset(DSET, data=np.ones((N_RAW, 8, 8), dtype=np.float32))
        paths.append(p)
    return paths


def _load(tab, cfg, *, start=0.0, step=0.0, chunk=CHUNK, channel="",
          collapse=False):
    """Point the tab at ``cfg`` and set the rotation fields, the way the GUI
    would. ``source_cfg`` is replaced rather than driven through the file
    pickers: the readout's input is the cfg, and what is being tested is what
    it makes of one."""
    tab._loader.source_cfg = lambda: cfg
    for w, v in ((tab._loader._combine_chunk, chunk),
                 (tab._ome_start, start), (tab._ome_step, step)):
        w.blockSignals(True); w.setValue(v); w.blockSignals(False)
    tab._ome_channel.blockSignals(True)
    tab._ome_channel.setEditText(channel)
    tab._ome_channel.blockSignals(False)
    tab._ome_collapse.blockSignals(True)
    tab._ome_collapse.setChecked(collapse)
    tab._ome_collapse.blockSignals(False)
    tab._recompute_omega_span()


def _one(path, chunk=CHUNK):
    return {"type": "hdf5", "path": str(path), "dataset": DSET,
            "chunk_size": chunk}


def _both(paths):
    return {"type": "hdf5_stack_glob", "paths": [str(p) for p in paths],
            "dataset": DSET, "chunk_size": CHUNK}


def _mapping(tab):
    """The summary's second line — the mapping — or "" if there isn't one."""
    lines = tab._cake_summary_text().splitlines()
    return lines[1].strip() if len(lines) > 1 else ""


# ── the rate the user cannot otherwise see ──────────────────────────────────

def test_the_summary_states_both_the_sub_frame_and_the_frame_rate(tab, stack):
    """Combining N sub-frames leaves consecutive OUTPUT frames N·OME_STEP
    apart. That multiplication is the single most confusable thing about
    these two coordinate systems, so both rates are stated side by side."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=5)
    head = tab._cake_summary_text().splitlines()[0]
    assert "Δω 0.25°/sub-frame" in head
    assert "= 1.25°/frame" in head          # 0.25 × 5


def test_no_frame_rate_is_claimed_when_nothing_is_combined(tab, stack):
    """With OME_SUM 1 the two rates are the same number, and printing it
    twice would imply a distinction that isn't there."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=1)
    head = tab._cake_summary_text().splitlines()[0]
    assert "Δω 0.25°/sub-frame" in head
    assert "/frame" not in head


def test_the_combine_box_is_named_as_ome_sum(tab, stack):
    """They are one widget, not two that happen to agree — the cake dialog
    edits this spin box directly. Saying so is the cheapest way to stop a
    user hunting for a separate OME_SUM field."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=5)
    assert "OME_SUM" in tab._cake_summary_text().splitlines()[0]


# ── the mapping line ────────────────────────────────────────────────────────

def test_the_mapping_names_the_raw_range_and_the_angles_it_produces(tab, stack):
    """The statement the whole change exists to make: these sub-frames become
    these angles. Both ends are computed with the run's own formula so the
    readout cannot drift from what gets written into the zarr."""
    from midas_gui.cake_params import omega_for_window
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=5)

    first, last = omega_for_window(5.0, 0.25, 0, 4), omega_for_window(5.0, 0.25, 15, 19)
    assert (first, last) == (5.5, 9.25)     # pinned, not just self-consistent
    line = _mapping(tab)
    assert f"sub-frames 0…{N_RAW - 1}" in line
    assert f"ω {first:g}°…{last:g}°" in line
    assert "4 frame(s)" in line


def test_a_start_end_filter_shifts_the_angles_it_reports(tab, stack):
    """start/end pick raw sub-frames, and ω is measured from sub-frame 0 of
    the file regardless — so dropping the first five must move the first
    angle by 5·OME_STEP, not leave it at OME_START."""
    from midas_gui.cake_params import omega_for_window
    cfg = dict(_one(stack[0]), frame_start=5, frame_end=19)
    _load(tab, cfg, start=5.0, step=0.25, chunk=5)

    line = _mapping(tab)
    assert "sub-frames 5…19" in line
    assert f"ω {omega_for_window(5.0, 0.25, 5, 9):g}°" in line   # 6.75, not 5.5
    assert "3 frame(s)" in line


def test_a_loaded_rotation_with_no_angles_set_says_so(tab, stack):
    """The state a real run was in when its ``/Omegas`` came out all zero:
    angles default to 0/0, ``omega_for_window`` returns a genuine 0.0 for
    them, and the output therefore looks fine. This line is the only place
    it becomes visible."""
    _load(tab, _one(stack[0]), start=0.0, step=0.0, chunk=5)
    line = _mapping(tab)
    assert "ω 0° on all 4 frames" in line
    assert "OME_START/OME_STEP not set" in line


def test_nothing_is_claimed_about_angles_that_are_read_from_a_channel(tab, stack):
    """A measured channel supplies one angle per frame at run time. The
    readout can name the channel; inventing a range from the unused
    OME_START/OME_STEP would be a lie."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25, channel="/measurement/omega")
    line = _mapping(tab)
    assert "/measurement/omega" in line
    assert "5.5" not in line


def test_the_averaged_override_reports_one_angle_for_the_whole_run(tab, stack):
    """"These images were averaged or summed" collapses the run to a single
    exposure, so the mapping is one angle, not a range."""
    from midas_gui.cake_params import omega_for_window
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=5, collapse=True)
    line = _mapping(tab)
    assert f"one ω {omega_for_window(5.0, 0.25, 0, N_RAW - 1):g}°" in line  # 7.375
    assert "…" in line.split("→")[0]        # the raw range is still stated


# ── multi-file: each file is its own rotation ───────────────────────────────

def test_a_multi_file_pick_reports_the_range_per_file(tab, stack):
    """Each HDF5 sub-frame stack is one rotation and restarts at OME_START
    (see ``workers._HDF5StackGlobSource.omega_channel_window``), so a flat
    "sub-frames 0…39 → ω 5°…15°" would claim a continuous ramp this run does
    not produce. Frame COUNT is still run-wide: 8 frames over two files."""
    _load(tab, _both(stack), start=5.0, step=0.25, chunk=5)
    line = _mapping(tab)
    assert f"sub-frames 0…{N_RAW - 1} of each file" in line
    assert "ω 5.5°…9.25° in every file" in line
    assert "8 frame(s)" in line
    assert "restarts at OME_START" in line


# ── the loader hint ─────────────────────────────────────────────────────────

def test_the_loader_hint_carries_the_same_angles(tab, stack):
    """Second half of "both places": the range hint sits right under the
    start/end boxes, which is where the sub-frame coordinate system is
    actually operated."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=5)
    tab._loader._set_frame_hint("Single file, 20 raw sub-frame(s).")
    assert tab._loader._fr_hint.text() == \
        "Single file, 20 raw sub-frame(s).  → ω 5.5°…9.25°."


def test_editing_an_angle_updates_the_hint_without_reloading(tab, stack):
    """The windows are cached on the source; the angles are arithmetic over
    them. Dragging OME_START must therefore re-render both readouts without
    touching the filesystem — which is also why the hint function is required
    to be cheap."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=5)
    tab._loader._set_frame_hint("base.")
    tab._loader.source_cfg = lambda: (_ for _ in ()).throw(
        AssertionError("the source must not be reopened for an angle edit"))
    tab._ome_start.setValue(105.0)
    assert "ω 105.5°…109.25°." in tab._loader._fr_hint.text()
    assert "ω 105.5°…109.25°" in _mapping(tab)


def test_a_panel_with_no_omega_owner_gets_the_hint_it_always_had(qapp):
    """``DataLoaderPanel`` is shared with tabs that know nothing about
    rotation. Unset, the tail must add not even a space — this is the
    regression that would quietly reformat every other tab's hint."""
    from midas_gui.widgets import DataLoaderPanel
    panel = DataLoaderPanel(mode="stream", unify_combine=True)
    panel._set_frame_hint("Single file (scan point 000000).")
    assert panel._fr_hint.text() == "Single file (scan point 000000)."


def test_an_empty_hint_stays_empty(qapp, tab, stack):
    """No source described means no range to qualify; a bare angle clause
    under an empty hint reads as an error message."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25, chunk=5)
    tab._loader._set_frame_hint("")
    assert tab._loader._fr_hint.text() == ""


def test_a_hint_function_that_raises_cannot_break_the_hint(qapp):
    """It runs on every re-render, including while a tab is still being
    built. A hint is never worth taking the panel down for."""
    from midas_gui.widgets import DataLoaderPanel
    panel = DataLoaderPanel(mode="stream", unify_combine=True)
    panel.set_omega_hint_fn(lambda: 1 / 0)
    panel._set_frame_hint("Single file.")
    assert panel._fr_hint.text() == "Single file."


# --- cost of the readout -------------------------------------------------
#
# The span walk opens every file's HDF5 header on the GUI thread. That is
# affordable once per source change and unaffordable per signal: dataChanged
# and the "Combine sub-frames" valueChanged both fire repeatedly for one user
# action (and once per step while the spin box is held down), and a folder
# pick multiplies each firing by the number of files. These two tests pin the
# two defences — coalesce the burst, and do nothing at all when the source
# has not actually changed.

def test_a_repeat_signal_for_an_unchanged_source_does_no_work(tab, stack):
    """The second refresh must not re-open anything: same cfg, same windows."""
    import midas_gui.workers as wk

    cfg = _one(stack[0])
    _load(tab, cfg, start=5.0, step=0.25)
    before = dict(tab._omega_span)

    real, calls = wk._open_source_cfg, []

    def counted(c):
        calls.append(c)
        return real(c)

    wk._open_source_cfg = counted
    try:
        tab._recompute_omega_span()
        tab._recompute_omega_span()
    finally:
        wk._open_source_cfg = real
    assert calls == []
    assert tab._omega_span == before


def test_a_changed_source_is_picked_up_despite_the_short_circuit(tab, stack):
    """The cheap path must not become a stale one — a real change still walks."""
    _load(tab, _one(stack[0]), start=5.0, step=0.25)
    assert tab._omega_span["n"] == N_RAW // CHUNK
    # Same file, twice the chunk: half as many output frames, wider windows.
    _load(tab, _one(stack[0], chunk=CHUNK * 2), start=5.0, step=0.25,
          chunk=CHUNK * 2)
    assert tab._omega_span["n"] == N_RAW // (CHUNK * 2)
    assert tab._omega_span["first"] == (0, CHUNK * 2 - 1)


def test_scheduling_defers_the_walk_instead_of_running_it_inline(tab, stack):
    """``_schedule_omega_span`` is what the signals are wired to; it must
    return without touching the filesystem, or the coalescing buys nothing."""
    import midas_gui.workers as wk

    tab._loader.source_cfg = lambda: _one(stack[0])
    real, calls = wk._open_source_cfg, []

    def counted(c):
        calls.append(c)
        return real(c)

    wk._open_source_cfg = counted
    try:
        for _ in range(5):
            tab._schedule_omega_span()
        assert calls == []            # nothing yet — all five coalesced
        assert tab._omega_span is None
        tab._omega_span_timer.timeout.emit()      # what the timer will do
        assert len(calls) == 1
    finally:
        wk._open_source_cfg = real
    assert tab._omega_span["n"] == N_RAW // CHUNK
