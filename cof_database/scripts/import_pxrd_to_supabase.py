#!/usr/bin/env python3
"""Stream the deterministic PXRD clean-set pilot into Supabase.

The importer is idempotent: metadata is upserted and existing Storage objects
are skipped unless --force-assets is supplied. Images and CSVs are converted in
memory, so no second multi-gigabyte staging tree is created on disk.

Quality rule: `quality_status` is DERIVED, never asserted. It has exactly two
values, 'pending' and 'flagged'. There is no 'reviewed': no human reviews
figures in this project, so the schema offers no way to claim one did.
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
through the merge-duplicates upsert. An ABSENT local value is not 'active'
either: it is 'unchecked', and publishing it is refused by default because it
too could unflag a live retraction. See --allow-unchecked-publication-status.

Derived-quantity rule: a number is published with whatever it was derived from.
d-spacing is computed from an assumed Cu K-alpha wavelength for every curve in
this corpus, so the wavelength and its provenance travel in their own columns
rather than living in a frontend caption. Descriptor availability is a
four-state enum, because "no hump", "the hump region was never plotted" and "no
descriptor was computed" are three different facts. Cross-paper material groups
are LABEL agreement and are named that way throughout; see material_labels.py.

Column rule: every optional column is emitted only when the local SQLite
column exists. The upsert uses resolution=merge-duplicates, which builds its
UPDATE SET list from the payload keys, so an omitted key preserves the live
Supabase value while a hardcoded None would overwrite it. Every gate is
TABLE-level, never row-level: PostgREST builds one column list per bulk insert
and rejects a batch whose objects have differing key sets (PGRST102), so a key
added under a per-row condition would fail up to 300 rows at once.
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

# Sibling module in this directory; Python puts the script's own directory on
# sys.path, so this works however the script is invoked.
from material_labels import build_groups


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

UNCHECKED_PUBLICATION_STATUS = "unchecked"
PUBLICATION_STATUSES = {
    UNCHECKED_PUBLICATION_STATUS,
    "active",
    "retracted",
    "withdrawn",
    "concern",
    "corrected",
}
PUBLICATION_STATUS_SOURCES = {"crossref-update", "crossref-title", "manual"}
# Written by backfill_crossref_metadata.py. Gated on publication_status.
# crossref_fetched_at is the evidence that a lookup happened at all: the backfill
# writes it in the same UPDATE as the status, so a status without it is a
# corrupted local row rather than a verdict.
PAPER_STATUS_COLUMNS = (
    "publication_status",
    "publication_status_notice_doi",
    "publication_status_source",
    "publication_status_updated",
    "crossref_fetched_at",
)

# local SQLite curves column -> published pxrd_curves column, plus the CHECK
# range each one must satisfy so a stray value cannot fail a 300-row batch.
CURVE_FIDELITY_COLUMNS = {
    "two_theta_uncertainty_deg": ("two_theta_uncertainty_deg", 0.0, None),
    "confidence": ("trace_confidence", 0.0, 1.0),
    "snap_rate": ("snap_rate", 0.0, 1.0),
    "mean_snap_residual_px": ("mean_snap_residual_px", 0.0, None),
    # First-peak geometry. Ranges are the detector's own gates in
    # tools/first_peak.py (FWHM_MIN 0.05, FWHM_MAX 3.0) plus physical bounds.
    # first_peak_snr is deliberately absent: tools/first_peak.py floors sigma at
    # 1e-3 * range, so SNR saturates at a ceiling of 1000 (pilot median 966). It
    # measures how smooth our own vectorization was, not signal quality, and
    # publishing it would invite exactly the misreading this database prevents.
    "first_peak_two_theta": ("first_peak_two_theta_deg", 0.0, 180.0),
    "first_peak_d_angstrom": ("first_peak_d_angstrom", 0.0, None),
    "first_peak_fwhm_deg": ("first_peak_fwhm_deg", 0.05, 3.0),
}
# Text columns need their own map: bounded_float would silently null them.
CURVE_TEXT_COLUMNS = {
    "first_peak_status": (
        "first_peak_status",
        {"ok", "low_confidence", "truncated_at_window_start", "no_bragg_peak"},
    ),
}

# tools/first_peak.py: CU_KA = 1.5406. Applied whenever the paper did not report
# a usable wavelength, which in this corpus is every published curve.
# Every image asset is encoded at this effort. See webp_bytes for the
# measurement behind it.
WEBP_METHOD = 4

CU_KA_ANGSTROM = 1.5406
WAVELENGTH_ASSUMED = "assumed_cu_ka"
WAVELENGTH_REPORTED = "paper_reported"

# local descriptors column -> published pxrd_curves column, with its CHECK range.
# stacking_hump_height is deliberately absent: un-normalized relative-intensity
# units (pilot 0.061-102.7), so it cannot be compared between curves.
# intensity_ratio_100_001 is the scale-free form of the same quantity.
DESCRIPTOR_COLUMNS = {
    "crystalline_fraction": ("crystalline_fraction", 0.0, 1.0),
    "stacking_hump_center_deg": ("stacking_hump_center_deg", 15.0, 35.0),
    "stacking_hump_fwhm_deg": ("stacking_hump_fwhm_deg", 0.0, None),
    "intensity_ratio_100_001": ("intensity_ratio_100_001", 0.0, None),
}
HUMP_DETECTED = "hump_detected"
HUMP_ABSENT = "no_hump_detected"
HUMP_WINDOW_NOT_COVERED = "window_not_covered"
DESCRIPTOR_NOT_COMPUTED = "not_computed"

# tools/build_db.py keeps the top 40 peaks BY PROMINENCE, so a capped list is
# not merely short - it is non-monotone in 2-theta.
PEAK_LIST_CAP = 40
PEAK_ENTRY_KEYS = ("two_theta", "rel_height", "prominence")


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
    """Return ('pending' | 'flagged', reasons).

    Those are the only two values the column permits. A 'reviewed' state was
    removed rather than reserved: nothing in this project can legally write it,
    and leaving it in the CHECK would have let a future writer reintroduce the
    green tick that 1,866 unreviewed figures once wore.

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
    """Map a local publication_status to a published one. Never invents 'active'.

    An absent local value means no Crossref lookup has been recorded for this
    paper, which is not the same fact as "Crossref was asked and said nothing is
    wrong". Publishing the first as the second would assert a clean bill of
    health nobody earned, and the previous `or "active"` did exactly that: NULL,
    "" and "   " all became an affirmative 'active'.
    """
    status = str(value or "").strip().lower() or UNCHECKED_PUBLICATION_STATUS
    if status not in PUBLICATION_STATUSES:
        raise ValueError(
            f"{paper_id}: local publication_status {value!r} is not one of "
            f"{sorted(PUBLICATION_STATUSES)}. Refusing to publish. Coercing it "
            "to 'active' would silently unflag a retracted paper, because the "
            "upsert merges duplicates."
        )
    return status


