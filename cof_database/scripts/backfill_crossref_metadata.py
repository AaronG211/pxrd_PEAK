#!/usr/bin/env python3
"""Backfill journal, publication year, authors, and unresolved titles from Crossref.

The script reads DOIs from the local SQLite paper table, queries the public
Crossref REST API in batches, and records every proposed change before touching
anything. It is audit-only unless --apply is supplied, and it makes no model
calls and no Supabase calls; publishing is the separate
`import_pxrd_to_supabase.py --metadata-only` step.

Author rule: every Crossref contributor is rendered as "Given Family Suffix"
(or the organisation `name`), joined with ", ". Lists longer than 12 names are
truncated to the first 12 followed by "et al." so the single Supabase `authors`
text column stays readable. The rule is restated in the JSON report.

Year rule: the first usable of `published-print`, `published-online`, `issued`.
The printed issue year is the citation year of record; `issued` is Crossref's
earliest deposited date and runs a year early for the ~7% of this corpus that
appeared online in December and in print the following January. Anything
non-integer or outside 1800-2200 is rejected, never clamped and never written,
and is listed under `rejected_years` so the Supabase CHECK constraint cannot fire.

Title rule: a title is filled only when the local one is unresolved (null,
empty, equal to the paper id, or equal to the DOI - the same test the frontend
uses in lib/api.ts). A resolved local title carrying mis-decoded bytes that
Crossref renders cleanly is repaired. Other resolved titles are left alone
unless --prefer-crossref-titles is supplied; either way every divergence is
reported.

Failure rule: a network or HTTP failure is not a Crossref verdict. Papers in a
failed batch are left entirely untouched - no timestamp, no status - so a rerun
retries them instead of skipping them as already fetched. The run exits 1
whenever any batch failed.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SEED = "pxrd-free-pilot-v1"
CROSSREF_WORKS = "https://api.crossref.org/works"
CROSSREF_SELECT = (
    "DOI,title,container-title,short-container-title,author,issued,"
    "published-print,published-online,type,publisher,license"
)
USER_AGENT = "pxrd-open-database/1.0 (+https://pxrd-peak.vercel.app)"
BATCH_SIZE = 50
REQUEST_DELAY_SECONDS = 1.1
REQUEST_TIMEOUT = (15, 60)
MAX_AUTHORS_LISTED = 12
MIN_YEAR = 1800
MAX_YEAR = 2200

MISSING = object()
YEAR_FIELDS = ("published-print", "published-online", "issued")
# Classic UTF-8-decoded-as-Latin-1 signatures, plus the replacement character.
MOJIBAKE_MARKERS = re.compile(
    "Ã[\u0080-\u00bf]|â€[\u0093\u0094\u0098\u0099\u009c\u009d]"
    "|Â[\u00a0-\u00bf]|\ufffd"
)

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")
DOI_LIKE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?10\.\S+$", re.IGNORECASE)
DOI_PREFIX = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)
RATE_LIMIT_INTERVAL = re.compile(r"^(\d+)\s*([smh]?)$")
INTERVAL_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600}

BREAK = "\x00"
SCRIPT_END = "\x01"
HYPHENS = "\u2010\u2011\u002d"
DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u002d"
UNICODE_SPACES = (
    "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u202f\u205f\u3000"
)
ZERO_WIDTH = "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad"
OPENING_BRACKETS = "([{"
CLOSING_BRACKETS = ")]}"
ELEMENT_SYMBOL = re.compile(r"^[A-Z][a-z]?(?=[0-9\x01\x00]|$)")

CROSSREF_COLUMNS = (
    ("authors", "TEXT"),
    ("journal", "TEXT"),
    ("publication_year", "INTEGER"),
    ("crossref_fetched_at", "TEXT"),
    ("crossref_license", "TEXT"),
    ("crossref_status", "TEXT"),
)


def strip_jats_markup(raw: str) -> str:
    # Crossref stores deposited JATS verbatim: 10.4% of pilot titles carry
    # <sub>/<sup>/<i>/<b>/<scp>, and 58 of those are pretty-printed with a
    # newline plus 20 spaces around every element. Neither "drop the tags" nor
    # "drop the whitespace" is correct on its own, so each boundary is decided
    # from the characters that surround it.
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]*", BREAK, text)
    text = re.sub(BREAK + r"*<(?:sub|sup)>" + BREAK + r"*", "", text)
    text = re.sub(BREAK + r"*</(?:sub|sup)>", SCRIPT_END, text)
    text = re.sub(r"<[^>]*>", "", text)
    text = html.unescape(text)

    pieces: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character not in (BREAK, SCRIPT_END):
            pieces.append(character)
            index += 1
            continue
        is_script_end = character == SCRIPT_END
        cursor = index + 1
        breaks = 0
        while cursor < len(text) and text[cursor] == BREAK:
            breaks += 1
            cursor += 1
        if is_script_end and breaks == 0:
            index = cursor
            continue
        tail = text[cursor : cursor + 4]
        previous = pieces[-1] if pieces else ""
        if is_script_end:
            keep_space = not (
                not tail
                or tail[0].isdigit()
                or tail[0] in DASHES + "/,;:.)]}+"
                or ELEMENT_SYMBOL.match(tail)
            )
        else:
            keep_space = not (
                previous in OPENING_BRACKETS
                or (tail != "" and tail[0] in CLOSING_BRACKETS)
            )
        pieces.append(" " if keep_space else "")
        index = cursor
    return "".join(pieces)


def normalize_text(raw: str) -> str:
    text = html.unescape(raw)
    text = unicodedata.normalize("NFC", text)
    text = "".join(
        " " if character in UNICODE_SPACES or unicodedata.category(character) == "Cc" else character
        for character in text
    )
    text = "".join(
        character
        for character in text
        if character not in ZERO_WIDTH and unicodedata.category(character) != "Co"
    )
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(raw: str) -> str:
    title = normalize_text(strip_jats_markup(raw))
    title = re.sub(r"([^\s" + DASHES + r"])\s+([" + HYPHENS + r"])(?=\S)", r"\1\2", title)
    title = re.sub(r"\s*\*+\s*$", "", title)
    return re.sub(r"\s+", " ", title).strip()


def title_is_usable(title: str, paper_id: str, doi: str | None) -> bool:
    if not 20 <= len(title) <= 500:
        return False
    if DOI_LIKE.fullmatch(title):
        return False
    if identity_key(title) in {identity_key(paper_id), identity_key(doi or "")}:
        return False
    if len(re.findall(r"[A-Za-z]", title)) < 10 or len(title.split()) < 4:
        return False
    return True


def identity_key(value: str) -> str:
    # Mirrors hasResolvedTitle in frontend/src/lib/api.ts, including its
    # single-occurrence slash replacement, so the two agree on which titles the
    # website renders as "Title unavailable".
    return DOI_PREFIX.sub("", value.strip().lower()).replace("/", "_", 1)


def title_is_unresolved(title: str | None, paper_id: str, doi: str | None) -> bool:
    if title is None or not title.strip():
        return True
    key = identity_key(title)
    if key == identity_key(paper_id):
        return True
    return bool(doi) and key == identity_key(doi)


def alphanumeric_fold(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def looks_mojibake(value: str) -> bool:
    return bool(MOJIBAKE_MARKERS.search(value))


def divergence_kind(local: str, crossref: str) -> str:
    if local == crossref:
        return "identical"
    if re.sub(r"\s+", "", local) == re.sub(r"\s+", "", crossref):
        return "whitespace"
    if alphanumeric_fold(local) == alphanumeric_fold(crossref):
        return "punctuation"
    # A local title carrying mis-decoded bytes that Crossref renders cleanly is
    # definitively corrupt, so it is repaired even without
    # --prefer-crossref-titles. The reverse case stays "material" and is only
    # reported, never acted on.
    if looks_mojibake(local) and not looks_mojibake(crossref):
        return "local_mojibake"
    return "material"


def render_authors(
    entries: list[dict[str, Any]] | None,
) -> tuple[str | None, int, str | None]:
    """Return (rendered, total, full_list_when_truncated)."""
    if not entries:
        return None, 0, None
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("name"):
            rendered = normalize_text(str(entry["name"]))
        else:
            parts = [
                str(entry[key])
                for key in ("given", "family", "suffix")
                if entry.get(key)
            ]
            rendered = normalize_text(" ".join(parts))
        if rendered:
            names.append(rendered)
    if not names:
        return None, 0, None
    total = len(names)
    if total > MAX_AUTHORS_LISTED:
        # The truncated tail is preserved in the report so no name is lost.
        return (
            ", ".join(names[:MAX_AUTHORS_LISTED]) + " et al.",
            total,
            ", ".join(names),
        )
    return ", ".join(names), total, None


def date_part_year(field: Any) -> tuple[int | None, Any]:
    """Return (year, offending_value); offending is MISSING when simply absent."""
    if not isinstance(field, dict):
        return None, MISSING
    parts = field.get("date-parts") or []
    if not parts or not isinstance(parts[0], list) or not parts[0]:
        return None, MISSING
    raw = parts[0][0]
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None, raw
    if not MIN_YEAR <= raw <= MAX_YEAR:
        return None, raw
    return raw, MISSING


def extract_year(item: dict[str, Any]) -> tuple[int | None, str, list[dict[str, Any]]]:
    # The citation year of record is the printed issue year. Crossref `issued`
    # is the EARLIEST deposited date, so for the ~7% of this corpus that
    # appeared online in one year and in print the next, `issued` is a year
    # early. Measured on 300 pilot DOIs: 21 differed, and `issued` was the
    # earlier value in every one. Hence published-print, then published-online,
    # then issued.
    rejected: list[dict[str, Any]] = []
    for name in YEAR_FIELDS:
        year, offending = date_part_year(item.get(name))
        if year is not None:
            return year, name, rejected
        if offending is not MISSING:
            # A present-but-unusable date (including Crossref's [[None]] shape)
            # is reported rather than silently skipped.
            rejected.append({"field": name, "value": offending})
    return None, "missing", rejected


def extract_journal(item: dict[str, Any]) -> tuple[str | None, str]:
    for candidate in item.get("container-title") or []:
        journal = normalize_text(str(candidate))
        if journal:
            return journal, "container-title"
    publisher = normalize_text(str(item.get("publisher") or ""))
    if publisher:
        return publisher, "publisher"
    return None, "missing"


def extract_license(item: dict[str, Any]) -> str | None:
    licenses = [entry for entry in (item.get("license") or []) if isinstance(entry, dict)]
    if not licenses:
        return None
    preferred = [entry for entry in licenses if entry.get("content-version") == "vor"]
    url = str((preferred or licenses)[0].get("URL") or "").strip()
    return url or None


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


def all_papers(conn: sqlite3.Connection, limit: int) -> list[str]:
    paper_ids = [row[0] for row in conn.execute("select paper_id from papers")]
    ranked = sorted(
        paper_ids,
        key=lambda paper_id: hashlib.sha256(f"{SEED}:{paper_id}".encode()).hexdigest(),
    )
    return ranked[:limit]


def existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(papers)")}


def ensure_columns(conn: sqlite3.Connection) -> list[str]:
    columns = existing_columns(conn)
    added: list[str] = []
    for name, sql_type in CROSSREF_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {sql_type}")
            added.append(name)
    if added:
        conn.commit()
    return added


def load_papers(conn: sqlite3.Connection, paper_ids: list[str]) -> dict[str, dict[str, Any]]:
    columns = existing_columns(conn)
    wanted = ["paper_id", "doi", "title", "authors", "journal", "publication_year", "crossref_fetched_at"]
    available = [name for name in wanted if name in columns]
    marks = ",".join("?" for _ in paper_ids)
    rows = conn.execute(
        f"select {','.join(available)} from papers where paper_id in ({marks})",
        paper_ids,
    ).fetchall()
    loaded: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = {name: None for name in wanted}
        record.update({name: row[name] for name in available})
        loaded[record["paper_id"]] = record
    return loaded


def build_session() -> requests.Session:
    retry = Retry(
        total=6,
        connect=6,
        read=6,
        status=6,
        backoff_factor=0.75,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=2))
    return session


def header_delay(response: requests.Response, floor: float) -> float:
    limit = (response.headers.get("x-rate-limit-limit") or "").strip()
    interval = (response.headers.get("x-rate-limit-interval") or "").strip()
    match = RATE_LIMIT_INTERVAL.fullmatch(interval)
    if not limit.isdigit() or int(limit) < 1 or match is None:
        return floor
    seconds = int(match.group(1)) * INTERVAL_SECONDS[match.group(2)]
    return max(floor, seconds / int(limit))


def fetch_batch(
    session: requests.Session, dois: list[str], mailto: str | None
) -> tuple[list[dict[str, Any]], requests.Response | None, str | None]:
    params = {
        "filter": ",".join(f"doi:{doi}" for doi in dois),
        "select": CROSSREF_SELECT,
        "rows": str(len(dois)),
    }
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": USER_AGENT if not mailto else f"{USER_AGENT} mailto:{mailto}"}
    try:
        response = session.get(
            CROSSREF_WORKS, params=params, headers=headers, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        return [], None, f"request failed: {exc}"
    if response.status_code != 200:
        return [], response, f"HTTP {response.status_code}: {response.text[:200]}"
    try:
        payload = response.json()
    except ValueError:
        return [], response, f"non-JSON body: {response.text[:200]}"
    items = ((payload or {}).get("message") or {}).get("items") or []
    return [item for item in items if isinstance(item, dict)], response, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("../outputs/pxrd.db"))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument(
        "--all-papers",
        action="store_true",
        help=(
            "Draw from every paper row instead of the deterministic clean-set "
            "prefix. --limit still applies, so pass --limit 2370 for full coverage"
        ),
    )
    parser.add_argument("--apply", action="store_true", help="Write fetched metadata to SQLite")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch papers that already carry a Crossref timestamp",
    )
    parser.add_argument(
        "--prefer-crossref-titles",
        action="store_true",
        help="Replace resolved local titles when Crossref differs",
    )
    parser.add_argument(
        "--mailto",
        default=None,
        help="Contact address for the Crossref polite pool; defaults to CROSSREF_MAILTO or unset",
    )
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_SECONDS)
    parser.add_argument(
        "--report", type=Path, default=Path("backups/crossref_metadata_backfill.json")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = args.db.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not 1 <= args.limit <= 5000:
        raise ValueError("--limit must be between 1 and 5000")
    if not 1 <= args.batch_size <= 150:
        raise ValueError("--batch-size must be between 1 and 150")
    if args.delay < 0:
        raise ValueError("--delay must not be negative")

    mailto = (args.mailto or os.environ.get("CROSSREF_MAILTO") or "").strip() or None
    if mailto and "@" not in mailto:
        raise ValueError("--mailto must be an email address")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            added_columns = ensure_columns(conn)
        else:
            added_columns = [
                name for name, _ in CROSSREF_COLUMNS if name not in existing_columns(conn)
            ]
        paper_ids = (
            all_papers(conn, args.limit)
            if args.all_papers
            else deterministic_clean_papers(conn, args.limit)
        )
        papers = load_papers(conn, paper_ids)

        pending: list[str] = []
        without_doi: list[str] = []
        invalid_dois: list[dict[str, str]] = []
        already_fetched: list[str] = []
        for paper_id in paper_ids:
            record = papers.get(paper_id)
            doi = (record or {}).get("doi")
            if not record or not doi or not str(doi).strip():
                without_doi.append(paper_id)
                continue
            if not DOI_PATTERN.fullmatch(str(doi).strip()):
                invalid_dois.append({"paper_id": paper_id, "doi": str(doi)})
                continue
            if record.get("crossref_fetched_at") and not args.refresh:
                already_fetched.append(paper_id)
                continue
            pending.append(paper_id)

        by_doi = {str(papers[paper_id]["doi"]).strip().lower(): paper_id for paper_id in pending}
        session = build_session()
        fetched_at = datetime.now(timezone.utc).isoformat()
        items_by_paper: dict[str, dict[str, Any]] = {}
        failed_batches: list[dict[str, Any]] = []
        failed_papers: set[str] = set()
        unresolved_dois: list[str] = []
        network_calls = 0
        delay = args.delay

        batches = [
            pending[start : start + args.batch_size]
            for start in range(0, len(pending), args.batch_size)
        ]
        for index, batch in enumerate(batches):
            if index:
                time.sleep(delay)
            dois = [str(papers[paper_id]["doi"]).strip() for paper_id in batch]
            items, response, error = fetch_batch(session, dois, mailto)
            network_calls += 1
            if response is not None:
                delay = header_delay(response, args.delay)
            if error is not None:
                # A transport failure is NOT a Crossref verdict. These papers are
                # excluded from `updates` entirely, so --apply never stamps them
                # with crossref_fetched_at and the next run retries them instead
                # of skipping them forever as "already fetched".
                failed_batches.append({"batch": index, "papers": batch, "error": error})
                failed_papers.update(batch)
                print(
                    f"Crossref batch {index + 1}/{len(batches)}: FAILED ({error}); "
                    f"{len(batch)} papers left untouched for retry."
                )
                continue
            returned: set[str] = set()
            for item in items:
                key = str(item.get("DOI") or "").strip().lower()
                paper_id = by_doi.get(key)
                if paper_id is None:
                    continue
                returned.add(paper_id)
                items_by_paper[paper_id] = item
            missing = [paper_id for paper_id in batch if paper_id not in returned]
            unresolved_dois.extend(str(papers[paper_id]["doi"]) for paper_id in missing)
            print(
                f"Crossref batch {index + 1}/{len(batches)}: "
                f"{len(returned)}/{len(batch)} resolved; next delay {delay:.2f}s."
            )

        updates: list[dict[str, Any]] = []
        rejected_years: list[dict[str, Any]] = []
        title_divergences: list[dict[str, Any]] = []
        retracted: list[dict[str, str]] = []
        field_changes: list[dict[str, Any]] = []
        truncated_authors: list[dict[str, Any]] = []
        journal_from_publisher: list[str] = []
        still_unresolved_titles: list[dict[str, Any]] = []
        repaired_mojibake: list[dict[str, Any]] = []
        filled = {"journal": 0, "publication_year": 0, "authors": 0, "title": 0}

        for paper_id in pending:
            if paper_id in failed_papers:
                continue
            item = items_by_paper.get(paper_id)
            record = papers[paper_id]
            doi = str(record["doi"]).strip()
            if item is None:
                updates.append(
                    {
                        "paper_id": paper_id,
                        "doi": doi,
                        "crossref_status": "not_found",
                        "journal": record["journal"],
                        "publication_year": record["publication_year"],
                        "authors": record["authors"],
                        "title": record["title"],
                        "title_action": "keep",
                        "crossref_license": None,
                    }
                )
                continue

            journal, journal_source = extract_journal(item)
            if journal_source == "publisher":
                journal_from_publisher.append(paper_id)
            year, year_source, year_rejections = extract_year(item)
            for rejection in year_rejections:
                rejected_years.append({"paper_id": paper_id, "doi": doi, **rejection})
            authors, author_count, authors_full = render_authors(item.get("author"))
            if author_count > MAX_AUTHORS_LISTED:
                truncated_authors.append(
                    {
                        "paper_id": paper_id,
                        "author_count": author_count,
                        "authors_full": authors_full,
                    }
                )
            crossref_title = normalize_title(str((item.get("title") or [""])[0] or ""))
            if crossref_title.startswith("RETRACTED:"):
                retracted.append({"paper_id": paper_id, "doi": doi, "title": crossref_title})

            unresolved = title_is_unresolved(record["title"], paper_id, doi)
            usable = bool(crossref_title) and title_is_usable(crossref_title, paper_id, doi)
            title_action = "keep"
            title = record["title"]
            if usable and unresolved:
                title_action = "fill_unresolved"
                title = crossref_title
            elif usable and not unresolved:
                kind = divergence_kind(normalize_text(str(record["title"])), crossref_title)
                if kind != "identical":
                    title_divergences.append(
                        {
                            "paper_id": paper_id,
                            "doi": doi,
                            "kind": kind,
                            "local_title": record["title"],
                            "crossref_title": crossref_title,
                        }
                    )
                    if kind == "local_mojibake":
                        title_action = "repair_mojibake"
                        title = crossref_title
                        repaired_mojibake.append(
                            {
                                "paper_id": paper_id,
                                "doi": doi,
                                "local_title": record["title"],
                                "crossref_title": crossref_title,
                            }
                        )
                    elif args.prefer_crossref_titles:
                        title_action = "prefer_crossref"
                        title = crossref_title

            for name, value in (
                ("journal", journal),
                ("publication_year", year),
                ("authors", authors),
            ):
                if value is None:
                    continue
                if record[name] is None or str(record[name]).strip() == "":
                    filled[name] += 1
                elif record[name] != value:
                    field_changes.append(
                        {
                            "paper_id": paper_id,
                            "field": name,
                            "previous": record[name],
                            "value": value,
                        }
                    )
            if title_action != "keep":
                filled["title"] += 1
            if title_is_unresolved(title, paper_id, doi):
                # Crossref answered, but the site will still render
                # "Title unavailable" for this paper. Name it explicitly.
                still_unresolved_titles.append(
                    {
                        "paper_id": paper_id,
                        "doi": doi,
                        "crossref_title": crossref_title or None,
                    }
                )

            has_fields = any(value is not None for value in (journal, year, authors)) or usable
            updates.append(
                {
                    "paper_id": paper_id,
                    "doi": doi,
                    "crossref_status": "ok" if has_fields else "no_fields",
                    "previous_journal": record["journal"],
                    "journal": journal if journal is not None else record["journal"],
                    "journal_source": journal_source,
                    "previous_publication_year": record["publication_year"],
                    "publication_year": year if year is not None else record["publication_year"],
                    "year_source": year_source,
                    "previous_authors": record["authors"],
                    "authors": authors if authors is not None else record["authors"],
                    "author_count": author_count,
                    "previous_title": record["title"],
                    "title": title,
                    "title_action": title_action,
                    "crossref_license": extract_license(item),
                }
            )

        report = {
            "generated_at": fetched_at,
            "source": "crossref_rest_api",
            "network_calls": network_calls,
            "model_calls": 0,
            "mode": "apply" if args.apply else "audit",
            "polite_pool": mailto is not None,
            "batch_size": args.batch_size,
            "author_rule": (
                f"Given Family Suffix (or organisation name) joined with ', '; "
                f"lists longer than {MAX_AUTHORS_LISTED} names are truncated to the first "
                f"{MAX_AUTHORS_LISTED} followed by 'et al.'"
            ),
            "year_rule": (
                "first usable of published-print, published-online, issued "
                f"(the printed issue year is the citation year of record); values "
                f"outside {MIN_YEAR}-{MAX_YEAR} or non-integer are rejected and "
                "never written"
            ),
            "title_rule": (
                "fill only unresolved titles (null, empty, equal to paper_id, or equal to DOI); "
                "a resolved local title carrying mis-decoded bytes that Crossref renders cleanly "
                "is repaired; --prefer-crossref-titles also replaces other diverging resolved titles"
            ),
            "selected_papers": len(paper_ids),
            "requested_dois": len(pending),
            "resolved": len(items_by_paper),
            "missing_columns": added_columns,
            "papers_without_doi": without_doi,
            "invalid_dois": invalid_dois,
            "already_fetched_skipped": already_fetched,
            "unresolved_dois": unresolved_dois,
            "failed_batches": failed_batches,
            "fetch_failed_papers": sorted(failed_papers),
            "fields_filled": filled,
            "rejected_years": rejected_years,
            "journal_from_publisher": journal_from_publisher,
            "truncated_author_lists": truncated_authors,
            "retracted_titles": retracted,
            "repaired_mojibake_titles": repaired_mojibake,
            "still_unresolved_titles": still_unresolved_titles,
            "title_divergences": title_divergences,
            "field_changes": field_changes,
            "updates": updates,
        }
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

        if args.apply:
            conn.executemany(
                """
                update papers
                set journal=?, publication_year=?, authors=?, title=?,
                    crossref_fetched_at=?, crossref_license=?, crossref_status=?
                where paper_id=?
                """,
                [
                    (
                        row["journal"],
                        row["publication_year"],
                        row["authors"],
                        row["title"],
                        fetched_at,
                        row.get("crossref_license"),
                        row["crossref_status"],
                        row["paper_id"],
                    )
                    for row in updates
                ],
            )
            conn.commit()
    finally:
        conn.close()

    mode = "updated" if args.apply else "would update"
    print(
        f"{mode} {len(updates)}/{len(paper_ids)} papers; "
        f"{len(pending)} DOIs requested, {len(items_by_paper)} resolved, "
        f"{len(unresolved_dois)} unresolved, {len(already_fetched)} already fetched, "
        f"{len(without_doi)} without a DOI, {len(invalid_dois)} malformed."
    )
    print(
        f"Fields: journal {filled['journal']}, year {filled['publication_year']}, "
        f"authors {filled['authors']}, titles {filled['title']}; "
        f"{len(title_divergences)} title divergences, {len(field_changes)} value changes, "
        f"{len(rejected_years)} years rejected."
    )
    print(
        f"Network: {network_calls} Crossref requests, {len(failed_batches)} failed batches."
    )
    if failed_papers:
        print(
            f"WARNING: {len(failed_papers)} papers were left untouched by a transport "
            "failure and were NOT recorded as missing from Crossref. Rerun the same "
            "command to retry only those."
        )
    if still_unresolved_titles:
        print(
            f"{len(still_unresolved_titles)} papers still have no usable title and will "
            "render as 'Title unavailable'."
        )
    if repaired_mojibake:
        print(f"{len(repaired_mojibake)} mis-encoded local titles repaired from Crossref.")
    print(f"Report: {report_path}")
    return 0 if not failed_batches else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; rerun the same command to resume.", file=sys.stderr)
        raise SystemExit(130)
