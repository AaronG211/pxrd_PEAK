"""Signal cleanup utilities for extracted PXRD curves."""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np
from scipy import sparse
from scipy.signal import find_peaks, savgol_filter
from scipy.sparse.linalg import spsolve

from .schemas import ReportedPeak


_MAX_INTERPOLATED_POINTS = 20_000


def baseline_asls(y_values: Sequence[float], lam: float = 1e7, p: float = 0.01, niter: int = 10) -> np.ndarray:
    """Asymmetric least-squares baseline correction.

    lam controls baseline stiffness: larger = stiffer baseline that cannot
    follow broad diffuse peaks (good for PXRD broad humps like 001 features).
    p controls asymmetry: small p means the baseline hugs the signal bottom.
    """

    y = np.asarray(y_values, dtype=float)
    if y.size < 3:
        return np.zeros_like(y)

    length = y.size
    diff = sparse.diags([1.0, -2.0, 1.0], [0, -1, -2], shape=(length, length - 2))
    weights = np.ones(length)

    for _ in range(niter):
        weight_matrix = sparse.spdiags(weights, 0, length, length)
        system = (weight_matrix + lam * diff.dot(diff.transpose())).tocsc()
        baseline = spsolve(system, weights * y)
        weights = p * (y > baseline) + (1 - p) * (y < baseline)
    return np.asarray(baseline)


def normalize_intensity(y_values: Sequence[float]) -> np.ndarray:
    values = np.asarray(y_values, dtype=float)
    peak = float(values.max()) if values.size else 0.0
    if peak <= 0:
        return np.zeros_like(values)
    return values / peak * 100.0


def smooth_intensity(y_values: Sequence[float], window: int, polyorder: int) -> np.ndarray:
    values = np.asarray(y_values, dtype=float)
    if values.size < max(window, polyorder + 2):
        return values
    valid_window = window if window % 2 == 1 else window + 1
    valid_window = min(valid_window, values.size if values.size % 2 == 1 else values.size - 1)
    if valid_window <= polyorder:
        return values
    return savgol_filter(values, valid_window, polyorder)


def interpolate_curve(
    x_values: Sequence[float],
    y_values: Sequence[float],
    *,
    step_deg: float,
) -> Tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    if x.size == 0:
        return np.asarray([]), np.asarray([])
    order = np.argsort(x)
    x = x[order]
    y = y[order]
    grid = np.arange(x.min(), x.max() + step_deg, step_deg)
    if grid.size > _MAX_INTERPOLATED_POINTS:
        # Keep pathological calibrations from inflating the signal to millions of points.
        grid = np.linspace(x.min(), x.max(), num=_MAX_INTERPOLATED_POINTS)
    grid_y = np.interp(grid, x, y)
    return grid, grid_y


def align_intensity_to_x_grid(
    source_x_values: Sequence[float],
    source_y_values: Sequence[float],
    target_x_values: Sequence[float],
) -> np.ndarray:
    source_x = np.asarray(source_x_values, dtype=float)
    source_y = np.asarray(source_y_values, dtype=float)
    target_x = np.asarray(target_x_values, dtype=float)
    if source_x.size == 0 or source_y.size == 0 or target_x.size == 0:
        return np.asarray([], dtype=float)
    if source_x.size != source_y.size:
        return np.asarray([], dtype=float)

    order = np.argsort(source_x)
    source_x = source_x[order]
    source_y = source_y[order]
    unique_x, unique_indices = np.unique(source_x, return_index=True)
    unique_y = source_y[unique_indices]
    if unique_x.size == 0:
        return np.asarray([], dtype=float)
    if unique_x.size == 1:
        return np.full(target_x.shape, float(unique_y[0]), dtype=float)
    return np.interp(target_x, unique_x, unique_y)


def detect_pxrd_peaks(
    x_values: Sequence[float],
    y_values: Sequence[float],
    *,
    prominence_scale: float = 1.0,
) -> List[ReportedPeak]:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    if x.size == 0:
        return []

    base_prominence = max(2.5, float(np.nanmax(y)) * 0.08)
    prominence = max(0.5, base_prominence * max(0.05, float(prominence_scale)))
    indices, _ = find_peaks(y, prominence=prominence, distance=max(1, len(y) // 20))
    peaks: List[ReportedPeak] = []
    for index in indices:
        refined_x, refined_y = _parabolic_refine(x, y, int(index))
        peaks.append(
            ReportedPeak(
                label=None,
                two_theta_deg=float(refined_x),
                intensity_note="{:.2f}".format(refined_y),
            )
        )
    peaks.sort(key=lambda item: float(item.intensity_note or 0.0), reverse=True)
    return peaks[:8]


def _parabolic_refine(x: np.ndarray, y: np.ndarray, index: int) -> Tuple[float, float]:
    """3-point parabolic interpolation around a discrete peak index.

    Returns sub-pixel (x, y) at the true apex. Falls back to the sampled value
    when the neighbors do not form a concave triple.
    """
    if index <= 0 or index >= len(y) - 1:
        return float(x[index]), float(y[index])
    y_prev = float(y[index - 1])
    y_mid = float(y[index])
    y_next = float(y[index + 1])
    denom = (y_prev - 2.0 * y_mid + y_next)
    if denom >= 0:
        return float(x[index]), y_mid
    offset = 0.5 * (y_prev - y_next) / denom
    if abs(offset) > 1.0:
        return float(x[index]), y_mid
    x_prev = float(x[index - 1])
    x_next = float(x[index + 1])
    if offset >= 0:
        refined_x = float(x[index]) + offset * (x_next - float(x[index]))
    else:
        refined_x = float(x[index]) + offset * (float(x[index]) - x_prev)
    refined_y = y_mid - 0.25 * (y_prev - y_next) * offset
    return refined_x, refined_y


def process_curve(
    x_values: Sequence[float],
    y_values: Sequence[float],
    *,
    step_deg: float,
    smoothing_window: int,
    smoothing_polyorder: int,
    als_lam: float = 1e7,
    peak_prominence_scale: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[ReportedPeak]]:
    baseline = baseline_asls(y_values, lam=als_lam)
    corrected = np.asarray(y_values, dtype=float) - baseline
    # Anchor zero at the 5th percentile so a single ALS overshoot cannot drag
    # the whole trace upward; negatives are then clipped so off-peak noise does
    # not appear as spurious intensity.
    if corrected.size:
        zero_ref = float(np.percentile(corrected, 5.0))
        corrected = np.maximum(corrected - zero_ref, 0.0)
    # A near-flat trace has no signal to normalize: scaling sub-pixel numeric
    # residue up to 100 would present amplifier noise as diffraction.
    if corrected.size and float(corrected.max()) < 1.0:
        grid_x, grid_y = interpolate_curve(x_values, np.zeros_like(corrected), step_deg=step_deg)
        return grid_x, grid_y, baseline, []
    normalized = normalize_intensity(corrected)
    grid_x, grid_y = interpolate_curve(x_values, normalized, step_deg=step_deg)
    smoothed = smooth_intensity(grid_y, smoothing_window, smoothing_polyorder)
    # Savitzky-Golay overshoots at sharp features, so smoothing AFTER
    # normalization walks values out of [0, 100]; clip and rescale so the
    # published contract (relative intensity 0..100) actually holds.
    smoothed = np.clip(smoothed, 0.0, None)
    top = float(smoothed.max()) if smoothed.size else 0.0
    if top > 0:
        smoothed = smoothed * (100.0 / top)
    peaks = detect_pxrd_peaks(grid_x, smoothed, prominence_scale=peak_prominence_scale)
    return grid_x, smoothed, baseline, peaks
