"""Consensus x-axis calibration.

Three independent evidence sources are fused:
  A. CV tick-mark pixel positions  +  LLM-read tick values (paired in order)
  B. OCR (pixel, value) pairs from tick labels under the axis
  C. LLM arbitration on a magnified axis strip (only when A and B disagree)

A calibration is trusted only when at least two sources agree, or one source
fits with near-zero residual AND survives plausibility checks. Anything else
fails — and a figure without trusted calibration is auto-rejected, never
handed to a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .schemas import AxisFit, CalibrationEvidence
from .vision import BBox

# Plausibility window for powder XRD 2θ axes.
_MIN_DEG, _MAX_DEG = -5.0, 130.0
_MAX_SPAN, _MIN_SPAN = 135.0, 1.5
# Two fits "agree" when their predicted 2θ differs by less than this anywhere
# across the plot width.
_AGREEMENT_TOL_DEG = 0.5


@dataclass(frozen=True)
class Pairing:
    """One candidate assignment of tick-mark pixels to printed values."""

    pixels: List[float]
    values: List[float]
    spanning: bool


def fit_from_pairs(pixels: Sequence[float], values: Sequence[float], method: str) -> Optional[AxisFit]:
    """Least-squares line through (pixel, value) pairs with outlier rejection."""

    px = np.asarray(pixels, dtype=float)
    vals = np.asarray(values, dtype=float)
    if px.size < 2 or px.size != vals.size:
        return None

    # Iteratively drop gross outliers (a corrupted OCR digit drags OLS so far
    # that comparing against the global RMSE would mask it — use the median
    # residual as the robust scale instead).
    for _ in range(2):
        slope, intercept = np.polyfit(px, vals, 1)
        pred = slope * px + intercept
        resid = np.abs(pred - vals)
        rmse = float(np.sqrt(np.mean(resid ** 2)))
        if px.size <= 3 or rmse < 0.05:
            break
        worst = int(np.argmax(resid))
        med = float(np.median(resid))
        if resid[worst] <= max(0.3, 3.0 * med):
            break
        px = np.delete(px, worst)
        vals = np.delete(vals, worst)

    slope, intercept = np.polyfit(px, vals, 1)
    pred = slope * px + intercept
    rmse = float(np.sqrt(np.mean((pred - vals) ** 2)))
    return AxisFit(
        slope=float(slope), intercept=float(intercept),
        n_points=int(px.size), rmse_deg=rmse, method=method,
    )


def plausible(fit: AxisFit, frame: BBox, *, data_extent: Optional[Tuple[float, float]] = None) -> bool:
    """2θ window check over the calibrated data extent.

    Frame edges can overshoot the real axes by tens of pixels (multi-panel
    bleed); extrapolating a good fit to a bad edge must not kill it. When the
    supporting points' pixel extent is known, evaluate there instead.
    """

    x0, _, x1, _ = frame
    if data_extent is not None and data_extent[1] > data_extent[0]:
        pad = (data_extent[1] - data_extent[0]) * 0.10
        x0 = max(x0, data_extent[0] - pad)
        x1 = min(x1, data_extent[1] + pad)
    lo, hi = fit.to_deg(x0), fit.to_deg(x1)
    if lo >= hi:
        return False
    span = hi - lo
    return _MIN_DEG <= lo and hi <= _MAX_DEG and _MIN_SPAN <= span <= _MAX_SPAN


def fits_agree(a: AxisFit, b: AxisFit, frame: BBox) -> float:
    """Max |Δ2θ| between two fits across the plot width (degrees)."""

    x0, _, x1, _ = frame
    xs = np.linspace(x0, x1, 24)
    return float(np.max(np.abs((a.slope - b.slope) * xs + (a.intercept - b.intercept))))


def pairing_candidates(
    tick_pixels: Sequence[float],
    tick_values: Sequence[float],
) -> List["Pairing"]:
    """All plausible pairings of CV tick positions with LLM-read values.

    Exact count match pairs 1:1. When CV finds more marks than there are
    labels, the axis may carry minor ticks: the label step is subdivided by
    k ∈ {1, 2, 5} and the labeled sequence is slid across the marks. Every
    pairing that fits a line tightly is returned — a perfectly regular grid
    cannot disambiguate k by residual alone, so the caller must cross-check
    against an independent source (OCR) before trusting one.
    """

    px = sorted(float(p) for p in tick_pixels)
    vals = [float(v) for v in tick_values]
    if len(px) < 2 or len(vals) < 2:
        return []
    if len(px) == len(vals):
        return [Pairing(pixels=px, values=vals, spanning=True)]

    if not _is_arithmetic(vals):
        return []
    step = (vals[-1] - vals[0]) / (len(vals) - 1)

    n_px, n_v = len(px), len(vals)
    out: List[Tuple[float, float, List[float], List[float]]] = []  # (¬span, rmse, px, vals)
    tick_extent = max(1e-9, px[-1] - px[0])
    if n_px > n_v:
        for k in (1, 2, 5):
            sub = step / k
            max_offset = n_px - 1 - k * (n_v - 1)
            for offset in range(0, max_offset + 1):
                ext_vals = [vals[0] + sub * (i - offset) for i in range(n_px)]
                fit = fit_from_pairs(px, ext_vals, "probe")
                if fit is None or fit.rmse_deg >= 0.25:
                    continue
                # In practice the printed labels span most of the axis; an
                # interpretation that bunches them on one side is unlikely.
                labeled_span = (px[offset + k * (n_v - 1)] - px[offset]) / tick_extent
                out.append((0.0 if labeled_span >= 0.75 else 1.0, fit.rmse_deg, px, ext_vals))
    else:
        # LLM read more labels than CV found marks: subsample values.
        # A 2-point window fits any line exactly, so demand at least 3 marks.
        if n_px < 3:
            return []
        for offset in range(n_v - n_px + 1):
            sub_vals = vals[offset:offset + n_px]
            fit = fit_from_pairs(px, sub_vals, "probe")
            if fit is not None and fit.rmse_deg < 0.25:
                out.append((0.0, fit.rmse_deg, px, sub_vals))

    # Deduplicate pairings implying the same line; keep span-preferred order.
    seen = set()
    unique: List[Pairing] = []
    for non_span, _, p, v in sorted(out, key=lambda item: (item[0], item[1])):
        slope = (v[-1] - v[0]) / max(1e-9, p[-1] - p[0])
        key = (round(slope, 6), round(v[0] - slope * p[0], 3))
        if key in seen:
            continue
        seen.add(key)
        unique.append(Pairing(pixels=p, values=v, spanning=non_span == 0.0))
    return unique


def pair_ticks_with_values(
    tick_pixels: Sequence[float],
    tick_values: Sequence[float],
) -> Optional[Tuple[List[float], List[float]]]:
    """Single unambiguous pairing, or None when zero or several are possible.

    Spanning interpretations (labels covering most of the axis) dominate:
    exactly one spanning candidate resolves the ambiguity even when bunched
    interpretations also fit.
    """

    candidates = pairing_candidates(tick_pixels, tick_values)
    if not candidates:
        return None
    spanning = [c for c in candidates if c.spanning]
    if len(candidates) == 1:
        return candidates[0].pixels, candidates[0].values
    if len(spanning) == 1:
        return spanning[0].pixels, spanning[0].values
    return None


def _is_arithmetic(vals: Sequence[float], rel_tol: float = 0.08) -> bool:
    if len(vals) < 3:
        return True
    diffs = np.diff(np.asarray(vals, dtype=float))
    med = float(np.median(diffs))
    if med == 0:
        return False
    return bool(np.all(np.abs(diffs - med) <= abs(med) * rel_tol + 1e-9))


def consensus_calibration(
    *,
    frame: BBox,
    tick_pixels: Sequence[float],
    llm_tick_values: Sequence[float],
    ocr_pairs: Sequence[Tuple[float, float]],
) -> CalibrationEvidence:
    """Fuse evidence sources into one trusted calibration, or fail loudly."""

    evidence = CalibrationEvidence(
        tick_pixels=list(tick_pixels),
        tick_values=list(llm_tick_values),
        ocr_pairs=[(float(p), float(v)) for p, v in ocr_pairs],
    )
    candidates: List[AxisFit] = []

    # OCR fit first — it independently carries both pixels and values, so it
    # can disambiguate multiple tick/value pairings.
    ocr_fit: Optional[AxisFit] = None
    ocr_extent: Optional[Tuple[float, float]] = None
    if len(ocr_pairs) >= 2:
        ocr_px = [p for p, _ in ocr_pairs]
        ocr_extent = (min(ocr_px), max(ocr_px))
        fit_b = fit_from_pairs(ocr_px, [v for _, v in ocr_pairs], "ocr_pairs")
        if fit_b and plausible(fit_b, frame, data_extent=ocr_extent) and fit_b.rmse_deg < 0.5:
            ocr_fit = fit_b
        elif fit_b:
            evidence.notes.append(
                f"ocr fit discarded (rmse={fit_b.rmse_deg:.3f}, "
                f"plausible={plausible(fit_b, frame, data_extent=ocr_extent)})"
            )

    tick_extent: Optional[Tuple[float, float]] = None
    if len(tick_pixels) >= 2:
        tick_extent = (min(tick_pixels), max(tick_pixels))
    pairings = pairing_candidates(tick_pixels, llm_tick_values)
    cv_fits: List[AxisFit] = []
    kept_pairings: List[Pairing] = []
    for pairing in pairings:
        fit_a = fit_from_pairs(pairing.pixels, pairing.values, "cv_ticks+llm_values")
        if fit_a and plausible(fit_a, frame, data_extent=tick_extent) and fit_a.rmse_deg < 0.35:
            cv_fits.append(fit_a)
            kept_pairings.append(pairing)
    cv_fit: Optional[AxisFit] = None
    if cv_fits:
        if ocr_fit is not None:
            cv_fit = min(cv_fits, key=lambda f: fits_agree(f, ocr_fit, frame))
        elif len(cv_fits) == 1:
            cv_fit = cv_fits[0]
        else:
            chosen = _arbitrate_with_ocr_points(cv_fits, ocr_pairs)
            if chosen is not None:
                cv_fit = chosen
            else:
                spanning_fits = [
                    f for f, pairing in zip(cv_fits, kept_pairings) if pairing.spanning
                ]
                if len(spanning_fits) == 1:
                    cv_fit = spanning_fits[0]
                else:
                    evidence.notes.append(
                        f"{len(cv_fits)} ambiguous tick/value pairings and no OCR to disambiguate"
                    )
    elif not pairings:
        evidence.notes.append(
            f"cv ticks ({len(tick_pixels)}) and llm values ({len(llm_tick_values)}) could not be paired"
        )

    if cv_fit is not None:
        candidates.append(cv_fit)
    if ocr_fit is not None:
        candidates.append(ocr_fit)
    evidence.candidates = candidates

    if len(candidates) >= 2:
        disagreement = fits_agree(candidates[0], candidates[1], frame)
        evidence.agreement_deg = disagreement
        if disagreement <= _AGREEMENT_TOL_DEG:
            best = min(candidates, key=lambda f: f.rmse_deg)
            best = AxisFit(
                slope=best.slope, intercept=best.intercept, n_points=best.n_points,
                rmse_deg=best.rmse_deg, method="consensus",
            )
            evidence.fit = best
            evidence.status = "consensus"
            return evidence
        evidence.status = "disagreement"
        evidence.notes.append(f"candidates disagree by {disagreement:.2f} deg")
        return evidence

    if len(candidates) == 1:
        only = candidates[0]
        # A single source is acceptable only with a tight, well-supported fit.
        if only.n_points >= 3 and only.rmse_deg <= 0.15:
            evidence.fit = only
            evidence.status = "single_method"
            return evidence
        evidence.status = "weak_single"
        evidence.notes.append(
            f"single candidate too weak (n={only.n_points}, rmse={only.rmse_deg:.3f})"
        )
        return evidence

    evidence.status = "failed"
    return evidence


def _arbitrate_with_ocr_points(
    cv_fits: List[AxisFit],
    ocr_pairs: Sequence[Tuple[float, float]],
) -> Optional[AxisFit]:
    """Pick among ambiguous pairings using raw OCR points.

    Even one or two OCR-read labels — too few for an independent fit — are
    enough to test which candidate line passes through them. Requires a clear
    winner: best error < 0.5°, runner-up > 1.0°.
    """

    if not ocr_pairs or len(cv_fits) < 2:
        return None
    errors = []
    for fit in cv_fits:
        err = max(abs(fit.to_deg(p) - v) for p, v in ocr_pairs)
        errors.append(err)
    order = np.argsort(errors)
    best, second = order[0], order[1]
    if errors[best] < 0.5 and errors[second] > 1.0:
        return cv_fits[int(best)]
    return None


def apply_arbitration(
    evidence: CalibrationEvidence,
    *,
    frame: BBox,
    arbitrated_values: Sequence[float],
) -> CalibrationEvidence:
    """Re-pair CV tick pixels with arbitrated values after an LLM re-read."""

    paired = pair_ticks_with_values(evidence.tick_pixels, arbitrated_values)
    if not paired:
        evidence.notes.append("arbitration could not pair ticks")
        return evidence
    fit = fit_from_pairs(paired[0], paired[1], "arbitrated")
    # Same trust bar as the primary path: plausibility over the supporting
    # pixels (not the whole frame, which may overshoot on multi-panel crops)
    # and at least 3 points — two points fit any line with zero residual.
    extent = (min(paired[0]), max(paired[0])) if len(paired[0]) >= 2 else None
    if (
        fit
        and fit.n_points >= 3
        and plausible(fit, frame, data_extent=extent)
        and fit.rmse_deg < 0.35
    ):
        evidence.fit = fit
        evidence.status = "arbitrated"
        evidence.tick_values = list(arbitrated_values)
    else:
        evidence.notes.append("arbitrated fit implausible or under-supported")
    return evidence