def coerce_checked_at(value: Any, status: str, paper_id: str) -> str | None:
    """The timestamp that makes `status` evidence rather than assertion.

    Supabase enforces `(publication_status = 'unchecked') = (checked_at is
    null)`, so an incoherent pair would fail a whole 200-row batch.
    backfill_crossref_metadata.py writes crossref_fetched_at and
    publication_status in the same UPDATE, so a verdict without a timestamp is a
    corrupted local row and is refused rather than back-dated.
    """
    text = str(value or "").strip()
    if status == UNCHECKED_PUBLICATION_STATUS:
        return None
    if not text:
        raise ValueError(
            f"{paper_id}: local publication_status is {status!r} but "
            "crossref_fetched_at is empty, so nothing records that the lookup "
            "happened. Refusing to publish a verdict with no evidence. Re-run "
            "backfill_crossref_metadata.py --refresh --apply for this paper."
        )
    return text


def coerce_enum(value: Any, allowed: set[str]) -> str | None:
    """Coerce a stored enum member, or None. Guards the Supabase CHECK.

    An unrecognised value is dropped rather than sent: one stray token would
    fail the entire batch it travels in, not the single row that carries it.
    """
    text = str(value or "").strip().lower()
    return text if text in allowed else None


def derive_hump_status(descriptor: dict[str, Any] | None) -> str:
    """Three facts that a single NULL would destroy, plus their absence.

    'not_computed'       no descriptor row exists for this curve
    'window_not_covered' fewer than 3 of the 15-35 deg window were plotted, so
                         the figure simply cannot say. NO DATA.
    'no_hump_detected'   the window was plotted and no hump was found. This is a
                         REAL negative - a well-ordered sample - and a filter
                         that merges it with the line above is lying.
    'hump_detected'      a hump was fitted
    """
    if descriptor is None:
        return DESCRIPTOR_NOT_COMPUTED
    if not descriptor.get("hump_window_covered"):
        return HUMP_WINDOW_NOT_COVERED
    if descriptor.get("stacking_hump_center_deg") is None:
        return HUMP_ABSENT
    return HUMP_DETECTED


