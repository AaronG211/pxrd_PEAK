"""Parse PDF files into structured text using PyMuPDF."""
from pathlib import Path
import fitz  # PyMuPDF
import re


def parse_pdf(file_path: str | Path) -> dict:
    """Return {pages: [{page: int, text: str}], doi: str|None, title: str|None, page_count: int}."""
    doc = fitz.open(str(file_path))
    pages: list[dict] = []
    full_first_page = ""

    for i, page in enumerate(doc):
        text = page.get_text("text")
        pages.append({"page": i + 1, "text": text})
        if i == 0:
            full_first_page = text

    doi = _extract_doi(full_first_page) or _extract_doi("\n".join(p["text"] for p in pages[:3]))
    title = _extract_title(full_first_page)
    page_count = len(pages)
    doc.close()

    return {"pages": pages, "doi": doi, "title": title, "page_count": page_count}


def _extract_doi(text: str) -> str | None:
    m = re.search(r'10\.\d{4,9}/[^\s]+', text)
    return m.group(0).rstrip(".,;)") if m else None


def _extract_title(text: str) -> str | None:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        # Usually the longest line in the first few lines is the title
        candidates = lines[:10]
        best = max(candidates, key=len)
        if len(best) > 15:
            return best[:300]
    return None
