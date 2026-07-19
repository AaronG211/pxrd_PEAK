"""Typed models for the v2 fully-automatic pipeline.

Every figure terminates in exactly one of two states: ``accepted`` (with a
quantified, non-circular evidence chain) or ``rejected`` (with a machine
reason). There is no ``review_required``.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# LLM structured outputs
# --------------------------------------------------------------------------


class PanelBox(BaseModel):
    """One PXRD panel located on a page, in fractional page coordinates."""

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    panel_label: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PagePanels(BaseModel):
    """All PXRD panels the model can see on one rendered page."""

    panels: List[PanelBox] = Field(default_factory=list)
    notes: str = ""


class CurveSpec(BaseModel):
    """Semantic description of one diffraction trace inside a panel."""

    order_from_top: int = Field(default=1, ge=1)
    label: Optional[str] = None
    material_name: Optional[str] = None
    sample_state: Optional[str] = None  # experimental / simulated / calcined ...
    color: str = "black"
    is_stick_pattern: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class FracBox(BaseModel):
    """Axis-aligned box in fractional image coordinates."""

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)


class FigureAnalysis(BaseModel):
    """One-shot deep reading of a panel crop: classification + inventory + axis."""

    is_pxrd: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    figure_label: Optional[str] = None
    plot_box: Optional[FracBox] = None
    curves: List[CurveSpec] = Field(default_factory=list)
    x_tick_values: List[float] = Field(default_factory=list)
    x_axis_title: str = ""
    x_unit_is_degrees: bool = True
    y_axis_title: str = ""
    grayscale_only: bool = False
    has_inset: bool = False
    caption_text: str = ""


class TickReading(BaseModel):
    """LLM reading of the x-axis strip: ordered tick values, left to right."""

    tick_values: List[float] = Field(default_factory=list)
    axis_title: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""


class AnchorPoint(BaseModel):
    x_frac: float = Field(ge=-0.05, le=1.05)
    y_frac: float = Field(ge=-0.05, le=1.05)


class CurveAnchor(BaseModel):
    """Coarse polyline for one curve in fractional plot-box coordinates.

    (0,0) is the top-left of the plot interior, (1,1) the bottom-right.
    """

    order_from_top: int = Field(default=1, ge=1)
    label: Optional[str] = None
    points: List[AnchorPoint] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class CurveAnchorSet(BaseModel):
    curves: List[CurveAnchor] = Field(default_factory=list)
    notes: str = ""


class CalibrationArbitration(BaseModel):
    """LLM arbitration when calibration candidates disagree."""

    first_tick_value: Optional[float] = None
    last_tick_value: Optional[float] = None
    tick_values: List[float] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""


# --------------------------------------------------------------------------
# Deterministic pipeline results
# --------------------------------------------------------------------------


class CalibrationMethod(str, Enum):
    CV_TICKS_LLM_VALUES = "cv_ticks+llm_values"
    OCR_PAIRS = "ocr_pairs"
    CONSENSUS = "consensus"
    ARBITRATED = "arbitrated"


class AxisFit(BaseModel):
    """Linear pixel→2θ map with fit diagnostics."""

    slope: float
    intercept: float
    n_points: int
    rmse_deg: float = 0.0
    method: str = ""

    def to_deg(self, px: float) -> float:
        return self.slope * px + self.intercept


class CalibrationEvidence(BaseModel):
    fit: Optional[AxisFit] = None
    candidates: List[AxisFit] = Field(default_factory=list)
    agreement_deg: Optional[float] = None  # max |Δ2θ| between candidate fits over plot width
    tick_pixels: List[float] = Field(default_factory=list)
    tick_values: List[float] = Field(default_factory=list)
    ocr_pairs: List[Tuple[float, float]] = Field(default_factory=list)
    status: str = "unknown"  # consensus / single_method / arbitrated / failed
    notes: List[str] = Field(default_factory=list)


class TraceEvidence(BaseModel):
    """Per-curve tracing + verification evidence."""

    snap_rate: float = 0.0          # fraction of columns where CV found curve pixels near anchor
    pixel_precision: float = 0.0    # traced pixels close to original foreground
    pixel_recall: float = 0.0       # original curve pixels recovered by trace (per series band)
    mean_snap_residual_px: float = 0.0
    anchor_vs_final_rmsd_frac: float = 0.0
    repair_rounds: int = 0
    # Anchor-INDEPENDENT coverage, computed on the series-specific color mask
    # only (rung 1): every colored ink column counts, whether or not the
    # anchor went near it. Recorded, not gated — the anchor-banded recall
    # above cannot see ink the anchor missed; this can.
    independent_recall: Optional[float] = None
    notes: List[str] = Field(default_factory=list)


class SeriesResult(BaseModel):
    series_id: str
    label: Optional[str] = None
    material_name: Optional[str] = None
    sample_state: Optional[str] = None
    color: str = "black"
    status: str = "accepted"            # accepted | rejected
    reject_reason: Optional[str] = None
    confidence: float = 0.0
    two_theta_deg: List[float] = Field(default_factory=list)
    intensity_norm: List[float] = Field(default_factory=list)   # 0..100, relative
    two_theta_uncertainty_deg: float = 0.0
    peaks_two_theta: List[float] = Field(default_factory=list)
    evidence: TraceEvidence = Field(default_factory=TraceEvidence)
    csv_path: Optional[str] = None
    # The LLM anchor polyline (fractional plot coords) this trace grew from.
    # Persisted so tracing/fidelity can be replayed offline — without it,
    # re-scoring a curve costs an API call.
    anchor_points: List[Tuple[float, float]] = Field(default_factory=list)


class FigureResult(BaseModel):
    figure_id: str
    paper_id: str
    page_number: int
    panel_bbox: Tuple[int, int, int, int]
    crop_path: str
    status: str = "rejected"            # accepted | rejected | partial
    reject_reason: Optional[str] = None
    confidence: float = 0.0
    figure_label: Optional[str] = None
    analysis: Optional[FigureAnalysis] = None
    # Plot frame chosen at run time (crop pixel coords). Persisted so offline
    # tools replay the exact geometry instead of re-deriving a possibly
    # different frame.
    frame_bbox: Optional[Tuple[int, int, int, int]] = None
    calibration: CalibrationEvidence = Field(default_factory=CalibrationEvidence)
    series: List[SeriesResult] = Field(default_factory=list)
    overlay_path: Optional[str] = None
    elapsed_seconds: float = 0.0


class PaperResult(BaseModel):
    paper_id: str
    source_path: str
    doi: Optional[str] = None
    title: Optional[str] = None
    figures: List[FigureResult] = Field(default_factory=list)


class RunReport(BaseModel):
    run_dir: str
    papers: List[PaperResult] = Field(default_factory=list)

    @property
    def n_accepted(self) -> int:
        return sum(
            1 for p in self.papers for f in p.figures if f.status in ("accepted", "partial")
        )

    @property
    def n_rejected(self) -> int:
        return sum(1 for p in self.papers for f in p.figures if f.status == "rejected")
