"""Pydantic schemas shared across the pipeline."""

from __future__ import annotations

import json
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class DoctorCheck(BaseModel):
    name: str
    status: CheckStatus
    detail: str


class DoctorReport(BaseModel):
    checks: List[DoctorCheck]

    @property
    def ok(self) -> bool:
        return all(check.status != CheckStatus.FAIL for check in self.checks)


class ReportedPeak(BaseModel):
    label: Optional[str] = None
    two_theta_deg: Optional[float] = None
    intensity_note: Optional[str] = None


class PxrdClassification(BaseModel):
    is_pxrd: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    figure_label: Optional[str] = None
    likely_series_count: int = Field(default=1, ge=0)


class PagePxrdScreening(BaseModel):
    has_pxrd_graph: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class MetadataExtraction(BaseModel):
    sample_name: Optional[str] = None
    material_name: Optional[str] = None
    figure_label: Optional[str] = None
    series_label: Optional[str] = None
    reported_peaks: List[ReportedPeak] = Field(default_factory=list)
    notes: Optional[str] = None


class ArtifactKind(str, Enum):
    AXIS_FRAME = "axis_frame"
    TICK_LABEL = "tick_label"
    AXIS_TITLE = "axis_title"
    PEAK_LABEL = "peak_label"
    LEGEND = "legend"
    INSET = "inset"
    TEXT = "text"
    OTHER = "other"


class ArtifactRegion(BaseModel):
    kind: ArtifactKind = ArtifactKind.OTHER
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bbox: List[int] = Field(min_length=4, max_length=4)
    note: str = ""


class ArtifactAssistance(BaseModel):
    regions: List[ArtifactRegion] = Field(default_factory=list)
    summary: str = ""


class CurveDescriptor(BaseModel):
    order_from_top: int = Field(default=1, ge=1)
    label: Optional[str] = None
    material_name: Optional[str] = None
    sample_name: Optional[str] = None
    visible_color: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""


class FigureCurveAnalysis(BaseModel):
    curve_count: int = Field(default=1, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    curves: List[CurveDescriptor] = Field(default_factory=list)


class ReplotFailureCause(str, Enum):
    CALIBRATION = "calibration"
    TRACKING = "tracking"
    BASELINE = "baseline"
    PEAK_MISS = "peak_miss"
    WRONG_CURVE = "wrong_curve"
    OTHER = "other"


class ReplotDisagreementRegion(BaseModel):
    bbox: List[int] = Field(min_length=4, max_length=4)
    description: str = ""


class ReplotVerification(BaseModel):
    faithful: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    likely_cause: Optional[ReplotFailureCause] = None
    disagreement_regions: List[ReplotDisagreementRegion] = Field(default_factory=list)
    fix_hint: str = ""
    notes: str = ""


class LegendTraceAssignment(BaseModel):
    legend_text: str
    assigned_curve_index: int = Field(default=-1, ge=-1)
    visible_color: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""


class LegendAssignment(BaseModel):
    assignments: List[LegendTraceAssignment] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class AxisCalibration(BaseModel):
    slope: float
    intercept: float
    tick_count: int
    source: str

    def to_value(self, pixel_position: float) -> float:
        return self.slope * pixel_position + self.intercept


class FigureCandidate(BaseModel):
    candidate_id: str
    page_number: int
    bbox: Tuple[int, int, int, int]
    page_image_path: str
    crop_image_path: str
    caption: str = ""
    context: str = ""
    figure_label: Optional[str] = None
    keyword_score: float = 0.0
    axis_label_text: str = ""
    axis_label_source: Optional[str] = None


_DIGITIZED_CURVE_ARRAY_FIELDS = frozenset(
    {
        "raw_two_theta",
        "raw_intensity",
        "processed_two_theta",
        "processed_intensity",
        "baseline",
    }
)


class DigitizedCurve(BaseModel):
    status: str
    reason: Optional[str] = None
    series_key: Optional[str] = None
    series_index: int = Field(default=0, ge=0)
    series_order_from_top: int = Field(default=1, ge=1)
    series_label: Optional[str] = None
    material_name: Optional[str] = None
    sample_name: Optional[str] = None
    visible_color_hint: Optional[str] = None
    semantic_confidence: Optional[float] = None
    plot_bbox: Tuple[int, int, int, int]
    x_axis: Optional[AxisCalibration] = None
    y_axis: Optional[AxisCalibration] = None
    multi_series_detected: bool = False
    series_count_estimate: int = 1
    raw_two_theta: List[float] = Field(default_factory=list)
    raw_intensity: List[float] = Field(default_factory=list)
    processed_two_theta: List[float] = Field(default_factory=list)
    processed_intensity: List[float] = Field(default_factory=list)
    baseline: List[float] = Field(default_factory=list)
    peaks: List[ReportedPeak] = Field(default_factory=list)
    overlay_similarity: float = 0.0
    peak_position_delta_deg: Optional[float] = None
    reliability_score: float = 0.0
    flagged_reasons: List[str] = Field(default_factory=list)
    non_curve_boxes: List[Tuple[int, int, int, int]] = Field(default_factory=list)
    ai_non_curve_assisted: bool = False
    ai_non_curve_regions: List[ArtifactRegion] = Field(default_factory=list)
    csv_path: Optional[str] = None
    annotated_image_path: Optional[str] = None
    replot_image_path: Optional[str] = None
    replot_verification: Optional[ReplotVerification] = None
    replot_retry_count: int = 0

    def summary_json(self, *, indent: int = 2) -> str:
        """Persistence-safe JSON: excludes per-point arrays that live on disk."""

        return self.model_dump_json(indent=indent, exclude=_DIGITIZED_CURVE_ARRAY_FIELDS)


class FigureDigitizationResult(BaseModel):
    status: str
    reason: Optional[str] = None
    plot_bbox: Tuple[int, int, int, int]
    x_axis: Optional[AxisCalibration] = None
    y_axis: Optional[AxisCalibration] = None
    multi_series_detected: bool = False
    series_count_estimate: int = 1
    curve_analysis: Optional[FigureCurveAnalysis] = None
    reliability_score: float = 0.0
    flagged_reasons: List[str] = Field(default_factory=list)
    non_curve_boxes: List[Tuple[int, int, int, int]] = Field(default_factory=list)
    ai_non_curve_assisted: bool = False
    ai_non_curve_regions: List[ArtifactRegion] = Field(default_factory=list)
    annotated_image_path: Optional[str] = None
    series: List[DigitizedCurve] = Field(default_factory=list)
    legend_assignment: Optional[LegendAssignment] = None

    def summary_json(self, *, indent: int = 2) -> str:
        payload = self.model_dump()
        payload["series"] = [
            series.model_dump(exclude=_DIGITIZED_CURVE_ARRAY_FIELDS)
            for series in self.series
        ]
        return json.dumps(payload, indent=indent)


class PaperMetadata(BaseModel):
    paper_id: str
    source_path: str
    title: Optional[str] = None
    doi: Optional[str] = None
    year: Optional[int] = None


class PipelineFigureResult(BaseModel):
    candidate: FigureCandidate
    classification: PxrdClassification
    metadata: MetadataExtraction
    digitization: FigureDigitizationResult
