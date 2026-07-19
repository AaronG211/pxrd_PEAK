"""Build per-figure QA montages for a v2 run: crop | overlay | CSV replot.

Usage: python tools/qa_montage.py <run_dir>
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from collections import defaultdict

import matplotlib.colors as mcolors

from color_sampler import sample_clusters

# Shared with the pipeline so scan and replot agree on what "computed" means.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pxrd_fetcher.v2.curves import curve_is_computed

# Qualitative palette used to guarantee every replot line is distinguishable
# (fallback when pixel-sampling is unavailable or two series collide).
_PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#393b79", "#637939"]

# Map common color names (from LLM) to matplotlib-safe equivalents
_COLOR_MAP = {
    "black": "#111111",
    "red": "#e03030",
    "blue": "#2255cc",
    "green": "#228822",
    "orange": "#e07020",
    "purple": "#882299",
    "brown": "#8B4513",
    "pink": "#e070a0",
    "gray": "#888888",
    "grey": "#888888",
    "cyan": "#00aaaa",
    "magenta": "#cc44aa",
    "dark blue": "#1a3a8a",
    "dark green": "#145214",
    "dark red": "#8b0000",
    "navy": "#001f5b",
    "olive": "#6b8e23",
    "teal": "#008080",
    "violet": "#8a2be2",
    "dark gray": "#444444",
    "light blue": "#6699dd",
    "lime": "#32cd32",
}
_FALLBACK_COLORS = ["#2255cc", "#e03030", "#228822", "#e07020", "#882299",
                    "#00aaaa", "#8B4513", "#888888"]


def _resolve_color(color_str: str, idx: int) -> str:
    if not color_str:
        return _FALLBACK_COLORS[idx % len(_FALLBACK_COLORS)]
    c = color_str.strip().lower()
    return _COLOR_MAP.get(c, c)


def _hex(rgb) -> str:
    r, g, b = (int(max(0, min(255, c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    if len(h) == 6:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return None


def _too_close(a: str, b: str, thresh: int = 45) -> bool:
    ra, rb = _hex_to_rgb(a), _hex_to_rgb(b)
    if ra is None or rb is None:
        return a == b
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5 < thresh


def _series_index(sid: str) -> int:
    """Extract the 1-based series number from an id like '...-s06'."""
    tail = sid.rsplit("-s", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def _rgb(c) -> tuple:
    return tuple(c)


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _name_to_rgb(name: str):
    """Canonical RGB for an LLM color name, or None if unparseable."""
    if not name:
        return None
    c = name.strip().lower()
    hexc = _COLOR_MAP.get(c, c)
    try:
        r, g, b = mcolors.to_rgb(hexc)
        return (r * 255, g * 255, b * 255)
    except (ValueError, TypeError):
        return None


def _shade_variant(base, j: int, total: int):
    """Distinct lightness variants of a base RGB for same-named series."""
    if base is None:
        return None
    if total == 1:
        return base
    # Spread lightness from 0.65× to 1.25× across the group.
    f = 0.65 + (1.25 - 0.65) * (j / max(1, total - 1))
    return tuple(min(255, max(0, v * f)) for v in base)


def _build_color_map(fig_dir: Path, result: dict) -> dict[int, str]:
    """Map series-number → hex color.

    Identity comes from the LLM color NAME (reliable), the exact shade from
    pixel-sampled clusters matched to that name (faithful). Same-named series
    are split into lightness variants by vertical order. A qualitative palette
    backstops anything unmatched, so no two series ever share a color."""
    series = result.get("series", [])
    n = len(series)
    crop = fig_dir / "crop.png"
    plot_box = result.get("analysis", {}).get("plot_box")

    clusters = []
    if crop.exists():
        clusters = sample_clusters(str(crop), plot_box, k=n + 3)

    # Group series indices (0-based, top→bottom) by their LLM color name.
    names = [(s.get("color") or "").strip().lower() for s in series]
    counts: dict[str, int] = defaultdict(int)
    for nm in names:
        counts[nm] += 1

    color_by_idx: dict[int, str] = {}
    used: list[str] = []
    used_clusters: set[int] = set()

    def _take_palette() -> str:
        for c in _PALETTE:
            if all(not _too_close(c, u) for u in used):
                return c
        return _PALETTE[len(used) % len(_PALETTE)]

    # Principle: a color the original draws UNIQUELY can be reproduced
    # faithfully (sampled shade matched to the name). A color SHARED by several
    # curves (the original distinguishes them only by label/position) cannot be
    # both faithful and distinguishable, so those get a qualitative palette.
    for i, nm in enumerate(names):
        ref = _name_to_rgb(nm)
        chosen = None
        if counts[nm] == 1 and ref is not None:
            best, bestd = None, 1e9
            for ci in range(len(clusters)):
                if ci in used_clusters:
                    continue
                dd = _dist(clusters[ci]["rgb"], ref)
                if dd < bestd:
                    best, bestd = ci, dd
            if best is not None and bestd < 110:
                used_clusters.add(best)
                chosen = _hex(clusters[best]["rgb"])     # faithful sampled shade
            else:
                chosen = _hex(ref)                        # canonical name color
        if chosen is None or any(_too_close(chosen, u) for u in used):
            chosen = _take_palette()
        used.append(chosen)
        color_by_idx[i + 1] = chosen
    return color_by_idx


def replot_from_csvs(fig_dir: Path, result: dict, out_path: Path) -> bool:
    # Label lookup + pixel-sampled color map (series-number → hex)
    series_meta: dict[str, dict] = {}
    for s in result.get("series", []):
        sid = s.get("series_id", "")
        series_meta[sid] = {
            "label": s.get("label") or s.get("series_label") or sid.split("-")[-1],
            "state": s.get("sample_state") or "",
            "material": s.get("material_name") or "",
        }
    # Only CSVs belonging to THIS result: a re-used output dir can hold stale
    # series files from earlier runs, which must not leak into the replot.
    series_files = sorted(
        p for p in fig_dir.glob("*-s*.csv") if p.stem in series_meta
    )
    if not series_files:
        return False
    color_by_idx = _build_color_map(fig_dir, result)

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=120)

    all_intensities = []
    series_data = []
    for i, path in enumerate(series_files):
        xs, ys = [], []
        label = ""
        sid = path.stem  # e.g. figid-s01
        meta = series_meta.get(sid, {})
        # Skip model-derived curves (simulated / calculated / Pawley-refined /
        # difference) — only measured experimental traces are replotted.
        if curve_is_computed(meta.get("state"), meta.get("label"), meta.get("material")):
            continue
        color = color_by_idx.get(_series_index(sid), _PALETTE[i % len(_PALETTE)])
        label = meta.get("label", "") or sid

        with path.open() as fh:
            for row in csv.DictReader(fh):
                try:
                    xs.append(float(row["two_theta_deg"]))
                    ys.append(float(row["relative_intensity"]))
                except (ValueError, KeyError):
                    continue
        if not xs:
            continue
        ys_arr = np.array(ys)
        span = ys_arr.max() - ys_arr.min()
        if span < 1e-6:
            span = 1.0
        ys_norm = (ys_arr - ys_arr.min()) / span * 100.0
        series_data.append((xs, ys_norm, label, color))
        all_intensities.append(ys_norm)

    if not series_data:
        return False

    # Auto offset: 60% of normalised range. s01 is the TOP curve in the
    # original figure (order_from_top), so it gets the LARGEST offset — the
    # replot then stacks in the same visual order as the source.
    offset_step = 60.0
    n = len(series_data)
    for i, (xs, ys_norm, label, color) in enumerate(series_data):
        offset = (n - 1 - i) * offset_step
        ax.plot(xs, ys_norm + offset, lw=1.0, color=color, label=label[:35])

    ax.set_xlabel("2θ (deg)", fontsize=9)
    ax.set_ylabel("Relative intensity (offset)", fontsize=9)
    ax.tick_params(labelsize=8)
    fig_id = result.get("figure_id", fig_dir.name)
    status = result.get("status", "")
    conf = result.get("confidence", 0)
    ax.set_title(f"{fig_id}\n{status}  conf={conf:.2f}", fontsize=7, pad=4)
    ax.legend(fontsize=7, loc="upper right", framealpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return True


def montage(fig_dir: Path, result: dict) -> Path | None:
    crop    = fig_dir / "crop.png"
    overlay = fig_dir / "overlay.png"
    replot  = fig_dir / "replot_qa.png"

    if not replot_from_csvs(fig_dir, result, replot):
        return None

    panels = [p for p in (crop, overlay, replot) if p.exists()]
    images = [Image.open(p).convert("RGB") for p in panels]
    target_h = 440
    resized = [im.resize((int(im.width * target_h / im.height), target_h), Image.LANCZOS)
               for im in images]
    gap = 12
    total_w = sum(im.width for im in resized) + gap * (len(resized) - 1)
    sheet = Image.new("RGB", (total_w, target_h), (240, 240, 240))
    x = 0
    for im in resized:
        sheet.paste(im, (x, 0))
        x += im.width + gap
    out = fig_dir / "qa_montage.png"
    sheet.save(out)
    return out


def main(run_dir: Path) -> None:
    made, skipped = [], []
    for result_file in sorted(run_dir.rglob("result.json")):
        data = json.loads(result_file.read_text())
        if data["status"] in ("accepted", "partial"):
            out = montage(result_file.parent, data)
            if out:
                made.append((data["figure_id"], data["status"], data["confidence"], out))
            else:
                skipped.append(data["figure_id"])
        else:
            skipped.append(data["figure_id"] + f" [{data['status']}]")

    print(f"\n{'='*60}")
    print(f"Montages built: {len(made)}")
    for fid, status, conf, path in made:
        print(f"  {fid}: {status} conf={conf:.2f}  -> {path.name}")
    if skipped:
        print(f"\nSkipped ({len(skipped)}):")
        for s in skipped:
            print(f"  {s}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
