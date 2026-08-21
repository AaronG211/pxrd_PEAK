#!/usr/bin/env python3
"""Backfill paper titles from embedded PDF metadata without network or model calls."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import fitz


SEED = "pxrd-free-pilot-v1"
DOI_LIKE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?10\.\S+$", re.IGNORECASE)
PAGE_RANGE = re.compile(r"\b\d+\s*\.\.\s*\d+\b")
BAD_MARKERS = (
    "microsoft word",
    "untitled document",
    "full text pdf",
    "downloaded from",
    "elsevier editorial system",
)


def normalize_title(raw: str) -> str:
    title = html.unescape(raw)
    title = unicodedata.normalize("NFC", title)
    title = "".join(character for character in title if unicodedata.category(character) != "Cc")
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"\*+$", "", title).strip()
    return title


def title_is_usable(title: str, paper_id: str) -> bool:
    lower = title.casefold()
    if not 20 <= len(title) <= 500:
        return False
    if DOI_LIKE.fullmatch(title) or title.casefold() == paper_id.casefold():
        return False
    if title.lower().endswith((".pdf", ".doc", ".docx")):
        return False
    if PAGE_RANGE.search(title) or any(marker in lower for marker in BAD_MARKERS):
        return False
    if len(re.findall(r"[A-Za-z]", title)) < 10 or len(title.split()) < 4:
        return False
    return True


def selected_papers(conn: sqlite3.Connection, limit: int) -> list[str]:
    ids = [
        row[0]
        for row in conn.execute(
            "select distinct paper_id from curves where in_clean_set=1"
        )
    ]
    return sorted(
        ids,
        key=lambda paper_id: hashlib.sha256(
            f"{SEED}:{paper_id}".encode()
        ).hexdigest(),
    )[:limit]


def extract_embedded_title(pdf_path: Path, paper_id: str) -> str | None:
    try:
        with fitz.open(pdf_path) as document:
            title = normalize_title(document.metadata.get("title") or "")
    except Exception:
        return None
    return title if title_is_usable(title, paper_id) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("../outputs/pxrd.db"))
    parser.add_argument("--pdf-root", type=Path, default=Path("../sample_papers"))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--apply", action="store_true", help="Write accepted titles to SQLite")
    parser.add_argument("--report", type=Path, default=Path("backups/pdf_title_backfill.json"))
    args = parser.parse_args()

    db_path = args.db.resolve()
    pdf_root = args.pdf_root.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not pdf_root.is_dir():
        raise FileNotFoundError(pdf_root)
    if not 1 <= args.limit <= 2000:
        raise ValueError("--limit must be between 1 and 2000")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        paper_ids = selected_papers(conn, args.limit)
        marks = ",".join("?" for _ in paper_ids)
        existing = {
            row["paper_id"]: row["title"]
            for row in conn.execute(
                f"select paper_id, title from papers where paper_id in ({marks})",
                paper_ids,
            )
        }
        accepted: list[dict[str, str | None]] = []
        missing_pdf: list[str] = []
        rejected: list[str] = []
        for paper_id in paper_ids:
            pdf_path = pdf_root / f"{paper_id}.pdf"
            if not pdf_path.is_file():
                missing_pdf.append(paper_id)
                continue
            title = extract_embedded_title(pdf_path, paper_id)
            if title is None:
                rejected.append(paper_id)
                continue
            accepted.append(
                {
                    "paper_id": paper_id,
                    "previous_title": existing.get(paper_id),
                    "title": title,
                }
            )

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "embedded_pdf_metadata",
            "network_calls": 0,
            "model_calls": 0,
            "selected_papers": len(paper_ids),
            "accepted_titles": len(accepted),
            "missing_pdf": missing_pdf,
            "rejected_metadata": rejected,
            "updates": accepted,
        }
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

        if args.apply:
            conn.executemany(
                "update papers set title=? where paper_id=?",
                [(row["title"], row["paper_id"]) for row in accepted],
            )
            conn.commit()
    finally:
        conn.close()

    mode = "updated" if args.apply else "would update"
    print(
        f"{mode} {len(accepted)}/{len(paper_ids)} titles; "
        f"{len(missing_pdf)} PDFs missing; {len(rejected)} metadata titles rejected."
    )
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
