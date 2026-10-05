"""Solve Cell backend — deterministic unit-cell-solving pipeline.

Deliberately free of any ``midas_gui``/PyQt5 imports (see ``pipeline.py``) so
this package can be lifted into a standalone ``midas_solve_cell`` package
later with a plain directory move, matching this repo's other MIDAS backends
(``midas_calibrate_v2``, ``midas_hkls``, ...).
"""
