# PXRD Fetcher

Local-first CLI for extracting approximate PXRD line data from PDF figures.

Hybrid design: an LLM reads figure semantics (panels, axes, legends) and drops
coarse anchor polylines; deterministic OpenCV snaps them to real ink; every
trace is verified against the original pixels and ships with machine-readable
evidence, or a machine-readable rejection reason.

## Start here

- `docs/pxrd_core_modules.pdf` — visual walkthrough of the four core modules
  (the snap, consensus calibration, the repair ladder, offline QC), including
  what breaks when adapting to other figure types and the bugs we hit.
- `docs/pxrd_pipeline_slides.html` — interactive stage-by-stage deck.
- Core code: `src/pxrd_fetcher/v2/` (`vision.py`, `calibrate.py`,
  `extract.py`, `pipeline.py`) and `tools/quality_filter.py`.
- Tests: `python -m pytest tests/ --ignore=tests/test_integration_sample_papers.py`
