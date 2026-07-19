"""Export all accepted figures as a single PDF: crop (left) | replot (right).

Usage: python tools/export_pdf.py outputs/full-run-01 [--out report.pdf]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
from PIL import Image


def add_page(pdf: PdfPages, fig_id: str, status: str, conf: float,
             crop_path: Path, replot_path: Path) -> None:
    crop   = np.array(Image.open(crop_path).convert("RGB"))
    replot = np.array(Image.open(replot_path).convert("RGB"))

    # Landscape A4-ish: 11.7 × 6 inches
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5),
                             gridspec_kw={"width_ratios": [1, 1]})
    fig.subplots_adjust(top=0.88, bottom=0.02, left=0.02, right=0.98, wspace=0.04)

    for ax, img, title in zip(axes, [crop, replot], ["Original crop", "CSV replot"]):
        ax.imshow(img)
        ax.set_title(title, fontsize=10, pad=4)
        ax.axis("off")

    label = f"{fig_id}   [{status}  conf={conf:.2f}]"
    fig.suptitle(label, fontsize=9, y=0.97, ha="center", color="#333333",
                 fontfamily="monospace")

    pdf.savefig(fig, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir
    out_pdf = args.out or (run_dir / "crop_vs_replot.pdf")

    entries = []
    for result_file in sorted(run_dir.rglob("result.json")):
        data = json.loads(result_file.read_text())
        if data["status"] not in ("accepted", "partial"):
            continue
        fig_dir     = result_file.parent
        crop_path   = fig_dir / "crop.png"
        replot_path = fig_dir / "replot_qa.png"
        if crop_path.exists() and replot_path.exists():
            entries.append((data["figure_id"], data["status"], data["confidence"],
                            crop_path, replot_path))

    if not entries:
        print("No accepted figures with images found.")
        return

    with PdfPages(out_pdf) as pdf:
        for fig_id, status, conf, crop_path, replot_path in entries:
            add_page(pdf, fig_id, status, conf, crop_path, replot_path)
            print(f"  + {fig_id}  conf={conf:.2f}")

    print(f"\nSaved {len(entries)} pages → {out_pdf}")


if __name__ == "__main__":
    main()
