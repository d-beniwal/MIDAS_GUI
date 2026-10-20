"""Which dark a given input file gets — the decision, with no Qt attached.

Batch Correction and Batch Integrate used to answer this differently and
nothing said so: ``BatchCorrectionWorker`` resolved a dark per input file
through ``frame_correct.resolve_dark``, while ``BatchWorker`` (caking) took
whatever array the GUI handed it and applied that to every file. The same
selection therefore produced different corrections depending on which tab
ran it. This module is the single answer both of them, and both background
CLIs, now ask.

It lives apart from the workers for the reason ``queue_policy`` does: it is
a decision that is easy to get subtly wrong in the middle of a QThread, and
it has to be reachable identically from two threads and two command-line
entry points, none of which should need a ``QApplication`` to find out
which dark to subtract.

The rule is a function of HOW THE DATA WAS PICKED
-------------------------------------------------
Explicit picks use the Dark field as chosen. Picks that traverse
subfolders resolve a dark per file, because one dark cannot be right for a
tree of scans measured at different times:

====================  =========================================
pick                  dark
====================  =========================================
one file              the Dark field
multiple files        the Dark field
a folder (flat)       the Dark field
a folder, recursive   resolved per file
files sharing a stem  resolved per file
====================  =========================================

A stem match is on the autodetect side because it globs
``<folder>/**/<stem>*`` and crosses subfolder boundaries whatever the
"Include subfolders" box says.

Unresolvable in autodetect mode is a SKIP
------------------------------------------
Not a fallback. ``resolve_dark`` returns at its rung 3 whenever a
``fallback`` is passed and so never reaches "no dark found" — which means
handing it the GUI dark as a safety net silently converts "I could not find
this file's dark" into "I used some other scan's dark". In autodetect mode
this module passes ``fallback=None`` on purpose, and the caller is expected
to skip the file and report it rather than correct it with a guess.

Nothing here imports Qt. Keep it that way.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

#: Picks that traverse subfolders, and so resolve a dark per file.
AUTO_ORIGINS = frozenset({"folder_recursive", "stem"})

#: Every origin ``DataLoaderPanel.pick_origin`` can report.
ORIGINS = frozenset({"file", "files", "folder"}) | AUTO_ORIGINS

#: ``override`` values. ``None`` means "decide from the pick".
ALWAYS_EXPLICIT = "always_explicit"
ALWAYS_AUTO = "always_auto"


@dataclass(frozen=True)
class DarkRequest:
    """What the GUI knows, independent of any one input file."""
    origin: str = "file"
    path: Optional[str] = None          #: the Dark field's file, if any
    dataset: str = "exchange/data_dark"  #: container searched inside it
    array: Optional[np.ndarray] = None   #: the Dark field, already averaged
    panel: Optional[int] = None          #: Hydra panel, for the message
    override: Optional[str] = None


@dataclass(frozen=True)
class DarkChoice:
    """``status`` is ``"ok"`` or ``"skipped"``; ``why`` is always sayable."""
    array: Optional[np.ndarray]
    why: str
    status: str = "ok"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


def wants_auto(req: DarkRequest) -> bool:
    """Should this request resolve a dark per file?"""
    if req.override == ALWAYS_AUTO:
        return True
    if req.override == ALWAYS_EXPLICIT:
        return False
    return req.origin in AUTO_ORIGINS


def describe(req: DarkRequest) -> str:
    """One line for the UI, so the mode is never a guess."""
    if wants_auto(req):
        return ("Dark: resolved per file (the selection spans subfolders)"
                if req.override is None else
                "Dark: resolved per file (forced)")
    which = os.path.basename(req.path) if req.path else "none set"
    return (f"Dark: the Dark field for every file — {which}"
            if req.override is None else
            f"Dark: the Dark field for every file, forced — {which}")


def resolve(file_path, req: DarkRequest, *, shape=None) -> DarkChoice:
    """The dark for one input file.

    In explicit mode the sibling ladder is skipped entirely
    (``auto=False``) and the Dark field is used as given. In autodetect
    mode the ladder runs with **no fallback**, so a file it cannot resolve
    comes back ``skipped`` instead of quietly inheriting another scan's
    dark.
    """
    from midas_gui.frame_correct import resolve_dark

    if wants_auto(req):
        arr, why = resolve_dark(file_path, dark_dataset=req.dataset,
                                shape=shape, fallback=None, auto=True)
        if arr is None:
            return DarkChoice(None, _skip_reason(file_path, req), "skipped")
        return DarkChoice(arr, why)

    if req.array is None:
        # Explicit mode with nothing chosen is a legitimate run — correcting
        # without a dark is allowed, it just has to be said out loud.
        return DarkChoice(None, "no Dark field set — correcting without one")
    arr, why = resolve_dark(file_path, dark_dataset=req.dataset, shape=shape,
                            fallback=req.array, auto=False)
    return DarkChoice(arr, why)


def _skip_reason(file_path, req: DarkRequest) -> str:
    panel = f" [{panel_token(req.panel)}]" if req.panel else ""
    return (f"no dark found for {os.path.basename(str(file_path))}{panel} "
            f"in {os.path.dirname(str(file_path))} — file skipped")


# ─────────────────────────────────────────── Hydra panel substitution ──
#: A panel token that is a whole path component or a dot-delimited segment.
#: Anchored on BOTH sides so ``average1/``, ``edge2/`` and ``image3`` are
#: left alone — the existing ``hydra.PANEL_IN_NAME`` rewrites those — while
#: still matching ``scan_ge1.h5``, which ``helpers._HYDRA_PANEL_RE``'s
#: leading ``\b`` misses because ``_`` is a word character.
_PANEL_TOKEN = re.compile(r"(?<![0-9A-Za-z])(ge)\s*([1-4])(?![0-9A-Za-z])",
                          re.IGNORECASE)


def panel_token(n) -> str:
    return f"ge{int(n)}"


def substitute_panel(path, n) -> Optional[str]:
    """Rewrite every panel token in ``path`` to panel ``n``.

    Case-preserving, and applied to the WHOLE path: a real file carries the
    token twice —
    ``/data/white_oct26/ge1/SCAN/SCAN_005826.ge1.h5`` — once as a directory
    and once in the extension, and rewriting only one half yields a path
    that does not exist.

    Returns ``None`` when the path carries no panel token at all, which is
    the caller's signal that a structural substitution is not available
    (rather than that the sibling is missing).
    """
    text = str(path)
    out, count = _PANEL_TOKEN.subn(
        lambda m, _n=int(n): m.group(1) + str(_n), text)
    return out if count else None


def panel_dark_path(anchor, n) -> tuple:
    """``(path, why)`` for panel ``n``'s dark, derived from ``anchor``.

    ``path`` is None when the structure cannot be derived or the derived
    file does not exist — never another panel's file. Applying ge1's dark
    to ge2 data is the specific mistake this function exists to prevent, so
    it fails rather than falls back.
    """
    if not anchor:
        return None, "no dark anchor path set"
    cand = substitute_panel(anchor, n)
    if cand is None:
        return None, (f"{os.path.basename(str(anchor))} carries no ge1-ge4 "
                      f"token, so {panel_token(n)}'s dark cannot be derived "
                      f"from it")
    if not os.path.isfile(cand):
        return None, f"{panel_token(n)} dark not found: {cand}"
    return cand, f"{panel_token(n)} dark derived from the anchor: {cand}"
