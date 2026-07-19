"""Scan sample_papers/ and report which PDFs have no PXRD panels.

Usage:
    python tools/scan_no_pxrd.py [--delete] [--yes] [--workers N]

Without --delete: prints a report only.
With --delete:    deletes the papers with zero PXRD panels.
--yes:            skip confirmation prompt (use with --delete for non-interactive runs).
--workers N:      parallel workers (default 5; be cautious with API rate limits).
"""

from __future__ import annotations

import argparse
import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pxrd_fetcher.config import get_settings
from pxrd_fetcher.openai_service import OpenAIService
from pxrd_fetcher.v2.llm import VisionLLM

_PAGE_LLM_MAX_DIM = 1600
_RENDER_DPI = 150
_print_lock = threading.Lock()


def _log(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs, flush=True)


def _render_page_pil(page: fitz.Page, dpi: int = 150):
    from PIL import Image
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def _downscale(img, max_dim: int):
    from PIL import Image
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    scale = max_dim / max(w, h)
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def scan_pdf(pdf_path: Path, llm: VisionLLM, tmp_dir: Path) -> tuple[Path, bool]:
    """Return (path, has_pxrd). Stops scanning as soon as one PXRD panel is found."""
    doc = fitz.open(pdf_path)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem
    for page_index, page in enumerate(doc):
        page_no = page_index + 1
        img = _render_page_pil(page, dpi=_RENDER_DPI)
        img = _downscale(img, _PAGE_LLM_MAX_DIM)
        page_text = page.get_text("text")
        # Thread-safe temp filename
        tid = threading.get_ident()
        img_path = tmp_dir / f"{stem}-p{page_no:03d}-{tid}.png"
        img.save(img_path)
        try:
            panels = llm.locate_panels(img_path, page_text)
            if panels.panels:
                return pdf_path, True
        except Exception as exc:
            _log(f"    [warn] {pdf_path.name} page {page_no}: {exc}")
        finally:
            img_path.unlink(missing_ok=True)
    return pdf_path, False


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--delete", action="store_true", help="Delete papers with no PXRD panels")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt (use with --delete)")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers (default 5)")
    parser.add_argument("--papers-dir", default="sample_papers", help="Directory with PDF papers")
    args = parser.parse_args()

    papers_dir = Path(args.papers_dir)
    pdfs = sorted(papers_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {papers_dir}")
        return

    settings = get_settings()
    service = OpenAIService(settings)
    llm = VisionLLM(service)
    tmp_dir = Path("/tmp/scan_no_pxrd")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    has_pxrd: list[Path] = []
    no_pxrd: list[Path] = []
    total = len(pdfs)

    print(f"Scanning {total} PDFs with {args.workers} workers ...\n")

    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scan_pdf, pdf, llm, tmp_dir): pdf for pdf in pdfs}
        for fut in as_completed(futures):
            completed += 1
            try:
                path, found = fut.result()
            except Exception as exc:
                path = futures[fut]
                _log(f"[{completed:3d}/{total}] ERROR {path.name}: {exc}")
                continue
            status = "PXRD found" if found else "NO PXRD"
            _log(f"[{completed:3d}/{total}] {path.name:55s} {status}")
            if found:
                has_pxrd.append(path)
            else:
                no_pxrd.append(path)

    # Sort for stable output
    has_pxrd.sort()
    no_pxrd.sort()

    print()
    print("=" * 60)
    print(f"PXRD found:    {len(has_pxrd):3d} papers")
    print(f"No PXRD:       {len(no_pxrd):3d} papers")
    print("=" * 60)

    if not no_pxrd:
        print("Nothing to delete.")
        return

    print("\nPapers with NO PXRD graphs:")
    for p in no_pxrd:
        print(f"  {p.name}")

    if args.delete:
        print(f"\nAbout to DELETE {len(no_pxrd)} file(s). This cannot be undone.")
        if args.yes:
            confirm = "yes"
            print("Auto-confirmed (--yes).")
        else:
            confirm = input("Type 'yes' to confirm: ").strip()
        if confirm.lower() == "yes":
            for p in no_pxrd:
                p.unlink()
                print(f"  deleted: {p.name}")
            print(f"\nDone. {len(no_pxrd)} files deleted.")
        else:
            print("Aborted — nothing deleted.")
    else:
        print(f"\nRun with --delete to remove these {len(no_pxrd)} papers.")


if __name__ == "__main__":
    main()
