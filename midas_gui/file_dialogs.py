"""File dialogs whose sidebar always lists every mounted external volume.

The native macOS/Linux dialog can hide external SSDs / USB sticks / network
shares (Finder "External disks" sidebar off, removable-volume permission, or
Qt's own Linux dialog, which has no volume list).  ``install()`` swaps the
``QFileDialog`` static helpers used across the GUI for a Qt-widget dialog
whose sidebar is rebuilt on every open from the volumes mounted *right now*,
so any drive works with no per-device configuration.  Windows keeps the native
dialog, which already lists drives.
"""
import os
import sys

from PyQt5 import QtCore, QtWidgets

_MOUNT_ROOTS = ("/Volumes", "/media", "/run/media", "/mnt")
_installed = False
_orig = {}


def external_volume_paths():
    """Mount points of external / removable / network volumes, detected live."""
    found = []

    def add(p):
        if p and p != "/" and os.path.isdir(p) and p not in found:
            found.append(p)

    try:
        for v in QtCore.QStorageInfo.mountedVolumes():
            p = v.rootPath()
            if v.isValid() and v.isReady() and (
                    sys.platform.startswith("win")
                    or any(p.startswith(r + "/") for r in _MOUNT_ROOTS)):
                add(p)
    except Exception:
        pass
    for root in _MOUNT_ROOTS:  # catches shares QStorageInfo misses; skips boot-disk symlink
        try:
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if not os.path.islink(p):
                    add(p)
        except OSError:
            pass
    return found


def location_entries():
    """[(label, path)] for Home/Desktop/Documents/Downloads + live external volumes."""
    out = []
    for label, loc in (("Home", QtCore.QStandardPaths.HomeLocation),
                       ("Desktop", QtCore.QStandardPaths.DesktopLocation),
                       ("Documents", QtCore.QStandardPaths.DocumentsLocation),
                       ("Downloads", QtCore.QStandardPaths.DownloadLocation)):
        p = QtCore.QStandardPaths.writableLocation(loc)
        if p and os.path.isdir(p):
            out.append((label, p))
    for p in external_volume_paths():
        out.append((os.path.basename(p.rstrip("/\\")) or p, p))
    return out


class LocationsPanel(QtWidgets.QListWidget):
    """Clickable side list of common places + mounted drives.

    Re-scans every 2 s while visible, so drives plugged in or ejected with the
    dialog open appear/disappear without reopening it."""
    pathChosen = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(150)
        self.setTextElideMode(QtCore.Qt.ElideMiddle)
        self._entries = None
        self.itemClicked.connect(
            lambda it: self.pathChosen.emit(it.data(QtCore.Qt.UserRole)))
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        self._timer.start()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def refresh(self):
        entries = location_entries()
        if entries == self._entries:
            return
        self._entries = entries
        self.clear()
        for label, path in entries:
            it = QtWidgets.QListWidgetItem(label)
            it.setData(QtCore.Qt.UserRole, path)
            it.setToolTip(path)
            self.addItem(it)


def _sidebar_urls():
    urls = []
    for loc in (QtCore.QStandardPaths.HomeLocation,
                QtCore.QStandardPaths.DesktopLocation,
                QtCore.QStandardPaths.DocumentsLocation,
                QtCore.QStandardPaths.DownloadLocation):
        p = QtCore.QStandardPaths.writableLocation(loc)
        if p and os.path.isdir(p):
            urls.append(QtCore.QUrl.fromLocalFile(p))
    urls += [QtCore.QUrl.fromLocalFile(p) for p in external_volume_paths()]
    return urls


def _make(parent, caption, directory, filt, initial_filter, options):
    dlg = QtWidgets.QFileDialog(parent, caption)
    dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
    if options:
        dlg.setOptions(dlg.options() | options)
    dlg.setSidebarUrls(_sidebar_urls())
    if directory:
        if os.path.isdir(directory):
            dlg.setDirectory(directory)
        else:
            dlg.setDirectory(os.path.dirname(directory) or ".")
            dlg.selectFile(os.path.basename(directory))
    if filt:
        dlg.setNameFilter(filt)
        if initial_filter:
            dlg.selectNameFilter(initial_filter)
    return dlg


def _file(mode, accept, multi=False):
    def fn(parent=None, caption="", directory="", filter="", initialFilter="",
           options=QtWidgets.QFileDialog.Options()):
        dlg = _make(parent, caption, directory, filter, initialFilter, options)
        dlg.setFileMode(mode)
        dlg.setAcceptMode(accept)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return ([] if multi else ""), ""
        files = dlg.selectedFiles()
        return (files if multi else (files[0] if files else "")), dlg.selectedNameFilter()
    return fn


def _get_existing_directory(parent=None, caption="", directory="",
                            options=QtWidgets.QFileDialog.ShowDirsOnly):
    dlg = _make(parent, caption, directory, "", "", options)
    dlg.setFileMode(QtWidgets.QFileDialog.Directory)
    dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly,
                  bool(options & QtWidgets.QFileDialog.ShowDirsOnly))
    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return ""
    files = dlg.selectedFiles()
    return files[0] if files else ""


def install():
    """Patch QFileDialog's static helpers (idempotent; no-op on Windows)."""
    global _installed
    if _installed or sys.platform.startswith("win"):
        return
    QFD = QtWidgets.QFileDialog
    for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName",
                 "getExistingDirectory"):
        _orig[name] = getattr(QFD, name)
    QFD.getOpenFileName = staticmethod(_file(QFD.ExistingFile, QFD.AcceptOpen))
    QFD.getOpenFileNames = staticmethod(_file(QFD.ExistingFiles, QFD.AcceptOpen, multi=True))
    QFD.getSaveFileName = staticmethod(_file(QFD.AnyFile, QFD.AcceptSave))
    QFD.getExistingDirectory = staticmethod(_get_existing_directory)
    _installed = True
