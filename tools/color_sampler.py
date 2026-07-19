"""Sample real ink-color clusters from a PXRD crop's plot interior.

The pipeline stores curves in physical (2θ, intensity) units with a per-series
non-linear normalisation, so the pixel trace cannot be reconstructed offline.
But result.json stores `analysis.plot_box` (the axes-frame rectangle in crop
fractions), which isolates the correct panel and excludes neighbour bleed.

This module just returns the dominant ink-color clusters inside plot_box (each
with its mean row). The caller matches clusters to series using the LLM color
NAME as the identity anchor (reliable) and the cluster's exact RGB as the shade
(faithful), falling back to a palette when sampling is unavailable.
"""

from __future__ import annotations

from typing import List, Optional

import cv2
import numpy as np


def sample_clusters(
    crop_path: str,
    plot_box: Optional[dict],
    k: int,
) -> List[dict]:
    """Return up to `k` ink-color clusters: [{'rgb':(r,g,b),'row':float,'size':int}].

    Empty list when there is too little ink to sample (mono/blank)."""
    if k <= 0:
        return []
    bgr = cv2.imread(crop_path)
    if bgr is None:
        return []
    H, W = bgr.shape[:2]

    if plot_box:
        x0 = int(plot_box.get("x0", 0.0) * W)
        y0 = int(plot_box.get("y0", 0.0) * H)
        x1 = int(plot_box.get("x1", 1.0) * W)
        y1 = int(plot_box.get("y1", 1.0) * H)
    else:
        x0, y0, x1, y1 = 0, 0, W, H
    # Shrink inward to avoid frame lines and tick labels.
    mx = max(2, int((x1 - x0) * 0.02))
    my = max(2, int((y1 - y0) * 0.02))
    x0, y0 = max(0, x0 + mx), max(0, y0 + my)
    x1, y1 = min(W, x1 - mx), min(H, y1 - my)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []

    interior = bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(interior, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1].astype(np.int32)
    V = hsv[:, :, 2].astype(np.int32)

    background = (V > 235) & (S < 30)                 # white paper
    colored = (S >= 55) & (V >= 45) & ~background     # saturated ink
    dark = (V <= 110) & ~background                   # black / very-dark curves
    keep = colored | dark
    if keep.sum() < 150:
        return []

    rows = np.nonzero(keep)[0].astype(np.float32)
    rgb = interior[:, :, ::-1][keep].astype(np.float32)  # BGR→RGB

    # Subsample for speed.
    if rgb.shape[0] > 25000:
        idx = np.random.default_rng(0).choice(rgb.shape[0], 25000, replace=False)
        rgb, rows = rgb[idx], rows[idx]

    K = int(min(k, rgb.shape[0]))
    lab = cv2.cvtColor(rgb.reshape(-1, 1, 3).astype(np.uint8),
                       cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _, labels, _ = cv2.kmeans(lab, K, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    labels = labels.ravel()

    clusters = []
    for c in range(K):
        m = labels == c
        if m.sum() < 30:
            continue
        clusters.append({
            "rgb": tuple(float(v) for v in np.median(rgb[m], axis=0)),
            "row": float(np.mean(rows[m])),
            "size": int(m.sum()),
        })
    return clusters
