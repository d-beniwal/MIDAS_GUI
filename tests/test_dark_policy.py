"""The dark decision: which dark a given input file gets, and why.

Plain pytest — ``dark_policy`` imports no Qt, and keeping it that way is
the point of the split. No forked marker, no ``QApplication``.

The behaviour under test is the one that was wrong: Batch Correction and
Batch Integrate answered this differently, so the same selection produced
different corrections depending on which tab ran it.
"""
from __future__ import annotations

import numpy as np
import pytest

from midas_gui import dark_policy as D


def _req(**kw):
    return D.DarkRequest(**kw)


# ── which mode a pick implies ───────────────────────────────────────────

@pytest.mark.parametrize("origin,auto", [
    ("file", False),
    ("files", False),
    ("folder", False),
    ("folder_recursive", True),
    ("stem", True),
])
def test_the_pick_decides_the_mode(origin, auto):
    """The whole rule, in one table. A traversal resolves per file; an
    explicit pick uses the Dark field."""
    assert D.wants_auto(_req(origin=origin)) is auto


def test_a_stem_pick_autodetects_even_though_it_is_a_flat_list():
    """It globs <folder>/**/<stem>* and crosses subfolder boundaries
    whatever the "Include subfolders" box says, so it is a traversal
    however flat the resulting list looks."""
    assert D.wants_auto(_req(origin="stem"))
    assert "stem" in D.AUTO_ORIGINS


def test_every_origin_the_loader_can_report_is_known_here():
    """Guards the seam: a new pick mode added to the loader must be
    classified deliberately, not default to explicit by omission."""
    assert D.ORIGINS == {"file", "files", "folder", "folder_recursive", "stem"}


def test_the_override_beats_the_pick_both_ways():
    assert D.wants_auto(_req(origin="file", override=D.ALWAYS_AUTO))
    assert not D.wants_auto(
        _req(origin="folder_recursive", override=D.ALWAYS_EXPLICIT))


def test_the_mode_is_describable_without_running_anything():
    """The UI must be able to state the mode before a run, or the user
    finds out which dark was used by reading the log afterwards."""
    assert "per file" in D.describe(_req(origin="folder_recursive"))
    assert "every file" in D.describe(_req(origin="file", path="/d/dk.h5"))
    assert "dk.h5" in D.describe(_req(origin="file", path="/d/dk.h5"))


# ── resolution ──────────────────────────────────────────────────────────

def _vrx(path, *, data=None, dark=None, shape=(4, 5)):
    import h5py
    path.parent.mkdir(parents=True, exist_ok=True)
    ramp = np.linspace(-0.5, 0.5, shape[0] * shape[1],
                       dtype=np.float32).reshape(shape)
    ramp -= ramp.mean()
    with h5py.File(str(path), "w") as f:
        if data is not None:
            f.create_dataset("exchange/data",
                             data=np.full((2,) + shape, float(data),
                                          np.float32) + ramp[None])
        if dark is not None:
            f.create_dataset("exchange/data_dark",
                             data=np.full((2,) + shape, float(dark),
                                          np.float32) + ramp[None])
    return path


def test_auto_mode_with_nothing_resolvable_skips_rather_than_falling_back(
        tmp_path):
    """The load-bearing one.

    resolve_dark returns at its rung 3 whenever a fallback is passed and so
    never reaches "no dark found" — so handing it the GUI dark as a safety
    net would silently convert "I could not find this file's dark" into "I
    used some other scan's dark". In a traversal that is exactly the error
    that must not be allowed to pass unnoticed.
    """
    data = _vrx(tmp_path / "scan_000020.vrx.h5", data=100.0)
    got = D.resolve(data, _req(origin="folder_recursive",
                               array=np.full((4, 5), 6.0, np.float32)),
                    shape=(4, 5))
    assert got.skipped
    assert got.array is None
    assert "scan_000020.vrx.h5" in got.why


def test_auto_mode_finds_the_sibling_dark(tmp_path):
    _vrx(tmp_path / "scan_dark_before_000019.vrx.h5", dark=3.0)
    data = _vrx(tmp_path / "scan_000020.vrx.h5", data=100.0)
    got = D.resolve(data, _req(origin="folder_recursive"), shape=(4, 5))
    assert not got.skipped
    assert np.allclose(got.array.mean(), 3.0)
    assert "dark_before_000019" in got.why