def peak_list(raw: Any, n_peaks: Any) -> tuple[list[dict[str, float]] | None, bool]:
    """Parse one curve's UNGATED peak list. Returns (entries, truncated).

    The list comes from tools/build_db.py's bare `find_peaks(prominence = 0.03 *
    range)` and passes none of the physics gates behind first_peak_status. It is
    published so a multi-peak search can exist, and every surface built on it has
    to say what it is. Unparseable input yields None, which publishes as SQL NULL
    - "we have no list", not "this curve has no peaks".
    """
    if raw is None:
        return None, False
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError):
        return None, False
    if not isinstance(entries, list):
        return None, False
    parsed: list[dict[str, float]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        two_theta = bounded_float(entry.get("two_theta"), minimum=0.0, maximum=180.0)
        if two_theta is None:
            continue
        rel_height = bounded_float(entry.get("rel_height"), minimum=0.0, maximum=1.0)
        prominence = bounded_float(entry.get("prominence"), minimum=0.0, maximum=1.0)
        parsed.append(
            {
                "two_theta": two_theta,
                "rel_height": rel_height,
                "prominence": prominence,
            }
        )
    parsed.sort(key=lambda item: item["two_theta"])
    return parsed, bounded_int(n_peaks) == PEAK_LIST_CAP


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
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[FigureRef],
    dict[str, int],
]:
    """Build the four upsert payloads, plus a tally of why figures were flagged.

    The tally exists because no published column records the flag reason. It is
    the run's audit trail: without it an operator can see that 139 figures are
    flagged but not which check did the flagging.

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
            status = coerce_publication_status(
                record.get("publication_status"), paper_id
            )
            paper["publication_status"] = status
            paper["publication_status_checked_at"] = coerce_checked_at(
                record.get("crossref_fetched_at"), status, paper_id
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
    flag_reasons: dict[str, int] = {}
    for figure_id, paper_id, label, page, caption, figure_status in figure_rows:
        order = figure_order.get(paper_id, 0)
        figure_order[paper_id] = order + 1
        base = f"papers/{paper_id}/{figure_id}"
        verification = (
            read_verification(assets_root / paper_id / figure_id / "result.json")
            if assets_root is not None
            else UNVERIFIED_FIGURE
        )
        quality_status, reasons = derive_quality_status(figure_status, verification.status)
        for reason in reasons:
            flag_reasons[reason] = flag_reasons.get(reason, 0) + 1
        figure: dict[str, Any] = {
            "id": figure_id,
            "paper_id": paper_id,
            "figure_label": label or None,
            # Guarded like every newer column: the Supabase CHECK is
            # `page_number is null or page_number > 0`, and one zero would fail
            # the whole 100-row batch it travels in rather than its own row.
            # Nothing in the corpus violates it today (min 1, max 46).
            "page_number": bounded_int(page, minimum=1),
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
    texts = [name for name in CURVE_TEXT_COLUMNS if name in curve_columns]
    publish_wavelength = "first_peak_two_theta" in curve_columns
    # The recorded provenance of the stored d-spacing. Selected separately from
    # CURVE_FIDELITY_COLUMNS because it is evidence about another column, never
    # published in its own right.
    wavelength_flag_known = "first_peak_wavelength_assumed" in curve_columns
    publish_peaks = "peaks_json" in curve_columns
    base_curve_columns = [
        "series_id",
        "paper_id",
        "figure_id",
        "label",
        "material_name",
        "sample_state",
        "two_theta_min",
        "two_theta_max",
        "n_points",
        "n_peaks",
    ]
    curve_select = [*base_curve_columns, *fidelity, *texts]
    if wavelength_flag_known:
        curve_select.append("first_peak_wavelength_assumed")
    if publish_peaks:
        curve_select.append("peaks_json")
    curve_rows = conn.execute(
        f"""
        select {", ".join("c." + name for name in curve_select)}
        from curves c
        where c.in_clean_set=1 and c.paper_id in ({marks})
        order by c.figure_id, c.series_id
        """,
        paper_ids,
    ).fetchall()

    descriptors = read_descriptors(conn, paper_ids)
    wavelengths = read_wavelengths(conn, paper_ids)
    # Cross-paper material LABEL groups, computed from material_name alone. See
    # material_labels.py for why peter_match is not consulted: it cannot express
    # cross-paper identity, and its only cross-paper field is third-party
    # content this project may not redistribute.
    # Indexed by name rather than position: curve_select's order is assembled
    # from three lists and must stay free to change.
    at = {name: index for index, name in enumerate(curve_select)}
    label_groups, group_of_series = build_groups(
        (row[at["series_id"]], row[at["paper_id"]], row[at["material_name"]])
        for row in curve_rows
    )

    curves: list[dict[str, Any]] = []
    curve_order: dict[str, int] = {}
    wavelength_anomalies: dict[str, int] = {}
    for row in curve_rows:
        record = dict(zip(curve_select, row))
        series_id = record["series_id"]
        figure_id = record["figure_id"]
        order = curve_order.get(figure_id, 0)
        curve_order[figure_id] = order + 1
        role = infer_role(
            context_roles.get(series_id), record["label"], record["sample_state"]
        )
        paper_id = record["paper_id"]
        curve: dict[str, Any] = {
            "id": series_id,
            "figure_id": figure_id,
            "series_id": series_id,
            "label": record["label"] or f"Series {order + 1}",
            "material_name": record["material_name"] or None,
            "curve_role": role,
            "sample_state": record["sample_state"] or None,
            "two_theta_min": bounded_float(record["two_theta_min"]),
            "two_theta_max": bounded_float(record["two_theta_max"]),
            "point_count": bounded_int(record["n_points"]) or 0,
            "peak_count": bounded_int(record["n_peaks"]),
            "data_path": f"papers/{paper_id}/{figure_id}/curves.csv.gz",
            "in_clean_set": True,
            # NULL means "not linked to any other paper by label". It never
            # means "unique material", and the empty state must say so.
            "material_group_id": group_of_series.get(series_id),
            "sort_order": order,
        }
        # A reversed pair passes both single-column guards and still fails the
        # table CHECK, so it needs a pairwise one. Dropping both is right:
        # neither bound can be trusted once their order is wrong.
        if (
            curve["two_theta_min"] is not None
            and curve["two_theta_max"] is not None
            and curve["two_theta_min"] > curve["two_theta_max"]
        ):
            curve["two_theta_min"] = curve["two_theta_max"] = None
        for name in fidelity:
            target, low, high = CURVE_FIDELITY_COLUMNS[name]
            curve[target] = bounded_float(record[name], minimum=low, maximum=high)
        for name in texts:
            target, allowed = CURVE_TEXT_COLUMNS[name]
            curve[target] = coerce_enum(record[name], allowed)
        if publish_wavelength:
            anomaly = apply_wavelength(
                curve,
                wavelengths.get(paper_id),
                record.get("first_peak_wavelength_assumed"),
                wavelength_flag_known,
            )
            if anomaly is not None:
                wavelength_anomalies[anomaly] = wavelength_anomalies.get(anomaly, 0) + 1
        if descriptors is not None:
            apply_descriptors(curve, descriptors.get(series_id))
        if publish_peaks:
            entries, truncated = peak_list(record["peaks_json"], record["n_peaks"])
            curve["peaks"] = entries
            curve["peak_list_truncated"] = truncated
        curves.append(curve)

    # Only groups that actually span papers are published, so the site never
    # renders a one-paper label as if it were linkage. The refused ones are
    # published too, with their specificity, so the gate stays inspectable - but
    # no curve is ever allowed to point at one (build_groups assigns ids only to
    # publishable groups).
    groups = [
        {
            "id": group.group_key,
            "group_key": group.group_key,
            "display_name": group.display_name,
            "label_variants": group.label_variants,
            "paper_count": group.paper_count,
            "curve_count": group.curve_count,
            "match_basis": group.match_basis,
            "specificity": group.specificity,
        }
        for group in sorted(label_groups.values(), key=lambda item: item.group_key)
        if group.paper_count > 1
    ]
    if wavelength_anomalies.get("stale_paper_wavelength"):
        count = wavelength_anomalies["stale_paper_wavelength"]
        print(
            f"NOTE: {count} curves carry a paper-reported wavelength that "
            "tools/first_peak.py did not use; their d-spacing was computed from "
            f"assumed Cu K-alpha {CU_KA_ANGSTROM} A and is published as such. "
            "Rerun tools/first_peak.py to recompute d against the reported value."
        )
    if wavelength_anomalies.get("unverifiable_wavelength"):
        count = wavelength_anomalies["unverifiable_wavelength"]
        print(
            f"NOTE: {count} curves have a first peak whose wavelength provenance "
            "could not be confirmed from curves.first_peak_wavelength_assumed; "
            "they are published as the corpus-wide Cu K-alpha assumption."
        )
    return papers, figures, curves, groups, figure_refs, flag_reasons


def read_descriptors(
    conn: sqlite3.Connection, paper_ids: list[str]
) -> dict[str, dict[str, Any]] | None:
    """Per-curve crystallinity descriptors, or None when the table is absent.

    The None is load-bearing and is not the same as an empty dict. No local table
    means "this database cannot speak about descriptors", and the caller must
    then omit the columns entirely so merge-duplicates preserves whatever is
    live. An empty dict would instead publish 'not_computed' for every curve and
    NULL out a live descriptor set.

    A curve missing from a non-None map has no descriptor row, which
    derive_hump_status reports as 'not_computed' - a different fact again from a
    low value. The pilot happens to have 100% coverage because
    tools/descriptors.py reads the same clean_curves view the importer
    publishes, but nothing here relies on that.
    """
    if not conn.execute(
        "select 1 from sqlite_master where type='table' and name='descriptors'"
    ).fetchone():
        return None
    columns = {row[1] for row in conn.execute("PRAGMA table_info(descriptors)")}
    wanted = [
        name
        for name in ("hump_window_covered", "version", *DESCRIPTOR_COLUMNS)
        if name in columns
    ]
    selected = ["series_id", *wanted]
    marks = placeholders(paper_ids)
    rows = conn.execute(
        f"""
        select {", ".join("d." + name for name in selected)}
        from descriptors d
        join curves c on c.series_id = d.series_id
        where c.in_clean_set=1 and c.paper_id in ({marks})
        """,
        paper_ids,
    ).fetchall()
    return {row[0]: dict(zip(selected, row)) for row in rows}


def read_wavelengths(conn: sqlite3.Connection, paper_ids: list[str]) -> dict[str, float]:
    """Per-paper mined wavelength, where a paper reported one at all.

    tools/mine_contexts.py finds a machine-readable wavelength in exactly ONE
    paper of the 2,370-paper corpus, and none of its curves are in the clean set.
    Every published d-spacing is therefore Cu K-alpha assumed. The lookup exists
    so that stops being true silently the day a paper does report one.
    """
    if "wavelength_angstrom" not in {
        row[1] for row in conn.execute("PRAGMA table_info(papers)")
    }:
        return {}
    marks = placeholders(paper_ids)
    rows = conn.execute(
        f"""
        select paper_id, wavelength_angstrom
        from papers
        where paper_id in ({marks}) and wavelength_angstrom is not null
        """,
        paper_ids,
    ).fetchall()
    # tools/first_peak.py accepts a mined wavelength only inside (0.4, 3.0) and
    # otherwise falls back to Cu K-alpha; the same band gates it here so the
    # published wavelength always matches the one the d-spacing was computed from.
    return {
        paper_id: value
        for paper_id, raw in rows
        if (value := bounded_float(raw)) is not None and 0.4 < value < 3.0
    }


def apply_wavelength(
    curve: dict[str, Any],
    reported: float | None,
    assumed_flag: Any = None,
    known_flag: bool = False,
) -> str | None:
    """Attach the wavelength the d-spacing was computed from, plus where it came from.

    Bragg's law needs a wavelength, and this corpus almost never has one, so
    d = lambda / (2 sin(theta)) is computed from an ASSUMED Cu K-alpha 1.5406 A.
    Carrying the assumption as two columns rather than a caption means a reuser
    can filter on it, and a pattern actually collected on Mo K-alpha (0.7107 A)
    is off by 2.17x in d whether or not anyone read the caption.

    The authority on WHICH wavelength produced the stored d is
    curves.first_peak_wavelength_assumed, written by tools/first_peak.py at the
    time it computed the value - NOT papers.wavelength_angstrom, which can be
    mined later and then describes a wavelength the stored d never used. This
    function therefore reports the recorded flag and only falls back to the
    paper table when the flag is unavailable.

    Returns a short anomaly tag when the two sources disagree, or None.

    The keys are always both present or both absent, because the Supabase CHECK
    ties them to first_peak_two_theta_deg being non-null.
    """
    if curve.get("first_peak_two_theta_deg") is None:
        curve["first_peak_wavelength_angstrom"] = None
        curve["first_peak_wavelength_source"] = None
        return None

    if not known_flag:
        # Pre-feature database: the flag column does not exist, so the paper
        # table is the only evidence there is.
        if reported is None:
            curve["first_peak_wavelength_angstrom"] = CU_KA_ANGSTROM
            curve["first_peak_wavelength_source"] = WAVELENGTH_ASSUMED
        else:
            curve["first_peak_wavelength_angstrom"] = reported
            curve["first_peak_wavelength_source"] = WAVELENGTH_REPORTED
        return None

    flag = bounded_int(assumed_flag)
    if flag == 1:
        # first_peak.py assumed Cu K-alpha. A paper wavelength mined afterwards
        # does not retroactively change the d that was already computed, so the
        # assumption is published and the disagreement is surfaced instead.
        curve["first_peak_wavelength_angstrom"] = CU_KA_ANGSTROM
        curve["first_peak_wavelength_source"] = WAVELENGTH_ASSUMED
        return "stale_paper_wavelength" if reported is not None else None
    if flag == 0 and reported is not None:
        curve["first_peak_wavelength_angstrom"] = reported
        curve["first_peak_wavelength_source"] = WAVELENGTH_REPORTED
        return None
    # flag says a paper wavelength was used but the paper table no longer has
    # one, or the flag is NULL beside a real peak. Either way the provenance is
    # unverifiable; publish the corpus-wide assumption and count the row rather
    # than assert a wavelength no evidence supports.
    curve["first_peak_wavelength_angstrom"] = CU_KA_ANGSTROM
    curve["first_peak_wavelength_source"] = WAVELENGTH_ASSUMED
    return "unverifiable_wavelength"


def apply_descriptors(curve: dict[str, Any], descriptor: dict[str, Any] | None) -> None:
    """Attach the crystallinity block, keeping absence distinct from a low value.

    Hump geometry is emitted only under 'hump_detected'. Supabase enforces the
    same rule, so an inconsistent row would fail a whole 300-row batch rather
    than quietly publish a width for a hump that was never found.
    """
    status = derive_hump_status(descriptor)
    record: dict[str, Any] = descriptor if descriptor is not None else {}
    curve["stacking_hump_status"] = status
    curve["descriptor_version"] = str(record.get("version") or "").strip() or None
    for name, (target, low, high) in DESCRIPTOR_COLUMNS.items():
        value = bounded_float(record.get(name), minimum=low, maximum=high)
        # Hump geometry exists only under 'hump_detected'. Publishing a width or
        # a ratio beside 'window_not_covered' would be publishing a measurement
        # of something the figure never showed.
        if target != "crystalline_fraction" and status != HUMP_DETECTED:
            value = None
        curve[target] = value


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


def webp_bytes(path: Path, method: int = WEBP_METHOD) -> bytes:
    """Lossless WebP. `method` trades encode time for size, never quality.

    method=4, not the encoder default of 0. Measured over the pilot, per image:

        crop.png     -> source.webp    305 KB at method=0, 177 KB at method=4
        overlay.png  -> overlay.webp   257 KB at method=0, 159 KB at method=4

    At method=0 the published pilot measured 1005 MiB against the Free plan's
    1024 MiB ceiling - 98% full, with 19 MiB of headroom. At method=4 the same
    bytes encode to 771 MiB. The cost is about two minutes of CPU across eight
    workers for the whole pilot, one time. method=6 was measured and is not
    worth it: 1 KB smaller per image for 30% more time.

    Lossy WebP was measured and REJECTED, and should stay rejected. At q=90 the
    overlay drops to 86 KB, but PSNR sits at 35 dB and trace pixels move by up
    to 127/255: thin saturated lines over a photographic crop are the worst case
    for DCT ringing, so the error lands exactly on the pixels the overlay exists
    to show. Palette quantisation fails for the same reason - sampled overlays
    carry up to 43,000 distinct colours.
    """
    with Image.open(path) as image:
        image.load()
        output = io.BytesIO()
        image.save(output, format="WEBP", lossless=True, method=method)
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
            (
                f"{remote_base}/overlay.webp",
                lambda: webp_bytes(overlay),
                "image/webp",
            )
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


def tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Count non-null values of `key`. NULL is counted separately by the caller,
    because "no value" is never one of the enum members it would sit beside."""
    counts: dict[str, int] = {}
    for row in rows:
        if row.get(key) is not None:
            counts[str(row[key])] = counts.get(str(row[key]), 0) + 1
    return counts


