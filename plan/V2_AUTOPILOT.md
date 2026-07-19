# v2 "Autopilot" — Fully-Automatic PXRD Extraction Architecture (implemented)

**Status:** implemented in `src/pxrd_fetcher/v2/`, CLI command `pxrd-fetcher auto <pdf|dir>`
**Date:** 2026-06-11 (design snapshot)
**Goal:** eliminate manual review entirely. Every figure has exactly two terminal states — `accepted` (with a quantified evidence chain) or `rejected` (with a machine-readable reason). There is no `review_required`.

> **Note (added later):** this is a point-in-time design doc. One claim below —
> that the recall metric is "non-circular" — turned out to be wrong: the recall
> definition described here was banded around the trace itself and was later
> found to be circular and fixed. See `docs/pxrd_core_modules.pdf` (the recall
> case study) for the corrected metric. Everything else still reflects the code.

---

## 1. Core principle

> **CV owns pixels, the LLM owns semantics, and a deterministic pixel-fidelity verifier is the final arbiter.**

v1 failed because it made each component do what it is bad at: hand-tuned CV made semantic judgments (which curve is which), and the LLM only assisted at the margins. v2 reverses the division of labor and **trusts no single source** — every critical quantity (calibration, curve trace) is cross-validated by at least two independent sources.

## 2. Pipeline

```
PDF ─render(300dpi)─► pages
  1. locate_panels (LLM)        page image → fractional bbox of each PXRD panel
     └ adaptive crop expansion (CV)   ink crossing an edge → expand that way (avoid clipping tops/legends)
  2. analyze_figure (LLM, one call)
     classify + curve list (color/label/stacking order) + x-axis tick values + coarse plot_box
  3. frame selection (CV, evidence-scored) + topology-adaptive finalize
     candidates = long horizontal lines (small gaps merged) → scored by "ticks + numeric labels below the line + ink inside + size"
     ticks without a supporting label count only as weak evidence (avoid SEM-inset false ticks)
     top edge = upper bound of content ink (stops a flat baseline from posing as a frame line)
     search restricted to the neighborhood of the LLM plot_box (avoid neighboring-panel interference)
     vertical edges, two cases:
       "VERIFIED edge" = a continuous stroke ≥65% of frame height, within a narrow ±3.5% band around the bottom-axis endpoint
         (stacked curves share peak columns → counting single-column ink would pose as a frame line; the continuity test does not)
       or "OPEN edge" (the author never drew a frame) — open edges are never invented; they are bounded by three layers of in-figure evidence:
         physical extension of the bottom-axis line (merge gaps 0.6%, never weld in a neighbor's axis >20px away)
         ∩ LLM plot_box ±3% (only the LLM can answer the semantic question "which panel is mine")
         ∩ tick evidence ±1.4 tick intervals
     every frame candidate (any source) goes through finalize_frame — a single choke point
  4. consensus calibration (frame ladder: evidence-scored frame → hint-neighborhood detect → whole-crop detect; first to clear the trust bar wins)
     A: CV tick-pixel positions × LLM tick values
        pairing supports minor-tick substeps k∈{1,2,5}; ties broken by OCR points (2 points suffice to decide)
        "labels should span most of the axis" as a prior
     B: OCR (pixel, value) pairs (row-clustered to the topmost numeric row; blocks axis-title / neighbor-panel leakage)
     A and B each fit robustly (median-residual outlier rejection) → consensus only if full-width prediction differs by < 0.5°
     plausibility evaluated over the tick data range (no false kills from extrapolating past the frame edge)
     on disagreement → magnified axis-strip LLM arbitration → still failing = whole figure rejected
  5. anchor-guided tracing
     LLM draws a coarse polyline on the image (overlaid with a red 10×10 reference grid), 40–80 points per curve
     CV snaps to real curve pixels within a window around the anchor (repair ladder: color mask → foreground mask → wide window)
  6. pixel-fidelity verification (see note above re: recall)
     precision: fraction of traced columns whose y lies within tol of real ink
     recall:    fraction of real ink within the anchor band explained by the trace
     snap_rate: fraction of columns where pixels were found near the anchor
  7. decision: snap≥0.55 ∧ prec≥0.70 ∧ rec≥0.60 ∧ conf≥0.55 → accepted; otherwise rejected
     2θ output window = tick range ±1.25 intervals ∩ frame range (data can only reach where the axis was drawn — a hard cutoff against neighbor-panel leakage)
```

## 3. Key differences from v1

| Dimension | v1 | v2 |
|---|---|---|
| Terminal states | accepted / review_required / skipped | accepted / partial / rejected |
| Fidelity metric | overlay_similarity (compared to its own trace — circular) | precision/recall vs the **original image pixels** |
| Calibration | single-source OCR + fallback pixel calibration (silent failure) | two-source consensus + arbitration; failure = rejection (loud failure) |
| Curve tracing | topmost-pixel assumption + color separation (breaks on stacked/grayscale) | LLM semantic anchors + CV pixel snapping |
| Panel location | caption regex + Canny contours | LLM direct location + evidence-driven expansion |
| Uncertainty | none | per-point 2θ uncertainty = calibration residual + pixel quantization |
| Failure mode | goes to a review queue for a human | automatic repair ladder → if still failing, reject with evidence |

## 4. Evidence chain (per figure, result.json)

- `calibration`: method, RMSE (°), two-source agreement (°), tick pixels and values
- `series[].evidence`: snap_rate, pixel_precision, pixel_recall, anchor deviation, repair rounds
- `confidence`: 0.30·snap + 0.30·prec + 0.25·rec + 0.15·anchor agreement
- `overlay.png`: the extracted trace drawn directly on the original crop — the most intuitive QA artifact

## 5. Acceptance philosophy

Fully-automatic ≠ accept-everything. **Accepted data must be trustworthy; rejected data must have a reason.**
Better to reject a suspect figure (with a reason) than to let unverified data into the downstream.
The rejection rate is itself a quality signal: if a class of figures is systematically rejected → add a rung to the repair ladder, do not loosen the threshold.
