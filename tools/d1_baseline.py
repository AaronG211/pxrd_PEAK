"""Direction 1 baseline: PXRD + recipe fingerprints -> application labels.

Follows plan/PEAK_COF_revised_project_directions.docx:
  * labels: applications_one_word_list grouped into 8 broad multilabel classes
  * unit: one row per matched (DOI, COF) — the representative curve is the
    highest-confidence untreated experimental curve of that COF (tier A/B)
  * leakage control: no catalysis_*, gas-uptake, or application-ish predictors
  * four feature sets: frequency baseline / PXRD-only / chemistry-only / both
  * negative control: same pipeline on permuted labels
  * grouped 5-fold CV by DOI so no paper straddles train/test

Usage: python tools/d1_baseline.py outputs/pxrd.db "data/cof_extraction - Copy.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

GROUPS = {
    "photocatalysis": ("photocat", "h2 evolution", "hydrogen evolution", "h2o2",
                       "water splitting", "co2 photoreduction", "photoreduction",
                       "solar", "photodegrad", "photocataly"),
    "gas_adsorption": ("co2 capture", "gas separation", "gas storage", "h2 storage",
                       "ch4", "iodine", "adsorption", "uptake", "capture", "storage"),
    "catalysis": ("cataly", "suzuki", "orr", "oer", "her", "co2 reduction",
                  "oxidation", "hydrogenation", "coupling", "electrocataly"),
    "water_membrane": ("pfas", "dye", "nanofiltration", "water purification",
                       "water treatment", "membrane", "desalination", "removal",
                       "extraction", "separation"),
    "sensing": ("sens", "fluorescen", "sers", "voc", "photonic", "detect",
                "luminescen", "probe"),
    "energy_storage": ("supercapacitor", "battery", "li-s", "electrode",
                       "lithium", "sodium", "zinc", "anode", "cathode"),
    "ion_conduction": ("proton conduct", "fuel cell", "ion conduct",
                       "ionic conduct", "ion separation", "conductivity"),
    "biomedical": ("drug", "bioimag", "antibacter", "glycoprotein", "biomed",
                   "therapy", "tumor", "antimicrob", "biosens", "cancer"),
}


def label_groups(s: str) -> set[str]:
    toks = [t.strip().lower() for t in re.split(r"[;,]", s or "") if t.strip()]
    out = set()
    for t in toks:
        for g, kws in GROUPS.items():
            if any(k in t for k in kws):
                out.add(g)
                break
    return out


def peak_hist(peaks_json: str, edges) -> np.ndarray:
    v = np.zeros(len(edges) - 1)
    try:
        for p in json.loads(peaks_json or "[]"):
            i = np.searchsorted(edges, p["two_theta"]) - 1
            if 0 <= i < len(v):
                v[i] += p.get("prominence", 0.1)
    except Exception:
        pass
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("peter_csv", type=Path)
    args = ap.parse_args()

    with args.peter_csv.open(encoding="utf-8-sig", errors="replace") as fh:
        prows = list(csv.DictReader(fh))

    con = sqlite3.connect(args.db)
    # representative curve per (doi, peter_row): untreated experimental, best conf
    cand = defaultdict(list)
    q = """SELECT m.peter_row, c.series_id, c.paper_id, c.confidence,
                  c.sample_state, c.first_peak_two_theta, c.first_peak_status,
                  c.first_peak_fwhm_deg, c.first_peak_snr, c.n_peaks,
                  c.two_theta_min, c.two_theta_max, c.peaks_json,
                  x.treatment_type
           FROM clean_curves c
           JOIN peter_match m ON m.series_id = c.series_id
           LEFT JOIN contexts x ON x.series_id = c.series_id
           WHERE m.match_tier IN ('A','B')"""
    for r in con.execute(q):
        treated = bool(r[13]) or bool(re.search(
            r"soak|hcl|naoh|koh|h2so4|boil|treat|day|calcin|after",
            (r[4] or ""), re.I))
        cand[r[0]].append((treated, -(r[3] or 0), r))
    rows = []
    for pr_idx, lst in cand.items():
        lst.sort(key=lambda x: (x[0], x[1]))
        rows.append((pr_idx, lst[0][2]))
    print(f"matched (DOI, COF) units: {len(rows)}")

    edges = np.arange(2, 52, 2.5)          # 2θ histogram bins
    X_px, X_ch, Y, groups_doi = [], [], [], []
    cat_fields = ["structure_class", "dimensionality", "stacking_mode",
                  "synthesis_method", "solvent_1_name", "solvent_2_name",
                  "modulator_1_name"]
    cat_vocab = {f: [v for v, n in Counter(
        (p.get(f) or "").strip().lower() for _, p in
        ((i, prows[i]) for i, _ in rows)).most_common(12) if v] for f in cat_fields}

    n_no_label = 0
    for pr_idx, r in rows:
        p = prows[pr_idx]
        labs = label_groups(p.get("applications_one_word_list"))
        if not labs:
            n_no_label += 1
            continue
        fp = r[5] if (r[6] in ("ok", "low_confidence") and r[5]) else np.nan
        px = [fp, r[7] or np.nan, r[8] or np.nan, r[9] or 0,
              (r[11] or 0) - (r[10] or 0)]
        px += list(peak_hist(r[12], edges))
        X_px.append(px)
        ch = []
        for f in cat_fields:
            v = (p.get(f) or "").strip().lower()
            ch += [1.0 if v == u else 0.0 for u in cat_vocab[f]]
        for f, lo, hi in (("temp_C", -50, 400), ("time_h", 0, 2000)):
            try:
                x = float(p.get(f))
                ch.append(x if lo <= x <= hi else np.nan)
            except (TypeError, ValueError):
                ch.append(np.nan)
        X_ch.append(ch)
        Y.append(labs)
        groups_doi.append(r[2])

    X_px, X_ch = np.array(X_px, float), np.array(X_ch, float)
    print(f"usable rows (with application labels): {len(Y)}  "
          f"(dropped {n_no_label} unlabeled)")
    classes = sorted(GROUPS)
    Ybin = np.array([[1 if c in y else 0 for c in classes] for y in Y])
    print("label counts:", dict(zip(classes, Ybin.sum(0))))

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import f1_score
    from sklearn.model_selection import GroupKFold
    from sklearn.multioutput import MultiOutputClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(42)
    gkf = GroupKFold(n_splits=5)
    gidx = np.array([hash(g) for g in groups_doi])

    def run(X, y, tag):
        f1M, f1m = [], []
        for tr, te in gkf.split(X, y, gidx):
            clf = make_pipeline(
                SimpleImputer(strategy="median"), StandardScaler(),
                MultiOutputClassifier(RandomForestClassifier(
                    n_estimators=300, min_samples_leaf=3, random_state=0,
                    n_jobs=-1)))
            clf.fit(X[tr], y[tr])
            pred = clf.predict(X[te])
            f1M.append(f1_score(y[te], pred, average="macro", zero_division=0))
            f1m.append(f1_score(y[te], pred, average="micro", zero_division=0))
        print(f"  {tag:28s} macroF1={np.mean(f1M):.3f}  microF1={np.mean(f1m):.3f}")
        return np.mean(f1M)

    print("\n=== Direction 1 baselines (RF, grouped 5-fold by DOI) ===")
    # frequency baseline: always predict the most common label set
    top = Ybin.mean(0) >= 0.5
    if not top.any():
        top = np.zeros(len(classes), bool)
        top[int(np.argmax(Ybin.mean(0)))] = True
    freq_pred = np.tile(top, (len(Ybin), 1))
    print(f"  {'frequency baseline':28s} macroF1="
          f"{f1_score(Ybin, freq_pred, average='macro', zero_division=0):.3f}  "
          f"microF1={f1_score(Ybin, freq_pred, average='micro', zero_division=0):.3f}")
    run(X_px, Ybin, "PXRD-only")
    run(X_ch, Ybin, "chemistry-only")
    both = run(np.hstack([X_px, X_ch]), Ybin, "PXRD + chemistry")
    Yshuf = Ybin[rng.permutation(len(Ybin))]
    run(np.hstack([X_px, X_ch]), Yshuf, "shuffled-label control")
    con.close()


if __name__ == "__main__":
    main()
