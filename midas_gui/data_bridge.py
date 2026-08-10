"""Cross-tab "what data is currently loaded elsewhere" registry.

Tabs that load detector data (Data Viewer, Calibrate, Refinement, Batch
Integrate, Pump Probe, Mask Builder) each register themselves with a single
`DataSourceRegistry` instance owned by `MainWindow`. Any tab can then ask the
registry what's currently loaded in the others and offer to import it,
without those tabs needing to know about each other directly.

Deliberately pull-based (no signals/caching): `available()` calls
`describe_source()` on every registered provider at call time, right before a
menu is shown, so it's always accurate and there's no invalidation to track.

Each descriptor also carries a `field` tag ("data", "dark", "bright",
"background", …) so an importer only sees sources of its own type — a tab's
Dark selector should offer other tabs' Dark fields, not their raw Data or
Bright field. `describe_source()` may return a single dict, a list of dicts
(a panel can offer its main Data plus its own Dark/Bright/Background fields
at once), or None.
"""
from __future__ import annotations

from typing import Optional


class DataSourceRegistry:
    def __init__(self):
        self._sources: list = []   # [(label, provider), ...]

    def register(self, label: str, provider) -> None:
        self._sources.append((label, provider))

    def available(self, *, exclude=None, kind: Optional[str] = None,
                  field: Optional[str] = None) -> list:
        """Live snapshot of importable sources, each a dict from
        `provider.describe_source()` (kind "path" or "buffer"; field "data",
        "dark", "bright", "background", …), skipping `exclude` and optionally
        filtered to a single `kind` and/or `field`. A descriptor with no
        `field` key is treated as "data" (plain path/buffer sources predate
        the field tag)."""
        out = []
        for label, provider in self._sources:
            if provider is exclude:
                continue
            try:
                desc = provider.describe_source()
            except Exception:
                desc = None
            if desc is None:
                continue
            descs = desc if isinstance(desc, list) else [desc]
            for d in descs:
                if kind is not None and d.get("kind") != kind:
                    continue
                if field is not None and d.get("field", "data") != field:
                    continue
                out.append(d)
        return out