def render_tally(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))


def summarize_curves(curves: list[dict[str, Any]], groups: list[dict[str, Any]]) -> str:
    """What the derived per-curve columns actually say, in the run's own numbers."""
    lines: list[str] = []
    if not curves:
        return ""
    peak_status = tally(curves, "first_peak_status")
    if peak_status:
        lines.append("first_peak_status: " + render_tally(peak_status))
        sources = tally(curves, "first_peak_wavelength_source")
        if sources:
            no_peak = sum(
                1 for curve in curves if curve.get("first_peak_two_theta_deg") is None
            )
            lines.append(
                "  d-spacing wavelength: "
                + render_tally(sources)
                + f"; {no_peak} curves have no first peak and therefore no "
                f"d-spacing.  ('{WAVELENGTH_ASSUMED}' = Cu K-alpha "
                f"{CU_KA_ANGSTROM} A, a default we chose, not a value any paper "
                "reported. d is a restatement of 2-theta, not a measurement.)"
            )
    hump = tally(curves, "stacking_hump_status")
    if hump:
        lines.append(
            "stacking_hump_status: "
            + render_tally(hump)
            + "  ('no_hump_detected' is a real negative; 'window_not_covered' "
            "and 'not_computed' are absence of data, not low crystallinity)"
        )
    truncated = sum(1 for curve in curves if curve.get("peak_list_truncated"))
    if any("peaks" in curve for curve in curves):
        entries = sum(len(curve.get("peaks") or ()) for curve in curves)
        lines.append(
            f"unvetted peak list: {entries} entries over {len(curves)} curves; "
            f"{truncated} curves are capped at {PEAK_LIST_CAP} peaks and are "
            "therefore incomplete. This list passes none of the physics gates "
            "behind first_peak_status."
        )
    if groups:
        linked = sum(1 for curve in curves if curve.get("material_group_id"))
        publishable = [
            group
            for group in groups
            if group["specificity"] == "specific" and group["paper_count"] > 1
        ]
        widest = max((group["paper_count"] for group in publishable), default=0)
        refused = render_tally(
            {
                name: count
                for name, count in tally(groups, "specificity").items()
                if name != "specific"
            }
        )
        lines.append(
            f"material label groups: {len(publishable)} of {len(groups)} "
            f"multi-paper labels are specific enough to link, spanning up to "
            f"{widest} papers each and covering {linked} curves."
            + (f" Refused: {refused}." if refused else "")
            + " These are LABEL agreement, never verified material identity."
        )
    return "\n".join(lines)


