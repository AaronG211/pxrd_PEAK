"""Peak-height fidelity audit — anchor-INDEPENDENT check of traced curves.

Motivation: tracing and its fidelity metrics both look only near the LLM
anchor polyline, so a peak the anchor missed can be amputated while snap /
precision / recall all score ~1.0 (reproduced synthetically: 300px:150px peaks
extracted as 23px:24px with all gates passing). This audit measures how often
that actually happens in the corpus, using ground truth that never sees the
anchor: the upper envelope of the original figure ink.

Scope: single-curve figures only (there the ink's upper envelope IS the curve;
multi-curve figures have no per-series envelope attribution).

Per figure:
  1. Replay the plot frame offline (same choose_frame path as the pipeline).
  2. Foreground ink -> connected components; drop pure horizontal rules
     (h<=4px & w>=50% frame), pure vertical rules (w<=4px & h>=50% frame),
     and small text-like blobs (width < 5% frame AND area < 300, unless it is
     the largest component). Border bands (4px inside each edge) stripped.
  3. Ink envelope: per column, height of the topmost kept ink above the frame
     bottom. Trace profile: the series CSV mapped back to pixel x via the
     stored calibration fit. Both normalized to [0,1] over common support
     (5th..99.5th percentile scaling).
  4. Metrics:
       worst_peak_loss  max over envelope peaks (prominence >= 0.15, width
                        <= 80 cols) of env_value - trace_value at that column
       ratio_distortion |log2( (t2/t1) / (e2/e1) )| for the two tallest
                        envelope peaks — the "2:1 became 1:1" number
  5. flag = worst_peak_loss >= 0.35  (trace captured < ~half of a real peak)

--controls injects synthetic amputation (clip the trace's tallest peak to 30%)
into the first N figures and reports whether the audit catches it — the audit
of the audit.

Pure local compute; zero API. Results -> outputs/analysis/peak_audit_v0.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.signal import find_peaks, peak_widths

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cv2  # noqa: E402
from pxrd_fetcher.ocr import get_ocr_backend  # noqa: E402
from pxrd_fetcher.v2.pipeline import choose_frame  # noqa: E402
from pxrd_fetcher.v2.schemas import FigureAnalysis  # noqa: E402
from pxrd_fetcher.v2.vision import build_curve_foreground  # noqa: E402

ENV_PEAK_PROM = 0.15
ENV_PEAK_MAX_W = 80
LOSS_FLAG = 0.35
BORDER = 4


def ink_envelope(plot_rgb: np.ndarray) -> np.ndarray:
    """Height-above-bottom of the topmost curve ink per column (NaN = no ink)."""

    h, w = plot_rgb.shape[:2]
    fg = build_curve_foreground(plot_rgb).foreground
    fg[:BORDER, :] = False
    fg[h - BORDER:, :] = False
    fg[:, :BORDER] = False
    fg[:, w - BORDER:] = False

    # Frame-overshoot guard: when the chosen frame swallowed a header bar or
    # neighboring panel above the plot, a full-width dark band (the real
    # panel divider) sits inside the frame — trim everything above the
    # lowest such band in the top 60%.
    row_frac = fg.mean(axis=1)
    bands = np.flatnonzero(row_frac[: int(0.6 * h)] > 0.5)
    if bands.size:
        fg[: bands.max() + 3, :] = False

    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        fg.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, dtype=bool)
    for i in range(1, n):
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        bottom = stats[i, cv2.CC_STAT_TOP] + ch
        # The experimental curve is wide and its baseline hugs the plot
        # bottom. In-plot molecule drawings, legends, header rules and
        # Bragg-stick rows all float higher or are narrow — requiring the
        # component to reach the bottom quarter removes them wholesale.
        if bottom < 0.75 * h:
            continue
        if ch <= 8 and cw >= 0.4 * w:              # horizontal rule
            continue
        if cw <= 4 and ch >= 0.5 * h:              # vertical rule / cursor
            continue
        if cw < 0.10 * w and area < 500:           # text / tick stubs
            continue
        keep[i] = True
    mask = keep[labels]

    env = np.full(w, np.nan)
    for x in range(w):
        ys = np.flatnonzero(mask[:, x])
        if ys.size:
            # median ink row = the stroke CENTERLINE — matches what tracing
            # targets and is immune to noisy-band thickness (top-of-ink is not)
            env[x] = (h - 1 - BORDER) - float(np.median(ys))
    return env


def norm_profile(v: np.ndarray, support: np.ndarray) -> np.ndarray:
    vals = v[support]
    lo = np.nanpercentile(vals, 5)
    hi = np.nanpercentile(vals, 99.5)
    if hi - lo <= 0:
        return np.full_like(v, np.nan)
    return np.clip((v - lo) / (hi - lo), 0, 1.3)


def audit_figure(fig_dir: Path, series_id: str, ocr, *, amputate: bool = False):
    d = json.loads((fig_dir / "result.json").read_text())
    crop = np.asarray(Image.open(d["crop_path"]).convert("RGB"))
    analysis = FigureAnalysis.model_validate(d["analysis"])
    # Prefer the run-time frame when persisted (newer results): re-derived
    # frames can differ by a few px and misalign sharp peaks (3 of 11 false
    # flags in the v0 audit were exactly this).
    frame = tuple(d["frame_bbox"]) if d.get("frame_bbox") else None
    if frame is None:
        frame = choose_frame(crop, analysis, ocr)
    if frame is None:
        return {"status": "no_frame"}
    x0, y0, x1, y1 = frame
    plot = crop[y0:y1, x0:x1]
    if plot.size == 0:
        return {"status": "empty_plot"}

    fit = d["calibration"]["fit"]
    slope, intercept = fit["slope"], fit["intercept"]

    csvs = sorted(fig_dir.glob(f"*{series_id.split('-')[-1]}.csv"))
    csv_path = csvs[0] if csvs else None
    if csv_path is None:
        return {"status": "no_csv"}
    tt, iv = [], []
    with csv_path.open() as fh:
        for row in csv.DictReader(fh):
            try:
                tt.append(float(row["two_theta_deg"]))
                iv.append(float(row["relative_intensity"]))
            except (KeyError, ValueError):
                continue
    if len(tt) < 50:
        return {"status": "short_csv"}
    tt, iv = np.asarray(tt), np.asarray(iv)

    if amputate:                                   # positive control
        base = np.percentile(iv, 20)
        peak_i = int(np.argmax(iv))
        lo_i, hi_i = max(0, peak_i - 25), min(len(iv), peak_i + 25)
        seg = iv[lo_i:hi_i]
        iv = iv.copy()
        iv[lo_i:hi_i] = base + (seg - base) * 0.30

    env = ink_envelope(plot)
    w = env.size
    trace = np.full(w, np.nan)
    x_px = (tt - intercept) / slope - x0            # crop -> plot coords
    inside = (x_px >= 0) & (x_px <= w - 1)
    if inside.sum() < 30:
        return {"status": "trace_outside_frame"}
    cols = np.arange(w)
    lo_c, hi_c = int(x_px[inside].min()), int(x_px[inside].max())
    seg = (cols >= lo_c) & (cols <= hi_c)
    trace[seg] = np.interp(cols[seg], x_px[inside], iv[inside])

    support = ~np.isnan(env) & ~np.isnan(trace)
    if support.sum() < 0.3 * w:
        return {"status": "little_overlap", "overlap_frac": round(support.mean(), 3)}

    # No pre-smoothing: the min-width gate (>=3 cols) rejects pixel spikes on
    # the raw profile, and the rectangle test needs SHARP corners to see —
    # a median filter would round in-plot bars into peak-like shapes.
    env_n = norm_profile(env, support)
    tr_n = norm_profile(trace, support)
    env_n[~support] = np.nan
    tr_n[~support] = np.nan

    # Occlusion guard: an in-plot drawing/legend can form a flat ceiling that
    # hides the real curve from the ink profile entirely.
    ev = env[support]
    flat_frac = float(np.mean(np.abs(ev - np.median(ev)) <= 1.5))
    if flat_frac > 0.6:
        return {"status": "env_occluded", "flat_frac": round(flat_frac, 3)}

    def peak_losses(src, dst):
        """Per-peak prominence in src minus matched local prominence in dst.

        Prominence-vs-local-base is slope-immune: a steep background lifts a
        peak and its flanks together, so ALS-subtracted traces and raw ink
        centerlines stay comparable without any explicit baseline model."""
        fill = np.where(np.isnan(src), 0, src)
        pk, props = find_peaks(fill, prominence=ENV_PEAK_PROM,
                               width=(3, ENV_PEAK_MAX_W))
        if len(pk):
            # rel_height counts DOWN from the apex: 0.1 = width near the top,
            # 0.5 = half-prominence width.
            w_top = peak_widths(fill, pk, rel_height=0.1)[0]
            w_half = peak_widths(fill, pk, rel_height=0.5)[0]
        out = []
        for j, (p, width) in enumerate(zip(pk, props["widths"])):
            if not support[p] or not (edge_lo <= p <= edge_hi):
                continue
            # Rectangular pulses (flat top, vertical sides) are in-plot bars /
            # rules, not reflections: a diffraction peak narrows sharply
            # toward its apex, a rectangle does not.
            if w_top[j] > 0.75 * w_half[j]:
                continue
            # Symmetric local prominence: same window, same formula for BOTH
            # profiles — scipy's global prominence vs a local window would
            # systematically shortchange narrow peaks on elevated shoulders.
            win = int(max(8, 1.5 * width))
            sl_peak = slice(max(0, p - 4), min(w, p + 5))
            sl_base = slice(max(0, p - win), min(w, p + win))
            def local_prom(v):
                vmax = np.nanmax(v[sl_peak])
                vbase = np.nanmin(v[sl_base])
                return 0.0 if (np.isnan(vmax) or np.isnan(vbase)) \
                    else float(vmax - vbase)
            s_prom = local_prom(src)
            if s_prom < ENV_PEAK_PROM:
                continue
            out.append((int(p), round(s_prom, 3), round(local_prom(dst), 3)))
        return out

    edge_lo, edge_hi = lo_c + 10, hi_c - 10
    peak_rows = peak_losses(env_n, tr_n)
    losses = [e - t for _, e, t in peak_rows]
    gains = [t - e for _, t, e in peak_losses(tr_n, env_n)]

    ratio_dist = None
    if len(peak_rows) >= 2:
        order = np.argsort([e for _, e, _ in peak_rows])[::-1][:2]
        (p1, e1, t1), (p2, e2, t2) = peak_rows[order[0]], peak_rows[order[1]]
        t1 = max(t1, 1e-3)
        t2 = max(t2, 1e-3)
        if e1 > 0.2 and e2 > 0.1 and t1 > 0.02:
            ratio_dist = abs(float(np.log2((t2 / t1) / (e2 / e1))))

    worst = max(losses) if losses else 0.0
    worst_gain = max(gains) if gains else 0.0
    return {
        "status": "ok",
        "n_env_peaks": len(peak_rows),
        "worst_peak_loss": round(float(worst), 3),
        "worst_trace_gain": round(float(worst_gain), 3),
        "ratio_distortion_log2": None if ratio_dist is None else round(ratio_dist, 3),
        "flag_amputated": bool(worst >= LOSS_FLAG or worst_gain >= LOSS_FLAG),
        "peaks": peak_rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="outputs/analysis/peak_audit_sample.json")
    ap.add_argument("--out", default="outputs/analysis/peak_audit_v0.csv")
    ap.add_argument("--controls", type=int, default=0,
                    help="run synthetic-amputation positive controls on first N")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    sample = json.loads(Path(args.sample).read_text())
    if args.limit:
        sample = sample[: args.limit]
    ocr = get_ocr_backend("rapidocr")

    if args.controls:
        print("=== POSITIVE CONTROLS (tallest trace peak clipped to 30%) ===")
        caught = 0
        for item in sample[: args.controls]:
            fig_dir = Path(f"outputs/full-run-01/{item['paper_id']}/{item['figure_id']}")
            clean = audit_figure(fig_dir, item["series_id"], ocr)
            hurt = audit_figure(fig_dir, item["series_id"], ocr, amputate=True)
            if clean.get("status") != "ok":
                print(f"  {item['figure_id']}: skipped ({clean.get('status')})")
                continue
            c_flag, h_flag = clean.get("flag_amputated"), hurt.get("flag_amputated")
            caught += int(h_flag and not c_flag)
            print(f"  {item['figure_id']}: clean loss={clean['worst_peak_loss']}"
                  f" flag={c_flag} | amputated loss={hurt.get('worst_peak_loss')}"
                  f" flag={h_flag}")
        print(f"controls caught (flag flips False->True): {caught}")
        return

    rows = []
    for k, item in enumerate(sample, 1):
        fig_dir = Path(f"outputs/full-run-01/{item['paper_id']}/{item['figure_id']}")
        try:
            r = audit_figure(fig_dir, item["series_id"], ocr)
        except Exception as exc:  # noqa: BLE001
            r = {"status": f"error:{type(exc).__name__}"}
        r["figure_id"] = item["figure_id"]
        r["series_id"] = item["series_id"]
        rows.append(r)
        if k % 25 == 0:
            print(f"  {k}/{len(sample)}")

    fields = ["figure_id", "series_id", "status", "n_env_peaks",
              "worst_peak_loss", "worst_trace_gain", "ratio_distortion_log2",
              "flag_amputated", "peaks"]
    with open(args.out, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            wr.writerow({f: r.get(f) for f in fields})

    ok = [r for r in rows if r["status"] == "ok"]
    flagged = [r for r in ok if r["flag_amputated"]]
    dists = [r["ratio_distortion_log2"] for r in ok
             if r.get("ratio_distortion_log2") is not None]
    print(f"\naudited ok: {len(ok)}/{len(rows)}  "
          f"(skips: {len(rows) - len(ok)})")
    print(f"flagged amputated (worst_loss >= {LOSS_FLAG}): {len(flagged)}"
          f"  = {100.0 * len(flagged) / max(1, len(ok)):.1f}% of audited")
    if dists:
        d = np.array(dists)
        print(f"top-2 ratio distortion |log2|: median={np.median(d):.3f}  "
              f"p90={np.percentile(d, 90):.3f}  >1 octave: {(d > 1).sum()}/{len(d)}")
    print(f"saved -> {args.out}")


if __name__ == "__main__":
    main()
