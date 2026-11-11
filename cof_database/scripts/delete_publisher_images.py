#!/usr/bin/env python3
"""List, and with --apply delete, the publisher-derived images in Storage.

Until 2026-11-11 the importer uploaded, for every published figure, a crop of
the source article's figure (source.webp) and, with --upload-overlays, the
extracted trace drawn over that crop (overlay.webp). Both are made from the
publisher's pixels. The site no longer shows either, the importer no longer
uploads them, and this script removes the copies already in the public
pxrd-assets bucket.

WHAT IT DELETES. An image (by extension, also inside .gz) whose file name
says it was made from the source article's pixels: a name with the word
source, crop, overlay or montage in it, or a rendered page.

    source.*, crop.*      the figure crop
    overlay.*             the extracted trace drawn over the crop
    qa_montage.*          the pipeline's QA montage (crop beside replot)
    page-NNN.*            a rendered PDF page

These words are checked first, so digitized_overlay.webp is deleted, not kept.

WHAT IT NEVER DELETES. curves.csv.gz and every other data file, and our own
replot, matched by its exact name (digitized.*, replot_qa.*). Any other object
is reported as unclassified and left in place, so a name nobody expected is
shown to a person instead of being deleted on a guess. The private
digitization buckets (pxrd-user-uploads, pxrd-user-results) hold users' own
files and are not read. With the secret key the script also lists the
project's buckets and warns about any other public one, which it does not
read either.

A dry run is the default: it lists the whole bucket, counts, and writes
nothing. --apply deletes the publisher-derived objects in batches, then lists
the bucket again and exits 1 unless none are left. Either way it exits 1,
before deleting anything, when the listing holds no curves.csv.gz: a key that
may not list the bucket can get an empty list rather than an error, and an
empty list must not read as a clean bucket. The listing uses Storage's paged list-v2
endpoint (about 20 requests for the bucket) and falls back to walking the
folders one level at a time where that endpoint is missing.

CREDENTIALS are read the same way as the importer: SUPABASE_URL and
SUPABASE_SECRET_KEY from the environment, else from cof_database/.env. A dry
run needs read access only, so it also accepts SUPABASE_ANON_KEY from the
environment (the public key the website ships; the bucket is public and its
listing is open to it). When the environment already holds what a run needs,
the .env file is not opened. No key is ever printed, and a network error is
reported by its type only, so the project URL is not printed with it.

    python3 scripts/delete_publisher_images.py                  # dry run
    python3 scripts/delete_publisher_images.py --report r.json  # dry run + list
    python3 scripts/delete_publisher_images.py --apply          # delete
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Sibling module: the same bucket and the same .env reader as the importer.
from import_pxrd_to_supabase import BUCKET, load_env_file

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
LIST_V2_PAGE = 1000
# The folder walk lists the 2,370-entry papers/ folder in pages; 1,000 per page
# timed out (HTTP 544) on a cold Free-plan database on 2026-11-11, 100 did not.
LIST_PAGE = 100
DELETE_BATCH = 100

IMAGE_SUFFIXES = {
    ".png", ".webp", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".svg", ".avif",
}
DATA_SUFFIXES = {".csv", ".json", ".parquet", ".txt"}
# Words of a file name that say it was made from the source's pixels.
PUBLISHER_WORDS = {"source", "crop", "overlay", "montage"}
PAGE_RENDER = re.compile(r"^page[-_]?\d+$")
# Our own replots, by exact stem: digitized.webp on the site, replot_qa.png in
# the pipeline's run tree.
OWN_IMAGE_STEMS = {"digitized", "replot_qa"}
PLACEHOLDER = ".emptyFolderPlaceholder"

PUBLISHER = "publisher_derived"
OWN_IMAGE = "own_replot"
DATA = "data"
OTHER = "unclassified"


def classify(path: str) -> str:
    """Which of the four classes an object path is in. Only PUBLISHER is deleted."""
    name = path.rsplit("/", 1)[-1]
    if name == PLACEHOLDER:
        return DATA
    lower = name.lower()
    # A gzipped file is judged by what is inside: source.png.gz is an image.
    inner = lower[: -len(".gz")] if lower.endswith(".gz") else lower
    stem, _, _ = inner.partition(".")
    suffix = "." + inner.rsplit(".", 1)[-1] if "." in inner else ""
    if suffix in DATA_SUFFIXES:
        return DATA
    if suffix not in IMAGE_SUFFIXES:
        return OTHER
    if PUBLISHER_WORDS & set(re.split(r"[^a-z0-9]+", stem)) or PAGE_RENDER.match(stem):
        return PUBLISHER
    if stem in OWN_IMAGE_STEMS:
        return OWN_IMAGE
    return OTHER


def credentials(apply: bool) -> tuple[str, str]:
    """(url, key). The secret key when present; the public key for a dry run."""

    def from_environment() -> tuple[str, str, str]:
        return (
            os.environ.get("SUPABASE_URL", "").strip(),
            os.environ.get("SUPABASE_SECRET_KEY", "").strip(),
            os.environ.get("SUPABASE_ANON_KEY", "").strip(),
        )

    url, secret, anon = from_environment()
    if not url or not (secret or (anon and not apply)):
        load_env_file(ENV_FILE, allowed_keys={"SUPABASE_URL", "SUPABASE_SECRET_KEY"})
        url, secret, anon = from_environment()
    if not url:
        raise RuntimeError("SUPABASE_URL is required")
    if apply and not secret:
        raise RuntimeError("--apply needs SUPABASE_SECRET_KEY: the public key cannot delete")
    key = secret or anon
    if not key:
        raise RuntimeError("SUPABASE_SECRET_KEY (or, for a dry run, SUPABASE_ANON_KEY) is required")
    return url.rstrip("/"), key


class Storage:
    def __init__(self, url: str, key: str) -> None:
        self.url = url
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        retry = Retry(
            total=6,
            backoff_factor=0.75,
            # 544 is Storage's DatabaseTimeout, which a cold database returns.
            status_forcelist=(429, 500, 502, 503, 504, 544),
            allowed_methods=frozenset({"GET", "POST", "DELETE"}),
            respect_retry_after_header=True,
        )
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))

    def list_all(self) -> list[dict[str, Any]] | None:
        """Every object in the bucket from list-v2, or None where it is missing."""
        objects: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"prefix": "", "limit": LIST_V2_PAGE, "with_delimiter": False}
            if cursor:
                body["cursor"] = cursor
            response = self.session.post(
                f"{self.url}/storage/v1/object/list-v2/{BUCKET}",
                json=body,
                headers=self.headers,
                timeout=(15, 120),
            )
            if response.status_code in (400, 404) and cursor is None:
                return None
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Listing failed: HTTP {response.status_code} {response.text[:300]}"
                )
            page = response.json()
            for entry in page.get("objects", []):
                metadata = entry.get("metadata") or {}
                objects.append({"path": entry["name"], "size": metadata.get("size") or 0})
            cursor = page.get("nextCursor")
            if not page.get("hasNext") or not cursor:
                return objects

    def buckets(self) -> list[dict[str, Any]] | None:
        """Every bucket the key may see, or None when the request is refused."""
        response = self.session.get(
            f"{self.url}/storage/v1/bucket", headers=self.headers, timeout=(15, 60)
        )
        if response.status_code >= 400:
            return None
        try:
            listed = response.json()
        except ValueError:
            return None
        return listed if isinstance(listed, list) else None

    def list_level(self, prefix: str) -> tuple[str, list[dict[str, Any]]]:
        """Every entry directly under `prefix`: files carry an id, folders do not."""
        entries: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self.session.post(
                f"{self.url}/storage/v1/object/list/{BUCKET}",
                json={
                    "prefix": prefix,
                    "limit": LIST_PAGE,
                    "offset": offset,
                    "sortBy": {"column": "name", "order": "asc"},
                },
                headers=self.headers,
                timeout=(15, 90),
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Listing {prefix or '/'} failed: HTTP {response.status_code} "
                    f"{response.text[:300]}"
                )
            batch = response.json()
            entries.extend(batch)
            if len(batch) < LIST_PAGE:
                return prefix, entries
            offset += LIST_PAGE

    def delete(self, paths: list[str]) -> int:
        """Delete up to DELETE_BATCH objects; returns how many the server removed."""
        response = self.session.delete(
            f"{self.url}/storage/v1/object/{BUCKET}",
            json={"prefixes": paths},
            headers=self.headers,
            timeout=(15, 180),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Delete failed: HTTP {response.status_code} {response.text[:300]}"
            )
        return len(response.json())


def walk(storage: Storage, workers: int, folders: bool = False) -> list[dict[str, Any]]:
    """Every object in the bucket: list-v2 when available, else a folder walk."""
    listed_v2 = None if folders else storage.list_all()
    if listed_v2 is not None:
        return sorted(listed_v2, key=lambda item: item["path"])
    objects: list[dict[str, Any]] = []
    listed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(storage.list_level, "")}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                prefix, entries = future.result()
                listed += 1
                for entry in entries:
                    path = f"{prefix}/{entry['name']}" if prefix else entry["name"]
                    if entry.get("id") is None:
                        pending.add(pool.submit(storage.list_level, path))
                    else:
                        metadata = entry.get("metadata") or {}
                        objects.append({"path": path, "size": metadata.get("size") or 0})
                if listed % 1000 == 0:
                    print(f"  listed {listed} folders, {len(objects)} objects so far", file=sys.stderr)
    return sorted(objects, key=lambda item: item["path"])


def summarize(objects: list[dict[str, Any]]) -> dict[str, Any]:
    by_class: dict[str, list[dict[str, Any]]] = {PUBLISHER: [], OWN_IMAGE: [], DATA: [], OTHER: []}
    for item in objects:
        by_class[classify(item["path"])].append(item)
    names = {
        cls: Counter(item["path"].rsplit("/", 1)[-1] for item in items)
        for cls, items in by_class.items()
    }
    return {
        "bucket": BUCKET,
        "objects": len(objects),
        "classes": {
            cls: {
                "count": len(items),
                "mib": round(sum(item["size"] for item in items) / 1048576, 1),
                "by_file_name": dict(names[cls].most_common(20)),
            }
            for cls, items in by_class.items()
        },
        "paths": {cls: [item["path"] for item in items] for cls, items in by_class.items()},
    }


def print_summary(summary: dict[str, Any], heading: str) -> None:
    print(f"{heading}: {summary['objects']} objects in bucket {summary['bucket']}")
    for cls, info in summary["classes"].items():
        names = ", ".join(f"{name} {count}" for name, count in info["by_file_name"].items())
        print(f"  {cls:<18} {info['count']:>6}  {info['mib']:>8.1f} MiB  {names}")
    others = summary["paths"][OTHER]
    if others:
        print(f"  {len(others)} unclassified objects are left in place. First ones:")
        for path in others[:20]:
            print(f"    {path}")


def report_buckets(storage: Storage) -> None:
    """Print the project's buckets; warn about a public one this script skips."""
    listed = storage.buckets()
    if not listed:
        print(
            "Buckets: this key may not list them (the public key usually cannot); "
            "run with SUPABASE_SECRET_KEY to see every bucket."
        )
        return
    print("Buckets: " + ", ".join(
        f"{item.get('id')} ({'public' if item.get('public') else 'private'})" for item in listed
    ))
    for item in listed:
        if item.get("public") and item.get("id") != BUCKET:
            print(
                f"  WARNING: {item.get('id')} is public and is not read by this "
                "script. Look at what it holds before calling the cleanup done."
            )


