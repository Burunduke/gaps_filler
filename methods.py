# -*- coding: utf-8 -*-
"""Per-stage method registries for the hyperspectral pipeline.

Each stage (frame filter / mosaic / gap fill) has a list of dicts. Each
dict describes one available method version: a stable ``id``, a UI
``label`` (shown in the QGIS algorithm dialog dropdown), a ``tooltip``
(the "When to use / Limits" copy from ``hyperspectral_plan.md`` —
shown to the user as parameter help), and a ``func`` callable that
implements the method.

Adding a new version is purely additive: append a new dict to the
matching list. The default selection is index 0 of each list, so the
order matters — keep the current production-default version first.

This module is plain Python: no Qt, no QGIS imports. It is safe to
import from anywhere in the plugin (the heavy modules ``frame_filter``,
``mosaic``, ``fill_nodata`` it references already import cleanly today).
"""

from . import fill_nodata, frame_filter, mosaic


# ---------------------------------------------------------------------------
# Stage A — Frame filtering
# ---------------------------------------------------------------------------

FRAME_FILTER_METHODS = [
    {
        "id": "v1_hard_thresholds",
        "label": "v1 — Hard threshold heuristics (default)",
        "tooltip": (
            "Default for production. Tune FilterThresholds once per "
            "sensor model. "
            "Best when all frames in the run share altitude / lighting "
            "/ sensor. "
            "Limit: if every frame is rejected, check the live log for "
            "the most common reason and relax that one threshold "
            "(do not jump to v2)."
        ),
        "func": frame_filter.filter_frames,
    },
]


# ---------------------------------------------------------------------------
# Stage B — Mosaic building
# ---------------------------------------------------------------------------

MOSAIC_METHODS = [
    {
        "id": "v1_first_write_wins",
        "label": "v1 — First-write-wins (default)",
        "tooltip": (
            "Default for production. Spectrally faithful — every output "
            "pixel comes from exactly one source frame, no mixing. "
            "Use when downstream analysis is spectral (classifiers, "
            "indices). "
            "Limit: visible seams; if seams are unacceptable, try v3 "
            "(best placement) or v4 (feathered)."
        ),
        "func": mosaic.mosaic_frames,
    },
]


# ---------------------------------------------------------------------------
# Stage C — Gap filling
# ---------------------------------------------------------------------------

GAP_FILL_METHODS = [
    {
        "id": "v2_idw_quadrants",
        "label": "v2 — IDW with quadrant sweeps (default)",
        "tooltip": (
            "Default for production. Good for small-to-medium mosaics "
            "with gaps <= 100 px. "
            "Limit: slow — if Stage C runtime hurts, switch to v3 "
            "(same algorithm, C speed). For gaps > max_search_dist the "
            "pixel stays NaN by design."
        ),
        # gaps_filler_algorithm dispatches at file level; fill_nodata_file
        # forwards to the array-level fill_nodata internally.
        "func": fill_nodata.fill_nodata_file,
    },
    {
        "id": "v3_gdal_fillnodata",
        "label": "v3 — gdal.FillNodata (C-speed)",
        "tooltip": (
            "Use when Stage C runtime hurts and the algorithm is good "
            "enough (it's the same family as v2). "
            "Limit: keep v2 selectable as a fallback for environments "
            "where GDAL's FillNodata misbehaves on NaN sentinels."
        ),
        # Same signature as fill_nodata_file -- drops in cleanly. Falls
        # back to the v2 pure-Python path automatically if gdal.FillNodata
        # raises (Pipeline TO-DO #7 in hyperspectral_plan.md).
        "func": fill_nodata.fill_nodata_file_gdal,
    },
]


def labels(registry):
    """Return the list of UI labels for an enum parameter."""
    return [entry["label"] for entry in registry]


def tooltip_block(registry):
    """Render all option tooltips as one block for the parameter help.

    QGIS's enum parameter shows a single help string for the whole
    dropdown (not per option), so we concatenate the per-option
    "When to use / Limits" text under their labels.
    """
    parts = []
    for entry in registry:
        parts.append("{}: {}".format(entry["label"], entry["tooltip"]))
    return "\n\n".join(parts)
