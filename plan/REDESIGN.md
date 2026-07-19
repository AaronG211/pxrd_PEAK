# PXRD Fetcher — Architecture Redesign

**Status:** Proposal / RFC
**Date:** 2026-06-04
**Driving concern:** *Accuracy & trust* — we cannot currently tell when an extracted curve is correct, and we cannot tell whether a code change made extraction better or worse.

---

## 1. Purpose recap (what the system is for)

PXRD Fetcher digitizes powder X-ray diffraction figures embedded in COF chemistry PDFs into machine-readable `2θ → intensity` curves, so the data can be reused for cross-paper meta-analysis. It is a local-first Python CLI backed by a multimodal LLM (OpenAI) plus classical computer vision.

- **Inputs:** local PDFs; an OpenAI API key/model (`.env`).
- **Outputs:** per-series CSV (`paper_id, figure_id, series_id, series_label, two_theta_deg, intensity_raw, intensity_norm`), a SQLite database, a JSON run summary, annotated images, matplotlib replots, and a read-only review HTML.

The end goal stated in the original design doc is **reusable, comparable scientific data** ("list all COFs with a peak at 7.5°", compare experimental vs simulated patterns across publications).

---

## 2. Current architecture (as built)

```
PDF ──render(450dpi, PyMuPDF)──► page PNGs
   ──screen page (GPT + keyword/OCR fallback)──► keep PXRD pages
   ──detect figure panels (Canny/contours + caption regex)──► crops
   per crop:
     classify is-PXRD?            (GPT + image)   → PxrdClassification
     extract metadata             (GPT, text)     → sample/material/reported peaks
     analyze curve count & labels (GPT + image)   → FigureCurveAnalysis
     digitize                     (classical CV)  → DigitizedCurve[]
        detect plot bbox → OCR x-ticks → RANSAC calibration →
        build curve mask → Viterbi/beam trace →
        ALS baseline + SavGol smooth + peak pick
     verify replot                (GPT compares)  → one mechanical repair retry
   persist → SQLite (run/paper/figure/series/point) + per-figure CSV
   export → accepted_series.csv + summary.json + review.html
```

Module map and responsibilities:

| Module | Lines | Role |
|---|---|---|
| `digitize.py` | 3174 | Classical CV digitization core (plot detection, calibration, tracing, multi-curve, AI-assist hooks) |
| `preprocess.py` | 689 | PDF render, figure-candidate detection, caption/context extraction |
| `openai_service.py` | 525 | All LLM calls; structured outputs; retry; disk cache |
| `pipeline.py` | 469 | Linear orchestration over a mutable `stats` dict |
| `storage.py` | 303 | SQLite via SQLModel (run/paper/figure/series/point/review) |
| `postprocess.py` | 184 | ALS baseline, normalize, SavGol, peak detection |
| `schemas.py` | 253 | Pydantic models shared across stages |
| others | — | `ocr`, `text`, `review`, `exporter`, `config`, `doctor`, `utils` |

### Observed reality (from the shipped DB, 79 runs)

- Figures: **73 accepted / 46 review-required / 15 skipped**.
- Reliability scores cluster around **~0.5**.
- **Every y-axis calibration is `fallback-y`, `tick_count: 0`** — the y-axis is never numerically calibrated (intensity is treated as relative only).

---

## 3. Diagnosis — why "accuracy & trust" is unmet

### 3.1 There is no ground truth and no fidelity metric (root cause)

- `overlay_similarity` compares the replot to **its own extracted trace** — it is circular. It measures internal self-consistency, not agreement with the figure.
- `peak_position_delta_deg` only fires when the caption happens to enumerate peaks.
- The only genuine image-vs-image check is the GPT `verify_replot` call: unvalidated, non-deterministic, and itself unmeasured.

Consequence: a `reliability_score` of 0.5 is uninterpretable, and no one can prove a change helped. **Trust requires measurement, and measurement does not exist.** This is the foundation that must be built first.

### 3.2 The hardest, most failure-prone work runs on brittle hand-tuned CV

