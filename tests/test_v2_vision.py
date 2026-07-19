"""Unit tests for v2 CV primitives on synthetic figures."""

import numpy as np
import pytest

from pxrd_fetcher.v2.vision import (
    build_curve_foreground,
    detect_plot_frame,
    detect_x_tick_pixels,
    finalize_frame,
    select_plot_frame,
    snap_trace,
    trace_fidelity,
)


def synthetic_panel(
    *,
    w=700, h=500,
    frame=(60, 30, 660, 430),
    n_ticks=9,
    tick_len=8,
    curve_fn=None,
    curve_color=(0, 0, 0),
    draw_top=True, draw_right=True,
):
    """Render a minimal synthetic PXRD-like panel."""

    img = np.full((h, w, 3), 255, dtype=np.uint8)
    x0, y0, x1, y1 = frame
    img[y1, x0:x1 + 1] = 0                      # bottom axis
    img[y0:y1 + 1, x0] = 0                      # left axis
    if draw_top:
        img[y0, x0:x1 + 1] = 0
    if draw_right:
        img[y0:y1 + 1, x1] = 0
    # outward ticks
    for tx in np.linspace(x0, x1, n_ticks):
        img[y1 + 1:y1 + 1 + tick_len, int(round(tx))] = 0
    # curve
    if curve_fn is not None:
        for px in range(x0 + 1, x1):
            t = (px - x0) / (x1 - x0)
            cy = int(round(y0 + curve_fn(t) * (y1 - y0)))
            cy = np.clip(cy, y0 + 1, y1 - 1)
            img[cy - 1:cy + 2, px] = curve_color
    return img


def peaky(t):
    """Curve with a sharp peak at t=0.2 and a broad hump at t=0.6 (y_frac)."""
    y = 0.85
    y -= 0.55 * np.exp(-((t - 0.2) ** 2) / 0.0008)
    y -= 0.25 * np.exp(-((t - 0.6) ** 2) / 0.01)
    return y


class TestFrameDetection:
    def test_full_rectangle(self):
        img = synthetic_panel()
        frame = detect_plot_frame(img)
        assert frame is not None
        x0, y0, x1, y1 = frame
        assert abs(x0 - 60) <= 3 and abs(y1 - 430) <= 3
        assert abs(x1 - 660) <= 3 and abs(y0 - 30) <= 3

    def test_bottom_left_only(self):
        img = synthetic_panel(draw_top=False, draw_right=False, curve_fn=peaky)
        frame = detect_plot_frame(img)
        assert frame is not None
        x0, y0, x1, y1 = frame
        assert abs(x0 - 60) <= 3 and abs(y1 - 430) <= 3
        # right/top fall back to ink extents — must still cover the curve
        assert x1 > 600 and y0 < 200


class TestTickDetection:
    def test_nine_even_ticks(self):
        img = synthetic_panel(n_ticks=9)
        frame = detect_plot_frame(img)
        ticks = detect_x_tick_pixels(img, frame)
        assert len(ticks) == 9
        gaps = np.diff(ticks)
        assert np.allclose(gaps, gaps[0], atol=2.0)


class TestSnapAndFidelity:
    def test_snap_recovers_curve_from_noisy_anchor(self):
        img = synthetic_panel(curve_fn=peaky)
        frame = detect_plot_frame(img)
        x0, y0, x1, y1 = frame
        plot = img[y0:y1, x0:x1]
        pm = build_curve_foreground(plot)
        h, w = plot.shape[:2]

        rng = np.random.default_rng(7)
        ts = np.linspace(0.01, 0.99, 50)
        anchor = np.stack(
            [ts, np.array([peaky(t) for t in ts]) + rng.normal(0, 0.03, ts.size)],
            axis=1,
        )
        tr = snap_trace(anchor, (h, w), pm.foreground)
        fid = trace_fidelity(tr.y_px, pm.foreground, anchor_y=tr.anchor_y)
        assert tr.snap_rate > 0.9
        assert fid.precision > 0.9
        assert fid.recall > 0.85
        # The sharp peak apex must be captured (min y near t=0.2)
        peak_col = int(0.2 * w)
        apex = np.nanmin(tr.y_px[peak_col - 8:peak_col + 8])
        expected_apex = (peaky(0.2)) * (h - 1)
        assert abs(apex - expected_apex) < h * 0.05

    def test_fidelity_rejects_wrong_trace(self):
        img = synthetic_panel(curve_fn=peaky)
        frame = detect_plot_frame(img)
        x0, y0, x1, y1 = frame
        plot = img[y0:y1, x0:x1]
        pm = build_curve_foreground(plot)
        h, w = plot.shape[:2]
        flat = np.full(w, h * 0.5)  # a made-up flat line through the middle
        fid = trace_fidelity(flat, pm.foreground)
        assert fid.precision < 0.3
        assert fid.recall < 0.3

    def test_recall_penalizes_abandoned_columns(self):
        """A trace that quits early must not score well.

        The pre-fix definition banded recall around the trace itself and skipped
        NaN columns, so abandoning ink *raised* the score — a trace covering 10%
        of the curve scored a perfect 1.0.
        """
        img = synthetic_panel(curve_fn=peaky)
        x0, y0, x1, y1 = detect_plot_frame(img)
        plot = img[y0:y1, x0:x1]
        pm = build_curve_foreground(plot)
        h, w = plot.shape[:2]

        ts = np.linspace(0.01, 0.99, 50)
        anchor = np.stack([ts, np.array([peaky(t) for t in ts])], axis=1)
        tr = snap_trace(anchor, (h, w), pm.foreground)
        assert trace_fidelity(tr.y_px, pm.foreground, anchor_y=tr.anchor_y).recall > 0.9

        # recall must track coverage, not reward abandonment
        prev = 1.0
        for frac in (0.5, 0.25, 0.1):
            part = tr.y_px.copy()
            part[int(w * frac):] = np.nan
            r = trace_fidelity(part, pm.foreground, anchor_y=tr.anchor_y).recall
            assert r < prev, f"recall rose when coverage fell to {frac}"
            assert abs(r - frac) < 0.15, f"recall {r:.2f} should track coverage {frac}"
            prev = r

    def test_recall_is_not_pinned_above_precision(self):
        """recall must be able to fail independently of precision.

        Pre-fix, tol < band made recall >= precision an algebraic identity, so
        the recall gate was dead code: it never rejected a curve in 11,445.
        """
        img = synthetic_panel(curve_fn=peaky)
        x0, y0, x1, y1 = detect_plot_frame(img)
        plot = img[y0:y1, x0:x1]
        pm = build_curve_foreground(plot)
        h, w = plot.shape[:2]

        ts = np.linspace(0.01, 0.99, 50)
        anchor = np.stack([ts, np.array([peaky(t) for t in ts])], axis=1)
        tr = snap_trace(anchor, (h, w), pm.foreground)
        part = tr.y_px.copy()
        part[int(w * 0.25):] = np.nan          # accurate where drawn, mostly absent
        fid = trace_fidelity(part, pm.foreground, anchor_y=tr.anchor_y)
        assert fid.precision > 0.9              # what it drew, it drew well
        assert fid.recall < fid.precision       # but it explained little ink


