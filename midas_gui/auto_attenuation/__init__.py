"""Auto Attenuation: a self-contained attenuator/exposure-time advisor.

Launched from the main GUI's Tools menu as a separate OS process (see
``midas_gui.auto_attenuation.app``), operating on a one-time snapshot of
whatever the Data Viewer currently has loaded (buffer, dark, mask,
geometry). The numerical core here is ported from the standalone
``pyAutoBeam`` toolkit but does not import it — this package has no
dependency on anything outside midas_gui's own requirements.
"""
