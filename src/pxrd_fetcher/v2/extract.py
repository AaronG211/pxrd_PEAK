"""Per-figure curve extraction: anchor-guided tracing with auto-repair.

For each series the LLM supplies a coarse anchor polyline (semantically the
right curve), CV snaps it onto real ink (pixel precision), and a deterministic
fidelity check against the ORIGINAL pixels decides acceptance. Failures are
retried with progressively relaxed masks before being rejected — never
escalated to a human.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from ..postprocess import process_curve
from .schemas import (
    AxisFit,
    CurveAnchor,
    CurveSpec,
    SeriesResult,
    TraceEvidence,
)
from .vision import (
    PlotMask,
    SnapTrace,
    build_curve_foreground,
    color_mask_for,
    snap_trace,
    trace_fidelity,
)

# Acceptance thresholds (deterministic, documented).
MIN_SNAP_RATE = 0.55
MIN_PRECISION = 0.70
MIN_RECALL = 0.60
MIN_CONFIDENCE = 0.55


def extract_series(
    plot_rgb: np.ndarray,
    anchor: CurveAnchor,
    spec: Optional[CurveSpec],
    fit: AxisFit,
    plot_mask: PlotMask,
    *,
    frame_x0: int,
    series_id: str,
    two_theta_window: Optional[Tuple[float, float]] = None,
    interpolation_step_deg: float = 0.02,
    smoothing_window: int = 11,
    smoothing_polyorder: int = 3,
) -> Tuple[SeriesResult, Optional[np.ndarray]]:
    """Trace one series to a calibrated, normalized curve with evidence.

    Returns the result plus the raw per-column pixel trace (plot coords) so
    the caller can render a faithful overlay on the original image.
    """

    h, w = plot_rgb.shape[:2]
    evidence = TraceEvidence()
    color = (spec.color if spec else "black") or "black"

    pts = np.array([[p.x_frac, p.y_frac] for p in anchor.points], dtype=float)
    result = SeriesResult(
        series_id=series_id,
        label=(spec.label if spec else None) or anchor.label,
        material_name=spec.material_name if spec else None,
        sample_state=spec.sample_state if spec else None,
        color=color,
        evidence=evidence,
    )
    if pts.shape[0] < 8:
        result.status = "rejected"
        result.reject_reason = "anchor polyline too short"
        return result, None

    # --- Repair ladder: try increasingly permissive candidate masks --------
    attempts: List[Tuple[str, np.ndarray, float]] = []
    cmask = color_mask_for(plot_rgb, color)
    if cmask is not None:
        masked = np.logical_and(cmask, plot_mask.foreground)
        attempts.append((f"color:{color}", masked, 0.045))
    attempts.append(("foreground", plot_mask.foreground, 0.045))
    attempts.append(("foreground-wide", plot_mask.foreground, 0.09))

    best: Optional[Tuple[SnapTrace, object, str]] = None
    for round_idx, (name, mask, window) in enumerate(attempts):
        if not mask.any():
            continue
        trace = snap_trace(pts, (h, w), mask, window_frac=window)
        fidelity = trace_fidelity(trace.y_px, mask, anchor_y=trace.anchor_y)
        passes = (
            trace.snap_rate >= MIN_SNAP_RATE
            and fidelity.precision >= MIN_PRECISION
            and fidelity.recall >= MIN_RECALL
        )
        # A rung that clears every hard gate WINS, even if an earlier failing
        # rung had a higher composite score — otherwise the figure is rejected
        # on the failing rung while a passing result exists.
        if passes:
            best = (trace, fidelity, name)
            evidence.repair_rounds = round_idx
            break
        score = trace.snap_rate * 0.4 + fidelity.precision * 0.4 + fidelity.recall * 0.2
        if best is None or score > _score_of(best):
            best = (trace, fidelity, name)
            evidence.repair_rounds = round_idx

    if best is None:
        result.status = "rejected"
        result.reject_reason = "no usable pixel mask for tracing"
        return result, None

    trace, fidelity, mask_name = best
    evidence.snap_rate = trace.snap_rate
    evidence.mean_snap_residual_px = trace.mean_residual_px
    evidence.pixel_precision = fidelity.precision
    evidence.pixel_recall = fidelity.recall
    evidence.notes.append(f"mask={mask_name}")

    # Anchor-independent coverage, only meaningful on the series-specific
    # color mask (rung 1): there, every colored ink column belongs to THIS
    # series, so full-column recall (no anchor band) sees ink the anchor
    # missed entirely — the blind spot of the banded recall above. Recorded
    # as evidence, not gated: legends and duplicate-colored curves add noise.
    if cmask is not None and mask_name == f"color:{color}":
        indep = trace_fidelity(trace.y_px, masked)
        evidence.independent_recall = indep.recall

    anchor_dev = _anchor_deviation(pts, trace, (h, w))
    evidence.anchor_vs_final_rmsd_frac = anchor_dev

    confidence = _confidence(trace.snap_rate, fidelity.precision, fidelity.recall, anchor_dev)
    result.confidence = confidence

    accepted = (
        trace.snap_rate >= MIN_SNAP_RATE
        and fidelity.precision >= MIN_PRECISION
        and fidelity.recall >= MIN_RECALL
        and confidence >= MIN_CONFIDENCE
    )
    if not accepted:
        result.status = "rejected"
        result.reject_reason = (
            f"fidelity below threshold (snap={trace.snap_rate:.2f}, "
            f"prec={fidelity.precision:.2f}, rec={fidelity.recall:.2f}, conf={confidence:.2f})"
        )
        return result, trace.y_px

    # --- Convert to calibrated 2θ / relative-intensity space ----------------
    cols = np.flatnonzero(~np.isnan(trace.y_px))
    two_theta = np.array([fit.to_deg(frame_x0 + float(x)) for x in cols])
    # y pixels grow downward; intensity grows upward.
    intensity_px = np.array([float(h - 1 - trace.y_px[x]) for x in cols])

    # Clip to the calibrated axis window: trace segments outside the tick
    # span belong to neighboring panels or hallucinated extensions, and 2θ
    # below ~0.2° is physically meaningless in a powder pattern.
    if two_theta_window is not None:
        lo, hi = two_theta_window
        keep = (two_theta >= lo) & (two_theta <= hi)
        if keep.sum() < 0.3 * keep.size:
            result.status = "rejected"
            result.reject_reason = (
                f"only {keep.mean():.0%} of the trace lies inside the calibrated "
                f"axis window [{lo:.1f}, {hi:.1f}] deg"
            )
            return result, trace.y_px
        if not keep.all():
            evidence.notes.append(
                f"clipped {int((~keep).sum())} cols outside [{lo:.1f}, {hi:.1f}] deg"
            )
        two_theta = two_theta[keep]
        intensity_px = intensity_px[keep]

    # A trace surviving as a sliver of the axis is not a diffraction pattern.
    if two_theta.size:
        span = float(two_theta.max() - two_theta.min())
        if span < 5.0:
            result.status = "rejected"
            result.reject_reason = (
                f"trace spans only {span:.1f} deg of the axis after clipping"
            )
            return result, trace.y_px

    grid_x, smoothed, _, peaks = process_curve(
        two_theta,
        intensity_px,
        step_deg=interpolation_step_deg,
        smoothing_window=smoothing_window,
        smoothing_polyorder=smoothing_polyorder,
    )
    result.two_theta_deg = [round(float(v), 4) for v in grid_x]
    result.intensity_norm = [round(float(v), 4) for v in smoothed]
    result.peaks_two_theta = [
        float(p.two_theta_deg) for p in peaks if p.two_theta_deg is not None
    ]
    # Per-point 2θ uncertainty: calibration residual + one-pixel quantization.
    result.two_theta_uncertainty_deg = round(
        float(fit.rmse_deg + abs(fit.slope)), 4
    )
    result.status = "accepted"
    return result, trace.y_px


def _score_of(best: Tuple[SnapTrace, object, str]) -> float:
    trace, fidelity, _ = best
    return trace.snap_rate * 0.4 + fidelity.precision * 0.4 + fidelity.recall * 0.2


def _anchor_deviation(
    pts: np.ndarray, trace: SnapTrace, plot_shape: Tuple[int, int]
) -> float:
    """RMSD between the anchor polyline and the snapped trace (fraction of height)."""

    h, w = plot_shape
    xs = np.clip(pts[:, 0], 0, 1) * (w - 1)
    ys = np.clip(pts[:, 1], 0, 1) * (h - 1)
    order = np.argsort(xs)
    ux, idx = np.unique(xs[order], return_index=True)
    if ux.size < 2:
        return 1.0
    uy = ys[order][idx]
    cols = np.flatnonzero(~np.isnan(trace.y_px))
    if cols.size == 0:
        return 1.0
    anchor_y = np.interp(cols, ux, uy)
    final_y = trace.y_px[cols]
    return float(np.sqrt(np.mean((anchor_y - final_y) ** 2)) / max(1, h))


def _confidence(snap_rate: float, precision: float, recall: float, anchor_dev: float) -> float:
    """Composite confidence in [0,1] from independent evidence terms."""

    dev_term = max(0.0, 1.0 - anchor_dev / 0.25)
    return round(
        0.30 * snap_rate + 0.30 * precision + 0.25 * recall + 0.15 * dev_term, 4
    )


def build_plot_mask(plot_rgb: np.ndarray, text_boxes) -> PlotMask:
    return build_curve_foreground(plot_rgb, text_boxes=text_boxes)
