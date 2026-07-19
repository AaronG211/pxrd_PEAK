"""Export utilities for accepted PXRD series."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .storage import Database
from .utils import ensure_dir, load_json


def write_series_csv(
    path: Path,
    *,
    paper_id: str,
    figure_id: str,
    series_id: str,
    series_label: str | None,
    two_theta_values: Iterable[float],
    raw_values: Iterable[float],
    normalized_values: Iterable[float],
) -> Path:
    ensure_dir(path.parent)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "paper_id",
                "figure_id",
                "series_id",
                "series_label",
                "two_theta_deg",
                "intensity_raw",
                "intensity_norm",
            ],
        )
        writer.writeheader()
        for two_theta, raw, normalized in zip(two_theta_values, raw_values, normalized_values):
            writer.writerow(
                {
                    "paper_id": paper_id,
                    "figure_id": figure_id,
                    "series_id": series_id,
                    "series_label": series_label or "",
                    "two_theta_deg": float(two_theta),
                    "intensity_raw": float(raw),
                    "intensity_norm": float(normalized),
                }
            )
    return path


def export_run(database: Database, run_id: int, run_dir: Path) -> Tuple[Path, Path]:
    """Export accepted series from a run into aggregate CSV and JSON files."""

    ensure_dir(run_dir)
    bundle = database.get_run_bundle(run_id)
    papers = {paper.id: paper for paper in bundle["papers"]}
    figure_map = {figure.id: figure for figure in bundle["figures"]}

    csv_path = run_dir / "accepted_series.csv"
    json_path = run_dir / "summary.json"

    rows: List[Dict[str, object]] = []
    summaries: List[Dict[str, object]] = []

    for item in database.list_exportable_series(run_id):
        series = item["series"]
        figure = figure_map.get(series.figure_id)
        if figure is None:
            continue
        paper = papers.get(figure.paper_db_id)
        for point in item["points"]:
            rows.append(
                {
                    "paper_id": getattr(paper, "paper_id", "unknown"),
                    "figure_id": figure.figure_key,
                    "series_id": series.series_key,
                    "series_label": series.label or "",
                    "two_theta_deg": float(point.two_theta_deg),
                    "intensity_raw": float(point.intensity_raw),
                    "intensity_norm": float(point.intensity_norm),
                }
            )

        summaries.append(
            {
                "paper_id": getattr(paper, "paper_id", "unknown"),
                "figure_id": figure.figure_key,
                "series_id": series.series_key,
                "status": series.status,
                "summary": load_json(series.summary_json, {}),
                "classification": load_json(figure.classification_json, {}),
                "metadata": load_json(figure.metadata_json, {}),
            }
        )

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "paper_id",
                "figure_id",
                "series_id",
                "series_label",
                "two_theta_deg",
                "intensity_raw",
                "intensity_norm",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    return csv_path, json_path
