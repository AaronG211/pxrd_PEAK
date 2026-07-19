"""COF-native descriptor extractor (v0) — proposal task 4.

Computes, per clean curve, the shape descriptors that first_peak.py does not:

  * crystalline_fraction      peak area / total area, above a rolling
                              10th-percentile baseline (4 deg window)
  * stacking_hump_center_deg  center of the pi-pi (001) hump in 15-35 deg,
  * stacking_hump_fwhm_deg    width at half prominence of that hump,
  * stacking_hump_height      raw height above a chord floor spanning the
                              search window (a 4-deg rolling baseline would
                              swallow humps wider than itself), found on a
                              0.6-deg Gaussian-smoothed signal so broad humps
                              win over sharp reflections; the first-peak
                              neighbourhood is masked out; maxima touching
                              the window edges are rejected as artifacts
  * intensity_ratio_100_001   first-peak height / hump height, both measured
                              on the raw curve above the rolling baseline
                              (within-curve relative — scale-free)

All descriptors are relative, within-curve quantities (claim discipline:
no absolute Scherrer, no cross-curve absolute intensity).

Writes/refreshes the `descriptors` table in pxrd.db. Pure local compute.

Usage:
  python tools/descriptors.py outputs/pxrd.db            # all clean curves
  python tools/descriptors.py outputs/pxrd.db --qa-dir X # also dump QA plots
"""

from __future__ import annotations

import argparse
import datetime
import sqlite3

import numpy as np
from scipy.ndimage import gaussian_filter1d, percentile_filter

VERSION = "v0"

HUMP_LO, HUMP_HI = 15.0, 35.0     # search window for the (001)/amorphous hump
HUMP_MIN_SPAN = 3.0               # need at least this much window coverage
HUMP_MIN_PROM = 0.02              # normalized-units prominence floor
HUMP_SMOOTH_SIGMA_DEG = 0.6       # Gaussian sigma flattening sharp reflections
BASELINE_WIN_DEG = 4.0            # rolling-percentile baseline window
BASELINE_PCT = 10
FP_MASK_MIN_DEG = 1.5             # mask around the first peak in hump search
FP_HEIGHT_WIN_DEG = 0.75          # window to read first-peak raw height