def summarize_verification(
    papers: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    flag_reasons: dict[str, int] | None = None,
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
            + "  ('pending' is the absence of a flag, not a pass; there is no "
            "'reviewed' state and no human has reviewed any of these)",
            "  flagged because: "
            + (
                ", ".join(
                    f"{name} {count}" for name, count in sorted((flag_reasons or {}).items())
                )
                or "nothing"
            ),
            "verification_status: "
            + ", ".join(f"{name} {count}" for name, count in sorted(verification.items())),
            f"{incomplete} figures did not digitize every detected series; "
            f"{with_omitted} omit at least one computed curve.",
        ]
        if verification.get(UNVERIFIED) == len(figures):
            # Loud, because every downstream number in this block is degraded:
            # quality_status cannot see the axis_arbitrated flag reason either,
            # so 'pending' here is the absence of a check, not a passed one.
            lines.append(
                "  WARNING: every figure is 'axis_unverified', which means no "
                "result.json was readable at all - the assets volume is "
                "detached or empty. quality_status above is degraded with it: "
                "the axis_arbitrated flag reason cannot fire, so 'pending' is "
                "the absence of a check rather than a passed one. These figures "
                "must not be published from this run."
            )
    statuses: dict[str, int] = {}
    for paper in papers:
        if "publication_status" in paper:
            key = paper["publication_status"]
            statuses[key] = statuses.get(key, 0) + 1
    if statuses:
        lines.append("publication_status: " + render_tally(statuses))
        unchecked = statuses.get(UNCHECKED_PUBLICATION_STATUS, 0)
        if unchecked:
            lines.append(
                f"  {unchecked} papers are 'unchecked': no Crossref lookup is "
                "recorded for them. That is not the same claim as 'active'."
            )
    else:
        lines.append(
            "publication_status: not present locally, so the four status "
            "columns are omitted from the payload entirely and whatever is "
            "already live is preserved. Run backfill_crossref_metadata.py "
            "--apply to detect retractions."
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
    parser.add_argument(
        "--allow-unchecked-publication-status",
        action="store_true",
        help=(
            "Publish papers whose local publication_status is empty as "
            "'unchecked'. Refused by default for the same reason as "
            "--allow-unverified: merge-duplicates would overwrite a live "
            "'retracted' with it. Fix the cause instead by running "
            "backfill_crossref_metadata.py --apply"
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
    # The assets tree is required only by the modes that read it. --metadata-only
    # passes assets_root=None and provably issues zero Storage requests, so
    # demanding the volume for it blocked the paper-metadata sync on any machine
    # without the drive attached, for nothing. --dry-run needs the tree to count
    # what is missing, but its whole job is to survey a tree that may be
    # incomplete, so it reports the absence instead of raising on it.
    assets_available = assets_root.is_dir()
    if not assets_available and not (args.metadata_only or args.dry_run):
        raise FileNotFoundError(
            f"{assets_root} is not a readable directory. Figures and curves need "
            "the pipeline assets volume. --metadata-only publishes paper "
            "metadata without it, and --dry-run reports what is missing."
        )
    if args.limit < 1 or args.limit > 2000:
        raise ValueError("--limit must be between 1 and 2000")

    conn = sqlite3.connect(db_path)
    try:
        paper_ids = deterministic_clean_papers(conn, args.limit)
        papers, figures, curves, groups, figure_refs, flag_reasons = fetch_metadata(
            conn,
            paper_ids,
            # --metadata-only never upserts figures, so it does not pay for the
            # result.json sweep. Neither does a dry run against an absent tree.
            assets_root=(
                assets_root
                if assets_available and not args.metadata_only
                else None
            ),
            include_overlays=args.upload_overlays and not args.metadata_only,
        )
    finally:
        conn.close()

    print(
        f"Selected {len(papers)} papers, {len(figures)} figures, "
        f"and {len(curves)} clean curves."
    )
    print(
        summarize_verification(
            papers, [] if args.metadata_only else figures, flag_reasons
        )
    )
    if not args.metadata_only:
        print(summarize_curves(curves, groups))

    # A dry run is the tool you reach for precisely when the tree may be
    # incomplete, so it reports before any guard can refuse the run.
    if args.dry_run:
        if not assets_available:
            print(
                f"Dry run: {assets_root} is not readable, so all "
                f"{len(figure_refs)} figures are missing their images and every "
                "figure is reported as 'axis_unverified' above. That is the "
                "state of this machine, not of the data."
            )
            return 1
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

    unchecked = [
        paper["id"]
        for paper in papers
        if paper.get("publication_status") == UNCHECKED_PUBLICATION_STATUS
    ]
    if unchecked and not args.allow_unchecked_publication_status:
        raise RuntimeError(
            f"{len(unchecked)} of {len(papers)} papers have no local "
            f"publication_status (first: {unchecked[:3]}). They would publish as "
            "'unchecked', and because the upsert merges duplicates that could "
            "overwrite a live 'retracted'. Run "
            "`python3 scripts/backfill_crossref_metadata.py --all-papers "
            "--limit 2370 --refresh --apply`, or pass "
            "--allow-unchecked-publication-status deliberately."
        )
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

    url = os.environ.get("SUPABASE_URL", "").strip()
    secret_key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not secret_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    supabase = Supabase(url, secret_key)
    supabase.verify()

    if args.metadata_only:
        supabase.upsert("pxrd_papers", papers, 200)
        print(f"Metadata-only upsert complete for {len(papers)} papers.")
        # Said out loud because it used to be silent. This mode writes exactly
        # one table, and the deploy recipe that used it to "publish" the pilot
        # never wrote a figure or a curve at all.
        print(
            "--metadata-only wrote pxrd_papers ONLY. pxrd_figures, pxrd_curves "
            "and pxrd_material_groups were NOT written, no Storage object was "
            "uploaded, and quality_status, verification_status, the peak and "
            "crystallinity columns and the material label groups are unchanged "
            "from whatever is already live. Run without --metadata-only, with "
            "the assets volume attached, to publish those."
        )
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
    # Groups must land before curves: pxrd_curves.material_group_id is a foreign
    # key into this table.
    supabase.upsert("pxrd_material_groups", groups, 200)
    supabase.upsert("pxrd_curves", curves, 300)
    print(
        f"Metadata upsert complete: {len(papers)} papers, {len(figures)} "
        f"figures, {len(groups)} material label groups, {len(curves)} curves."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; rerun the same command to resume.", file=sys.stderr)
        raise SystemExit(130)
