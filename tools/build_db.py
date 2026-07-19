"""Build the PXRD SQLite database from a completed run.

Schema (papers/figures are light normalisation around the two core tables):

  papers   1─n  figures  1─n  curves  1─n  points
                                 │
                                 └── 1─1 contexts   (empty; filled by the
                                                     text-mining phase later)

Everything extracted is ingested — including filter-flagged curves — with the
quality verdict recorded per curve, so nothing is lost and downstream use
filters by tier instead. `in_clean_set` marks curves that (a) are not flagged
by tools/quality_filter.py, (b) sit in a figure with no flagged sibling
(figure-level quarantine), and (c) are experimental (curve_is_computed
excludes simulated/refined/stacking references). The `clean_curves` view is
the default data-mining surface: measured curves, est. ~98.5% accuracy per the
250-figure calibration (geometric/perspective errors are the known blind spot
of the offline filter).

Peaks are detected here (scipy, prominence >= 3% of range, separation >=
0.1 deg) and stored as JSON on each curve — the working index for
similarity/lookup queries without touching the points table.

Usage: python tools/build_db.py outputs/full-run-01 outputs/pxrd.db
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pxrd_fetcher.utils import detect_doi_like_name  # noqa: E402
from pxrd_fetcher.v2.curves import curve_is_computed  # noqa: E402

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE papers (
    paper_id    TEXT PRIMARY KEY,
    doi         TEXT,
    title       TEXT,
    source_path TEXT,
    -- paper-level XRD measurement facts (text-mining phase fills these)
    instrument  TEXT,
    radiation   TEXT,
    wavelength_angstrom REAL,
    voltage_kv  REAL,
    current_ma  REAL,
    xrd_geometry TEXT,
    scan_range  TEXT,
    scan_step   TEXT,
    scan_speed  TEXT,
    xrd_methods_text TEXT
);

CREATE TABLE figures (
    figure_id    TEXT PRIMARY KEY,
    paper_id     TEXT NOT NULL REFERENCES papers(paper_id),
    page_number  INTEGER,
    figure_label TEXT,
    status       TEXT,
    confidence   REAL,
    x_axis_title TEXT,
    y_axis_title TEXT,
    quarantined  INTEGER NOT NULL,
    quarantine_reasons TEXT,
    -- text-mining phase fills these
    caption_text TEXT,
    figure_purpose TEXT,
    measurement_condition TEXT
);

CREATE TABLE curves (
    series_id     TEXT PRIMARY KEY,
    figure_id     TEXT NOT NULL REFERENCES figures(figure_id),
    paper_id      TEXT NOT NULL REFERENCES papers(paper_id),
    label         TEXT,
    material_name TEXT,
    sample_state  TEXT,
    color         TEXT,
    confidence    REAL,
    snap_rate     REAL,
    pixel_precision REAL,
    pixel_recall  REAL,
    mean_snap_residual_px REAL,
    two_theta_uncertainty_deg REAL,
    two_theta_min REAL,
    two_theta_max REAL,
    n_points      INTEGER,
    n_peaks       INTEGER,
    peaks_json    TEXT,
    flagged       INTEGER NOT NULL,
    flag_reasons  TEXT,
    in_clean_set  INTEGER NOT NULL
);

CREATE TABLE points (
    series_id          TEXT NOT NULL REFERENCES curves(series_id),
    two_theta_deg      REAL NOT NULL,
    relative_intensity REAL NOT NULL
);

CREATE TABLE contexts (
    series_id            TEXT PRIMARY KEY REFERENCES curves(series_id),
    -- identity
    canonical_material   TEXT,
    material_class       TEXT,
    compound_alias       TEXT,
    chemical_formula     TEXT,
    metal_center         TEXT,
    linkage_type         TEXT,
    building_blocks      TEXT,
    topology_net         TEXT,
    -- curve role
    curve_role           TEXT,
    sim_model            TEXT,
    reference_material   TEXT,
    jcpds_card           TEXT,
    -- crystallography quoted in the paper
    space_group          TEXT,
    unit_cell_a          REAL, unit_cell_b REAL, unit_cell_c REAL,
    unit_cell_alpha      REAL, unit_cell_beta REAL, unit_cell_gamma REAL,
    rwp                  REAL, rp REAL,
    ccdc_number          TEXT,
    crystallite_size_nm  REAL,
    d_spacing_angstrom   REAL,
    hkl_peaks            TEXT,
    -- sample
    sample_form          TEXT,
    composite_components TEXT,
    guest_or_solvate     TEXT,
    substrate            TEXT,
    -- synthesis
    synthesis_method     TEXT,
    synthesis_temp_c     REAL,
    synthesis_duration_h REAL,
    solvent              TEXT,
    solvent_ratio        TEXT,
    catalyst             TEXT,
    synthesis_atmosphere TEXT,
    synthesis_stage      TEXT,
    activation_method    TEXT,
    -- treatment / stability test / series
    treatment_type       TEXT,
    treatment_agent      TEXT,
    treatment_concentration TEXT,
    treatment_temp_c     REAL,
    treatment_duration_h REAL,
    series_variable      TEXT,
    series_value         TEXT,
    measurement_state    TEXT,
    -- provenance
    extraction_source    TEXT,
    extracted_by         TEXT,
    extracted_at         TEXT,
    extraction_confidence REAL,
    notes                TEXT
);

CREATE VIEW clean_curves AS
    SELECT c.* FROM curves c
    JOIN figures f ON f.figure_id = c.figure_id
    WHERE c.in_clean_set = 1;
"""

