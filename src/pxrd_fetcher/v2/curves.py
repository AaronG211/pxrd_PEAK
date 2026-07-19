"""Curve-type classification shared by the pipeline and the offline replot tools.

A PXRD figure mixes measured (experimental) traces with model-derived ones:
pure simulated/calculated patterns, Pawley/Rietveld refined profile fits, and
the difference residual. Only experimental traces are real measured data. Both
the scan (pipeline) and the replot (tools/qa_montage) import the single
classifier below so they can never disagree about what counts as "computed".
"""

from __future__ import annotations

from typing import Optional

# Substrings marking a model-derived (non-measured) curve. Chosen NOT to fire on
# experimental look-alikes: "calcined" (an experimental sample state) must not be
# read as "calculated", so we key on "calculat", never a bare "calc".
_COMPUTED_KW = (
    "simulat",                 # simulated, simulation
    "calculat",                # calculated, calculating
    "pawley", "pawly", "rietveld",
    "refine",                  # refined, refinement
    "difference",              # difference curve(s); NOT bare "differ" — that
                               # would swallow "different temperatures" etc.
    "residual",
    "theoret", "theory", "predicted",
    "modeled", "model-derived", "dft-derived",
    "stacking", "eclipsed", "staggered",  # AA/AB-stacking reference patterns
)
# Whole-string sample_state tokens (exact match — safe, never substring).
_COMPUTED_TOKENS = {"sim", "calc", "calcd", "diff", "ycal", "obs-calc"}
# Explicit in-label markers (e.g. "(simulated)", "NH2-MIL-125-Sim", "kgm simulated").
_LABEL_MARKERS = (
    "simulat", "(sim", "-sim", "calculat",
    "pawley", "rietveld", "refined", "difference", "ycal",
    "stacking", "eclipsed", "staggered",
)


def curve_is_computed(
    sample_state: Optional[str],
    label: Optional[str] = None,
    material_name: Optional[str] = None,
) -> bool:
    """True when a curve is model-derived — simulated, calculated, Pawley/
    Rietveld refined, a difference residual, or an AA/AB-stacking reference
    pattern — rather than measured data.

    An explicit, non-empty sample_state is trusted on its own: if it doesn't
    read as computed, the curve is kept even when material_name happens to
    mention "stacking" (e.g. a real sample named "AA stacking CTF-1" is an
    experimental material, not a stacking-model reference). The label/material
    text is only consulted as a fallback when sample_state is missing — that's
    how a bare "AB stacking" curve label with no state gets caught."""

    state = (sample_state or "").strip().lower()
    if state:
        if state in _COMPUTED_TOKENS:
            return True
        return any(k in state for k in _COMPUTED_KW)
    blob = f"{label or ''} {material_name or ''}".lower()
    return any(m in blob for m in _LABEL_MARKERS)
