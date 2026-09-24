"""Attenuator position -> Cu thickness lookup.

Default table ported from pyAutoBeam's ``attenuation/attenuator.py``. This
specific 12-position Cu foil set is whatever beamline pyAutoBeam was built
for — it is a reasonable default, but a different beamline's foil set
should be entered in the Auto Attenuation dialog's Advanced section rather
than requiring a code change, so callers pass a table (position -> mm)
around rather than importing the module-level default directly everywhere.
"""

# Attenuator position -> Cu thickness (mm). Position 0 = no attenuator.
DEFAULT_POSITION_THICKNESS_MM = {
    0: 0.00,
    1: 0.50,
    2: 1.00,
    3: 1.50,
    4: 2.00,
    5: 2.39,
    6: 4.78,
    7: 7.14,
    8: 9.53,
    9: 11.91,
    10: 14.30,
    11: 16.66,
}


def thickness_from_pos(pos, table=None):
    """Map an attenuator position index to Cu thickness in mm.

    Returns ``None`` for positions not in *table* (defaults to
    :data:`DEFAULT_POSITION_THICKNESS_MM`).
    """
    if table is None:
        table = DEFAULT_POSITION_THICKNESS_MM
    return table.get(pos, None)