INDEXES = """
CREATE INDEX idx_points_series ON points(series_id);
CREATE INDEX idx_curves_figure ON curves(figure_id);
CREATE INDEX idx_curves_paper  ON curves(paper_id);
CREATE INDEX idx_curves_clean  ON curves(in_clean_set);
CREATE INDEX idx_figures_paper ON figures(paper_id);
"""


def detect_peaks(tt: np.ndarray, y: np.ndarray) -> list[dict]:
    rng = float(y.max() - y.min())
    if rng <= 0:
        return []
    step = float(np.median(np.diff(tt))) if len(tt) > 1 else 0.02
    dist = max(1, int(0.1 / max(step, 1e-6)))
    idx, props = find_peaks(y, prominence=0.03 * rng, distance=dist)
    order = np.argsort(props["prominences"])[::-1][:40]
    peaks = [{"two_theta": round(float(tt[idx[i]]), 3),
              "rel_height": round(float((y[idx[i]] - y.min()) / rng), 4),
              "prominence": round(float(props["prominences"][i] / rng), 4)}
             for i in order]
    peaks.sort(key=lambda p: p["two_theta"])
    return peaks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("db_path", type=Path)
    args = ap.parse_args()

    if args.db_path.exists():
        raise SystemExit(f"refusing to overwrite existing {args.db_path}")

    # series-level quality flags from the offline filter
    flags: dict[str, str] = {}
    fig_quarantine: dict[str, list[str]] = {}
    flags_csv = args.run_dir / "quality_flags.csv"
    with flags_csv.open() as fh:
        for row in csv.DictReader(fh):
            if row["bad"] == "True":
                flags[row["series_id"]] = row["reasons"]
                fig_quarantine.setdefault(row["figure_id"], []).append(
                    f"{row['series_id'][-3:]}:{row['reasons']}")

    con = sqlite3.connect(args.db_path)
    con.executescript(SCHEMA)

    t0 = time.time()
    n_fig = n_curve = n_pts = n_clean = 0
    papers_seen: set[str] = set()

    for rf in sorted(args.run_dir.rglob("result.json")):
        d = json.loads(rf.read_text())
        if d["status"] not in ("accepted", "partial"):
            continue
        fig_dir = rf.parent
        fid, pid = d["figure_id"], d["paper_id"]

        if pid not in papers_seen:
            papers_seen.add(pid)
            src = f"sample_papers/{pid}.pdf"
            con.execute(
                "INSERT INTO papers(paper_id, doi, title, source_path) "
                "VALUES (?,?,?,?)",
                (pid, detect_doi_like_name(Path(src)), None, src))

        analysis = d.get("analysis") or {}
        quarantined = int(fid in fig_quarantine)
        con.execute(
            "INSERT INTO figures(figure_id, paper_id, page_number, "
            "figure_label, status, confidence, x_axis_title, y_axis_title, "
            "quarantined, quarantine_reasons) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fid, pid, d.get("page_number"), d.get("figure_label"),
             d["status"], d.get("confidence"),
             analysis.get("x_axis_title"), analysis.get("y_axis_title"),
             quarantined,
             "; ".join(fig_quarantine.get(fid, [])) or None))
        n_fig += 1

        for s in d.get("series", []):
            if s.get("status") != "accepted":
                continue
            sid = s["series_id"]
            path = fig_dir / f"{sid}.csv"
            if not path.exists():
                continue
            tt, ys = [], []
            with path.open() as fh:
                for row in csv.DictReader(fh):
                    try:
                        tt.append(float(row["two_theta_deg"]))
                        ys.append(float(row["relative_intensity"]))
                    except (ValueError, KeyError):
                        pass
            if len(tt) < 2:
                continue
            tta, ya = np.asarray(tt), np.asarray(ys)
            peaks = detect_peaks(tta, ya)
            e = s.get("evidence") or {}
            flagged = int(sid in flags)
            computed = curve_is_computed(s.get("sample_state"), s.get("label"),
                                         s.get("material_name"))
            clean = int(not flagged and not quarantined and not computed)
            n_clean += clean
            con.execute(
                "INSERT INTO curves VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, fid, pid, s.get("label"), s.get("material_name"),
                 s.get("sample_state"), s.get("color"), s.get("confidence"),
                 e.get("snap_rate"), e.get("pixel_precision"),
                 e.get("pixel_recall"), e.get("mean_snap_residual_px"),
                 s.get("two_theta_uncertainty_deg"),
                 round(float(tta.min()), 3), round(float(tta.max()), 3),
                 len(tta), len(peaks), json.dumps(peaks),
                 flagged, flags.get(sid), clean))
            con.executemany(
                "INSERT INTO points VALUES (?,?,?)",
                ((sid, t, y) for t, y in zip(tt, ys)))
            n_curve += 1
            n_pts += len(tt)

        if n_fig % 500 == 0:
            con.commit()
            print(f"  {n_fig} figures / {n_curve} curves / {n_pts:,} points "
                  f"({time.time() - t0:.0f}s)", flush=True)

    con.commit()
    print("building indexes ...", flush=True)
    con.executescript(INDEXES)
    con.commit()
    con.close()

    print(f"\nDONE in {(time.time() - t0) / 60:.1f} min")
    print(f"  papers:  {len(papers_seen)}")
    print(f"  figures: {n_fig}")
    print(f"  curves:  {n_curve}  (clean set: {n_clean})")
    print(f"  points:  {n_pts:,}")
    print(f"  db: {args.db_path} ({args.db_path.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
