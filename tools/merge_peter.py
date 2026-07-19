"""Merge our curve-level PXRD database with Peter's COF extraction table.

Tiered matching (every mapping carries a confidence tier, mirroring the
quality-filter philosophy — downstream analyses choose how deep to trust):

  A  name matched   DOI equal AND a Peter cof_name/alias/sample_label matches
                    the curve's material_name/label/canonical_material
                    (normalised substring either way, best = longest name;
                    ties across different rows -> tier 'A_ambiguous').
  B  single-COF     DOI equal AND Peter has exactly ONE COF row for the paper
                    -> every experimental curve belongs to it by elimination.
  C  doi_only       DOI equal but the COF row cannot be resolved.
  (curves whose paper Peter never mined are absent from the match table.)

Writes table `peter_match` into pxrd.db and a merged curve-level CSV with
Peter's key application/synthesis columns joined on. Also cross-validates our
first_peak_two_theta against the minimum of Peter's main_pxrd_peaks_2theta.

Usage: python tools/merge_peter.py outputs/pxrd.db "data/cof_extraction - Copy.csv"
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

PETER_COLS = [
    "cof_name", "structure_class", "dimensionality", "stacking_mode",
    "topology_reported", "bb_1_name", "bb_2_name", "modulator_1_name",
    "solvent_1_name", "solvent_2_name", "synthesis_method", "temp_C",
    "time_h", "activation_method", "bet_area_m2_g", "tga_decomposition_temp_C",
    "space_group", "main_pxrd_peaks_2theta", "radiation_wavelength_A",
    "pxrd_in_which_figure", "applications_one_word_list",
]


def norm_doi(d: str) -> str:
    d = (d or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d)


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def peak_list(s: str) -> list[float]:
    out = []
    for tok in re.split(r"[;,]", s or ""):
        try:
            v = float(tok.strip())
            if 0.2 < v < 90:
                out.append(v)
        except ValueError:
            pass
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("peter_csv", type=Path)
    args = ap.parse_args()

    # ---- load Peter rows grouped by DOI --------------------------------
    with args.peter_csv.open(encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    by_doi: dict[str, list[tuple[int, dict, set[str]]]] = defaultdict(list)
    for i, r in enumerate(rows):
        names = {norm_name(r.get(k)) for k in
                 ("cof_name", "cof_alias", "sample_label_in_paper")}
        names = {n for n in names if len(n) >= 3}
        by_doi[norm_doi(r["doi"])].append((i, r, names))

    con = sqlite3.connect(args.db)
    con.execute("DROP TABLE IF EXISTS peter_match")
    con.execute("""CREATE TABLE peter_match (
        series_id TEXT PRIMARY KEY REFERENCES curves(series_id),
        peter_row INTEGER NOT NULL,
        match_tier TEXT NOT NULL,          -- A / A_ambiguous / B / C
        matched_name TEXT,
        n_candidate_rows INTEGER)""")

    ctx_names = dict(con.execute(
        "SELECT series_id, canonical_material FROM contexts "
        "WHERE canonical_material IS NOT NULL"))

    tiers = defaultdict(int)
    fp_pairs = []          # (ours, peters_min) for cross-validation
    inserts = []
    for sid, pid, doi, mat, lab, fp, fpst in con.execute(
            "SELECT c.series_id, c.paper_id,"
            " (SELECT doi FROM papers p WHERE p.paper_id=c.paper_id),"
            " c.material_name, c.label, c.first_peak_two_theta,"
            " c.first_peak_status FROM clean_curves c"):
        d = norm_doi((doi or "").replace("_", "/", 1))
        cands = by_doi.get(d)
        if not cands:
            continue
        ours = [norm_name(x) for x in (mat, lab, ctx_names.get(sid)) if x]
        ours = [o for o in ours if len(o) >= 3]

        hits = []            # (name_len, row_idx, peter_name)
        for idx, r, names in cands:
            for pn in names:
                if any(pn in o or o in pn for o in ours):
                    hits.append((len(pn), idx, pn))
        tier = matched = None
        row_idx = None
        if hits:
            hits.sort(reverse=True)
            top_rows = {h[1] for h in hits if h[0] == hits[0][0]}
            row_idx, matched = hits[0][1], hits[0][2]
            tier = "A" if len(top_rows) == 1 else "A_ambiguous"
        elif len(cands) == 1:
            row_idx = cands[0][0]
            tier = "B"
        else:
            row_idx = cands[0][0]
            tier = "C"
        tiers[tier] += 1
        inserts.append((sid, row_idx, tier, matched, len(cands)))

        if tier in ("A", "B") and fp is not None and fpst in ("ok",):
            peaks = peak_list(rows[row_idx].get("main_pxrd_peaks_2theta"))
            if peaks:
                fp_pairs.append((fp, peaks[0]))

    con.executemany("INSERT INTO peter_match VALUES (?,?,?,?,?)", inserts)
    con.commit()

    # ---- merged curve-level CSV ----------------------------------------
    out_csv = Path("outputs/csv_export/merged_curves_peter.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    cur_cols = ["series_id", "paper_id", "figure_id", "material_name", "label",
                "sample_state", "two_theta_min", "two_theta_max", "n_peaks",
                "first_peak_two_theta", "first_peak_status", "confidence"]
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cur_cols + ["match_tier", "matched_name"]
                   + [f"peter_{c}" for c in PETER_COLS])
        for row in con.execute(
                f"SELECT {','.join('c.'+x for x in cur_cols)},"
                " m.peter_row, m.match_tier, m.matched_name"
                " FROM clean_curves c JOIN peter_match m"
                " ON m.series_id=c.series_id"):
        # noqa: E999 placeholder
            pr = rows[row[len(cur_cols)]]
            w.writerow(list(row[:len(cur_cols)]) + [row[len(cur_cols)+1],
                       row[len(cur_cols)+2]] + [pr.get(c) for c in PETER_COLS])

    print("match tiers:", dict(tiers))
    total = sum(tiers.values())
    print(f"matched curves total: {total}")
    if fp_pairs:
        import statistics
        diffs = [abs(a - b) for a, b in fp_pairs]
        close = sum(1 for x in diffs if x <= 0.3)
        print(f"\nfirst-peak cross-validation (ours vs Peter's min peak, "
              f"tier A/B + status ok): n={len(fp_pairs)}")
        print(f"  |Δ2θ| ≤ 0.3°: {close} ({close/len(fp_pairs)*100:.0f}%)  "
              f"median |Δ|: {statistics.median(diffs):.2f}°")
    print(f"\nmerged CSV -> {out_csv}")
    con.close()


if __name__ == "__main__":
    main()
