"""Pytest configuration for midas-gui.

Force Qt into offscreen mode so the GUI smoke tests run headless (CI, servers,
release.sh) with no X server / display, and silence the OpenMP duplicate-lib
abort on macOS (same workaround the MIDAS packages use).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def force_rmtree(path) -> None:
    """``shutil.rmtree`` that also copes with directories made read-only.

    Tests that exercise "can't write here" make a directory ``r-x`` on
    purpose. ``rmtree(ignore_errors=True)`` then quietly gives up and leaves
    it in /tmp — the tests pass and the litter accumulates, which is exactly
    what the working-directory work is about not doing. Restore write
    permission on the way down, then remove.
    """
    import shutil
    for root, dirs, _files in os.walk(path):
        for name in dirs:
            try:
                os.chmod(os.path.join(root, name), 0o700)
            except OSError:
                pass
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    shutil.rmtree(path, ignore_errors=True)
