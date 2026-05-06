# -*- coding: utf-8 -*-
"""Stage C orchestrator of the hyperspectral pipeline.

Chains Stage A (:mod:`frame_filter`) -> Stage B (:mod:`mosaic`) ->
Stage C (file-level gap fill via a callable from
:data:`methods.GAP_FILL_METHODS`). The mosaic from Stage B is written
to disk as ``<output>.mosaic.tif`` (float32 GeoTIFF with NaN as NoData)
and Stage C is a single call into the registry-supplied gap-fill
function which reads the mosaic, fills every band, and writes the final
output. The default callable is :func:`fill_nodata.fill_nodata_file`
(v2 IDW -- which itself loops the array-level
:func:`fill_nodata.fill_nodata` band-by-band, so the default code path
is byte-equivalent to the historical in-pipeline loop);
:class:`HyperspectralPipelineAlgorithm` swaps in
:func:`fill_nodata.fill_nodata_file_gdal` (v3) when the user selects it
from the dropdown.

Temp-file convention
--------------------

:func:`run_pipeline` writes two intermediate artefacts next to the final
``output_path`` and is responsible for removing them on every exit path
(success, exception, ``KeyboardInterrupt``, QGIS cancellation):

* ``<output>.mosaic.tif`` -- the Stage-B mosaic consumed by Stage C.
  Created by :func:`mosaic.mosaic_frames`. Required hand-off (Stage C
  reads it from disk), not merely a debug artefact.
* ``<output>.fillmask.tif`` -- the interior-hole mask written by
  :func:`fill_nodata.write_interior_fill_mask` when
  ``fill_only_interior=True`` (default). Skipped when the caller opts
  out.

Both are wrapped in a single ``try / finally`` so they are always
removed on the way out, even if the user cancels the run mid-Stage-B
(``rasterio`` may have left a partial GeoTIFF behind) or hits Ctrl-C
during gap-fill. The companion ``<output>.rejected.csv`` audit report
written by Stage A is **not** a temp file -- it is a deliverable and
stays on disk.
"""

from __future__ import annotations

import csv
import os
import re
from typing import Callable, Optional

import numpy as np
import rasterio

from . import fill_nodata, frame_filter, mosaic


# Reasons emitted by ``frame_filter.is_bad_frame`` follow a fixed shape:
# ``"<text> (key=<measured> <op> <threshold>)"`` for scalar comparisons or
# ``"<text> (key=<measured>, allowed=[<lo>, <hi>])"`` for the area check.
# These two regexes pull the numeric measured value and threshold out so
# the rejected-frames CSV (Pipeline TO-DO item #6 in
# ``hyperspectral_plan.md``) carries them as separate columns. If a reason
# does not match (future heuristic, unexpected text), the columns stay
# empty and the full reason string is still recorded.
_REASON_SCALAR_RE = re.compile(
    r"\(\s*[A-Za-z_]+\s*=\s*([-+0-9.eE+inf]+)\s*[<>]\s*([-+0-9.eE+inf]+)\s*\)"
)
_REASON_RANGE_RE = re.compile(
    r"\(\s*[A-Za-z_]+\s*=\s*([-+0-9.eEinf]+)\s*,\s*allowed\s*=\s*"
    r"\[\s*([-+0-9.eEinf]+)\s*,\s*([-+0-9.eEinf]+)\s*\]\s*\)"
)


def _parse_reason(reason: str) -> tuple[str, str]:
    """Extract ``(measured_value, threshold)`` from a rejection reason string.

    Returns ``("", "")`` when the reason text does not follow the known
    shape so the caller can still write a row with the raw reason.
    """
    m = _REASON_SCALAR_RE.search(reason)
    if m:
        return m.group(1), m.group(2)
    m = _REASON_RANGE_RE.search(reason)
    if m:
        return m.group(1), "[{}, {}]".format(m.group(2), m.group(3))
    return "", ""


def _write_rejected_report(report_path: str,
                           rejected: list[tuple[str, str]]) -> None:
    """Write ``rejected`` to ``report_path`` as a CSV for audit.

    Columns: ``path, reason, measured_value, threshold``. Always written
    (even when ``rejected`` is empty) so the presence of the file is a
    deterministic signal that Stage A ran.
    """
    with open(report_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["path", "reason", "measured_value", "threshold"])
        for path, reason in rejected:
            measured, threshold = _parse_reason(reason)
            writer.writerow([path, reason, measured, threshold])


