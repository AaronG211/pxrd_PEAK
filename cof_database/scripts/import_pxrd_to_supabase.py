#!/usr/bin/env python3
"""Stream the deterministic PXRD clean-set pilot into Supabase.

The importer is idempotent: metadata is upserted and existing Storage objects
are skipped unless --force-assets is supplied. Images and CSVs are converted in
memory, so no second multi-gigabyte staging tree is created on disk.

Quality rule: `quality_status` is DERIVED, never asserted. No human has
reviewed any figure in this database, so the importer never emits 'reviewed'.
A figure is 'flagged' only when an automated check disputed it - the two
independent 2-theta axis fits disagreed and needed a tie-break
(calibration.status == 'arbitrated'), or not every detected series survived
digitization (figures.status == 'partial'). Everything else is 'pending':
automated extraction passed its own checks and nothing has reviewed it since.
The finer provenance travels in `verification_status` and the axis/series
numbers beside it, so the website can show numbers rather than a colour.

Publication rule: `publication_status` is carried through from the local
papers table, where `backfill_crossref_metadata.py` writes it. It is never
invented here. An unrecognised local value aborts the run rather than being
coerced to 'active', because coercion would silently unflag a retracted paper
through the merge-duplicates upsert.

Column rule: every optional column is emitted only when the local SQLite
column exists. The upsert uses resolution=merge-duplicates, which builds its
UPDATE SET list from the payload keys, so an omitted key preserves the live
Supabase value while a hardcoded None would overwrite it.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image


BUCKET = "pxrd-assets"
SEED = "pxrd-free-pilot-v1"
VALID_ROLES = {
    "experimental",
    "simulated",
    "refined",
    "difference",
    "reference",
    "unclassified",
}

# result.json calibration.status -> published verification_status. The pipeline
# fits the 2-theta axis twice, once from detected tick marks and once from OCR
# of the axis labels, then compares the two fits across the plot width against
# a 0.5 deg tolerance (src/pxrd_fetcher/v2/calibrate.py).
CALIBRATION_VERIFICATION = {
    "consensus": "axis_cross_validated",
    "single_method": "axis_single_method",
    "arbitrated": "axis_arbitrated",
}
UNVERIFIED = "axis_unverified"
DISPUTED_AXIS = "axis_arbitrated"
INCOMPLETE_FIGURE_STATUS = "partial"

PUBLICATION_STATUSES = {"active", "retracted", "withdrawn", "concern", "corrected"}
PUBLICATION_STATUS_SOURCES = {"crossref-update", "crossref-title", "manual"}
# Written by backfill_crossref_metadata.py. Gated on publication_status.
PAPER_STATUS_COLUMNS = (
    "publication_status",
    "publication_status_notice_doi",
    "publication_status_source",
    "publication_status_updated",
)
# local SQLite curves column -> published pxrd_curves column, plus the CHECK
# range each one must satisfy so a stray value cannot fail a 300-row batch.
CURVE_FIDELITY_COLUMNS = {
    "two_theta_uncertainty_deg": ("two_theta_uncertainty_deg", 0.0, None),
    "confidence": ("trace_confidence", 0.0, 1.0),
    "snap_rate": ("snap_rate", 0.0, 1.0),
    "mean_snap_residual_px": ("mean_snap_residual_px", 0.0, None),
}


@dataclass(frozen=True)
class FigureRef:
    paper_id: str
    figure_id: str


@dataclass(frozen=True)
class FigureVerification:
    """What the pipeline can honestly say about one figure's calibration."""

    status: str
    agreement_deg: float | None
    rmse_deg: float | None
    tick_count: int | None
    series_detected: int | None
    series_digitized: int | None


UNVERIFIED_FIGURE = FigureVerification(UNVERIFIED, None, None, None, None, None)


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def load_env_file(path: Path, allowed_keys: set[str] | None = None) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (allowed_keys is None or key in allowed_keys):
            os.environ.setdefault(key, value)


