#!/usr/bin/env python3
"""Stream the deterministic PXRD clean-set pilot into Supabase.

The importer is idempotent: metadata is upserted and existing Storage objects
are skipped unless --force-assets is supplied. Images and CSVs are converted in
memory, so no second multi-gigabyte staging tree is created on disk.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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


@dataclass(frozen=True)
class FigureRef:
    paper_id: str
    figure_id: str


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


def fetch_metadata(
    conn: sqlite3.Connection, paper_ids: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[FigureRef]]:
    marks = placeholders(paper_ids)
    # journal / publication_year / authors are populated by
    # backfill_crossref_metadata.py. They are selected only when the columns
    # exist, so a database predating that backfill still imports. Sending them
    # matters: the upsert below uses resolution=merge-duplicates, so emitting a
    # hardcoded None here would overwrite live Supabase values with NULL.
    present = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    optional = [
        name
        for name in ("journal", "publication_year", "authors")
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
        papers.append(
            {
                "id": paper_id,
                "paper_number": f"PXRD-{rank[paper_id]:05d}",
                "doi": doi or None,
                "title": record["title"] or paper_id,
                "authors": record.get("authors") or None,
                "journal": record.get("journal") or None,
                "publication_year": safe_year(record.get("publication_year")),
                "source_url": f"https://doi.org/{doi}" if doi else None,
            }
        )

    figure_rows = conn.execute(
        f"""
        select distinct f.figure_id, f.paper_id, f.figure_label, f.page_number,
               f.caption_text
        from figures f
        join curves c on c.figure_id=f.figure_id
        where c.in_clean_set=1 and f.paper_id in ({marks})
        order by f.paper_id, f.page_number, f.figure_id
        """,
        paper_ids,
    ).fetchall()

    figures: list[dict[str, Any]] = []
    figure_refs: list[FigureRef] = []
    figure_order: dict[str, int] = {}
    for figure_id, paper_id, label, page, caption in figure_rows:
        order = figure_order.get(paper_id, 0)
        figure_order[paper_id] = order + 1
        base = f"papers/{paper_id}/{figure_id}"
        figures.append(
            {
                "id": figure_id,
                "paper_id": paper_id,
                "figure_label": label or None,
                "page_number": page,
                "caption": caption or None,
                "crop_path": f"{base}/source.webp",
                "digitized_plot_path": f"{base}/digitized.webp",
                "overlay_path": None,
                "quality_status": "reviewed",
                "sort_order": order,
            }
        )
        figure_refs.append(FigureRef(paper_id, figure_id))

    context_roles = dict(
        conn.execute(
            "select series_id, lower(curve_role) from contexts where curve_role is not null"
        ).fetchall()
    )
    curve_rows = conn.execute(
        f"""
        select c.series_id, c.figure_id, c.label, c.material_name, c.sample_state,
               c.two_theta_min, c.two_theta_max, c.n_points, c.n_peaks
        from curves c
        where c.in_clean_set=1 and c.paper_id in ({marks})
        order by c.figure_id, c.series_id
        """,
        paper_ids,
    ).fetchall()

    curves: list[dict[str, Any]] = []
    curve_order: dict[str, int] = {}
    for series_id, figure_id, label, material, state, theta_min, theta_max, n_points, n_peaks in curve_rows:
        order = curve_order.get(figure_id, 0)
        curve_order[figure_id] = order + 1
        role = infer_role(context_roles.get(series_id), label, state)
        paper_id = figure_id.rsplit("-p", 1)[0]
        curves.append(
            {
                "id": series_id,
                "figure_id": figure_id,
                "series_id": series_id,
                "label": label or f"Series {order + 1}",
                "material_name": material or None,
                "curve_role": role,
                "sample_state": state or None,
                "two_theta_min": theta_min,
                "two_theta_max": theta_max,
                "point_count": n_points or 0,
                "peak_count": n_peaks,
                "data_path": f"papers/{paper_id}/{figure_id}/curves.csv.gz",
                "in_clean_set": True,
                "sort_order": order,
            }
        )
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
) -> int:
    base = assets_root / figure.paper_id / figure.figure_id
    source = base / "crop.png"
    digitized = base / "replot_qa.png"
    if not source.is_file() or not digitized.is_file():
        raise FileNotFoundError(f"Missing figure assets under {base}")

    remote_base = f"papers/{figure.paper_id}/{figure.figure_id}"
    objects = (
        (f"{remote_base}/source.webp", lambda: webp_bytes(source), "image/webp"),
        (f"{remote_base}/digitized.webp", lambda: webp_bytes(digitized), "image/webp"),
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
        papers, figures, curves, figure_refs = fetch_metadata(conn, paper_ids)
    finally:
        conn.close()

    print(
        f"Selected {len(papers)} papers, {len(figures)} figures, "
        f"and {len(curves)} clean curves."
    )
    if args.dry_run:
        missing = 0
        raw_bytes = 0
        for figure in figure_refs:
            base = assets_root / figure.paper_id / figure.figure_id
            for name in ("crop.png", "replot_qa.png"):
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