`digitize.py` (3174 lines) hand-rolls Viterbi tracking, top-down beam tracing, morphological masks, and dozens of tuned constants (24-column lookback, hue-distance 18°, spike thresholds 0.18/0.07, span cap 120°, …). The multimodal model — already in the pipeline — is used only at the margins. The division of labor is inverted relative to where each tool is strong.

### 3.3 Known correctness traps

- **120° span guard** rejects legitimate wide-range scans, silently falling back to meaningless pixel calibration.
- **Beam tracing assumes the curve is the topmost pixel per column** — violated by vertically offset / stacked COF patterns (very common).
- **Color-based multi-curve separation** fails on grayscale "experimental vs simulated" stacks (also very common).
- **No per-point uncertainty**, so downstream consumers cannot weight or filter by confidence.

### 3.4 The monolith blocks testing (and therefore trust)

The pipeline is one linear pass over a mutable dict; stages are not independent, typed, or individually testable. This is *why* no accuracy harness exists — there is no seam to test against. It also means any digitization tweak forces a full re-render + reprocess (only LLM calls are cached).

### 3.5 Human review is a dead end

34% of figures need review, but `review.html` is read-only. Corrections cannot be captured, stored, re-applied, or learned from — so reliability never improves and the most trustworthy signal available (an expert's correction) is discarded.

---

## 4. Target architecture

Three structural moves, all in service of accuracy & trust:

1. **Independent, typed, content-addressed stages** → testable seams + cheap reprocessing.
2. **Extraction behind an interface, model-first** → replace the brittle CV core where the model is stronger, keep CV as fallback.
3. **A closed loop: evaluate → review/correct → re-extract → learn** → trust becomes measurable and improvable.

### 4.1 Stage pipeline over a content-addressed artifact store

Each stage is a pure function `In → Out`, keyed by `hash(inputs + stage_version + config)`. Stage outputs are cached independently on disk.

```
acquire → render → detect_figures → classify → calibrate
        → extract_curves → postprocess → verify → persist → export
```

Benefits directly serving trust:
- Each stage is unit-testable against fixtures (precondition for the eval harness).
- Reprocessing after a tracing change skips re-rendering and re-classification.
- A single figure can be re-extracted with corrected calibration without touching the rest.
- Stage version bumps invalidate exactly the affected cache entries.

### 4.2 Extraction behind a swappable interface (model-first)

```python
class CurveExtractor(Protocol):
    def calibrate_axes(self, img, hints) -> AxisCalibration: ...
    def trace_curves(self, img, calib, n_series, hints) -> list[Curve]: ...
```

Implementations:
- `ClassicalExtractor` — today's CV stack, retained for offline use and as a fallback.
- `MultimodalExtractor` — the model returns tick pixel-coordinates and curve polylines directly.
- `ReconcilingExtractor` — runs both, compares, and emits a disagreement signal (a real, non-circular fidelity check).

Selection is per-figure by confidence, or always-reconcile when budget allows. This retires most of the monolith's risk surface while keeping a deterministic fallback.

### 4.3 Genuine verification (replace the circular metric)

- **Image-vs-image fidelity:** rasterize the replot onto the *original* plot region and compute pixel/curve agreement (chamfer distance on the binarized curve, not against our own trace).
- **Cross-extractor agreement:** RMSD between classical and multimodal polylines after normalization.
- **Reported-peak agreement:** keep `peak_position_delta_deg`, but treat absence of reported peaks as "unverified," not "passed."
- **Per-point uncertainty:** propagate pixel resolution + calibration residual into a 2θ error estimate stored with every point.

### 4.4 Closed-loop human correction

- Persist corrections (calibration points, accept/reject, curve edits, peak confirmations) as first-class records.
- Re-extraction consumes corrections deterministically (corrected calibration overrides OCR).
- Corrected figures become **labeled ground truth** — the same labels that feed the eval harness (§5). This is the active-learning loop the original design doc called for.

### 4.5 Scientific data model

- Explicit `relative_intensity` semantics; stop implying `intensity_raw` is counts (y is uncalibrated).
- Per-point 2θ uncertainty.
- Export standard `.xy` alongside a documented JSON schema carrying units + full provenance (DOI, figure, calibration source, extractor name+version, confidence).
- Normalized queryable store (papers / figures / series / points / peaks) to support cross-paper queries.

### 4.6 Acquisition & scale (later)

- DOI/Unpaywall fetcher feeding the same content-hashed pipeline.
- Idempotent dedup by file hash and DOI.
- Batch/concurrent processing and LLM batch API.

---

## 5. The evaluation harness (build this first)

Trust is impossible without measurement. Before any architectural change, stand up a benchmark and metrics.

**Ground-truth sources (use both):**
1. **Synthetic figures** rendered from known `.xy` patterns with randomized axis ranges, colors, multi-curve stacking, fonts, and noise. Ground truth is *exact* and free — ideal for calibration and tracing accuracy.
2. **Hand-labeled real figures** (~30–50 from `sample_papers/`): true tick calibration points and digitized reference curves. Expensive but representative; grows for free as the correction loop (§4.4) runs.

**Metrics:**
- Axis calibration error (degrees).
- Curve RMSD after normalization.
- Peak-position MAE; peak recall / precision.
- Classification precision/recall (is-PXRD).

**Gate:** a benchmark run in CI; changes that regress headline metrics fail. Report per-figure so regressions are diagnosable.

This harness is also the objective scoreboard for deciding §4.2 — does the multimodal extractor actually beat the CV stack? Today that question is unanswerable.

---

## 6. Phased migration

Ordered so the instrument (measurement) comes before the changes it judges.

| Phase | Goal | Key work | Exit criterion |
|---|---|---|---|
| **0. Eval harness** | Make accuracy measurable | Synthetic generator + metrics + small hand-labeled set + CI gate | A single number for calibration error, curve RMSD, peak MAE on a fixed set |
| **1. Stage seams** | Make stages testable & cacheable | Extract typed stage functions over content-addressed cache; keep current logic behind them | Each stage unit-tested against fixtures; single-figure re-extraction works |
| **2. Model-first extractor** | Replace brittle core where it loses | `CurveExtractor` interface; `MultimodalExtractor`; reconcile vs classical | Benchmark shows model ≥ CV on calibration + RMSD, or documents where each wins |
| **3. Real verification** | Kill the circular metric | Image-vs-image fidelity + per-point uncertainty + cross-extractor agreement | Reliability score correlates with benchmark error |
| **4. Correction loop** | Capture & learn from review | Persist corrections; deterministic re-extraction; corrections feed the benchmark | An expert correction measurably improves that figure and grows the labeled set |
| **5. Data model & scale** | Trustworthy reusable output | `.xy` + provenance schema + uncertainty; DOI acquisition; concurrency | Cross-paper query works; corpus run is idempotent |

---

## 7. Risks & open questions

- **Multimodal coordinate accuracy:** LLMs are historically weak at precise pixel coordinates. Phase 2 must be gated by the Phase 0 benchmark — if the model loses, keep the CV core and apply model assist only where it measurably helps. This is a hypothesis to test, not a foregone conclusion.
- **Labeling cost:** synthetic data de-risks the cold start; the correction loop amortizes real labels over time.
- **Cost/latency** of reconcile-everything: make it a config tier (fast / accurate).
- **y-axis is genuinely uncalibrated** in most COF PXRD figures (intensity is "a.u."). Decision: standardize on **relative intensity only** and label it as such, rather than implying absolute counts.
- **Scope of `digitize.py` retirement:** keep it as `ClassicalExtractor`; do not delete until the benchmark says the replacement is at least as good on the cases it currently handles.

---

## 8. One-line summary

Today the pipeline produces numbers no one can verify, using brittle code no one can safely change. The redesign makes accuracy **measurable** (eval harness), the code **changeable** (typed cached stages), the hard work **more robust** (model-first extraction behind an interface), and the output **trustworthy** (real fidelity metrics, uncertainty, provenance, and a human-correction loop that feeds back into the benchmark).
