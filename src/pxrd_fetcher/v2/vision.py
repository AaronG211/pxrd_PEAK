"""Deterministic CV primitives for the v2 pipeline.

Division of labor: this module owns everything pixel-precise — plot frame
detection, tick-mark localization, curve foreground masks, anchor-guided
snap tracing, and the non-circular pixel-fidelity verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

BBox = Tuple[int, int, int, int]


# --------------------------------------------------------------------------
# Plot frame detection
# --------------------------------------------------------------------------


def detect_plot_frame(crop_rgb: np.ndarray) -> Optional[BBox]:
    """Locate the plot interior (x0, y0, x1, y1) inside a panel crop.

    Strategy: strong horizontal/vertical line projections. PXRD panels almost
    always have a solid bottom axis; left/right/top edges may be lighter or
    absent, so we fall back to ink extents when a full rectangle is missing.
    """

    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    dark = (gray < 128).astype(np.uint8)

    row_runs = _long_run_lengths(dark, axis=1)   # per-row longest horizontal dark run
    col_runs = _long_run_lengths(dark, axis=0)   # per-col longest vertical dark run

    h_lines = [y for y in range(h) if row_runs[y] > 0.55 * w]
    v_lines = [x for x in range(w) if col_runs[x] > 0.45 * h]

    bottom = _pick_extreme(h_lines, lo=int(h * 0.35), hi=h - 1, prefer="max")
    top = _pick_extreme(h_lines, lo=0, hi=int(h * 0.45), prefer="min")
    left = _pick_extreme(v_lines, lo=0, hi=int(w * 0.45), prefer="min")
    right = _pick_extreme(v_lines, lo=int(w * 0.55), hi=w - 1, prefer="max")

    if bottom is None:
        return None
    if left is None:
        left = _ink_extent(dark, axis=0, side="min", limit=bottom)
    if right is None:
        right = _ink_extent(dark, axis=0, side="max", limit=bottom)
    if top is None:
        top = _ink_extent(dark[: bottom], axis=1, side="min", limit=None)
    if top is None or left is None or right is None:
        return None

    x0, y0, x1, y1 = int(left), int(top), int(right), int(bottom)
    if x1 - x0 < w * 0.3 or y1 - y0 < h * 0.2:
        return None
    return (x0, y0, x1, y1)


def _long_run_lengths(mask: np.ndarray, axis: int) -> np.ndarray:
    """Longest consecutive run of 1s along the given axis, per line."""

    if axis == 0:
        mask = mask.T
    n_lines, length = mask.shape
    out = np.zeros(n_lines, dtype=np.int32)
    for i in range(n_lines):
        line = mask[i]
        if not line.any():
            continue
        # run-length via diff on padded array
        padded = np.concatenate(([0], line, [0]))
        diff = np.diff(padded)
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)
        out[i] = int((ends - starts).max())
    return out


def _pick_extreme(lines: Sequence[int], lo: int, hi: int, prefer: str) -> Optional[int]:
    valid = [v for v in lines if lo <= v <= hi]
    if not valid:
        return None
    return max(valid) if prefer == "max" else min(valid)


def _ink_extent(mask: np.ndarray, axis: int, side: str, limit: Optional[int]) -> Optional[int]:
    # axis=0 → x extents (per-column ink counts); axis=1 → y extents (per-row).
    proj = mask.sum(axis=0) if axis == 0 else mask.sum(axis=1)
    nz = np.flatnonzero(proj > 2)
    if nz.size == 0:
        return None
    return int(nz.min()) if side == "min" else int(nz.max())


def select_plot_frame(
    crop_rgb: np.ndarray,
    *,
    hint: Optional[Tuple[float, float, float, float]] = None,  # fractional box
    numeric_token_xs: Optional[Sequence[Tuple[float, float]]] = None,  # (x, y) centers
) -> Optional[BBox]:
    """Choose the axes frame by *axis evidence*, not by line length alone.

    Multi-panel crops contain many long lines (panel borders, structure
    drawings, neighboring plots). Every candidate bottom-axis line is scored
    by: tick marks attached to it, numeric labels beneath it, curve ink above
    it, and agreement with the LLM hint box. The best-scoring candidate wins;
    no candidate with real evidence means no frame.
    """

    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    dark = (gray < 128).astype(np.uint8)

    # The LLM hint has proven reliable; restrict the search to its expanded
    # neighborhood first so inset photos, structure drawings, and neighboring
    # panels never get to compete. Fall back to a global search without it.
    if hint is not None:
        hx0, hy0, hx1, hy1 = hint
        mx, my = int(w * 0.10), int(h * 0.10)
        rx0 = max(0, int(hx0 * w) - mx)
        ry0 = max(0, int(hy0 * h) - my)
        rx1 = min(w, int(hx1 * w) + mx)
        ry1 = min(h, int(hy1 * h) + my)
        local = np.zeros_like(dark)
        local[ry0:ry1, rx0:rx1] = dark[ry0:ry1, rx0:rx1]
        best = _best_frame_candidate(crop_rgb, local, numeric_token_xs)
        if best is not None:
            return _snap_frame_edges(local, best)
    best = _best_frame_candidate(crop_rgb, dark, numeric_token_xs)
    if best is None:
        return None
    return _snap_frame_edges(dark, best)


def _best_frame_candidate(
    crop_rgb: np.ndarray,
    dark: np.ndarray,
    numeric_token_xs: Optional[Sequence[Tuple[float, float]]],
) -> Optional[BBox]:
    h, w = dark.shape
    candidates: List[Tuple[float, BBox]] = []
    for y_line, x_start, x_end in _candidate_axis_rows(dark):
        bbox = _frame_from_bottom_line(dark, y_line, x_start, x_end)
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        if (x1 - x0) < w * 0.12 or (y1 - y0) < h * 0.08:
            continue
        score = 0.0
        ticks = detect_x_tick_pixels(crop_rgb, bbox)
        below = []
        if numeric_token_xs:
            below = [
                tx for tx, ty in numeric_token_xs
                if y1 < ty < y1 + max(28.0, (y1 - y0) * 0.18) and x0 - 10 <= tx <= x1 + 10
            ]
        # Tick-like strokes without numeric labels under them are weak
        # evidence — inset photos and drawings produce regular strokes too.
        tick_weight = 1.5 if below else 0.3
        score += min(len(ticks), 8) * tick_weight
        score += min(len(below), 8) * 2.5
        interior = dark[y0 + 3:y1 - 3, x0 + 3:x1 - 3]
        ink = float(interior.mean()) if interior.size else 0.0
        if 0.003 <= ink <= 0.4:
            score += 2.0
        # Size prior: with equal axis evidence, the fuller box is the axis;
        # truncated fragments of the same line must not win on tick noise.
        score += 3.0 * (x1 - x0) / w
        candidates.append((score, bbox))

    if not candidates:
        return None
    best_score, best_bbox = max(candidates, key=lambda c: c[0])
    if best_score < 3.0:
        return None
    return best_bbox


def _snap_frame_edges(dark: np.ndarray, bbox: BBox) -> BBox:
    """Pull each frame edge onto VERIFIED axis lines only.

    A vertical frame line is a CONTIGUOUS stroke spanning most of the box
    height, sitting at an end of the bottom axis run. Per-column ink counts
    are not evidence: stacked PXRD curves share peak positions, so a single
    2θ column accumulates ink from every series and masquerades as a frame
    line while never forming one contiguous stroke. And the line must sit at
    the axis-run end — frame verticals meet the bottom axis where it stops,
    so the search zone is narrow; a wide zone walks onto interior peaks.
    Without a verified line the edge stays OPEN at the axis-run endpoint
    (authors often draw no box at all); finalize_frame() bounds open edges
    by tick evidence.
    """

    h, w = dark.shape
    x0, y0, x1, y1 = bbox
    box_w, box_h = x1 - x0, y1 - y0

    col_runs = _long_run_lengths(dark[y0:y1 + 1, :], axis=0)
    zone = max(6, int(box_w * 0.035))
    lo_l, hi_l = max(0, x0 - 5), min(w - 1, x0 + zone)
    lo_r, hi_r = max(0, x1 - zone), min(w - 1, x1 + 5)
    left_zone = np.flatnonzero(col_runs[lo_l:hi_l + 1] >= 0.65 * box_h) + lo_l
    right_zone = np.flatnonzero(col_runs[lo_r:hi_r + 1] >= 0.65 * box_h) + lo_r

    new_x0 = int(left_zone.min()) if left_zone.size else x0
    new_x1 = int(right_zone.max()) if right_zone.size else x1
    if new_x1 - new_x0 < box_w * 0.5:    # snapped onto something bogus
        new_x0, new_x1 = x0, x1

    # Top edge: the content boundary (ink extent), optionally snapped to a
    # frame line found just around it. Walking down from the bottom instead
    # would stop at the first flat baseline masquerading as a frame line.
    span = new_x1 - new_x0
    new_y0 = y0
    row_ink = dark[:y1 - 4, new_x0:new_x1 + 1].sum(axis=1)
    content_rows = np.flatnonzero(row_ink > 2)
    if content_rows.size:
        ink_top = int(content_rows.min())
        new_y0 = ink_top
        hi = min(row_ink.size, ink_top + max(6, int((y1 - ink_top) * 0.12)))
        for y in range(max(0, ink_top - 5), hi):
            if row_ink[y] > span * 0.55:
                new_y0 = y
                break
    if y1 - new_y0 < box_h * 0.4:        # degenerate; keep original
        new_y0 = y0

    return (new_x0, new_y0, new_x1, y1)


def finalize_frame(
    crop_rgb: np.ndarray,
    frame: BBox,
    hint: Optional[Tuple[float, float, float, float]] = None,
) -> BBox:
    """Adapt a frame candidate to the figure's actual axis topology.

    Authors frequently draw no plot box at all — just a bottom axis under
    stacked curves. Each side edge is therefore either a VERIFIED frame line
    (a contiguous near-full-height stroke at the bbox boundary) or an OPEN
    edge. Open edges must not be invented from arbitrary ink extents: they
    are set from the bottom axis run, clamped into the LLM plot-box hint
    (the semantic answer to "which panel is mine"), and bounded by the tick
    evidence. This is what keeps neighboring-panel ink (pulled in by crop
    padding) out of the plot, which fidelity metrics can never catch because
    that ink is real.

    Every frame candidate the pipeline considers must pass through here.
    """

    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    dark = (gray < 128).astype(np.uint8)
    h, w = dark.shape
    x0, y0, x1, y1 = frame
    box_h = max(1, y1 - y0)
    zone = max(6, int((x1 - x0) * 0.035))

    col_runs = _long_run_lengths(dark[y0:y1 + 1, :], axis=0)

    def _has_line(lo: int, hi: int) -> bool:
        lo, hi = max(0, lo), min(w - 1, hi)
        if hi < lo:
            return False
        return bool(np.any(col_runs[lo:hi + 1] >= 0.65 * box_h))

    left_line = _has_line(x0 - 5, x0 + zone)
    right_line = _has_line(x1 - zone, x1 + 5)
    if left_line and right_line:
        return frame                      # genuine box; trust it as drawn

    nx0, nx1 = x0, x1
    run = _axis_run_at(dark, y1, (x0 + x1) // 2)
    if run is not None:
        if not left_line:
            nx0 = run[0]
        if not right_line:
            nx1 = run[1]

    # Open edges stay inside the LLM plot-box neighborhood: when panels sit
    # so close that even their axis runs nearly touch, geometry alone cannot
    # say where ours ends — the hint can.
    if hint is not None:
        margin = int(w * 0.03)
        if not left_line:
            nx0 = max(nx0, int(hint[0] * w) - margin)
        if not right_line:
            nx1 = min(nx1, int(hint[2] * w) + margin)

    # Open edges may not extend far past the outermost ticks: the axis line
    # of a neighboring panel (or a run merged across the gap) is not ours.
    ticks = detect_x_tick_pixels(crop_rgb, (nx0, y0, nx1, y1))
    if len(ticks) >= 3:
        gap = float(np.median(np.diff(ticks)))
        if not left_line:
            nx0 = max(nx0, int(round(ticks[0] - 1.4 * gap)))
        if not right_line:
            nx1 = min(nx1, int(round(ticks[-1] + 1.4 * gap)))

    if nx1 - nx0 < max(40, (x1 - x0) * 0.35):
        return frame                      # truncation collapsed; keep original
    if (nx0, nx1) == (x0, x1):
        return frame
    # The x-extent changed, so the content top must be re-derived for the new
    # span. The search floor comes from the hint: the crop is padded, so the
    # full column range contains ink from whatever sits above the panel.
    floor = max(0, int(hint[1] * h) - int(0.10 * h)) if hint is not None else 0
    ny0 = y0
    band = dark[floor:max(floor + 1, y1 - 4), int(nx0):int(nx1) + 1]
    row_ink = band.sum(axis=1)
    content_rows = np.flatnonzero(row_ink > 2)
    if content_rows.size:
        ink_top = int(content_rows.min())
        ny0 = floor + ink_top
        span = nx1 - nx0
        hi = min(band.shape[0], ink_top + max(6, int((band.shape[0] - ink_top) * 0.12)))
        for yy in range(max(0, ink_top - 5), hi):
            if row_ink[yy] > span * 0.55:
                ny0 = floor + yy
                break
    if y1 - ny0 < 0.35 * box_h:           # degenerate; keep the original top
        ny0 = y0
    return (int(nx0), int(ny0), int(nx1), int(y1))


def _axis_run_at(dark: np.ndarray, y: int, x_anchor: int) -> Optional[Tuple[int, int]]:
    """Extent (x_start, x_end) of the horizontal axis run under x_anchor at row y.

    Reads a 5-row band (stroke thickness + anti-aliasing), merges runs split
    by small gaps, and returns the merged run containing x_anchor — falling
    back to the nearest run when the anchor lands in a label break.

    The merge gap is tight: anti-aliasing breaks span a few pixels, while
    tightly packed side-by-side panels can sit under 20px apart — bridging
    them welds two panels' axes into one run.
    """

    h, w = dark.shape
    band = dark[max(0, y - 2):min(h, y + 3), :]
    if band.size == 0:
        return None
    line = band.max(axis=0)
    if not line.any():
        return None
    padded = np.concatenate(([0], line, [0]))
    diff = np.diff(padded)
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    max_gap = max(3, int(w * 0.006))
    merged: List[Tuple[int, int]] = []
    for s, e in zip(starts, ends):
        if merged and s - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], int(e))
        else:
            merged.append((int(s), int(e)))
    for s, e in merged:
        if s - 2 <= x_anchor <= e + 1:
            return (s, e - 1)
    best = min(merged, key=lambda r: min(abs(x_anchor - r[0]), abs(x_anchor - r[1] + 1)))
    return (best[0], best[1] - 1)


def _candidate_axis_rows(dark: np.ndarray) -> List[Tuple[int, int, int]]:
    """All rows containing a long horizontal dark run: (row, run_start, run_end).

    Runs in a row separated by small gaps are merged first — axis lines get
    broken by overlapping labels and anti-aliasing, and a truncated axis run
    cuts the frame (and every tick left of the break) out of calibration.
    """

    h, w = dark.shape
    max_gap = max(3, int(w * 0.02))
    out: List[Tuple[int, int, int]] = []
    prev_row = -10
    for y in range(h):
        line = dark[y]
        if line.sum() < w * 0.12:
            continue
        padded = np.concatenate(([0], line, [0]))
        diff = np.diff(padded)
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)
        # Merge runs split by gaps ≤ max_gap.
        merged: List[Tuple[int, int]] = []
        for s, e in zip(starts, ends):
            if merged and s - merged[-1][1] <= max_gap:
                merged[-1] = (merged[-1][0], int(e))
            else:
                merged.append((int(s), int(e)))
        best = max(merged, key=lambda r: r[1] - r[0])
        if best[1] - best[0] < w * 0.12:
            continue
        # Adjacent rows are one physical line (stroke thickness + AA): keep
        # the longer run and the lower row (closer to the true axis edge).
        if y - prev_row <= 2 and out:
            prev = out[-1]
            if (best[1] - best[0]) > (prev[2] - prev[1]):
                out[-1] = (y, best[0], best[1] - 1)
            prev_row = y
            continue
        out.append((y, best[0], best[1] - 1))
        prev_row = y
    return out


def _frame_from_bottom_line(
    dark: np.ndarray, y_line: int, x_start: int, x_end: int
) -> Optional[BBox]:
    """Build a frame candidate treating (y_line, x_start..x_end) as the bottom axis.

    The top edge is NOT "the nearest long dark row above": flat stacked-PXRD
    baselines span the plot width and masquerade as frame lines. The top is
    where content ends — the ink extent — optionally snapped to a long row
    found just around that boundary (the real top frame line).
    """

    h, w = dark.shape
    if y_line < h * 0.05:
        return None
    span = x_end - x_start
    band = dark[: y_line - 4, x_start:x_end + 1]
    if band.size == 0:
        return None
    row_ink = band.sum(axis=1)
    content_rows = np.flatnonzero(row_ink > 2)
    if content_rows.size == 0:
        return None
    ink_top = int(content_rows.min())
    # Snap to a frame line near the content boundary, if one exists.
    top = ink_top
    lo = max(0, ink_top - 5)
    hi = min(band.shape[0], ink_top + max(6, int((y_line - ink_top) * 0.12)))
    for y in range(lo, hi):
        if row_ink[y] > span * 0.55:
            top = y
            break
    if y_line - top < h * 0.05:
        return None
    return (x_start, int(top), x_end, y_line)


def _iou(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / max(1, union)


# --------------------------------------------------------------------------
# Tick-mark detection (pixel-precise tick x positions on the bottom axis)
# --------------------------------------------------------------------------


def detect_x_tick_pixels(crop_rgb: np.ndarray, frame: BBox) -> List[float]:
    """Detect tick-mark x positions (crop pixel coords) along the bottom axis.

    Ticks are short vertical strokes attached to the bottom frame line —
    either below it (outward) or above (inward). Detected centers are
    regularized onto an even grid (ticks are equally spaced on linear axes),
    which also recovers ticks lost to anti-aliasing. Returns sorted x centers.
    """

    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    x0, _, x1, y1 = frame
    dark = (gray < 128).astype(np.uint8)

    candidates: List[List[float]] = []
    for band_lo, band_hi, inward in (
        (y1 + 2, min(h, y1 + 14), False),    # outward ticks (preferred: no frame columns)
        (max(0, y1 - 13), y1 - 2, True),     # inward ticks
    ):
        if band_hi - band_lo < 3:
            continue
        band = dark[band_lo:band_hi, x0:x1 + 1]
        col_counts = band.sum(axis=0)
        thresh = max(3, int((band_hi - band_lo) * 0.45))
        active = col_counts >= thresh
        if inward:
            # A tick is a SHORT stroke attached to the axis. Interior vertical
            # strokes (gridlines, curve segments, drop lines) also cross the
            # inward band — require attachment to the axis line and reject
            # columns whose stroke continues well above the band.
            attached = dark[max(0, y1 - 3):y1, x0:x1 + 1].sum(axis=0) > 0
            tall_lo = max(0, y1 - 34)
            tall = dark[tall_lo:max(tall_lo + 1, y1 - 16), x0:x1 + 1].sum(axis=0) > 2
            active = active & attached & ~tall
        centers = [c + x0 for c in _cluster_centers(active)]
        if inward:
            # The left/right frame verticals cross the inward band; a corner
            # tick is indistinguishable from them, so drop edge columns.
            centers = [c for c in centers if x0 + 3 < c < x1 - 3]
        regular = _regularize_tick_grid(centers)
        if regular and 3 <= len(regular) <= 40:
            candidates.append(regular)

    if not candidates:
        return []
    # Prefer the candidate with more ticks (denser axes carry more info)
    best = max(candidates, key=len)
    return best


def _regularize_tick_grid(centers: Sequence[float], snap_tol: float = 0.27) -> List[float]:
    """Snap detected ticks to an even grid, filling gaps left by missed ticks.

    Gaps must be near-integer multiples of the base spacing; otherwise the
    detection is judged unreliable and discarded.
    """

    pts = sorted(float(c) for c in centers)
    if len(pts) < 3:
        return []
    gaps = np.diff(pts)
    base = float(np.median(gaps))
    if base <= 4:
        return []
    multiples = np.round(gaps / base)
    if np.any(multiples < 1) or np.any(multiples > 3):
        return []
    # Most gaps must be single steps, or the "grid" is a coincidence.
    if float(np.mean(multiples == 1)) < 0.5:
        return []
    resid = np.abs(gaps - multiples * base)
    if np.any(resid > snap_tol * base):
        return []
    # Walk original points, interpolating ticks lost inside multi-step gaps.
    grid: List[float] = [pts[0]]
    for gap, mult in zip(gaps, multiples):
        prev = grid[-1]
        for k in range(1, int(mult) + 1):
            grid.append(prev + gap * k / mult)
    return grid


def _cluster_centers(active: np.ndarray, max_width: int = 12) -> List[float]:
    padded = np.concatenate(([False], active, [False]))
    diff = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    centers = []
    for s, e in zip(starts, ends):
        if e - s <= max_width:
            centers.append(float((s + e - 1) / 2.0))
    return centers


def _spacing_regular(centers: Sequence[float], tolerance: float = 0.35) -> bool:
    if len(centers) < 3:
        return False
    gaps = np.diff(sorted(centers))
    med = float(np.median(gaps))
    if med <= 4:
        return False
    return bool(np.all(np.abs(gaps - med) <= tolerance * med))


# --------------------------------------------------------------------------
# Curve foreground mask
# --------------------------------------------------------------------------


@dataclass
class PlotMask:
    foreground: np.ndarray             # bool, plot-interior coords; curve-ish ink
    text_boxes: List[BBox] = field(default_factory=list)


def build_curve_foreground(
    plot_rgb: np.ndarray,
    *,
    text_boxes: Optional[Sequence[BBox]] = None,
) -> PlotMask:
    """Foreground = non-background ink inside the plot interior, minus text/frame.

    Works for both colored and grayscale curves. Background is estimated from
    the dominant light color.
    """

    h, w = plot_rgb.shape[:2]
    gray = cv2.cvtColor(plot_rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(plot_rgb, cv2.COLOR_RGB2HSV)

    bg_level = float(np.percentile(gray, 75))
    ink = gray < max(80.0, bg_level - 45)
    saturated = hsv[..., 1] > 60
    fg = np.logical_or(ink, np.logical_and(saturated, gray < 235))

    # Frame strokes are deliberately NOT stripped. Flat PXRD baselines hug —
    # often overlap — the bottom axis, so removing border bands deletes real
    # data; the anchor-centered snap weighting already keeps axis lines from
    # hijacking a trace.

    boxes = list(text_boxes or [])
    for (bx0, by0, bx1, by1) in boxes:
        bx0, by0 = max(0, bx0), max(0, by0)
        bx1, by1 = min(w, bx1), min(h, by1)
        fg[by0:by1, bx0:bx1] = False

    return PlotMask(foreground=fg, text_boxes=boxes)


def color_mask_for(plot_rgb: np.ndarray, color_name: str) -> Optional[np.ndarray]:
    """Binary mask of pixels matching a simple English color name."""

    hsv = cv2.cvtColor(plot_rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(plot_rgb, cv2.COLOR_RGB2GRAY)
    h_, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    name = (color_name or "").strip().lower()

    chroma = s > 60
    ranges = {
        "red": ((h_ <= 8) | (h_ >= 172)),
        "orange": (h_ > 8) & (h_ <= 22),
        "yellow": (h_ > 22) & (h_ <= 38),
        "green": (h_ > 38) & (h_ <= 85),
        "cyan": (h_ > 85) & (h_ <= 100),
        "blue": (h_ > 100) & (h_ <= 130),
        "purple": (h_ > 130) & (h_ <= 155),
        "magenta": (h_ > 155) & (h_ < 172),
        "pink": ((h_ < 12) | (h_ > 160)) & (s > 30) & (s < 140) & (v > 150),
    }
    if name in ranges:
        return np.asarray(ranges[name] & chroma & (v > 40))
    if name in ("black", "dark", "darkgray", "dark gray"):
        return np.asarray((gray < 110) & (s < 90))
    if name in ("gray", "grey"):
        return np.asarray((gray >= 90) & (gray < 185) & (s < 60))
    if name == "brown":
        return np.asarray((h_ > 5) & (h_ <= 25) & (s > 50) & (v < 200))
    return None


# --------------------------------------------------------------------------
# Anchor-guided snap tracing
# --------------------------------------------------------------------------


@dataclass
class SnapTrace:
    """Result of refining an LLM anchor polyline against real pixels."""

    y_px: np.ndarray                 # per-column y (plot-interior coords), NaN where unsnapped
    snapped: np.ndarray              # bool per column
    snap_rate: float
    mean_residual_px: float
    # Interpolated anchor y per column (NaN outside the polyline's x-range).
    # Kept so trace_fidelity can measure recall against where the curve was
    # *claimed* to be, not against where the trace ended up — the latter is
    # circular and cannot see abandoned columns.
    anchor_y: Optional[np.ndarray] = None


def snap_trace(
    anchor_xy_frac: np.ndarray,      # (N,2) fractional coords in plot box
    plot_shape: Tuple[int, int],     # (h, w)
    candidate_mask: np.ndarray,      # bool (h, w): allowed curve pixels for this series
    *,
    window_frac: float = 0.045,
    smooth_continuity_px: Optional[float] = None,
) -> SnapTrace:
    """Refine a coarse anchor polyline to pixel precision.

    For every pixel column, interpolate the anchor y, then search a vertical
    window around it for candidate pixels; snap to their weighted centroid.
    A continuity pass suppresses isolated jumps.
    """

    h, w = plot_shape
    xs = np.clip(anchor_xy_frac[:, 0], 0.0, 1.0) * (w - 1)
    ys = np.clip(anchor_xy_frac[:, 1], 0.0, 1.0) * (h - 1)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    # Deduplicate x for interpolation
    ux, idx = np.unique(xs, return_index=True)
    if ux.size < 2:
        return SnapTrace(
            y_px=np.full(w, np.nan), snapped=np.zeros(w, bool),
            snap_rate=0.0, mean_residual_px=0.0,
            anchor_y=np.full(w, np.nan),
        )
    uy = ys[idx]

    cols = np.arange(w)
    anchor_y = np.interp(cols, ux, uy, left=np.nan, right=np.nan)

    window = max(4, int(h * window_frac))
    out_y = np.full(w, np.nan)
    snapped = np.zeros(w, dtype=bool)
    residuals: List[float] = []

    for x in cols:
        ay = anchor_y[x]
        if np.isnan(ay):
            continue
        lo = max(0, int(ay) - window)
        hi = min(h, int(ay) + window + 1)
        col = candidate_mask[lo:hi, x]
        ys_local = np.flatnonzero(col)
        if ys_local.size == 0:
            out_y[x] = ay
            continue
        # Weight toward the anchor to resist grabbing a neighboring curve.
        abs_y = ys_local + lo
        dist = np.abs(abs_y - ay)
        weights = 1.0 / (1.0 + (dist / max(1.0, window / 2.0)) ** 2)
        y_snap = float(np.average(abs_y, weights=weights))
        out_y[x] = y_snap
        snapped[x] = True
        residuals.append(abs(y_snap - ay))

    valid = ~np.isnan(out_y)
    if valid.sum() >= 5:
        out_y = _suppress_jumps(out_y, snapped, max_jump=max(6.0, h * 0.05))

    # Trim unsnapped leading/trailing runs: a polyline stretched past the real
    # data extent (models pad to the grid edge) must not fabricate values
    # where the figure has no ink. Interior gaps keep the anchor interpolation.
    snapped_cols = np.flatnonzero(snapped)
    if snapped_cols.size:
        out_y[: snapped_cols[0]] = np.nan
        out_y[snapped_cols[-1] + 1:] = np.nan

    n_eval_cols = int((~np.isnan(out_y)).sum())
    rate = float(snapped.sum() / max(1, n_eval_cols))
    mean_res = float(np.mean(residuals)) if residuals else 0.0
    return SnapTrace(
        y_px=out_y, snapped=snapped, snap_rate=min(1.0, rate),
        mean_residual_px=mean_res, anchor_y=anchor_y,
    )


def _suppress_jumps(y: np.ndarray, snapped: np.ndarray, max_jump: float) -> np.ndarray:
    """Median-filter only the snapped points that jump implausibly."""

    out = y.copy()
    valid_idx = np.flatnonzero(~np.isnan(y))
    if valid_idx.size < 7:
        return out
    vals = y[valid_idx]
    med = cv2.medianBlur(vals.astype(np.float32).reshape(-1, 1), 5).ravel()
    bad = np.abs(vals - med) > max_jump
    vals[bad] = med[bad]
    out[valid_idx] = vals
    return out


# --------------------------------------------------------------------------
# Non-circular pixel-fidelity verification
# --------------------------------------------------------------------------


@dataclass
class FidelityScore:
    precision: float    # traced path lies on real ink
    recall: float       # real ink near the path is explained by it
    n_eval_cols: int


def trace_fidelity(
    y_px: np.ndarray,                 # per-column trace (plot coords), NaN allowed
    candidate_mask: np.ndarray,       # bool (h,w) candidate pixels for this series
    *,
    anchor_y: Optional[np.ndarray] = None,   # per-column anchor y (SnapTrace.anchor_y)
    tol_px: Optional[int] = None,
    band_frac: float = 0.10,
) -> FidelityScore:
    """Compare the final trace against ORIGINAL figure pixels (not our own replot).

    precision: of the columns the trace covers, the fraction whose y lies within
               tol of a candidate pixel in that column. "Am I standing on ink?"

    recall:    of the columns where this series is *expected* to have ink, the
               fraction the trace actually explains within tol. "Did I explain
               the ink that is there?"

    The recall denominator is anchored on `anchor_y`, NOT on the trace itself,
    and deliberately includes columns the trace abandoned (y_px = NaN). Scoring
    recall inside a band around the trace makes it circular: ink the trace never
    went near cannot enter the denominator, so a trace that quits early scores
    *higher* the more it abandons (a trace covering 10% of a curve scored a
    perfect 1.0 under the old definition). Anchoring on the LLM polyline — which
    spans the whole curve — is what lets recall see abandoned ink.

    Without `anchor_y` the fallback is stricter, not laxer: every column holding
    ink counts toward the denominator, with no band. On single-curve panels that
    is the honest measure; on multi-curve panels it under-reports recall because
    a neighbour's ink also lands in the denominator. Pass `anchor_y` there.
    """

    h, w = candidate_mask.shape
    tol = tol_px if tol_px is not None else max(3, int(h * 0.02))
    band = max(6, int(h * band_frac))

    ty = np.full(w, np.nan, dtype=float)
    n = min(w, y_px.size)
    ty[:n] = y_px[:n]
    traced = ~np.isnan(ty)

    rows = np.arange(h)[:, None]                       # (h, 1)
    big = float(h * 4)                                 # sentinel > any real distance

    # Distance from each column's trace y to the nearest candidate pixel in that
    # column; +inf where the column holds no ink at all.
    d_trace = np.where(candidate_mask, np.abs(rows - ty[None, :]), np.inf)
    nearest_to_trace = d_trace.min(axis=0)             # (w,)

    evaluated = int(traced.sum())
    hits = int((traced & (nearest_to_trace <= tol)).sum())
    precision = hits / evaluated if evaluated else 0.0

    if anchor_y is not None:
        ay = np.full(w, np.nan, dtype=float)
        m = min(w, anchor_y.size)
        ay[:m] = anchor_y[:m]
        have_anchor = ~np.isnan(ay)
        # Ink belonging to this series: within `band` of where the anchor claims
        # the curve runs. Independent of where the trace actually went.
        in_band = candidate_mask & (np.abs(rows - ay[None, :]) <= band)
        in_band &= have_anchor[None, :]
        ink_cols_mask = in_band.any(axis=0)
        # Of that banded ink, how close did the trace come? Abandoned columns
        # (ty = NaN) yield `big`, so they count in the denominator and fail the
        # tol test — exactly the partial-coverage penalty the old code missed.
        d_band = np.where(in_band, np.abs(rows - np.where(traced, ty, big)[None, :]), np.inf)
        nearest_in_band = d_band.min(axis=0)
        explained = ink_cols_mask & traced & (nearest_in_band <= tol)
    else:
        ink_cols_mask = candidate_mask.any(axis=0)
        explained = ink_cols_mask & traced & (nearest_to_trace <= tol)

    ink_cols = int(ink_cols_mask.sum())
    recall = int(explained.sum()) / ink_cols if ink_cols else 0.0
    return FidelityScore(precision=float(precision), recall=float(recall), n_eval_cols=evaluated)


# --------------------------------------------------------------------------
# Overlay rendering (QA artifact)
# --------------------------------------------------------------------------

_OVERLAY_COLORS = [
    (255, 0, 0), (0, 160, 0), (0, 90, 255), (255, 140, 0),
    (160, 0, 200), (0, 180, 180), (220, 0, 130), (90, 90, 90),
]


def render_overlay(
    crop_rgb: np.ndarray,
    frame: BBox,
    traces: Sequence[Tuple[np.ndarray, str]],   # (per-column y in plot coords, label)
    out_path: str,
) -> None:
    """Draw extracted traces over the original crop — the primary QA artifact."""

    img = crop_rgb.copy()
    x0, y0, x1, y1 = frame
    cv2.rectangle(img, (x0, y0), (x1, y1), (0, 200, 0), 1)
    for i, (y_px, label) in enumerate(traces):
        color = _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
        pts = [
            (x0 + x, int(round(y0 + y)))
            for x, y in enumerate(y_px)
            if not np.isnan(y)
        ]
        for a, b in zip(pts[:-1], pts[1:]):
            if abs(b[0] - a[0]) <= 3:
                cv2.line(img, a, b, color, 2, cv2.LINE_AA)
        if pts and label:
            cv2.putText(
                img, label[:28], (x0 + 6, max(14, pts[0][1] - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA,
            )
    cv2.imwrite(out_path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


def draw_grid_overlay(crop_rgb: np.ndarray, frame: BBox) -> np.ndarray:
    """Draw a labeled 10x10 fractional grid over the plot box.

    Grounding aid for the LLM anchor-tracing call: known-position gridlines let
    the model express the polyline in fractions reliably.
    """

    img = crop_rgb.copy()
    x0, y0, x1, y1 = frame
    w, h = x1 - x0, y1 - y0
    for i in range(11):
        fx = x0 + int(round(i * w / 10.0))
        fy = y0 + int(round(i * h / 10.0))
        cv2.line(img, (fx, y0), (fx, y1), (255, 80, 80), 1)
        cv2.line(img, (x0, fy), (x1, fy), (255, 80, 80), 1)
        cv2.putText(img, f"{i/10:.1f}", (fx - 10, y1 + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(img, f"{i/10:.1f}", (max(0, x0 - 34), fy + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 0, 0), 1, cv2.LINE_AA)
    return img
