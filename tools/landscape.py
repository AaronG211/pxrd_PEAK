"""COF literature structural landscape v0 — Objective A first look.

Embeds every clean curve whose scan covers [3, 28] deg on a common 0.05-deg
grid (max-normalized), PCA-50, then t-SNE to 2D. Panels: (a) top building-
block families, (b) first-peak position, (c) reported stacking mode,
(d) highest-volume papers (visual batch check).

The chemistry-vs-batch questions are answered with 10-NN enrichment in
PCA-50 space (t-SNE distorts global distances):
  * same-paper neighbor enrichment      — upper bound on batch/style effect
    (conflated with chemistry: same paper usually means same material)
  * same-family cross-paper enrichment  — the signal that matters: does the
    same chemistry land nearby even when it comes from a different lab?

Pure local compute, no network, no API.
Usage: python tools/landscape.py outputs/pxrd.db
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import numpy as np

GRID_LO, GRID_HI, GRID_STEP = 3.0, 28.0, 0.05
TOP_FAMILIES = 8
TOP_PAPERS = 6
KNN = 10


def norm_stacking(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower().strip()
    if t in ("nan", ""):
        return None
    if re.search(r"\baa\b", t) or "eclips" in t:
        return "AA / eclipsed"
    if re.search(r"\bab\b", t) or "stagger" in t:
        return "AB / staggered"
    return None


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else "outputs/pxrd.db"
    outdir = Path("outputs/analysis")
    outdir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db)
    meta = con.execute(
        """SELECT series_id, paper_id, first_peak_two_theta FROM clean_curves
           WHERE two_theta_min<=? AND two_theta_max>=?""",
        (GRID_LO, GRID_HI),
    ).fetchall()
    print(f"curves covering [{GRID_LO}, {GRID_HI}] deg: {len(meta)}")

    links = {
        r[0]: r[1:]
        for r in con.execute(
            """SELECT series_id, bb_1_abbrev, bb_2_abbrev, stacking_mode
               FROM merged_curve_cof WHERE match_tier IN ('A','B')"""
        )
    }

    grid = np.arange(GRID_LO, GRID_HI + GRID_STEP / 2, GRID_STEP)
    X, sids, papers, fps, fams, stacks = [], [], [], [], [], []
    for sid, paper, fp in meta:
        pts = con.execute(
            "SELECT two_theta_deg, relative_intensity FROM points"
            " WHERE series_id=? ORDER BY two_theta_deg", (sid,)
        ).fetchall()
        if len(pts) < 50:
            continue
        x = np.asarray([p[0] for p in pts])
        y = np.asarray([p[1] for p in pts])
        v = np.interp(grid, x, y)
        vmax = v.max()
        if vmax <= 0:
            continue
        X.append(v / vmax)
        sids.append(sid)
        papers.append(paper)
        fps.append(fp if fp is not None else np.nan)

        fam = stk = None
        if sid in links:
            b1, b2, sm = links[sid]
            ok = lambda s: s and str(s).strip() and str(s).lower() != "nan"
            if ok(b1) and ok(b2):
                fam = "+".join(sorted([str(b1).strip(), str(b2).strip()]))
            stk = norm_stacking(sm)
        fams.append(fam)
        stacks.append(stk)

    X = np.vstack(X)
    papers = np.asarray(papers)
    fps = np.asarray(fps, dtype=float)
    n = len(X)
    print(f"embedded matrix: {X.shape}")

    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE
    from sklearn.neighbors import NearestNeighbors

    Xp = PCA(n_components=50, random_state=0).fit_transform(X)
    emb = TSNE(n_components=2, perplexity=30, init="pca",
               random_state=0).fit_transform(Xp)

    # ---- kNN enrichment metrics in PCA space ------------------------------
    nn = NearestNeighbors(n_neighbors=KNN + 1).fit(Xp)
    _, nbr = nn.kneighbors(Xp)
    nbr = nbr[:, 1:]

    paper_counts = {p: np.sum(papers == p) for p in set(papers)}
    obs_p, cha_p = [], []
    for i in range(n):
        obs_p.append(np.mean(papers[nbr[i]] == papers[i]))
        cha_p.append((paper_counts[papers[i]] - 1) / (n - 1))
    fam_arr = np.asarray([f if f else "" for f in fams])
    obs_f, cha_f = [], []
    for i in range(n):
        if not fam_arr[i]:
            continue
        same_fam_diff_paper = (fam_arr == fam_arr[i]) & (papers != papers[i])
        if same_fam_diff_paper.sum() == 0:
            continue
        obs_f.append(np.mean(same_fam_diff_paper[nbr[i]]))
        cha_f.append(same_fam_diff_paper.sum() / (n - 1))
    e_paper = np.mean(obs_p) / max(np.mean(cha_p), 1e-12)
    e_family = np.mean(obs_f) / max(np.mean(cha_f), 1e-12)
    print(f"same-paper 10-NN enrichment:              {e_paper:6.1f}x "
          f"(obs {np.mean(obs_p):.3f} vs chance {np.mean(cha_p):.5f})")
    print(f"same-family CROSS-paper 10-NN enrichment: {e_family:6.1f}x "
          f"(obs {np.mean(obs_f):.3f} vs chance {np.mean(cha_f):.5f}, "
          f"n={len(obs_f)} curves)")

    # ---- figure ------------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(12, 10), dpi=250)
    for ax in axes.flat:
        ax.scatter(emb[:, 0], emb[:, 1], s=2, c="#d5d5d5", linewidths=0)
        ax.set_xticks([]), ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)

    fam_top = [f for f, _ in sorted(
        ((f, fam_arr.tolist().count(f)) for f in set(fam_arr) if f),
        key=lambda t: -t[1])][:TOP_FAMILIES]
    cmap = plt.get_cmap("tab10")
    ax = axes[0, 0]
    for k, f in enumerate(fam_top):
        m = fam_arr == f
        ax.scatter(emb[m, 0], emb[m, 1], s=8, color=cmap(k),
                   label=f"{f} ({m.sum()})", linewidths=0)
    ax.legend(fontsize=6, markerscale=2, loc="best")
    ax.set_title(f"(a) Top {TOP_FAMILIES} building-block families", fontsize=10)

    ax = axes[0, 1]
    m = ~np.isnan(fps)
    sc = ax.scatter(emb[m, 0], emb[m, 1], s=3, c=np.clip(fps[m], 2, 12),
                    cmap="viridis", linewidths=0)
    plt.colorbar(sc, ax=ax, fraction=0.04, label="first peak 2theta (deg)")
    ax.set_title("(b) First-peak position", fontsize=10)

    ax = axes[1, 0]
    for stk, color in (("AA / eclipsed", "#1f3b73"), ("AB / staggered", "#b3541e")):
        m = np.asarray([s == stk for s in stacks])
        ax.scatter(emb[m, 0], emb[m, 1], s=8, color=color,
                   label=f"{stk} ({m.sum()})", linewidths=0)
    ax.legend(fontsize=7, markerscale=2)
    ax.set_title("(c) Reported stacking mode", fontsize=10)

    ax = axes[1, 1]
    top_papers = [p for p, _ in sorted(paper_counts.items(),
                                       key=lambda t: -t[1])][:TOP_PAPERS]
    for k, p in enumerate(top_papers):
        m = papers == p
        ax.scatter(emb[m, 0], emb[m, 1], s=8, color=cmap(k),
                   label=f"{p[:28]} ({m.sum()})", linewidths=0)
    ax.legend(fontsize=5.5, markerscale=2)
    ax.set_title("(d) Highest-volume papers (batch check)", fontsize=10)

    fig.suptitle(
        f"COF literature structural landscape v0 — {n} clean curves, "
        f"[{GRID_LO}-{GRID_HI}] deg, t-SNE of PCA-50", fontsize=11)
    fig.tight_layout()
    fig.savefig(outdir / "landscape_v0.png", bbox_inches="tight")

    with open(outdir / "landscape_embedding_v0.csv", "w") as f:
        f.write("series_id,paper_id,tsne_x,tsne_y,first_peak_two_theta,"
                "bb_family,stacking\n")
        for i in range(n):
            f.write(f"{sids[i]},{papers[i]},{emb[i,0]:.3f},{emb[i,1]:.3f},"
                    f"{'' if np.isnan(fps[i]) else round(fps[i],3)},"
                    f"{fams[i] or ''},{stacks[i] or ''}\n")
    print(f"saved: {outdir}/landscape_v0.png + landscape_embedding_v0.csv")


if __name__ == "__main__":
    main()