class Supabase:
    def __init__(self, url: str, secret_key: str) -> None:
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
        }
        self.local = threading.local()

    def session(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is None:
            retry = Retry(
                total=6,
                connect=6,
                read=6,
                status=6,
                backoff_factor=0.75,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "HEAD", "POST"}),
                respect_retry_after_header=True,
            )
            session = requests.Session()
            session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=4))
            self.local.session = session
        return session

    def verify(self) -> None:
        for resource in ("pxrd_papers", "pxrd_figures", "pxrd_curves", "pxrd_paper_index"):
            response = self.session().get(
                f"{self.url}/rest/v1/{resource}",
                params={"select": "id", "limit": 1},
                headers=self.headers,
                timeout=30,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Supabase resource {resource!r} is unavailable. "
                    "Run supabase/reset.sql and supabase/schema.sql first. "
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )

        response = self.session().get(
            f"{self.url}/storage/v1/bucket/{BUCKET}",
            headers=self.headers,
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Storage bucket {BUCKET!r} is unavailable. "
                f"HTTP {response.status_code}: {response.text[:300]}"
            )

    def object_exists(self, object_path: str) -> bool:
        response = self.session().head(
            f"{self.url}/storage/v1/object/public/{BUCKET}/{quote(object_path, safe='/')}",
            timeout=(15, 60),
        )
        return response.status_code == 200

    def upload(self, object_path: str, payload: bytes, content_type: str) -> None:
        response = self.session().post(
            f"{self.url}/storage/v1/object/{BUCKET}/{quote(object_path, safe='/')}",
            data=payload,
            headers={
                **self.headers,
                "Content-Type": content_type,
                "x-upsert": "true",
                "Cache-Control": "31536000",
            },
            timeout=(15, 180),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Upload failed for {object_path}: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )

    def upsert(self, table: str, rows: list[dict[str, Any]], batch_size: int) -> None:
        for batch in chunks(rows, batch_size):
            response = self.session().post(
                f"{self.url}/rest/v1/{table}",
                params={"on_conflict": "id"},
                json=batch,
                headers={
                    **self.headers,
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                timeout=120,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Upsert failed for {table}: HTTP {response.status_code} "
                    f"{response.text[:500]}"
                )


def deterministic_clean_papers(conn: sqlite3.Connection, limit: int) -> list[str]:
    rows = conn.execute(
        "select distinct paper_id from curves where in_clean_set=1"
    ).fetchall()
    paper_ids = [row[0] for row in rows]
    ranked = sorted(
        paper_ids,
        key=lambda paper_id: hashlib.sha256(f"{SEED}:{paper_id}".encode()).hexdigest(),
    )
    return ranked[:limit]


def placeholders(items: list[str]) -> str:
    return ",".join("?" for _ in items)


def safe_year(value: Any) -> int | None:
    """Coerce a stored year, or None. Guards the Supabase 1800-2200 CHECK.

    A single out-of-range value would fail the entire upsert batch, so anything
    unparseable or out of range is dropped rather than sent.
    """
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2200 else None


def bounded_float(value: Any, minimum: float | None = None, maximum: float | None = None) -> float | None:
    """Coerce a stored float, or None. Guards the Supabase range CHECKs.

    NaN and the infinities are rejected: json.loads accepts them as literals,
    and either one would fail a whole upsert batch rather than one row.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def bounded_int(value: Any, minimum: int = 0) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= minimum else None


def read_verification(result_path: Path) -> FigureVerification:
    """Read calibration provenance out of one pipeline result.json.

    A missing, unreadable, or unexpected file yields `axis_unverified` with no
    numbers. This function never guesses: an unrecognised calibration status is
    reported as unverified rather than assumed to be a consensus.
    """
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNVERIFIED_FIGURE
    if not isinstance(payload, dict):
        return UNVERIFIED_FIGURE

    calibration = payload.get("calibration")
    calibration = calibration if isinstance(calibration, dict) else {}
    fit = calibration.get("fit")
    fit = fit if isinstance(fit, dict) else {}

    raw_series = payload.get("series")
    if isinstance(raw_series, list):
        detected = len(raw_series)
        digitized = sum(
            1
            for entry in raw_series
            if isinstance(entry, dict) and entry.get("status") == "accepted"
        )
    else:
        detected = None
        digitized = None

    return FigureVerification(
        status=CALIBRATION_VERIFICATION.get(
            str(calibration.get("status") or "").strip().lower(), UNVERIFIED
        ),
        agreement_deg=bounded_float(calibration.get("agreement_deg"), minimum=0.0),
        rmse_deg=bounded_float(fit.get("rmse_deg"), minimum=0.0),
        tick_count=bounded_int(fit.get("n_points")),
        series_detected=detected,
        series_digitized=digitized,
    )


def derive_quality_status(
    figure_status: str | None, verification_status: str
) -> tuple[str, tuple[str, ...]]:
    """Return ('pending' | 'flagged', reasons). Never 'reviewed'.

    No human has reviewed any figure in this database, so 'reviewed' is not a
    value this importer is allowed to produce; it stays reserved for a
    human-review workflow that does not exist yet.

    'flagged' means an automated check DISPUTED the figure:
      axis_arbitrated    the tick-mark fit and the OCR fit disagreed by more
                         than 0.5 deg and a second read broke the tie, so the
                         axis rests on arbitration rather than on agreement
      series_incomplete  figures.status == 'partial': at least one detected
                         series failed digitization

    A single-method axis is deliberately NOT flagged. Nothing disputed it;
    there was simply only one witness. That is a disclosure, and it travels in
    verification_status. Computed curves (simulated / refined / difference)
    omitted from the clean set are likewise a disclosure, carried by
    series_omitted_computed, not a defect.
    """
    reasons: list[str] = []
    if verification_status == DISPUTED_AXIS:
        reasons.append("axis_arbitrated")
    if str(figure_status or "").strip().lower() == INCOMPLETE_FIGURE_STATUS:
        reasons.append("series_incomplete")
    return ("flagged" if reasons else "pending"), tuple(reasons)


def coerce_publication_status(value: Any, paper_id: str) -> str:
    status = str(value or "").strip().lower() or "active"
    if status not in PUBLICATION_STATUSES:
        raise ValueError(
            f"{paper_id}: local publication_status {value!r} is not one of "
            f"{sorted(PUBLICATION_STATUSES)}. Refusing to publish. Coercing it "
            "to 'active' would silently unflag a retracted paper, because the "
            "upsert merges duplicates."
        )
    return status


def coerce_publication_status_source(value: Any, paper_id: str) -> str | None:
    source = str(value or "").strip().lower()
    if not source:
        return None
    if source not in PUBLICATION_STATUS_SOURCES:
        raise ValueError(
            f"{paper_id}: local publication_status_source {value!r} is not one "
            f"of {sorted(PUBLICATION_STATUS_SOURCES)}. Refusing to publish an "
            "unattributable status."
        )
    return source


def coerce_publication_status_date(value: Any, paper_id: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"{paper_id}: local publication_status_updated {value!r} is not an "
            "ISO date. Refusing to publish rather than dropping the provenance "
            "of a retraction."
        ) from exc


def fetch_metadata(
    conn: sqlite3.Connection,
    paper_ids: list[str],
    assets_root: Path | None = None,
    include_overlays: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[FigureRef]]:
    """Build the three upsert payloads.

    `assets_root` is where the pipeline run directories live. When it is given,
    each figure's result.json supplies the axis-calibration provenance that the
    SQLite mirror does not carry. When it is None - the --metadata-only path,
    which never upserts figures - every figure is reported as axis_unverified
    rather than assumed to be fine.
    """
    marks = placeholders(paper_ids)

    # Optional columns are selected only when they exist, so a database
    # predating a backfill still imports. They are also EMITTED only when they
    # exist: resolution=merge-duplicates builds its UPDATE SET list from the
    # payload keys, so an omitted key preserves the live Supabase value while a
    # hardcoded None would overwrite it.
    present = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    optional = [
        name
        for name in ("journal", "publication_year", "authors", *PAPER_STATUS_COLUMNS)
        if name in present
    ]
    selected = ["paper_id", "doi", "title", *optional]
    paper_rows = conn.execute(
        f"""
        select {", ".join(selected)}
        from papers
        where paper_id in ({marks})
        """,
        paper_ids,
    ).fetchall()

    rank = {paper_id: index + 1 for index, paper_id in enumerate(paper_ids)}
    papers = []
    for row in paper_rows:
        record = dict(zip(selected, row))
        paper_id = record["paper_id"]
        doi = record["doi"]
        paper: dict[str, Any] = {
            "id": paper_id,
            "paper_number": f"PXRD-{rank[paper_id]:05d}",
            "doi": doi or None,
            "title": record["title"] or paper_id,
            "source_url": f"https://doi.org/{doi}" if doi else None,
        }
        if "authors" in present:
            paper["authors"] = record.get("authors") or None
        if "journal" in present:
            paper["journal"] = record.get("journal") or None
        if "publication_year" in present:
            paper["publication_year"] = safe_year(record.get("publication_year"))
        if "publication_status" in present:
            paper["publication_status"] = coerce_publication_status(
                record.get("publication_status"), paper_id
            )
            if "publication_status_notice_doi" in present:
                paper["publication_status_notice_doi"] = (
                    str(record.get("publication_status_notice_doi") or "").strip() or None
                )
            if "publication_status_source" in present:
                paper["publication_status_source"] = coerce_publication_status_source(
                    record.get("publication_status_source"), paper_id
                )
            if "publication_status_updated" in present:
                paper["publication_status_updated"] = coerce_publication_status_date(
                    record.get("publication_status_updated"), paper_id
                )
        papers.append(paper)

    figure_columns = {row[1] for row in conn.execute("PRAGMA table_info(figures)")}
    # figures.status distinguishes 'accepted' from 'partial'. A database
    # without it loses one of the two flag reasons rather than the whole run.
    status_select = "f.status" if "status" in figure_columns else "null"
    figure_rows = conn.execute(
        f"""
        select distinct f.figure_id, f.paper_id, f.figure_label, f.page_number,
               f.caption_text, {status_select}
        from figures f
        join curves c on c.figure_id=f.figure_id
        where c.in_clean_set=1 and f.paper_id in ({marks})
        order by f.paper_id, f.page_number, f.figure_id
        """,
        paper_ids,
    ).fetchall()

    # Curves excluded from the clean set on a published figure. Every one of
    # these in the pilot is a computed trace (simulated / Pawley refined /
    # difference), not a failure, so it is disclosed rather than flagged.
    omitted_computed = {
        figure_id: count
        for figure_id, count in conn.execute(
            f"""
            select figure_id, count(*)
            from curves
            where in_clean_set=0 and paper_id in ({marks})
            group by figure_id
            """,
            paper_ids,
        )
    }

    figures: list[dict[str, Any]] = []
    figure_refs: list[FigureRef] = []
    figure_order: dict[str, int] = {}
    for figure_id, paper_id, label, page, caption, figure_status in figure_rows:
        order = figure_order.get(paper_id, 0)
        figure_order[paper_id] = order + 1
        base = f"papers/{paper_id}/{figure_id}"
        verification = (
            read_verification(assets_root / paper_id / figure_id / "result.json")
            if assets_root is not None
            else UNVERIFIED_FIGURE
        )
        quality_status, _reasons = derive_quality_status(figure_status, verification.status)
        figure: dict[str, Any] = {
            "id": figure_id,
            "paper_id": paper_id,
            "figure_label": label or None,
            "page_number": page,
            "caption": caption or None,
            "crop_path": f"{base}/source.webp",
            "digitized_plot_path": f"{base}/digitized.webp",
            "quality_status": quality_status,
            "verification_status": verification.status,
            "axis_agreement_deg": verification.agreement_deg,
            "axis_rmse_deg": verification.rmse_deg,
            "axis_tick_count": verification.tick_count,
            "series_detected": verification.series_detected,
            "series_digitized": verification.series_digitized,
            "series_omitted_computed": omitted_computed.get(figure_id, 0),
            "sort_order": order,
        }
        if include_overlays:
            figure["overlay_path"] = f"{base}/overlay.webp"
        figures.append(figure)
        figure_refs.append(FigureRef(paper_id, figure_id))

    context_roles = dict(
        conn.execute(
            "select series_id, lower(curve_role) from contexts where curve_role is not null"
        ).fetchall()
    )
    curve_columns = {row[1] for row in conn.execute("PRAGMA table_info(curves)")}
    fidelity = [name for name in CURVE_FIDELITY_COLUMNS if name in curve_columns]
    base_curve_columns = [
        "series_id",
        "figure_id",
        "label",
        "material_name",
        "sample_state",
        "two_theta_min",
        "two_theta_max",
        "n_points",
        "n_peaks",
    ]
    curve_select = [*base_curve_columns, *fidelity]
    curve_rows = conn.execute(
        f"""
        select {", ".join("c." + name for name in curve_select)}
        from curves c
        where c.in_clean_set=1 and c.paper_id in ({marks})
        order by c.figure_id, c.series_id
        """,
        paper_ids,
    ).fetchall()

    curves: list[dict[str, Any]] = []
    curve_order: dict[str, int] = {}
    for row in curve_rows:
        record = dict(zip(curve_select, row))
        figure_id = record["figure_id"]
        order = curve_order.get(figure_id, 0)
        curve_order[figure_id] = order + 1
        role = infer_role(
            context_roles.get(record["series_id"]), record["label"], record["sample_state"]
        )
        paper_id = figure_id.rsplit("-p", 1)[0]
        curve: dict[str, Any] = {
            "id": record["series_id"],
            "figure_id": figure_id,
            "series_id": record["series_id"],
            "label": record["label"] or f"Series {order + 1}",
            "material_name": record["material_name"] or None,
            "curve_role": role,
            "sample_state": record["sample_state"] or None,
            "two_theta_min": record["two_theta_min"],
            "two_theta_max": record["two_theta_max"],
            "point_count": record["n_points"] or 0,
            "peak_count": record["n_peaks"],
            "data_path": f"papers/{paper_id}/{figure_id}/curves.csv.gz",
            "in_clean_set": True,
            "sort_order": order,
        }
        for name in fidelity:
            target, low, high = CURVE_FIDELITY_COLUMNS[name]
            curve[target] = bounded_float(record[name], minimum=low, maximum=high)
        curves.append(curve)
    return papers, figures, curves, figure_refs


def infer_role(context_role: str | None, label: str | None, state: str | None) -> str:
    if context_role in VALID_ROLES:
        return context_role
    state_lower = (state or "").strip().lower()
    if state_lower in {
        "experimental",
        "experiment",
        "exp",
        "observed",
        "measured",
        "experimental/observed",
        "experimentally observed",
    }:
        return "experimental"
    label_lower = (label or "").strip().lower()
    if any(token in label_lower for token in ("simulated", "simulation", "calculated")):
        return "simulated"
    if any(token in label_lower for token in ("pawley", "rietveld", "refined")):
        return "refined"
    if "difference" in label_lower:
        return "difference"
    if any(token in label_lower for token in ("reference", "jcpds")):
        return "reference"
    return "unclassified"


def webp_bytes(path: Path) -> bytes:
    with Image.open(path) as image:
        image.load()
        output = io.BytesIO()
        # method=0 is ~7x faster than the higher-effort lossless encoder on the
        # source figures. The 1,000-paper pilot still stays comfortably below
        # the Free-plan Storage ceiling.
        image.save(output, format="WEBP", lossless=True, method=0)
        return output.getvalue()


def figure_csv_gz(conn: sqlite3.Connection, figure_id: str) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=6, mtime=0) as zipped:
        with io.TextIOWrapper(zipped, encoding="utf-8", newline="") as text:
            writer = csv.writer(text)
            writer.writerow(
                [
                    "series_id",
                    "series_label",
                    "material_name",
                    "sample_state",
                    "two_theta_deg",
                    "relative_intensity",
                    "two_theta_uncertainty_deg",
                ]
            )
            rows = conn.execute(
                """
                select c.series_id, c.label, c.material_name, c.sample_state,
                       p.two_theta_deg, p.relative_intensity,
                       c.two_theta_uncertainty_deg
                from curves c
                join points p on p.series_id=c.series_id
                where c.figure_id=? and c.in_clean_set=1
                order by c.series_id, p.two_theta_deg
                """,
                (figure_id,),
            )
            writer.writerows(rows)
    return output.getvalue()


def upload_figure(
    supabase: Supabase,
    db_path: Path,
    assets_root: Path,
    figure: FigureRef,
    force_assets: bool,
    include_overlays: bool = False,
) -> int:
    base = assets_root / figure.paper_id / figure.figure_id
    source = base / "crop.png"
    digitized = base / "replot_qa.png"
    if not source.is_file() or not digitized.is_file():
        raise FileNotFoundError(f"Missing figure assets under {base}")
    overlay = base / "overlay.png"
    if include_overlays and not overlay.is_file():
        raise FileNotFoundError(f"Missing overlay.png under {base}")

    remote_base = f"papers/{figure.paper_id}/{figure.figure_id}"
    objects = [
        (f"{remote_base}/source.webp", lambda: webp_bytes(source), "image/webp"),
        (f"{remote_base}/digitized.webp", lambda: webp_bytes(digitized), "image/webp"),
    ]
    if include_overlays:
        objects.append(
            (f"{remote_base}/overlay.webp", lambda: webp_bytes(overlay), "image/webp")
        )
    uploaded_bytes = 0
    for object_path, build, content_type in objects:
        if not force_assets and supabase.object_exists(object_path):
            continue
        payload = build()
        supabase.upload(object_path, payload, content_type)
        uploaded_bytes += len(payload)

    csv_path = f"{remote_base}/curves.csv.gz"
    if force_assets or not supabase.object_exists(csv_path):
        conn = sqlite3.connect(db_path)
        try:
            payload = figure_csv_gz(conn, figure.figure_id)
        finally:
            conn.close()
        supabase.upload(csv_path, payload, "application/gzip")
        uploaded_bytes += len(payload)
    return uploaded_bytes


def summarize_verification(
    papers: list[dict[str, Any]], figures: list[dict[str, Any]]
) -> str:
    """One honest paragraph about what is actually being published."""
    quality: dict[str, int] = {}
    verification: dict[str, int] = {}
    incomplete = 0
    with_omitted = 0
    for figure in figures:
        quality[figure["quality_status"]] = quality.get(figure["quality_status"], 0) + 1
        key = figure["verification_status"]
        verification[key] = verification.get(key, 0) + 1
        detected = figure["series_detected"]
        digitized = figure["series_digitized"]
        if detected is not None and digitized is not None and digitized < detected:
            incomplete += 1
        if figure["series_omitted_computed"]:
            with_omitted += 1
    lines: list[str] = []
    if figures:
        lines += [
            "quality_status: "
            + ", ".join(f"{name} {count}" for name, count in sorted(quality.items()))
            + "  (no figure is ever 'reviewed'; no human has reviewed any of these)",
            "verification_status: "
            + ", ".join(f"{name} {count}" for name, count in sorted(verification.items())),
            f"{incomplete} figures did not digitize every detected series; "
            f"{with_omitted} omit at least one computed curve.",
        ]
    statuses: dict[str, int] = {}
    for paper in papers:
        if "publication_status" in paper:
            key = paper["publication_status"]
            statuses[key] = statuses.get(key, 0) + 1
    if statuses:
        lines.append(
            "publication_status: "
            + ", ".join(f"{name} {count}" for name, count in sorted(statuses.items()))
        )
    else:
        lines.append(
            "publication_status: not present locally; run "
            "backfill_crossref_metadata.py --apply to detect retractions."
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("../outputs/pxrd.db"))
    parser.add_argument("--assets-root", type=Path, default=Path("../outputs/full-run-01"))
    parser.add_argument("--limit", type=int, default=10, help="Number of clean-set papers to include")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-assets", action="store_true")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Upsert paper metadata only; do not inspect or upload assets",
    )
    parser.add_argument(
        "--upload-overlays",
        action="store_true",
        help=(
            "Also publish overlay.webp (the extracted trace drawn on the source "
            "figure) and set pxrd_figures.overlay_path. Roughly doubles the "
            "Storage footprint of the pilot, so it is opt-in. When it is off "
            "the overlay_path key is omitted entirely, which preserves any "
            "value already live rather than clearing it"
        ),
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help=(
            "Publish figures whose result.json could not be read. Refused by "
            "default: an unreadable calibration would be published as "
            "'axis_unverified' and could downgrade a live 'flagged' figure to "
            "'pending' through the merge-duplicates upsert"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(
        Path(__file__).resolve().parents[1] / ".env",
        allowed_keys={"SUPABASE_URL", "SUPABASE_SECRET_KEY"},
    )
    db_path = args.db.resolve()
    assets_root = args.assets_root.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not assets_root.is_dir():
        raise FileNotFoundError(assets_root)
    if args.limit < 1 or args.limit > 2000:
        raise ValueError("--limit must be between 1 and 2000")

    conn = sqlite3.connect(db_path)
    try:
        paper_ids = deterministic_clean_papers(conn, args.limit)
        papers, figures, curves, figure_refs = fetch_metadata(
            conn,
            paper_ids,
            # --metadata-only never upserts figures, so it does not pay for the
            # result.json sweep.
            assets_root=None if args.metadata_only else assets_root,
            include_overlays=args.upload_overlays and not args.metadata_only,
        )
    finally:
        conn.close()

    print(
        f"Selected {len(papers)} papers, {len(figures)} figures, "
        f"and {len(curves)} clean curves."
    )
    print(summarize_verification(papers, [] if args.metadata_only else figures))
    if not args.metadata_only:
        unverified = [
            figure["id"]
            for figure in figures
            if figure["verification_status"] == UNVERIFIED
        ]
        if unverified and not args.allow_unverified:
            raise RuntimeError(
                f"{len(unverified)} of {len(figures)} figures have no readable "
                f"result.json under {assets_root} (first: {unverified[:3]}). "
                "Refusing to publish them as 'axis_unverified', which could "
                "downgrade a live 'flagged' figure to 'pending'. Attach the "
                "assets volume, or pass --allow-unverified deliberately."
            )
    if args.dry_run:
        missing = 0
        raw_bytes = 0
        wanted = ("crop.png", "replot_qa.png")
        if args.upload_overlays:
            wanted = (*wanted, "overlay.png")
        for figure in figure_refs:
            base = assets_root / figure.paper_id / figure.figure_id
            for name in wanted:
                path = base / name
                if path.is_file():
                    raw_bytes += path.stat().st_size
                else:
                    missing += 1
        print(f"Dry run: {missing} missing image assets; {raw_bytes / 1048576:.1f} MiB raw images.")
        return 0 if missing == 0 else 1

    url = os.environ.get("SUPABASE_URL", "").strip()
    secret_key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not secret_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    supabase = Supabase(url, secret_key)
    supabase.verify()

    if args.metadata_only:
        supabase.upsert("pxrd_papers", papers, 200)
        print(f"Metadata-only upsert complete for {len(papers)} papers.")
        return 0

    uploaded_bytes = 0
    executor = ThreadPoolExecutor(max_workers=args.workers)
    try:
        futures = {
            executor.submit(
                upload_figure,
                supabase,
                db_path,
                assets_root,
                figure,
                args.force_assets,
                args.upload_overlays,
            ): figure
            for figure in figure_refs
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            figure = futures[future]
            try:
                uploaded_bytes += future.result()
            except Exception as exc:
                raise RuntimeError(f"Failed while uploading {figure.figure_id}: {exc}") from exc
            if completed % 25 == 0 or completed == len(futures):
                print(
                    f"Assets: {completed}/{len(futures)} figures; "
                    f"uploaded {uploaded_bytes / 1048576:.1f} MiB this run."
                )
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    supabase.upsert("pxrd_papers", papers, 200)
    supabase.upsert("pxrd_figures", figures, 100)
    supabase.upsert("pxrd_curves", curves, 300)
    print("Metadata upsert complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; rerun the same command to resume.", file=sys.stderr)
        raise SystemExit(130)