class TestFrameSelection:
    def test_picks_evidence_backed_panel_in_two_panel_crop(self):
        a = synthetic_panel(w=700, h=500, curve_fn=peaky)          # has ticks+curve
        b = synthetic_panel(w=700, h=500, n_ticks=0, curve_fn=None)  # bare box
        img = np.concatenate([a, b], axis=1)
        sel = select_plot_frame(
            img,
            hint=(0.02, 0.02, 0.5, 0.95),
            numeric_token_xs=[(x, 445) for x in np.linspace(60, 660, 9)],
        )
        assert sel is not None
        x0, y0, x1, y1 = sel
        assert x1 < 700  # stayed in the left panel
        assert abs(y1 - 430) <= 3


def borderless_panel(
    *, w=900, h=600, axis_y=520, axis_x=(80, 820), tick_xs=None, peak_x=140
):
    """Stacked-PXRD style panel with NO box: bottom axis + ticks only.

    Three stacked traces share a tall peak at the same 2θ (peak_x) — together
    those peak strokes pile enough ink into one column to fool any per-column
    ink *count*, while never forming a contiguous frame-like stroke.
    """

    img = np.full((h, w, 3), 255, dtype=np.uint8)
    ax0, ax1 = axis_x
    img[axis_y, ax0:ax1 + 1] = 0
    for tx in tick_xs if tick_xs is not None else np.linspace(100, 790, 8):
        img[axis_y + 1:axis_y + 9, int(round(tx))] = 0
    for base_y in (200, 320, 440):
        img[base_y, ax0 + 5:ax1 - 5] = 0                       # flat baseline
        img[base_y - 90:base_y, peak_x - 1:peak_x + 2] = 0     # shared sharp peak
    return img


class TestBorderlessFrames:
    def test_shared_peak_column_is_not_a_frame_edge(self):
        # The bug seen on real papers: stacked curves' shared first peak
        # accumulates column ink and was adopted as the "left frame line".
        img = borderless_panel()
        sel = select_plot_frame(img)
        assert sel is not None
        final = finalize_frame(img, sel)
        x0, y0, x1, y1 = final
        assert abs(y1 - 520) <= 3
        assert abs(x0 - 80) <= 5, f"left edge invented at {x0} (peak col is 140)"
        assert abs(x1 - 820) <= 5

    def test_neighbor_axis_run_is_not_merged(self):
        # A second panel's axis sliver sits 30px to the right (crop padding
        # leak). An open right edge must stop at OUR axis run's end.
        img = borderless_panel(axis_x=(80, 700))
        img[520, 730:880] = 0                       # neighbor's axis sliver
        img[380:519, 760:763] = 0                   # neighbor's curve ink
        stretched = (80, 100, 880, 520)             # candidate welded both panels
        final = finalize_frame(
            img, stretched, hint=(80 / 900, 100 / 600, 700 / 900, 520 / 600)
        )
        x0, _, x1, _ = final
        assert x0 >= 76
        assert x1 <= 706, f"right edge {x1} leaked into the neighbor panel"

    def test_open_edge_bounded_by_tick_evidence(self):
        # One long axis run, but ticks live only on its left part: an open
        # edge may not extend more than ~1.4 tick gaps past the last tick.
        img = borderless_panel(
            axis_x=(80, 880), tick_xs=np.linspace(100, 600, 8)
        )
        final = finalize_frame(img, (80, 100, 880, 520))
        _, _, x1, _ = final
        gap = 500 / 7.0
        assert x1 <= 600 + 1.4 * gap + 4, f"right edge {x1} ignored tick extent"

    def test_full_box_is_left_untouched(self):
        img = synthetic_panel(curve_fn=peaky)
        frame = detect_plot_frame(img)
        assert frame is not None
        # Verified frame lines on both sides: finalize must change nothing,
        # even with a hint that disagrees.
        assert finalize_frame(img, frame, hint=(0.2, 0.1, 0.8, 0.9)) == frame