def run(args: argparse.Namespace) -> int:
    url, key = credentials(args.apply)
    storage = Storage(url, key)
    report_buckets(storage)
    summary = summarize(walk(storage, args.workers, args.walk_folders))
    print_summary(summary, "Before" if args.apply else "Dry run")
    if args.report:
        args.report.write_text(json.dumps(summary, indent=1) + "\n")
        print(f"Report written to {args.report}")

    curves = sum(
        1 for path in summary["paths"][DATA] if path.rsplit("/", 1)[-1] == "curves.csv.gz"
    )
    if curves == 0:
        print(
            f"The listing holds {summary['objects']} objects and no curves.csv.gz, "
            "so it cannot be the whole bucket: this key probably may not list it. "
            "Nothing was deleted, and these counts say nothing."
        )
        return 1

    targets = summary["paths"][PUBLISHER]
    if not args.apply:
        print(
            f"Dry run: {len(targets)} publisher-derived objects would be deleted. "
            "Nothing was changed. Rerun with --apply to delete them."
        )
        return 0
    if not targets:
        print("Nothing to delete.")
        return 0

    deleted = 0
    for start in range(0, len(targets), DELETE_BATCH):
        batch = targets[start : start + DELETE_BATCH]
        # The class is re-checked at the point of deletion, so nothing but a
        # publisher-derived name can ever reach the request.
        if any(classify(path) != PUBLISHER for path in batch):
            raise RuntimeError("refusing to delete an object that is not publisher-derived")
        deleted += storage.delete(batch)
        print(f"  deleted {deleted} of {len(targets)}", file=sys.stderr)

    after = summarize(walk(storage, args.workers, args.walk_folders))
    print_summary(after, "After")
    left = after["classes"][PUBLISHER]["count"]
    print(f"Deleted {deleted} objects; {left} publisher-derived objects remain.")
    return 0 if left == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="Delete the publisher-derived objects.")
    parser.add_argument("--workers", type=int, default=8, help="Parallel listing requests (default 8).")
    parser.add_argument(
        "--walk-folders",
        action="store_true",
        help="List by walking the folders instead of list-v2: slower, a cross-check.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Also write every object path, by class, to this JSON file.",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except requests.RequestException as exc:
        # The exception text names the host, so only its type is printed. A
        # rerun is safe: it lists the bucket again before it deletes anything.
        print(
            f"Network error talking to Supabase ({type(exc).__name__}); "
            "nothing more is printed. Rerun the same command.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; rerun the same command.", file=sys.stderr)
        raise SystemExit(130)
