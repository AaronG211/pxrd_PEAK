"""Merge one random_select batch folder's montage PNGs into a single PDF.

Reads review.csv (preserves the sampled order) and lays out one qa_montage.png
(crop | overlay | replot) per page, captioned with figure_id/status/confidence
so pages are identifiable during review.

Usage: python tools/merge_batch_pdf.py outputs/random_select   (does all batch_* dirs)
       python tools/merge_batch_pdf.py outputs/random_select/batch_1   (just one)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
from PIL import Image


def merge_one(batch_dir: Path) -> Path | None:
    review = batch_dir / "review.csv"
    if not review.exists():
        return None
    rows = list(csv.DictReader(review.open()))
    out_pdf = batch_dir / f"{batch_dir.name}_review.pdf"

    with PdfPages(out_pdf) as pdf:
        for row in rows:
            png = batch_dir / f"{row['figure_id']}.png"
            if not png.exists():
                continue
            img = np.array(Image.open(png).convert("RGB"))
            h, w = img.shape[:2]
            fig_w = 14.0
            fig_h = fig_w * (h / w) + 0.6   # + room for the caption
            fig, ax = plt.subplots(figsize=(fig_w, fig_h))
            ax.imshow(img)
            ax.axis("off")
            label = (f"{row['figure_id']}   [{row['status']}  "
                     f"conf={float(row['confidence']):.2f}  series={row['n_series']}]")
            fig.suptitle(label, fontsize=9, y=0.99, ha="center",
                          color="#333333", fontfamily="monospace")
            fig.subplots_adjust(top=0.90, bottom=0.02, left=0.02, right=0.98)
            pdf.savefig(fig, dpi=150)
            plt.close(fig)
    return out_pdf


def main() -> None:
    target = Path(sys.argv[1])
    batches = [target] if (target / "review.csv").exists() else sorted(target.glob("batch_*"))
    if not batches:
        print(f"no batch_* folders (with review.csv) found under {target}")
        return
    for b in batches:
        out = merge_one(b)
        if out:
            n = len(list(csv.DictReader((b / "review.csv").open())))
            print(f"{b.name}: {n} pages -> {out}")
        else:
            print(f"{b.name}: skipped (no review.csv)")


if __name__ == "__main__":
    main()
