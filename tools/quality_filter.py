"""Offline (no-API) quality filter for replotted PXRD curves.

Flags series whose CSV values carry the signatures that tracing failures leave
behind. Physical grounding: a powder pattern is peaks rising from a slowly
varying background — it does not hold a flat level, jump, and settle at a new
flat level (staircase), it never dips sharply below its own local baseline and
recovers (notch), and after Savitzky-Golay smoothing it is never densely
jagged (sawtooth). Savgol smoothing spreads the artifacts' vertical edges over
several points, so detection is plateau-based (rolling-std segmentation), not
single-point-difference based.

Detectors per series (intensity normalised to [0, 1]):
  steps    — >= 2 adjacent plateau pairs at levels differing by > 0.10 with a
             transition gap <= 60 points. Real flat-topped peaks produce at
             most one up+down pair; staircases and square bumps produce more.
  notches  — >= 2 sharp dips: a point > 0.12 below both neighbourhoods only
             +-4 points away. Peaks only point UP; needle notches are
             non-physical. (Up-needles are NOT flagged: sharp crystalline
             reflections are real.)
  sawtooth — direction-reversal density > 0.03 (amplitude-weighted) or total
             variation > 20 x range: dense oscillation that smoothing would
             have removed from any faithful trace.

A figure is quarantined when ANY accepted series is flagged (deliberate
over-kill: the user prefers false kills over polluted data). Calibrated on the
250-figure human-verdicted random sample: catches 6/7 'wrong' (the 7th is a 3D
-perspective mis-calibration — invisible in CSV values by construction), 4/7
'minor_issue', kills ~10% of 'correct' figures (about half of those turned out
on inspection to be genuine subtle artifacts the human thumbnail pass missed).

Usage:
  python tools/quality_filter.py <run_dir> --calibrate <all_samples.csv>
  python tools/quality_filter.py <run_dir> --check-known-bad
  python tools/quality_filter.py <run_dir> --apply
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

# ---- plateau segmentation ---------------------------------------------------
ROLL_W = 15          # rolling-std window (points; 0.30 deg at 0.02 deg step)
FLAT_STD = 0.004     # a window flatter than this is plateau material
MIN_PLATEAU = 20     # minimum plateau length (points)
LEVEL_DIFF = 0.10    # plateau level difference that counts as a shift
MAX_TRANSITION = 60  # max points between plateaus of one step
# ---- rule thresholds ---------------------------------------------------------
MAX_STEPS = 1        # > 1 level shift  => staircase / square bumps
NOTCH_AMP = 0.12
NOTCH_HALF_W = 4     # needle notch: recovers within +-4 points
MAX_NOTCHES = 1
MAX_REV_FRAC = 0.03  # sawtooth density
REV_AMP = 0.008
MAX_TV = 20.0


def rolling_std(z: np.ndarray, w: int) -> np.ndarray:
    c1 = np.cumsum(np.insert(z, 0, 0.0))
    c2 = np.cumsum(np.insert(z * z, 0, 0.0))
    s1 = c1[w:] - c1[:-w]
    s2 = c2[w:] - c2[:-w]
    return np.sqrt(np.maximum(0.0, s2 / w - (s1 / w) ** 2))


def find_plateaus(z: np.ndarray) -> list[tuple[int, int, float]]:
    flat = rolling_std(z, ROLL_W) < FLAT_STD
    segs = []
    i = 0
    while i < len(flat):
        if flat[i]:
            j = i
            while j < len(flat) and flat[j]:
                j += 1
            length = (j - 1 + ROLL_W) - i
            if length >= MIN_PLATEAU:
                segs.append((i, i + length, float(z[i:i + length].mean())))
            i = j
        else:
            i += 1
    return segs


def count_steps(segs) -> int:
    n = 0
    for (a0, a1, la), (b0, b1, lb) in zip(segs, segs[1:]):
        if b0 - a1 <= MAX_TRANSITION and abs(lb - la) > LEVEL_DIFF:
            n += 1
    return n


def count_notches(z: np.ndarray) -> int:
    n = 0
    i = NOTCH_HALF_W
    while i < len(z) - NOTCH_HALF_W:
        if (z[i - NOTCH_HALF_W] - z[i] > NOTCH_AMP
                and z[i + NOTCH_HALF_W] - z[i] > NOTCH_AMP):
            n += 1
            i += NOTCH_HALF_W
        else:
            i += 1
    return n


def jaggedness(z: np.ndarray) -> tuple[float, float]:
    d = np.diff(z)
    sign = np.sign(d)
    rev = 0
    for i in range(1, len(d)):
        if sign[i] != 0 and sign[i - 1] != 0 and sign[i] != sign[i - 1] \
                and min(abs(d[i]), abs(d[i - 1])) > REV_AMP:
            rev += 1
    return rev / max(1, len(d) - 1), float(np.abs(d).sum())


def series_verdict(y: np.ndarray) -> tuple[dict, list[str]]:
    y = y[np.isfinite(y)]                      # NaN would poison every metric
    if len(y) < 60:                            # length check BEFORE max/min:
        return {"degenerate": True}, ["degenerate"]   # empty arrays crash max()
    rng = float(y.max() - y.min())
    if rng <= 0:
        return {"degenerate": True}, ["degenerate"]
    z = (y - y.min()) / rng
    segs = find_plateaus(z)
    f = {
        "n_steps": count_steps(segs),
        "n_notches": count_notches(z),
    }
    f["rev_frac"], f["tv"] = jaggedness(z)
    reasons = []
    if f["n_steps"] > MAX_STEPS:
        reasons.append(f"steps={f['n_steps']}")
    if f["n_notches"] > MAX_NOTCHES:
        reasons.append(f"notches={f['n_notches']}")
    if f["rev_frac"] > MAX_REV_FRAC:
        reasons.append(f"sawtooth={f['rev_frac']:.3f}")
    if f["tv"] > MAX_TV:
        reasons.append(f"tv={f['tv']:.1f}")
    return f, reasons


# ---- evidence layer (pipeline fidelity metrics, also offline) ---------------
# The CSV-shape rules see amplitude artifacts; they are blind to traces that
# followed ink cleanly but the WRONG ink (neighbour curve, partial coverage).
# Those leave their mark in the tracing evidence instead. Together the two
# layers caught 11/11 of the out-of-sample known-bad figures.
MIN_PRECISION = 0.80
MIN_SNAP = 0.80
MAX_RESIDUAL_PX = 15.0
# Recall only became meaningful after the trace_fidelity fix (the old
# definition was banded around the trace itself, making recall >= precision an
# identity — it carried no signal, which is why it was absent here). On runs
# made before the fix this gate never fires; on new runs it catches
# partial-coverage traces.
MIN_RECALL_OFFLINE = 0.70


def evidence_reasons(s: dict) -> list[str]:
    e = s.get("evidence") or {}
    reasons = []
    if e.get("pixel_precision", 1.0) < MIN_PRECISION:
        reasons.append(f"precision={e['pixel_precision']:.2f}")
    if e.get("snap_rate", 1.0) < MIN_SNAP:
        reasons.append(f"snap={e['snap_rate']:.2f}")
    if e.get("pixel_recall", 1.0) < MIN_RECALL_OFFLINE:
        reasons.append(f"recall={e['pixel_recall']:.2f}")
    if e.get("mean_snap_residual_px", 0.0) > MAX_RESIDUAL_PX:
        reasons.append(f"residual={e['mean_snap_residual_px']:.0f}px")
    return reasons


def scan_run(run_dir: Path):
    for rf in sorted(run_dir.rglob("result.json")):
        d = json.loads(rf.read_text())
        if d["status"] not in ("accepted", "partial"):
            continue
        fig_dir = rf.parent
        acc = {s["series_id"]: s for s in d.get("series", [])
               if s.get("status") == "accepted"}
        for path in sorted(fig_dir.glob(f"{d['figure_id']}-s*.csv")):
            if path.stem not in acc:
                continue
            ys = []
            with path.open() as fh:
                for row in csv.DictReader(fh):
                    try:
                        ys.append(float(row["relative_intensity"]))
                    except (ValueError, KeyError):
                        pass
            f, reasons = series_verdict(np.asarray(ys, dtype=float))
            reasons += evidence_reasons(acc[path.stem])
            yield d["figure_id"], path.stem, f, reasons


# Figures found bad in the two earlier visual reviews but NOT part of the
# 250-figure random sample — an out-of-sample recall check.
KNOWN_BAD = [
    "10.1002_advs.201802365-p003-f01",
    "10.1002_adfm.201900233-p006-f01",
    "10.1002_aic.16292-p004-f01",
    "10.1002_aic.15102-p006-f01",
    "10.1002_adma.201201185-p002-f01",
    "10.1016_j.apcatb.2024.123698-p004-f01",
    "10.1002_cssc.202401930-p004-f02",
    "10.1002_smll.202501327-p003-f01",
    "10.1002_anie.202016240-p004-f01",
    "10.1016_j.aca.2023.342061-p006-f01",
    "10.1002_smll.202403775-p003-f02",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--calibrate", type=Path, default=None)
    ap.add_argument("--check-known-bad", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rows, fig_bad = [], {}
    for fig_id, sid, f, reasons in scan_run(args.run_dir):
        rows.append({"figure_id": fig_id, "series_id": sid,
                     "bad": bool(reasons), "reasons": ";".join(reasons),
                     **{k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in f.items() if k != "degenerate"}})
        if reasons:
            fig_bad.setdefault(fig_id, []).append(f"{sid[-3:]}:{reasons[0]}")

    if args.calibrate:
        verdicts = {r["figure_id"]: r["verdict"].strip()
                    for r in csv.DictReader(args.calibrate.open())}
        conf: dict[tuple[str, str], int] = {}
        missed, killed = [], []
        for fid, v in verdicts.items():
            res = "flagged" if fid in fig_bad else "passed"
            conf[(v, res)] = conf.get((v, res), 0) + 1
            if v == "wrong" and res == "passed":
                missed.append(fid)
            if v == "correct" and res == "flagged":
                killed.append((fid, fig_bad[fid][:3]))
        print("=== calibration vs human verdicts ===")
        for (v, res), n in sorted(conf.items()):
            print(f"  {v:12s} {res:8s} {n}")
        print(f"\nmissed wrong ({len(missed)}): {missed}")
        print(f"killed correct ({len(killed)}):")
        for fid, why in killed:
            print("  ", fid, why)

    if args.check_known_bad:
        print("\n=== out-of-sample known-bad recall ===")
        n_hit = 0
        for fid in KNOWN_BAD:
            hit = fid in fig_bad
            n_hit += hit
            why = fig_bad.get(fid, [])[:3]
            print(f"  {'CAUGHT' if hit else 'MISSED':7s} {fid} {why}")
        print(f"  recall: {n_hit}/{len(KNOWN_BAD)}")

    if args.apply:
        if not rows:
            print("no accepted series found in run dir — nothing to flag")
            return
        out = args.run_dir / "quality_flags.csv"
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        n_bad = sum(1 for r in rows if r["bad"])
        figs = {r["figure_id"] for r in rows}
        print(f"\nseries flagged:      {n_bad}/{len(rows)} "
              f"({n_bad / len(rows) * 100:.1f}%)")
        print(f"figures quarantined: {len(fig_bad)}/{len(figs)} "
              f"({len(fig_bad) / len(figs) * 100:.1f}%)")
        print(f"flags -> {out}")


if __name__ == "__main__":
    main()
