#!/usr/bin/env python3
"""Export the digitized PXRD corpus as a self-describing bulk download.

The website offers data one figure at a time, which is fine for a reader and
useless for anyone who wants the corpus: 4,275 separate downloads across 2,238
pages. This writes the whole thing as three flat tables and a README.

WHAT IS IN IT. Every curve the pipeline accepted - all 11,445 - not just the
9,385 the site currently publishes. Each row says which class it is in and why,
so a recipient filters to whatever standard their task needs instead of
inheriting the site's. That is also the paper's own posture: it releases the
accepted set and discloses the flags rather than removing the flagged curves.

It deliberately includes the 204 model-derived traces the site omits. For a
reader asking a structural question - stacking mode above all - the authors' own
simulated AA/AB patterns plotted beside an experimental one are among the most
informative curves in the corpus, and the site's reason for omitting them (it
publishes measured patterns) is not a reason to withhold them from a dataset.

WHAT IS NOT IN IT.
  * Figure images. The source crops are publisher pixels; see the site's
    copyright notes. Only the numbers this project produced are exported.
  * Any column from peter_cof / peter_match. That table is a third-party,
    AI-extracted dataset and is not this project's to redistribute.
  * Local file paths (papers.source_path).

Makes no network and no model calls. Reads outputs/pxrd.db, and each figure's
result.json for axis-calibration provenance when --assets-root is readable.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import json
import sqlite3
from collections import Counter
from pathlib import Path

CURVE_COLUMNS = [
    # identity
    "series_id", "paper_id", "doi", "figure_id", "figure_label", "page_number",
    "label", "material_name", "sample_state",
    # why this curve is or is not on the website
    "admission", "admission_reason", "published_on_site", "in_clean_set",
    "flagged", "flag_reasons",
    # geometry and extraction fidelity
    "two_theta_min", "two_theta_max", "n_points",
    "two_theta_uncertainty_deg", "confidence", "pixel_precision", "snap_rate",
    "axis_calibration",
    # first peak
    "first_peak_two_theta", "first_peak_d_angstrom", "first_peak_fwhm_deg",
    "first_peak_status", "first_peak_wavelength_assumed",
    # crystallinity descriptors
    "crystalline_fraction", "stacking_hump_center_deg", "stacking_hump_fwhm_deg",
    "intensity_ratio_100_001", "hump_window_covered",
]
PAPER_COLUMNS = [
    "paper_id", "doi", "title", "authors", "journal", "publication_year",
    "publication_status", "publication_status_notice_doi", "crossref_license",
]


# Columns that only mean something when unpublished curves are present.
ACCEPTED_ONLY_COLUMNS = {"admission_reason", "published_on_site"}


def published_text(value):
    """Same rule as import_pxrd_to_supabase.published_text: a legend wrapped onto
    two lines is typography, not data, and breaks line-wise readers of the CSV."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def axis_calibration(root: Path | None, paper_id: str, figure_id: str) -> str | None:
    if root is None:
        return None
    path = root / paper_id / figure_id / "result.json"
    if not path.is_file():
        return None
    try:
        return (json.loads(path.read_text()).get("calibration") or {}).get("status")
    except (OSError, ValueError):
        return None


