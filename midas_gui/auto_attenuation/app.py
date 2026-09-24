"""Entry point for the Auto Attenuation popup process.

Invoked as ``python -m midas_gui.auto_attenuation.app --snapshot <path>``
by the main GUI (``midas_gui.app._open_auto_attenuation``). Builds its own
``QApplication`` and window — a genuinely separate OS process from the
main GUI, so it survives the main GUI closing and can't be brought down
by a crash in it (or vice versa).
"""

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Auto Attenuation")
    parser.add_argument("--snapshot", required=True,
                        help="Path to a .npz snapshot written by the main GUI")
    args = parser.parse_args(argv)

    from midas_gui.auto_attenuation.snapshot import load_snapshot
    snapshot = load_snapshot(args.snapshot)

    from midas_gui import helpers
    helpers.apply_ui_scale()   # must precede QApplication construction

    from PyQt5 import QtWidgets
    from midas_gui import style as S
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog

    app = QtWidgets.QApplication(sys.argv[:1])
    S.apply_theme(app, helpers._make_checkmark_svg(),
                  helpers._make_arrow_svg("up"), helpers._make_arrow_svg("down"))
    # The snapshot file is kept (not deleted here, unlike before) so the
    # window's "Refresh from Data Viewer" can ask the main GUI to overwrite
    # it in place — see refresh_server.py. AutoAttenuationDialog removes it
    # on close.
    win = AutoAttenuationDialog(snapshot, snapshot_path=args.snapshot)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
