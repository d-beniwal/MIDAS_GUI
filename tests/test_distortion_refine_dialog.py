"""``DistortionRefineDialog`` must not let the user select an unpaired
distortion amplitude/phase — the backend's own resolver
(``midas_calibrate_v2.forward.distortion.resolve_distortion_block``, called
inside ``calibrate()``) raises on exactly that: "an amplitude without its
phase is not a meaningful degree of freedom, and a phase without its
amplitude has zero gradient." Letting the GUI produce that selection was just
a deferred, less legible version of the same error. The three isotropic
terms (iso_R2/4/6) carry no such constraint and stay independently
toggleable.
"""
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_ticking_amplitude_ticks_its_phase(app):
    from midas_gui.dialogs import DistortionRefineDialog

    dlg = DistortionRefineDialog(selected=set())
    assert not dlg._boxes["phi3"].isChecked()
    dlg._boxes["a3"].setChecked(True)
    assert dlg._boxes["phi3"].isChecked()
    assert dlg.selected() == {"a3", "phi3"}


def test_ticking_phase_ticks_its_amplitude(app):
    from midas_gui.dialogs import DistortionRefineDialog

    dlg = DistortionRefineDialog(selected=set())
    dlg._boxes["phi5"].setChecked(True)
    assert dlg._boxes["a5"].isChecked()
    assert dlg.selected() == {"a5", "phi5"}


def test_unticking_either_half_unticks_the_pair(app):
    from midas_gui.dialogs import DistortionRefineDialog

    dlg = DistortionRefineDialog(selected={"a2", "phi2"})
    dlg._boxes["a2"].setChecked(False)
    assert not dlg._boxes["phi2"].isChecked()
    assert dlg.selected() == set()


def test_isotropic_terms_stay_independent(app):
    """iso_R2/4/6 carry no amplitude/phase constraint — each toggles alone."""
    from midas_gui.dialogs import DistortionRefineDialog

    dlg = DistortionRefineDialog(selected=set())
    dlg._boxes["iso_R2"].setChecked(True)
    assert dlg._boxes["iso_R4"].isChecked() is False
    assert dlg._boxes["iso_R6"].isChecked() is False
    assert dlg.selected() == {"iso_R2"}


def test_presets_still_apply_cleanly(app):
    """Presets already pair amplitude+phase (constants.DISTORTION_PRESETS);
    applying one must not be disturbed by the new pairing wiring."""
    from midas_gui.dialogs import DistortionRefineDialog
    from midas_gui.constants import DISTORTION_PRESETS

    dlg = DistortionRefineDialog(selected=set())
    dlg._apply_preset(DISTORTION_PRESETS["Iso + up to 2-fold"])
    assert dlg.selected() == set(DISTORTION_PRESETS["Iso + up to 2-fold"])


def test_constructing_with_a_preselected_unpaired_set_is_not_auto_fixed(app):
    """The dialog only enforces pairing on user interaction (toggled signals
    don't fire from the constructor's own setChecked calls in a way that
    would silently rewrite a caller-supplied selection) — this documents
    that a caller must not pass an unpaired set in the first place; it is
    not a defence against one.
    """
    from midas_gui.dialogs import DistortionRefineDialog

    dlg = DistortionRefineDialog(selected={"a1"})
    assert dlg._boxes["a1"].isChecked()
    assert not dlg._boxes["phi1"].isChecked()