def _uniform(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Resample to a uniform grid at the median native step."""
    step = float(np.median(np.diff(x)))
    step = max(step, 1e-3)
    gx = np.arange(x[0], x[-1] + step / 2, step)
    return gx, np.interp(gx, x, y), step


def extract(x: np.ndarray, y: np.ndarray, fp_pos: float | None,
            fp_fwhm: float | None) -> dict:
    gx, gy, step = _uniform(x, y)
    n = len(gx)
    out: dict = {
        "crystalline_fraction": None,
        "stacking_hump_center_deg": None,
        "stacking_hump_fwhm_deg": None,
        "stacking_hump_height": None,
        "intensity_ratio_100_001": None,
        "hump_window_covered": 0,
    }
    if n < 50:
        return out

    # rolling-percentile baseline, lightly smoothed
    win = max(int(round(BASELINE_WIN_DEG / step)) | 1, 21)
    base = percentile_filter(gy, BASELINE_PCT, size=min(win, n))
    base = gaussian_filter1d(base, max(win / 6.0, 1.0))
    above = np.clip(gy - base, 0.0, None)

    total = float(np.sum(np.clip(gy - gy.min(), 0.0, None)))
    if total > 0:
        out["crystalline_fraction"] = round(float(np.sum(above)) / total, 4)

    # ---- stacking hump on the smoothed signal -----------------------------
    lo, hi = max(HUMP_LO, gx[0]), min(HUMP_HI, gx[-1])
    if hi - lo < HUMP_MIN_SPAN:
        return out
    out["hump_window_covered"] = 1

    smooth = gaussian_filter1d(gy, HUMP_SMOOTH_SIGMA_DEG / step)

    # chord floor across the search window: anchor each end at the 10th
    # percentile of the smoothed signal in the outer ~1 deg of the window,
    # so humps wider than the rolling-baseline window are not swallowed
    wi = np.where((gx >= lo) & (gx <= hi))[0]
    edge_n = max(int(round(1.0 / step)), 3)
    edge_l = float(np.percentile(smooth[wi[:edge_n]], 10))
    edge_r = float(np.percentile(smooth[wi[-edge_n:]], 10))
    chord = np.interp(gx[wi], [gx[wi[0]], gx[wi[-1]]], [edge_l, edge_r])
    hump_sig = np.clip(smooth[wi] - chord, 0.0, None)

    mask = np.ones(len(wi), dtype=bool)
    if fp_pos is not None:
        half = max(FP_MASK_MIN_DEG, 3.0 * (fp_fwhm or 0.0))
        mask &= ~((gx[wi] >= fp_pos - half) & (gx[wi] <= fp_pos + half))
    # edge guard: a maximum sitting on the window boundary is an artifact
    mask &= (gx[wi] >= lo + 0.5) & (gx[wi] <= hi - 0.5)
    idx = np.where(mask)[0]
    if len(idx) < 10:
        return out

    j = idx[np.argmax(hump_sig[idx])]
    prom = float(hump_sig[j])
    if prom < HUMP_MIN_PROM:
        return out
    # reject if the masked argmax is not a genuine local max of the window
    # signal (i.e., it only wins because the true max was masked out)
    lo_edge = (j <= idx[0]) and hump_sig[max(j - 1, 0)] > hump_sig[j]
    hi_edge = (j >= idx[-1]) and (j + 1 < len(wi)) and hump_sig[j + 1] > hump_sig[j]
    if lo_edge or hi_edge:
        return out
    center = float(gx[wi[j]])

    # half-prominence width, walking out from the maximum
    half_h = prom / 2.0
    li = j
    while li > 0 and hump_sig[li] > half_h:
        li -= 1
    ri = j
    while ri < len(wi) - 1 and hump_sig[ri] > half_h:
        ri += 1
    clipped = (li == 0) or (ri == len(wi) - 1)
    fwhm = float(gx[wi[ri]] - gx[wi[li]])

    out["stacking_hump_center_deg"] = round(center, 3)
    if not clipped:
        out["stacking_hump_fwhm_deg"] = round(fwhm, 3)

    # raw height above the chord floor near the hump center
    hwin = (gx[wi] >= center - 0.5) & (gx[wi] <= center + 0.5)
    h_hump = float(np.max(np.clip(gy[wi] - chord, 0, None)[hwin])) if hwin.any() else None
    if h_hump:
        out["stacking_hump_height"] = round(h_hump, 4)

    # ---- (100)/(001) ratio -------------------------------------------------
    if fp_pos is not None and h_hump:
        fwin = (gx >= fp_pos - FP_HEIGHT_WIN_DEG) & (gx <= fp_pos + FP_HEIGHT_WIN_DEG)
        if fwin.any():
            h_fp = float(np.max(above[fwin]))
            if h_fp > 0:
                out["intensity_ratio_100_001"] = round(h_fp / h_hump, 3)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--qa-dir", default=None, help="dump QA overlay plots here")
    ap.add_argument("--qa-series", nargs="*", default=[])
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute(
        """CREATE TABLE IF NOT EXISTS descriptors (
            series_id TEXT PRIMARY KEY REFERENCES curves(series_id),
            crystalline_fraction REAL,
            stacking_hump_center_deg REAL,
            stacking_hump_fwhm_deg REAL,
            stacking_hump_height REAL,
            intensity_ratio_100_001 REAL,
            hump_window_covered INTEGER,
            version TEXT,
            computed_at TEXT)"""
    )

    rows = con.execute(
        """SELECT series_id, first_peak_two_theta, first_peak_fwhm_deg
           FROM clean_curves"""
    ).fetchall()
    now = datetime.datetime.now().isoformat(timespec="seconds")

    done = 0
    for sid, fp_pos, fp_fwhm in rows:
        pts = con.execute(
            "SELECT two_theta_deg, relative_intensity FROM points"
            " WHERE series_id=? ORDER BY two_theta_deg", (sid,)
        ).fetchall()
        if len(pts) < 50:
            continue
        x = np.asarray([p[0] for p in pts])
        y = np.asarray([p[1] for p in pts])
        d = extract(x, y, fp_pos, fp_fwhm)
        con.execute(
            """INSERT OR REPLACE INTO descriptors VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, d["crystalline_fraction"], d["stacking_hump_center_deg"],
             d["stacking_hump_fwhm_deg"], d["stacking_hump_height"],
             d["intensity_ratio_100_001"], d["hump_window_covered"],
             VERSION, now),
        )
        done += 1
        if done % 1000 == 0:
            con.commit()
            print(f"  {done}/{len(rows)}")

        if args.qa_dir and sid in args.qa_series:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            gx, gy, step = _uniform(x, y)
            win = max(int(round(BASELINE_WIN_DEG / step)) | 1, 21)
            base = percentile_filter(gy, BASELINE_PCT, size=min(win, len(gy)))
            base = gaussian_filter1d(base, max(win / 6.0, 1.0))
            smooth = gaussian_filter1d(gy, HUMP_SMOOTH_SIGMA_DEG / step)
            fig, ax = plt.subplots(figsize=(8, 3), dpi=150)
            ax.plot(gx, gy, lw=0.7, color="#888", label="curve")
            ax.plot(gx, base, lw=1.0, color="#b3541e", label="rolling baseline")
            ax.plot(gx, smooth, lw=1.0, color="#1f3b73", label="smoothed")
            if d["stacking_hump_center_deg"]:
                ax.axvline(d["stacking_hump_center_deg"], color="#4a7c59",
                           ls="--", lw=1, label=f"hump {d['stacking_hump_center_deg']}")
            ttl = (f"cf={d['crystalline_fraction']}  "
                   f"fwhm={d['stacking_hump_fwhm_deg']}  "
                   f"ratio={d['intensity_ratio_100_001']}")
            ax.set_title(f"{sid}\n{ttl}", fontsize=7)
            ax.legend(fontsize=6)
            fig.tight_layout()
            fig.savefig(f"{args.qa_dir}/qa_{sid.replace('/', '_')}.png")
            plt.close(fig)

    con.commit()
    n = con.execute("SELECT COUNT(*) FROM descriptors").fetchone()[0]
    n_hump = con.execute(
        "SELECT COUNT(stacking_hump_center_deg) FROM descriptors").fetchone()[0]
    n_cf = con.execute(
        "SELECT COUNT(crystalline_fraction) FROM descriptors").fetchone()[0]
    print(f"descriptors: {n} rows | crystalline_fraction {n_cf} | hump {n_hump}")
    con.close()


if __name__ == "__main__":
    main()
