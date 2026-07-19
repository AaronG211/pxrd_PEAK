"""Denoised first-peak detection for every curve in pxrd.db.

"First peak" = lowest-2θ Bragg reflection that survives four physics gates,
so a noise wiggle or background bump can never be reported as the first peak:

  SNR gate          height above a rolling-percentile local baseline must be
                    >= 5 sigma, sigma = 1.4826 * MAD(y - light_smooth(y)).
                    (3.5–5 sigma passes with status 'low_confidence'.)
  persistence gate  the peak must survive re-detection at three smoothing
                    scales (~0.1/0.3/0.6 deg) with apex drift < 0.12 deg —
                    noise dies under smoothing, real reflections do not.
  width gate        FWHM in [0.05, 3.0] deg: narrower is a spike, wider is an
                    amorphous hump (recorded as 'no_bragg_peak' if that is all
                    the curve has).
  edge gate         the apex must sit >= 0.25 deg inside the 2θ window and its
                    left flank must be visible; otherwise the true first peak
                    may lie at/below the window start -> value stays NULL with
                    status 'truncated_at_window_start' (honest > wrong).

d-spacing uses the paper's mined wavelength when plausible, else assumes
Cu Kα (1.5406 Å) and sets first_peak_wavelength_assumed = 1.

Writes columns onto curves (ALTER-if-missing, idempotent):
  first_peak_two_theta, first_peak_d_angstrom, first_peak_snr,
  first_peak_fwhm_deg, first_peak_status, first_peak_wavelength_assumed

Usage: python tools/first_peak.py outputs/pxrd.db
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import percentile_filter
from scipy.signal import find_peaks, savgol_filter

CU_KA = 1.5406

SNR_OK = 5.0
SNR_LOW = 3.5
FWHM_MIN = 0.05
FWHM_MAX = 3.0
EDGE_DEG = 0.25
SCALES_DEG = (0.10, 0.30, 0.60)
PERSIST_TOL_DEG = 0.12
# Candidate evaluation baseline must be WIDER than any amorphous hump's
# half-width, or the rolling percentile climbs the hump and the width gate
# measures a deceptively narrow FWHM (v1 failure on pyrolyzed samples).
BASE_WIN_DEG = 6.0


def _odd(n: int, lo: int = 5) -> int:
    n = max(lo, n)
    return n if n % 2 else n + 1


def analyze(tt: np.ndarray, y: np.ndarray) -> dict:
    n = len(y)
    if n < 60:
        return {"status": None}
    step = float(np.median(np.diff(tt)))
    if step <= 0:
        return {"status": None}

    # noise level from light-smooth residual (robust MAD)
    w_l = _odd(int(0.20 / step))
    if w_l >= n:
        return {"status": None}
    resid = y - savgol_filter(y, w_l, 2)
    sigma = 1.4826 * float(np.median(np.abs(resid - np.median(resid))))
    rng = float(y.max() - y.min())
    if rng <= 0:
        return {"status": "no_bragg_peak"}
    sigma = max(sigma, 1e-3 * rng)

    # rolling local baseline (10th percentile over ~2 deg)
    w_b = _odd(int(BASE_WIN_DEG / step))
    base = percentile_filter(y, 10, size=min(w_b, n - (1 - n % 2)))

    # candidate peaks at finest scale + persistence across coarser scales
    def peaks_at(scale_deg: float) -> np.ndarray:
        w = _odd(int(scale_deg / step))
        if w >= n:
            return np.array([], dtype=int)
        ys = savgol_filter(y, w, 2)
        idx, _ = find_peaks(
            ys, prominence=max(2.0 * sigma, 0.005 * rng),
            distance=max(1, int(0.1 / step)))
        return idx

    fine = peaks_at(SCALES_DEG[0])
    coarse_sets = [tt[peaks_at(s)] for s in SCALES_DEG[1:]]
    tol = PERSIST_TOL_DEG

    # Curve that OPENS mid-descent: the very first points are near the top of
    # the curve's range and fall substantially within 1 deg — the true first
    # peak is at or below the window start (pyrolyzed/zoomed figures). Range
    # conditions, not sigma alone: ultra-smooth traces have tiny sigma and a
    # pure-sigma test over-fires.
    k03 = max(3, int(0.30 / step))
    k10 = max(k03 + 1, int(1.0 / step))
    i0 = int(np.argmax(y[:k03]))
    drop = y[i0] - float(np.min(y[i0:k10]))
    opens_on_descent = (i0 <= 2
                        and drop > max(SNR_OK * sigma, 0.08 * rng)
                        and y[i0] > y.min() + 0.30 * rng)

    survivors = []
    for p in fine:
        pos = tt[p]
        if all(len(cs) and np.min(np.abs(cs - pos)) <= tol for cs in coarse_sets):
            survivors.append(int(p))

    truncated_seen = bool(opens_on_descent)
    hump_only = True
    best = None  # (tt, snr, fwhm, low_conf)

    # Prominence machinery instead of absolute half-height walks: half height
    # is measured relative to each peak's own prominence bases, so crossings
    # exist by construction and an elevated low-angle tail cannot blow up into
    # "left flank never found" (the v2 failure). Slope shoulders die here too:
    # their prominence (to the nearest higher dip) is tiny even when they sit
    # far above the wide baseline.
    from scipy.signal import peak_prominences, peak_widths
    surv = np.array(sorted(survivors), dtype=int)
    if len(surv):
        proms = peak_prominences(y, surv)
        widths = peak_widths(y, surv, rel_height=0.5,
                             prominence_data=proms)
        for j, p in enumerate(surv):
            snr = float(y[p] - base[p]) / sigma
            if snr < SNR_LOW:
                continue
            if proms[0][j] < max(3.0 * sigma, 0.02 * rng):
                continue                      # slope shoulder / ripple
            fwhm = float(widths[0][j]) * step
            if fwhm < FWHM_MIN:
                continue
            if fwhm > FWHM_MAX:
                continue                      # amorphous hump
            hump_only = False
            left_ips = float(widths[2][j])
            if tt[p] - tt[0] < EDGE_DEG or left_ips <= 2.0:
                truncated_seen = True         # flank runs into window start
                continue
            low_conf = snr < SNR_OK
            if best is None or tt[p] < best[0]:
                best = (float(tt[p]), snr, fwhm, low_conf)

    if best is not None:
        # A qualified peak wins. Truncation evidence at the window edge (an
        # opening descent or an edge-hugging candidate) no longer suppresses
        # the value — it demotes it to low_confidence: the reported peak is
        # real, but an even lower-angle one may sit outside the window.
        status = "low_confidence" if (best[3] or truncated_seen) else "ok"
        return {"status": status,
                "two_theta": best[0], "snr": best[1], "fwhm": best[2]}
    if truncated_seen:
        return {"status": "truncated_at_window_start"}
    return {"status": "no_bragg_peak"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    for col, typ in [
            ("first_peak_two_theta", "REAL"), ("first_peak_d_angstrom", "REAL"),
            ("first_peak_snr", "REAL"), ("first_peak_fwhm_deg", "REAL"),
            ("first_peak_status", "TEXT"),
            ("first_peak_wavelength_assumed", "INTEGER")]:
        try:
            con.execute(f"ALTER TABLE curves ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError as e:
            if "duplicate" not in str(e):
                raise

    wl = dict(con.execute(
        "SELECT paper_id, wavelength_angstrom FROM papers "
        "WHERE wavelength_angstrom IS NOT NULL"))

    sids = [r[0] for r in con.execute("SELECT series_id FROM curves")]
    t0 = time.time()
    n_done = 0
    stats: dict[str, int] = {}
    for sid in sids:
        rows = con.execute(
            "SELECT two_theta_deg, relative_intensity FROM points "
            "WHERE series_id=? ORDER BY two_theta_deg", (sid,)).fetchall()
        if not rows:
            continue
        tt = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        res = analyze(tt, y)
        status = res.get("status")
        stats[status or "skipped"] = stats.get(status or "skipped", 0) + 1

        two_theta = res.get("two_theta")
        d = assumed = None
        if two_theta is not None:
            pid = sid.rsplit("-p", 1)[0]
            lam = wl.get(pid)
            assumed = 0
            if lam is None or not (0.4 < lam < 3.0):
                lam, assumed = CU_KA, 1
            d = lam / (2.0 * math.sin(math.radians(two_theta / 2.0)))
        con.execute(
            "UPDATE curves SET first_peak_two_theta=?, first_peak_d_angstrom=?, "
            "first_peak_snr=?, first_peak_fwhm_deg=?, first_peak_status=?, "
            "first_peak_wavelength_assumed=? WHERE series_id=?",
            (two_theta, round(d, 3) if d else None,
             round(res["snr"], 1) if res.get("snr") else None,
             round(res["fwhm"], 3) if res.get("fwhm") else None,
             status, assumed, sid))
        n_done += 1
        if n_done % 2000 == 0:
            con.commit()
            print(f"  {n_done}/{len(sids)} ({time.time()-t0:.0f}s)", flush=True)
    con.commit()
    con.close()
    print(f"\nDONE {n_done} curves in {(time.time()-t0)/60:.1f} min")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
