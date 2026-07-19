"""Resumable batch runner: process new sample_papers into an existing run dir.

Runs the first N PDFs (sorted by filename), skipping any paper already present
in the run dir, and writes each paper's figures straight into it. Resume-safe:
a manifest (`_batch_done.txt`) records completed paper_ids, so re-running picks
up where it left off. Aggregate files (master CSV, report, PDF) are NOT written
here — regenerate them once after the batch finishes.

Usage: python tools/run_batch.py <run_dir> <papers_dir> [--limit 200] [--workers 4]
"""

from __future__ import annotations

import argparse
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pxrd_fetcher.config import get_settings
from pxrd_fetcher.ocr import get_ocr_backend
from pxrd_fetcher.openai_service import OpenAIService
from pxrd_fetcher.utils import derive_paper_id
from pxrd_fetcher.v2.llm import VisionLLM
from pxrd_fetcher.v2.pipeline import process_paper

_tls = threading.local()
_lock = threading.Lock()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("papers_dir", type=Path)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    run_dir: Path = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = run_dir / "_batch_done.txt"
    errors_log = run_dir / "_batch_errors.txt"

    # Seed the manifest with whatever is already in the run dir so the
    # pre-existing papers are treated as done from the start.
    if not manifest.exists():
        seed = sorted(d.name for d in run_dir.iterdir() if d.is_dir())
        manifest.write_text("\n".join(seed) + ("\n" if seed else ""))
    done = {x for x in manifest.read_text().split() if x}

    pdfs = sorted(args.papers_dir.glob("*.pdf"))[: args.limit]
    todo = [p for p in pdfs if derive_paper_id(p) not in done]

    settings = get_settings()
    if settings.openai_timeout_seconds < 240.0:
        settings.openai_timeout_seconds = 240.0
    llm = VisionLLM(OpenAIService(settings))  # shared (HTTP, thread-safe for calls)

    def ocr_for_thread():
        if not hasattr(_tls, "ocr"):
            _tls.ocr = get_ocr_backend(settings.ocr_backend)  # one per worker thread
        return _tls.ocr

    total = len(todo)
    print(f"[batch] window={len(pdfs)} PDFs, already-done={len(pdfs) - total}, "
          f"to-run={total}, workers={args.workers}", flush=True)
    t_start = time.time()
    counter = {"n": 0, "fig": 0, "err": 0}

    def work(pdf: Path):
        pid = derive_paper_id(pdf)
        paper = process_paper(pdf, run_dir, llm, ocr_for_thread(), settings)
        n_fig = sum(1 for f in paper.figures if f.status in ("accepted", "partial"))
        # No PXRD panels anywhere → drop the empty paper dir so the run stays clean.
        if not paper.figures:
            d = run_dir / pid
            if d.exists() and not any(d.rglob("result.json")):
                shutil.rmtree(d, ignore_errors=True)
        return pid, n_fig

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, p): p for p in todo}
        for fut in as_completed(futs):
            pdf = futs[fut]
            pid = derive_paper_id(pdf)
            try:
                pid, n_fig = fut.result()
                counter["fig"] += n_fig
                tag = f"{n_fig} fig" if n_fig else "no pxrd"
            except Exception as exc:  # noqa: BLE001 — keep the batch moving
                counter["err"] += 1
                tag = f"ERROR: {exc}"
                with _lock, errors_log.open("a") as fh:
                    fh.write(f"{pid}\t{exc}\n")
            with _lock:
                counter["n"] += 1
                with manifest.open("a") as fh:   # mark done either way (no infinite retry)
                    fh.write(pid + "\n")
                rate = counter["n"] / max(1e-9, (time.time() - t_start) / 60.0)
                print(f"[{counter['n']}/{total}] {pid}: {tag}   "
                      f"(figs={counter['fig']}, err={counter['err']}, "
                      f"{rate:.1f} papers/min)", flush=True)

    dt = (time.time() - t_start) / 60.0
    print(f"[batch] DONE {counter['n']}/{total} in {dt:.1f} min | "
          f"figures+={counter['fig']} | errors={counter['err']}", flush=True)


if __name__ == "__main__":
    main()
