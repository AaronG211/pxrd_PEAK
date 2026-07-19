"""Direction 2 baseline: empirical-Bayes hierarchical literature priors.

Partial pooling per the docx, in its simplest defensible form:
  * binary features  -> Beta-Binomial: hyperprior (a,b) fitted by marginal
    likelihood on all groups, per-group posterior Beta(a+y, b+n-y),
    95% credible interval, and posterior lift vs the pooled baseline.
  * continuous       -> random-effects normal model (DerSimonian-Laird tau^2),
    shrunken group means with 95% CI.

Three demo questions:
  Q1 acetic-acid modulator use rate  x structure class      (Peter table)
  Q2 synthesis temperature           x structure class      (Peter table)
  Q3 low-angle first peak (<5 deg)   x structure class      (merged curves)

Usage: python tools/d2_baseline.py outputs/pxrd.db "data/cof_extraction - Copy.csv"
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats
from scipy.special import betaln

MIN_GROUP = 15


def fit_beta_binom(ys, ns):
    grid = np.linspace(0.3, 60, 120)
    best, ll_best = (1.0, 1.0), -1e18
    for a in grid:
        for b in grid:
            ll = sum(betaln(a + y, b + n - y) - betaln(a, b)
                     for y, n in zip(ys, ns))
            if ll > ll_best:
                ll_best, best = ll, (a, b)
    return best


def beta_binom_report(title, data, baseline_note=""):
    data = {g: (y, n) for g, (y, n) in data.items() if n >= MIN_GROUP}
    ys = [y for y, n in data.values()]
    ns = [n for _, n in data.values()]
    a, b = fit_beta_binom(ys, ns)
    pooled = sum(ys) / sum(ns)
    print(f"\n=== {title} ===")
    print(f"hyperprior Beta({a:.1f},{b:.1f})   pooled baseline={pooled:.2f} "
          f"{baseline_note}")
    print(f"{'group':22s} {'n':>4s} {'raw':>5s} {'post':>5s} "
          f"{'95% CI':>13s} {'lift':>6s}")
    for g, (y, n) in sorted(data.items(), key=lambda kv: -kv[1][1]):
        pa, pb = a + y, b + n - y
        m = pa / (pa + pb)
        lo, hi = stats.beta.ppf([0.025, 0.975], pa, pb)
        mark = " *" if lo > pooled or hi < pooled else ""
        print(f"{g:22s} {n:4d} {y/n:5.2f} {m:5.2f} "
              f"[{lo:4.2f},{hi:4.2f}] {m-pooled:+5.2f}{mark}")
    print("  (* = 95% CI excludes the pooled baseline)")


def normal_report(title, vals_by_group, unit):
    print(f"\n=== {title} ===")
    rows = {g: np.asarray(v, float) for g, v in vals_by_group.items()
            if len(v) >= MIN_GROUP}
    means = {g: (v.mean(), max(v.std(ddof=1) / np.sqrt(len(v)), 0.5), len(v))
             for g, v in rows.items()}
    m = np.array([x[0] for x in means.values()])
    se = np.array([x[1] for x in means.values()])
    w = 1 / se**2
    grand = (w * m).sum() / w.sum()
    q = (w * (m - grand) ** 2).sum()
    tau2 = max(0.0, (q - (len(m) - 1)) / (w.sum() - (w**2).sum() / w.sum()))
    print(f"grand mean={grand:.0f}{unit}   between-group tau={np.sqrt(tau2):.0f}{unit}")
    print(f"{'group':22s} {'n':>4s} {'raw':>6s} {'shrunk':>7s} {'95% CI':>15s}")
    for g, (mu, s, n) in sorted(means.items(), key=lambda kv: -kv[1][2]):
        wg = 1 / (s**2 + tau2)
        post = (wg * mu + (1 / max(tau2, 1e-9)) * grand) / (wg + 1 / max(tau2, 1e-9)) \
            if tau2 > 0 else grand
        pvar = 1 / (wg + (1 / max(tau2, 1e-9))) if tau2 > 0 else s**2
        lo, hi = post - 1.96 * np.sqrt(pvar), post + 1.96 * np.sqrt(pvar)
        print(f"{g:22s} {n:4d} {mu:6.0f} {post:7.0f} [{lo:6.0f},{hi:6.0f}]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("peter_csv", type=Path)
    args = ap.parse_args()

    with args.peter_csv.open(encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh))

    def sclass(r):
        s = (r.get("structure_class") or "").strip().lower()
        return s if s and s not in ("nan", "not reported", "unknown") else None

    # Q1 acetic acid use
    q1 = defaultdict(lambda: [0, 0])
    for r in rows:
        g = sclass(r)
        if not g:
            continue
        mod = (r.get("modulator_1_name") or "").lower()
        if not mod or mod in ("nan", "not reported"):
            continue
        q1[g][1] += 1
        if "acetic" in mod or re.fullmatch(r"acoh", mod.strip()):
            q1[g][0] += 1
    beta_binom_report("Q1 acetic-acid modulator use | structure class",
                      {g: tuple(v) for g, v in q1.items()},
                      "(of rows reporting any modulator)")

    # Q2 temperature
    q2 = defaultdict(list)
    for r in rows:
        g = sclass(r)
        try:
            t = float(r.get("temp_C"))
        except (TypeError, ValueError):
            continue
        if g and -20 <= t <= 400:
            q2[g].append(t)
    normal_report("Q2 synthesis temperature (C) | structure class", q2, "C")

    # Q3 low-angle first peak (<5 deg) from OUR merged curves
    con = sqlite3.connect(args.db)
    q3 = defaultdict(lambda: [0, 0])
    for fp, st, pr in con.execute(
            "SELECT c.first_peak_two_theta, c.first_peak_status, m.peter_row "
            "FROM clean_curves c JOIN peter_match m ON m.series_id=c.series_id "
            "WHERE m.match_tier IN ('A','B') AND c.first_peak_status='ok'"):
        g = sclass(rows[pr])
        if not g or fp is None:
            continue
        q3[g][1] += 1
        if fp < 5.0:
            q3[g][0] += 1
    beta_binom_report("Q3 low-angle first peak (<5 deg) | structure class "
                      "(merged curve evidence)",
                      {g: tuple(v) for g, v in q3.items()},
                      "(of tier-A/B curves w/ status ok)")
    con.close()


if __name__ == "__main__":
    main()