def write_gz_csv(path: Path, header: list[str], rows) -> int:
    count = 0
    with gzip.open(path, "wt", newline="", encoding="utf-8", compresslevel=6) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--scope",
        choices=["accepted", "published"],
        default="accepted",
        help="accepted: every curve the pipeline accepted (11,445), labelled with why "
        "it is or is not on the site. published: only what the website and API "
        "serve — the scope of any public release.",
    )
    args = parser.parse_args()
    published_only = args.scope == "published"
    curve_columns = [c for c in CURVE_COLUMNS if not (published_only and c in ACCEPTED_ONLY_COLUMNS)]
    scope_sql = "where c.admission in ('clean','axis_verified')" if published_only else ""

    root = args.assets_root.resolve() if args.assets_root else None
    if root is not None and not root.is_dir():
        print(f"note: {root} unreadable; axis_calibration will be empty")
        root = None

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    curve_cols = {row[1] for row in conn.execute("PRAGMA table_info(curves)")}
    if "admission" not in curve_cols:
        raise SystemExit("curves.admission missing: run tools/admit_axis_verified.py --apply first")

    # ---- curves ------------------------------------------------------------
    rows = conn.execute("""
        select c.*, p.doi, f.figure_label, f.page_number,
               d.crystalline_fraction, d.stacking_hump_center_deg,
               d.stacking_hump_fwhm_deg, d.intensity_ratio_100_001,
               d.hump_window_covered
        from curves c
        join papers p on p.paper_id = c.paper_id
        join figures f on f.figure_id = c.figure_id
        left join descriptors d on d.series_id = c.series_id
        {scope_sql}
        order by c.paper_id, c.figure_id, c.series_id
    """.format(scope_sql=scope_sql)).fetchall()
    kept = {r["series_id"] for r in rows}

    calib_cache: dict[str, str | None] = {}
    tally: Counter[str] = Counter()

    position: Counter[str] = Counter()

    def curve_rows():
        for r in rows:
            fid = r["figure_id"]
            on_site = r["admission"] != "excluded"
            # Counted over published curves only, as the importer counts them;
            # counting the unpublished ones too would shift every later number.
            if on_site:
                position[fid] += 1
            if fid not in calib_cache:
                calib_cache[fid] = axis_calibration(root, r["paper_id"], fid)
            tally[r["admission"]] += 1
            record = dict(r)
            record["published_on_site"] = int(r["admission"] != "excluded")
            record["axis_calibration"] = calib_cache[fid]
            for field in ("label", "material_name", "sample_state"):
                record[field] = published_text(record.get(field))
            # The importer names an unlabelled published trace "Series N" by its
            # position among the figure's published curves (rows here are
            # ordered by figure, then series_id, as there), and the website and
            # API show that name. The export says the same so a curve looks
            # identical wherever it is read. An unpublished trace has no site
            # name, and inventing one could duplicate a published sibling's, so
            # it keeps the empty label it actually has.
            if record["label"] is None and on_site:
                record["label"] = f"Series {position[fid]}"
            yield [record.get(name) for name in curve_columns]

    n_curves = write_gz_csv(out / "curves.csv.gz", curve_columns, curve_rows())

    # ---- points ------------------------------------------------------------
    points = conn.execute(
        "select series_id, two_theta_deg, relative_intensity from points "
        "order by series_id, two_theta_deg"
    )
    n_points = write_gz_csv(
        out / "points.csv.gz",
        ["series_id", "two_theta_deg", "relative_intensity"],
        (tuple(p) for p in points if p[0] in kept),
    )

    # ---- papers ------------------------------------------------------------
    present = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    cols = [c for c in PAPER_COLUMNS if c in present]
    papers = conn.execute(
        f"select {', '.join(cols)} from papers "
        "where paper_id in (select distinct paper_id from curves c {scope_sql}) order by paper_id"
        .format(scope_sql=scope_sql)
    )
    n_papers = write_gz_csv(out / "papers.csv.gz", cols, (tuple(p) for p in papers))

    n_figures = len({r["figure_id"] for r in rows})
    reasons = dict(conn.execute(
        "select admission_reason, count(*) from curves where admission='excluded' "
        "group by 1 order by 2 desc").fetchall())
    calib = Counter(v for v in calib_cache.values())
    conn.close()

    stamp = {
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "scope": args.scope,
        "papers": n_papers, "figures": n_figures, "curves": n_curves, "points": n_points,
        "admission": dict(tally),
        **({} if published_only else {"excluded_reasons": reasons}),
        "axis_calibration_by_figure": {str(k): v for k, v in calib.items()},
    }
    (out / "manifest.json").write_text(json.dumps(stamp, indent=2) + "\n")
    print(json.dumps(stamp, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