def test_explicit_mode_ignores_a_sibling_dark_entirely(tmp_path):
    """The user named a dark; a file that happens to sit next to a
    dark_before must not quietly override it."""
    _vrx(tmp_path / "scan_dark_before_000019.vrx.h5", dark=3.0)
    data = _vrx(tmp_path / "scan_000020.vrx.h5", data=100.0)
    got = D.resolve(data, _req(origin="files",
                               array=np.full((4, 5), 6.0, np.float32)),
                    shape=(4, 5))
    assert np.allclose(got.array, 6.0)
    assert not got.skipped


def test_explicit_mode_with_no_dark_set_runs_without_one(tmp_path):
    """Correcting without a dark is legitimate — it just has to be said."""
    data = _vrx(tmp_path / "scan_000020.vrx.h5", data=100.0)
    got = D.resolve(data, _req(origin="file"), shape=(4, 5))
    assert got.array is None and not got.skipped
    assert "without one" in got.why


def test_a_zero_placeholder_does_not_satisfy_auto_mode(tmp_path):
    """The white_oct26 shape: the file's own data_dark is all zeros, so
    there is genuinely no dark and the file must be skipped rather than
    corrected with zeros."""
    data = _vrx(tmp_path / "scan_000020.vrx.h5", data=100.0)
    import h5py
    with h5py.File(str(data), "a") as f:
        if "exchange/data_dark" in f:
            del f["exchange/data_dark"]
        f.create_dataset("exchange/data_dark",
                         data=np.zeros((2, 4, 5), np.float32))
    got = D.resolve(data, _req(origin="folder_recursive"), shape=(4, 5))
    assert got.skipped, got.why


# ── Hydra: one panel's choice, every panel's path ───────────────────────

@pytest.mark.parametrize("src,want", [
    # the token appears TWICE in a real path — directory and extension
    ("/d/white/ge1/SCAN/SCAN_005826.ge1.h5", "/d/white/ge2/SCAN/SCAN_005826.ge2.h5"),
    # case preserved: helpers._HYDRA_PANEL_RE lowercases, producing a path
    # that does not exist on a case-sensitive filesystem
    ("/d/GE1/x.GE1.h5", "/d/GE2/x.GE2.h5"),
    # an underscore-prefixed token: helpers._HYDRA_PANEL_RE's leading \b
    # misses this, so it rewrote only the directory half
    ("/d/ge1/scan_ge1.instr.txt", "/d/ge2/scan_ge2.instr.txt"),
])
def test_panel_substitution_rewrites_every_token(src, want):
    assert D.substitute_panel(src, 2) == want


@pytest.mark.parametrize("src", [
    "/d/average1/scan.h5",      # hydra.PANEL_IN_NAME rewrites this one
    "/d/edge2/scan.h5",
    "/d/image3/scan.h5",
    "/d/ge12/scan.h5",          # ge12 is not panel 1
])
def test_panel_substitution_leaves_lookalikes_alone(src):
    out = D.substitute_panel(src, 4)
    assert out is None or "ge4" not in out.replace("/d/", "", 1).split("/")[0]


def test_a_path_with_no_panel_token_cannot_be_derived():
    assert D.substitute_panel("/d/darks/dark_mean.tif", 3) is None


def test_a_missing_sibling_panel_is_a_skip_naming_the_path(tmp_path):
    """Never another panel's dark. Applying ge1's dark to ge2 data is the
    mistake this exists to prevent, so it fails rather than falls back."""
    (tmp_path / "ge1").mkdir()
    anchor = tmp_path / "ge1" / "dark.ge1.h5"
    anchor.write_bytes(b"")
    path, why = D.panel_dark_path(str(anchor), 2)
    assert path is None
    assert "ge2" in why and "dark.ge2.h5" in why


def test_a_present_sibling_panel_resolves(tmp_path):
    for n in (1, 2):
        (tmp_path / f"ge{n}").mkdir()
        (tmp_path / f"ge{n}" / f"dark.ge{n}.h5").write_bytes(b"")
    path, why = D.panel_dark_path(str(tmp_path / "ge1" / "dark.ge1.h5"), 2)
    assert path == str(tmp_path / "ge2" / "dark.ge2.h5"), why


def test_an_anchor_with_no_token_says_so_rather_than_guessing(tmp_path):
    """A dark in a shared folder with no panel token is a real setup; the
    message must distinguish it from a missing sibling."""
    p = tmp_path / "dark_mean.tif"
    p.write_bytes(b"")
    path, why = D.panel_dark_path(str(p), 2)
    assert path is None
    assert "carries no ge1-ge4 token" in why