_ProgressCb = Callable[[float, str], None]
_LogCb = Callable[[str], None]


def _noop(fraction: float, message: str) -> None:
    return None


def _noop_log(message: str) -> None:
    return None


class _PipelineFeedback:
    """Duck-typed ``QgsProcessingFeedback`` bridging into our progress / log
    callbacks.

    The registry-supplied gap-fill callables (``fill_nodata_file`` /
    ``fill_nodata_file_gdal``) accept a ``feedback`` object and use it for
    ``pushInfo`` / ``setProgress`` / ``isCanceled``. This shim forwards
    those calls into the ``progress`` and ``log`` callbacks ``run_pipeline``
    already receives, mapping setProgress(0..100) into the [0.70 .. 1.00]
    fraction window reserved for Stage C.

    ``is_canceled`` is an optional zero-arg predicate; when supplied it is
    forwarded by :meth:`isCanceled` so the file-level gap-fill backends
    (which already poll ``feedback.isCanceled`` between bands / tiles)
    actually stop when the QGIS user clicks Cancel. Default ``None``
    preserves the historical "never cancels" behaviour for non-QGIS
    callers (Pipeline TO-DO #11 in ``hyperspectral_plan.md``).
    """

    def __init__(
        self,
        progress: _ProgressCb,
        log: _LogCb,
        is_canceled: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._progress = progress
        self._log = log
        self._is_canceled = is_canceled

    def pushInfo(self, message: str) -> None:  # noqa: N802 (QGIS API name)
        self._log(message)

    def reportError(self, message: str, fatalError: bool = False) -> None:  # noqa: N802,N803
        self._log(message)

    def setProgress(self, percent: float) -> None:  # noqa: N802
        # Stage C occupies the last 30% of the pipeline's progress bar.
        frac = 0.70 + 0.30 * max(0.0, min(100.0, float(percent))) / 100.0
        self._progress(frac, "")

    def isCanceled(self) -> bool:  # noqa: N802
        if self._is_canceled is None:
            return False
        return bool(self._is_canceled())


# Note: the interior-hole mask helper used to live here as
# ``_write_interior_fill_mask``. It was promoted to a public function in
# :mod:`fill_nodata` (``fill_nodata.write_interior_fill_mask``) so the
# standalone Fill NoData algorithm can reuse it; behaviour is unchanged.


def run_pipeline(
    input_paths: list[str],
    output_path: str,
    *,
    thresholds: "frame_filter.FilterThresholds | None" = None,
    max_distance: int = 100,
    smoothing_iterations: int = 0,
    progress: Optional[_ProgressCb] = None,
    log: Optional[_LogCb] = None,
    reproject_to_first: bool = False,
    gap_fill_func: Optional[Callable[..., None]] = None,
    fill_only_interior: bool = True,
    max_interior_gap_px: int = 0,
    tile_size: int = 0,
    n_workers: int = 1,
    is_canceled: Optional[Callable[[], bool]] = None,
) -> dict:
    """Run filter -> mosaic -> fill_nodata. Return a summary dict.

    ``gap_fill_func`` is a file-level callable from
    :data:`methods.GAP_FILL_METHODS` with signature
    ``(input_path, output_path, *, mask_path=None, max_search_dist,
    smoothing_iterations, feedback)``. When ``None``, defaults to
    :func:`fill_nodata.fill_nodata_file` (v2) so existing callers keep
    working byte-equivalently to the previous in-pipeline per-band loop.
    """
    cb: _ProgressCb = progress if progress is not None else _noop
    log_cb: _LogCb = log if log is not None else _noop_log
    if gap_fill_func is None:
        gap_fill_func = fill_nodata.fill_nodata_file

    # ---- Stage A: filter -------------------------------------------------
    # ``is_canceled`` is forwarded into ``filter_frames`` so cancellation
    # is honoured **between frames** (Pipeline TO-DO #11). Stage B's
    # progress callback already raises on cancel via the QGIS-supplied
    # ``progress`` shim, and Stage C polls ``feedback.isCanceled`` which
    # we now bridge via ``_PipelineFeedback`` below.
    good, rejected = frame_filter.filter_frames(
        input_paths, thresholds=thresholds, is_canceled=is_canceled)
    for p, reason in rejected:
        log_cb("REJECTED {}: {}".format(os.path.basename(p), reason))
    log_cb("Kept {} / {} frames".format(len(good), len(input_paths)))

    # Persist the rejected-frames report next to the final output for
    # audit (Pipeline TO-DO item #6 in ``hyperspectral_plan.md``). The
    # file is written before the all-rejected guard below so the user
    # can inspect *why* every frame was dropped without re-running the
    # filter pass. Best-effort: a write failure is logged but does not
    # abort the pipeline.
    report_path = output_path + ".rejected.csv"
    try:
        _write_rejected_report(report_path, rejected)
        log_cb("Rejected-frames report written to {}".format(report_path))
    except OSError as exc:
        log_cb("WARNING: could not write rejected-frames report "
               "({}): {}".format(report_path, exc))

    if len(good) == 0:
        raise RuntimeError("all frames rejected by filter")
    cb(0.05, "filtered: {} kept, {} rejected".format(len(good), len(rejected)))

    # ---- Stage B: mosaic -------------------------------------------------
    # Place the temp mosaic next to the final output as
    # ``<output>.mosaic.tif`` (see the "Temp-file convention" section in
    # this module's docstring). Path is predictable for debugging, and the
    # ``try / finally`` below removes it on every exit path -- including
    # ``KeyboardInterrupt`` and QGIS cancellation mid-mosaic, which is
    # why the ``mosaic_frames`` call lives **inside** the ``try`` block
    # (Pipeline TO-DO item in ``hyperspectral_plan.md``: "ensure cleanup
    # on KeyboardInterrupt / cancellation, not only on success").
    # Stage C consumes this file, so the on-disk intermediate is a
    # required hand-off rather than just a helpful artefact.
    mosaic_path = output_path + ".mosaic.tif"
    band_count = 0
    fill_mask_path: Optional[str] = None
    try:
        mosaic.mosaic_frames(
            good,
            mosaic_path,
            progress=lambda f, m: cb(0.05 + 0.65 * f, "mosaic: " + m),
            reproject_to_first=reproject_to_first,
        )
        # All-NaN-band guard (Pipeline TO-DO item #4): scan the Stage-B
        # mosaic on disk before invoking the file-level gap-fill callable.
        # Either gap-fill backend (v2 pure-Python or v3 gdal.FillNodata)
        # would silently produce an all-NaN output for an all-NaN input
        # band. Aborting here preserves the previous in-pipeline guard's
        # behaviour with a clear diagnostic.
        with rasterio.open(mosaic_path) as src:
            band_count = int(src.count)
            for b in range(1, band_count + 1):
                arr = src.read(b)
                if not np.isfinite(arr).any():
                    raise RuntimeError(
                        "band {}/{} is entirely NoData (all-NaN); "
                        "aborting gap fill".format(b, band_count))

        # Footprint-aware gap fill: build a mask that marks ONLY interior
        # holes (NaN pixels enclosed by valid data) as fillable. Outside
        # the swath stays untouched. Opt-out via ``fill_only_interior``.
        if fill_only_interior:
            fill_mask_path = output_path + ".fillmask.tif"
            fill_nodata.write_interior_fill_mask(
                mosaic_path, fill_mask_path,
                max_gap_px=int(max_interior_gap_px),
            )
            log_cb("Footprint mask written to {}".format(fill_mask_path))

        cb(0.70, "fill: dispatching gap-fill on {} band(s)".format(band_count))

        # ---- Stage C: registry-driven file-level gap fill ----------------
        # The two file-level callables in ``methods.GAP_FILL_METHODS``
        # share a signature (``fill_nodata_file`` / ``fill_nodata_file_gdal``).
        # They take the same ``max_search_dist`` and ``smoothing_iterations``
        # kwargs that ``run_pipeline`` already exposes; forward them as-is.
        gap_fill_func(
            mosaic_path,
            output_path,
            mask_path=fill_mask_path,
            max_search_dist=float(max_distance),
            smoothing_iterations=int(smoothing_iterations),
            feedback=_PipelineFeedback(cb, log_cb, is_canceled),
            tile_size=int(tile_size),
            n_workers=int(n_workers),
        )
        cb(1.0, "fill: done")
    finally:
        # Best-effort temp cleanup (mosaic + footprint mask).
        try:
            os.remove(mosaic_path)
        except OSError:
            pass
        if fill_mask_path is not None:
            try:
                os.remove(fill_mask_path)
            except OSError:
                pass

    return {
        "input_count": len(input_paths),
        "kept_count": len(good),
        "rejected": rejected,
        "output_path": output_path,
        "band_count": band_count,
    }
