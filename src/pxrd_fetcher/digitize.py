"""Digitization heuristics for PXRD figure crops."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .config import Settings
from .ocr import OCRBackend, OCREngineError, get_ocr_backend
from .postprocess import align_intensity_to_x_grid, process_curve, smooth_intensity
from .schemas import (
    ArtifactRegion,
    AxisCalibration,
    CurveDescriptor,
    DigitizedCurve,
    FigureCurveAnalysis,
    FigureDigitizationResult,
    LegendAssignment,
    ReplotFailureCause,
    ReplotVerification,
)
from .utils import (
    encode_image_object_to_data_url,
    encode_image_to_data_url,
    mean_or_none,
    slugify,
)


class DigitizationError(RuntimeError):
    """Raised when a figure cannot be digitized."""


_MAX_PXRD_TWO_THETA_SPAN = 120.0
_TARGET_HUE_DISTANCE_LIMIT = 18.0
_SERIES_ANNOTATION_COLORS: Tuple[Tuple[int, int, int], ...] = (
    (255, 128, 0),
    (255, 30, 30),
    (26, 170, 96),
    (198, 54, 138),
    (214, 124, 18),
    (0, 176, 176),
)


def digitize_figure(
    image_path: Path,
    output_dir: Path,
    settings: Settings,
    openai_service: object | None = None,
    curve_analysis: FigureCurveAnalysis | None = None,
    likely_series_count: int | None = None,
    caption: str | None = None,
    context: str | None = None,
) -> FigureDigitizationResult:
    """Extract one or more PXRD line series from a figure crop."""

    try:
        import cv2
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - import guard
        raise DigitizationError(
            "Missing digitization dependencies. Install OpenCV, Pillow, and NumPy."
        ) from exc

    image = Image.open(image_path).convert("RGB")
    output_dir.mkdir(parents=True, exist_ok=True)
    pixels = np.asarray(image)
    height, width = pixels.shape[:2]
    flagged: List[str] = []
    try:
        ocr_backend = get_ocr_backend(settings.ocr_backend)
    except OCREngineError as exc:
        raise DigitizationError(str(exc)) from exc

    if max(height, width) < settings.min_resolution_px:
        flagged.append("Low figure resolution")

    plot_bbox = detect_plot_bbox(pixels)
    plot_x0, plot_y0, plot_x1, plot_y1 = plot_bbox
    plot_pixels = pixels[plot_y0:plot_y1, plot_x0:plot_x1]

    x_entries = extract_axis_entries(pixels, plot_bbox, axis="x", ocr_backend=ocr_backend)
    y_entries = extract_axis_entries(pixels, plot_bbox, axis="y", ocr_backend=ocr_backend)
    x_axis = fit_axis_calibration(x_entries, source="ocr-x")
    y_axis = fit_axis_calibration(y_entries, source="ocr-y")
    if x_axis is not None and not _is_plausible_two_theta_calibration(
        x_axis,
        plot_width=max(1, plot_x1 - plot_x0),
    ):
        x_axis = None
    text_mask, _ = detect_text_mask(plot_pixels, ocr_backend)

    requires_manual_review = False
    if x_axis is None:
        flagged.append("Weak x-axis calibration")
        requires_manual_review = True

    multi_series_detected, series_count_estimate = estimate_series_count(plot_pixels)
    if multi_series_detected:
        flagged.append("Multiple series detected")

    curve_mask = build_curve_mask(plot_pixels, text_mask=text_mask)
    obstacle_mask = detect_obstacle_bboxes(plot_pixels)
    target_curve_count = _resolve_target_curve_count(
        settings=settings,
        curve_analysis=curve_analysis,
        likely_series_count=likely_series_count,
        heuristic_series_count=series_count_estimate,
    )
    extracted_traces = _extract_ranked_curve_traces(
        curve_mask,
        plot_pixels=plot_pixels,
        requested_count=target_curve_count,
        min_curve_coverage=max(0.06, settings.min_curve_coverage * 0.35),
        descriptors=curve_analysis.curves if curve_analysis else None,
        obstacle_mask=obstacle_mask,
    )

    if x_axis is None:
        x_axis = AxisCalibration(
            slope=1.0 / max(1, (plot_x1 - plot_x0)),
            intercept=0.0,
            tick_count=0,
            source="fallback-x",
        )
    if y_axis is None:
        y_axis = AxisCalibration(
            slope=-100.0 / max(1, (plot_y1 - plot_y0)),
            intercept=100.0 + (100.0 * plot_y0 / max(1, (plot_y1 - plot_y0))),
            tick_count=0,
            source="fallback-y",
        )
    extracted_traces, rejected_secondary_reasons = _filter_ranked_curve_traces(
        extracted_traces,
        curve_mask=curve_mask,
        text_mask=text_mask,
        x_axis=x_axis,
        curve_analysis=curve_analysis,
        target_curve_count=target_curve_count,
    )
    if rejected_secondary_reasons:
        for reason in rejected_secondary_reasons:
            if reason not in flagged:
                flagged.append(reason)
    if not extracted_traces:
        raise DigitizationError("Could not isolate enough curve points.")
    series_results: List[DigitizedCurve] = []
    trace_masks: List[np.ndarray] = []
    all_curve_columns: List[np.ndarray] = []
    all_curve_rows: List[np.ndarray] = []

    for series_index, (series_mask, curve_columns, curve_rows) in enumerate(extracted_traces):
        descriptor = _curve_descriptor_for_index(curve_analysis, series_index)
        series_result = _digitize_single_curve(
            image_path=image_path,
            output_dir=output_dir,
            settings=settings,
            plot_bbox=plot_bbox,
            x_axis=x_axis,
            y_axis=y_axis,
            curve_mask=series_mask,
            curve_columns=curve_columns,
            curve_rows=curve_rows,
            multi_series_detected=multi_series_detected or target_curve_count > 1,
            series_count_estimate=max(series_count_estimate, target_curve_count),
            shared_flagged_reasons=flagged,
            requires_manual_review=requires_manual_review,
            descriptor=descriptor,
            series_index=series_index,
        )
        series_results.append(series_result)
        trace_masks.append(series_mask)
        all_curve_columns.append(curve_columns)
        all_curve_rows.append(curve_rows)

    if target_curve_count > len(series_results):
        flagged.append(
            "Expected {expected} curve(s) but extracted {actual}".format(
                expected=target_curve_count,
                actual=len(series_results),
            )
        )
        requires_manual_review = True

    combined_curve_mask = np.zeros_like(curve_mask)
    for series_mask in trace_masks:
        combined_curve_mask = np.maximum(combined_curve_mask, series_mask)
    curve_columns = (
        np.concatenate(all_curve_columns).astype(float)
        if all_curve_columns
        else np.asarray([], dtype=float)
    )
    curve_rows = (
        np.concatenate(all_curve_rows).astype(float)
        if all_curve_rows
        else np.asarray([], dtype=float)
    )

    overlay_similarity = min(
        (series.overlay_similarity for series in series_results),
        default=0.0,
    )
    reliability_score = float(
        mean_or_none(series.reliability_score for series in series_results) or 0.0
    )
    non_curve_boxes_local, non_curve_mask = detect_non_curve_boxes(
        plot_pixels,
        curve_mask=curve_mask,
        primary_curve_mask=combined_curve_mask,
        curve_columns=curve_columns,
        curve_rows=curve_rows,
        ocr_backend=ocr_backend,
    )
    ai_non_curve_regions_local: List[ArtifactRegion] = []
    if _should_request_ai_non_curve_assistance(
        settings,
        x_axis=x_axis,
        overlay_similarity=overlay_similarity,
        flagged_reasons=flagged,
        multi_series_detected=multi_series_detected,
        artifact_mask=non_curve_mask,
        openai_service=openai_service,
    ):
        ai_non_curve_regions_local = _request_ai_non_curve_regions(
            plot_pixels,
            openai_service=openai_service,
            overlay_similarity=overlay_similarity,
            flagged_reasons=flagged,
            existing_box_count=len(non_curve_boxes_local),
            artifact_mask=non_curve_mask,
        )
        if ai_non_curve_regions_local:
            non_curve_boxes_local, non_curve_mask = detect_non_curve_boxes(
                plot_pixels,
                curve_mask=curve_mask,
                primary_curve_mask=combined_curve_mask,
                curve_columns=curve_columns,
                curve_rows=curve_rows,
                ocr_backend=ocr_backend,
                ai_regions=ai_non_curve_regions_local,
            )
    non_curve_boxes = [
        (plot_x0 + x0, plot_y0 + y0, plot_x0 + x1, plot_y0 + y1)
        for x0, y0, x1, y1 in non_curve_boxes_local
    ]
    ai_non_curve_regions = [
        region.model_copy(
            update={
                "bbox": [
                    plot_x0 + int(region.bbox[0]),
                    plot_y0 + int(region.bbox[1]),
                    plot_x0 + int(region.bbox[2]),
                    plot_y0 + int(region.bbox[3]),
                ]
            }
        )
        for region in ai_non_curve_regions_local
    ]
    non_curve_contours = _extract_artifact_contours(non_curve_mask)

    annotated_path = output_dir / "annotated.png"
    annotated_image = image.copy()
    draw = ImageDraw.Draw(annotated_image)
    draw.rectangle(plot_bbox, outline=(255, 0, 0), width=3)
    for pixel_position, _ in x_entries:
        draw.ellipse((pixel_position - 4, plot_y1 - 4, pixel_position + 4, plot_y1 + 4), fill=(0, 128, 255))
    for pixel_position, _ in y_entries:
        draw.ellipse((plot_x0 - 4, pixel_position - 4, plot_x0 + 4, pixel_position + 4), fill=(0, 200, 0))
    for region in ai_non_curve_regions:
        draw.rectangle(region.bbox, outline=(0, 190, 220), width=2)
    for contour in non_curve_contours:
        points = [(plot_x0 + x, plot_y0 + y) for x, y in contour]
        if len(points) == 1:
            px, py = points[0]
            draw.ellipse((px - 1, py - 1, px + 1, py + 1), fill=(160, 0, 200))
            continue
        draw.line(points + [points[0]], fill=(160, 0, 200), width=2)
    for series_index, series_result in enumerate(series_results):
        curve_color = _SERIES_ANNOTATION_COLORS[series_index % len(_SERIES_ANNOTATION_COLORS)]
        raw_two_theta = np.asarray(series_result.raw_two_theta, dtype=float)
        raw_intensity = np.asarray(series_result.raw_intensity, dtype=float)
        if raw_two_theta.size < 2 or raw_intensity.size < 2:
            continue
        global_x = ((raw_two_theta - x_axis.intercept) / _safe_slope_denominator(x_axis.slope)).astype(float)
        global_y = ((raw_intensity - y_axis.intercept) / _safe_slope_denominator(y_axis.slope)).astype(float)
        _draw_curve_polyline(draw, global_x, global_y, color=curve_color)
        label = series_result.series_label or "curve-{index:02d}".format(index=series_index + 1)
        if global_x.size:
            anchor_x = int(round(float(np.nanmedian(global_x[: min(5, global_x.size)]))))
            anchor_y = int(round(float(np.nanmedian(global_y[: min(5, global_y.size)]))))
            draw.text((anchor_x + 6, max(plot_y0 + 8, anchor_y - 14)), label, fill=curve_color)
    annotated_image.save(annotated_path)

    for series_result in series_results:
        series_result.annotated_image_path = str(annotated_path)
        series_result.non_curve_boxes = list(non_curve_boxes)
        series_result.ai_non_curve_assisted = bool(ai_non_curve_regions)
        series_result.ai_non_curve_regions = list(ai_non_curve_regions)

    legend_assignment = _maybe_assign_legend_to_curves(
        image_path=image_path,
        series_results=series_results,
        openai_service=openai_service,
        caption=caption or "",
        context=context or "",
    )

    _maybe_verify_and_repair_curves(
        image_path=image_path,
        plot_pixels=plot_pixels,
        series_results=series_results,
        openai_service=openai_service,
        caption=caption or "",
        settings=settings,
    )

    status = "review_required" if (
        requires_manual_review
        or any(series.status == "review_required" for series in series_results)
    ) else "accepted"

    figure_flagged_reasons = list(flagged)
    for series_result in series_results:
        for reason in series_result.flagged_reasons:
            if reason not in figure_flagged_reasons:
                figure_flagged_reasons.append(reason)

    return FigureDigitizationResult(
        status=status,
        reason=None,
        plot_bbox=plot_bbox,
        x_axis=x_axis,
        y_axis=y_axis,
        multi_series_detected=multi_series_detected or len(series_results) > 1,
        series_count_estimate=max(series_count_estimate, len(series_results), target_curve_count),
        curve_analysis=curve_analysis,
        reliability_score=float(reliability_score),
        flagged_reasons=figure_flagged_reasons,
        non_curve_boxes=non_curve_boxes,
        ai_non_curve_assisted=bool(ai_non_curve_regions),
        ai_non_curve_regions=ai_non_curve_regions,
        annotated_image_path=str(annotated_path),
        series=series_results,
        legend_assignment=legend_assignment,
    )


def _resolve_target_curve_count(
    *,
    settings: Settings,
    curve_analysis: FigureCurveAnalysis | None,
    likely_series_count: int | None,
    heuristic_series_count: int,
) -> int:
    if curve_analysis is not None and curve_analysis.confidence >= 0.78:
        return max(1, min(settings.max_curves_per_figure, int(curve_analysis.curve_count or 1)))

    candidates = [1, heuristic_series_count]
    if likely_series_count and likely_series_count > 0:
        candidates.append(int(likely_series_count))
    if curve_analysis is not None and curve_analysis.curve_count > 0:
        candidates.append(int(curve_analysis.curve_count))
    return max(1, min(settings.max_curves_per_figure, max(candidates)))


def _build_mask_by_color_name(plot_pixels: np.ndarray, color_name: str) -> Optional[np.ndarray]:
    import cv2

    color_name = color_name.lower().strip()
    hsv = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0]
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]

    if "black" in color_name or "gray" in color_name or "grey" in color_name:
        mask = (val < 100) | ((sat < 30) & (val < 180))
    elif "red" in color_name:
        mask = ((hue < 12) | (hue > 165)) & (sat > 40) & (val > 40)
    elif "blue" in color_name:
        mask = (hue > 100) & (hue < 140) & (sat > 40) & (val > 40)
    elif "green" in color_name:
        mask = (hue > 40) & (hue < 85) & (sat > 40) & (val > 40)
    elif "orange" in color_name:
        mask = (hue >= 12) & (hue <= 25) & (sat > 40) & (val > 40)
    elif "cyan" in color_name:
        mask = (hue >= 85) & (hue <= 100) & (sat > 40) & (val > 40)
    elif "magenta" in color_name or "purple" in color_name:
        mask = (hue >= 140) & (hue <= 165) & (sat > 40) & (val > 40)
    else:
        return None

    return mask.astype(np.uint8) * 255


def detect_obstacle_bboxes(plot_pixels: np.ndarray) -> Optional[np.ndarray]:
    """Detect rectangular inset/legend box regions and return a binary mask.

    The mask marks pixels inside detected rectangular frames as 255.  The
    top-down beam tracer treats these regions as fully transparent — it will
    pass straight through them regardless of their colour content.
    """
    import cv2

    height, width = plot_pixels.shape[:2]
    if height < 40 or width < 40:
        return None

    gray = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2GRAY)

    # Use Canny + dilation to find box borders (works for both solid and dashed lines)
    edges = cv2.Canny(gray, 30, 120)
    # Close small gaps in dashed borders
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((9, 9), dtype=np.uint8))

    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    obstacle_mask = np.zeros((height, width), dtype=np.uint8)

    # Real inset figures are substantial — require at least 10% wide, 15% tall.
    # The higher min_h filters out small in-plot text annotations like "(100)".
    min_w = int(width * 0.10)
    min_h = int(height * 0.15)

    hsv = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)

    for contour in contours:
        # Approximate to a polygon; keep only roughly rectangular shapes
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.04 * perimeter, True)
        if len(approx) < 4 or len(approx) > 8:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        if w < min_w or h < min_h:
            continue
        # Must not span almost the entire plot (that's the axis frame, not an inset).
        # Use 80% for height so tall vertical highlight bands are also excluded.
        if w > width * 0.90 or h > height * 0.80:
            continue
        # Aspect ratio: not too extreme (rules out thin lines and very tall narrow bands)
        aspect = w / max(1, h)
        if aspect > 6.0 or aspect < 0.20:
            continue

        # The interior must have non-trivial content (not just blank background)
        pad = 4
        roi_y0, roi_y1 = max(0, y + pad), min(height, y + h - pad)
        roi_x0, roi_x1 = max(0, x + pad), min(width, x + w - pad)
        if roi_y1 <= roi_y0 or roi_x1 <= roi_x0:
            continue

        roi_gray = gray[roi_y0:roi_y1, roi_x0:roi_x1]
        roi_hsv = hsv[roi_y0:roi_y1, roi_x0:roi_x1]
        roi_pixels = max(1, (roi_y1 - roi_y0) * (roi_x1 - roi_x0))

        # Content metric 1: pixel darkness (dark = potential content)
        dark_fraction = float((roi_gray < 200).sum()) / roi_pixels
        # Content metric 2: colour richness
        sat_fraction = float((roi_hsv[:, :, 1] > 40).sum()) / roi_pixels

        if dark_fraction < 0.05 and sat_fraction < 0.05:
            # Interior is basically blank white — not a real inset
            continue

        # Content metric 3: pixel complexity via Laplacian variance.
        # Solid-colour highlight bands (green/red/cyan overlays) are nearly
        # uniform and have very few sharp edges. Real inset figures are sharp.
        laplacian = cv2.Laplacian(roi_gray, cv2.CV_64F).var()
        if laplacian < 150.0:
            # Low complexity -> likely a background highlight or small text, not a complex inset
            continue

        cv2.rectangle(obstacle_mask, (x, y), (x + w, y + h), 255, -1)

    if not obstacle_mask.any():
        return None

    return obstacle_mask



def _extract_ranked_curve_traces(
    curve_mask: np.ndarray,
    *,
    plot_pixels: np.ndarray,
    requested_count: int,
    min_curve_coverage: float,
    descriptors: Optional[List[Any]] = None,
    obstacle_mask: Optional[np.ndarray] = None,
) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    import cv2

    if curve_mask.size == 0 or requested_count <= 0:
        return []

    traces: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    remaining_mask = curve_mask.copy()
    min_points = 20

    for i in range(requested_count):
        if not remaining_mask.any():
            break

        series_mask = None
        if descriptors and i < len(descriptors) and descriptors[i].visible_color:
            color_mask = _build_mask_by_color_name(plot_pixels, descriptors[i].visible_color)
            if color_mask is not None and color_mask.any():
                series_mask = cv2.bitwise_and(remaining_mask, color_mask)
                if series_mask.any():
                    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(series_mask, connectivity=8)
                    mask_to_keep = stats[:, cv2.CC_STAT_AREA] >= 10
                    mask_to_keep[0] = False
                    series_mask = mask_to_keep[labels].astype(np.uint8) * 255
                if not series_mask.any() or np.count_nonzero(series_mask) < 20:
                    series_mask = None

        if series_mask is None:
            series_mask = select_primary_curve_mask(remaining_mask, plot_pixels=plot_pixels)

        if not series_mask.any():
            break

        curve_columns, curve_rows = extract_curve_trace(
            series_mask, plot_pixels=plot_pixels, obstacle_mask=obstacle_mask
        )
        column_coverage = 0.0
        if curve_columns.size:
            unique_columns = np.unique(np.asarray(np.round(curve_columns), dtype=int))
            column_coverage = unique_columns.size / max(1.0, float(curve_mask.shape[1]))

        if curve_columns.size < min_points or column_coverage < min_curve_coverage:
            if traces:
                break
            candidate_columns, candidate_rows = extract_curve_trace(
                curve_mask, plot_pixels=plot_pixels, obstacle_mask=obstacle_mask
            )
            if candidate_columns.size < min_points:
                break
            traces.append((curve_mask.copy(), candidate_columns, candidate_rows))
            break

        traces.append((series_mask, curve_columns, curve_rows))
        removal_mask = _build_curve_support_mask(
            remaining_mask,
            series_mask,
            curve_columns,
            curve_rows,
        )
        removal_mask = cv2.dilate(removal_mask, np.ones((5, 5), dtype=np.uint8), iterations=1)
        next_mask = cv2.subtract(remaining_mask, removal_mask)
        if np.count_nonzero(next_mask) >= np.count_nonzero(remaining_mask):
            break
        remaining_mask = next_mask

    return traces


def _filter_ranked_curve_traces(
    traces: Sequence[Tuple[np.ndarray, np.ndarray, np.ndarray]],
    *,
    curve_mask: np.ndarray,
    text_mask: np.ndarray,
    x_axis: AxisCalibration,
    curve_analysis: FigureCurveAnalysis | None,
    target_curve_count: int,
) -> Tuple[List[Tuple[np.ndarray, np.ndarray, np.ndarray]], List[str]]:
    if not traces:
        return [], []

    kept: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = [traces[0]]
    rejected_reasons: List[str] = []
    primary_metrics = _curve_trace_metrics(traces[0], text_mask=text_mask, x_axis=x_axis, plot_width=curve_mask.shape[1])

    for trace in traces[1:]:
        metrics = _curve_trace_metrics(
            trace,
            text_mask=text_mask,
            x_axis=x_axis,
            plot_width=curve_mask.shape[1],
        )
        reject_reason = _secondary_trace_rejection_reason(
            metrics,
            primary_metrics=primary_metrics,
            curve_analysis=curve_analysis,
        )
        if reject_reason is not None:
            rejected_reasons.append(reject_reason)
            continue
        kept.append(trace)

    if target_curve_count > 0:
        kept = kept[:target_curve_count]
    return kept, _dedupe_preserve_order(rejected_reasons)


def _curve_trace_metrics(
    trace: Tuple[np.ndarray, np.ndarray, np.ndarray],
    *,
    text_mask: np.ndarray,
    x_axis: AxisCalibration,
    plot_width: int,
) -> dict:
    series_mask, curve_columns, _ = trace
    active_columns = np.unique(np.where(series_mask > 0)[1]).astype(int)
    dense_columns = np.unique(np.asarray(np.round(curve_columns), dtype=int))
    active_pixel_count = int(np.count_nonzero(series_mask))
    span_width = 0
    if dense_columns.size:
        span_width = int(dense_columns.max() - dense_columns.min() + 1)
    elif active_columns.size:
        span_width = int(active_columns.max() - active_columns.min() + 1)

    text_overlap_fraction = 0.0
    if active_pixel_count > 0 and text_mask.size and text_mask.shape == series_mask.shape:
        expanded_text_mask = _expand_binary_mask(text_mask > 0, radius_y=4, radius_x=6)
        text_overlap_fraction = float(np.count_nonzero((series_mask > 0) & expanded_text_mask)) / float(active_pixel_count)

    degree_span = 0.0
    if dense_columns.size >= 2:
        degree_span = float(x_axis.to_value(float(dense_columns[-1])) - x_axis.to_value(float(dense_columns[0])))

    dense_coverage = dense_columns.size / max(1.0, float(plot_width))
    support_coverage = active_columns.size / max(1.0, float(plot_width))
    support_density = active_columns.size / max(1.0, float(span_width))
    fill_inflation = dense_columns.size / max(1.0, float(active_columns.size))

    return {
        "degree_span": degree_span,
        "dense_coverage": dense_coverage,
        "support_coverage": support_coverage,
        "support_density": support_density,
        "fill_inflation": fill_inflation,
        "text_overlap_fraction": text_overlap_fraction,
    }


def _secondary_trace_rejection_reason(
    metrics: dict,
    *,
    primary_metrics: dict,
    curve_analysis: FigureCurveAnalysis | None,
) -> str | None:
    primary_span = max(0.0, float(primary_metrics["degree_span"]))
    short_span_limit = max(6.0, primary_span * 0.22)
    strong_span_limit = max(10.0, primary_span * 0.55)

    if metrics["text_overlap_fraction"] > 0.18:
        return "Rejected weak secondary curve candidate overlapping text/peak labels"
    if metrics["degree_span"] < short_span_limit:
        return "Rejected weak secondary curve candidate with short x-span"
    if metrics["support_coverage"] < 0.08 and metrics["fill_inflation"] > 1.8:
        return "Rejected weak secondary curve candidate inflated from sparse support"
    if metrics["support_density"] < 0.22 and metrics["fill_inflation"] > 1.6:
        return "Rejected weak secondary curve candidate with poor support density"

    if curve_analysis is not None and curve_analysis.confidence >= 0.78 and curve_analysis.curve_count <= 1:
        if metrics["degree_span"] < strong_span_limit:
            return "Rejected secondary curve candidate because AI identified a single full-span curve"
        if metrics["support_coverage"] < 0.2:
            return "Rejected secondary curve candidate because AI identified a single full-span curve"
        if metrics["text_overlap_fraction"] > 0.08:
            return "Rejected secondary curve candidate because AI identified a single full-span curve"

    return None


def _expand_binary_mask(mask: np.ndarray, *, radius_y: int, radius_x: int) -> np.ndarray:
    if mask.size == 0 or (radius_y <= 0 and radius_x <= 0):
        return mask.astype(bool)

    expanded = np.zeros_like(mask, dtype=bool)
    for offset_y in range(-radius_y, radius_y + 1):
        for offset_x in range(-radius_x, radius_x + 1):
            source_y0 = max(0, -offset_y)
            source_y1 = mask.shape[0] - max(0, offset_y)
            source_x0 = max(0, -offset_x)
            source_x1 = mask.shape[1] - max(0, offset_x)
            target_y0 = max(0, offset_y)
            target_y1 = target_y0 + max(0, source_y1 - source_y0)
            target_x0 = max(0, offset_x)
            target_x1 = target_x0 + max(0, source_x1 - source_x0)
            if source_y1 <= source_y0 or source_x1 <= source_x0:
                continue
            expanded[target_y0:target_y1, target_x0:target_x1] |= mask[source_y0:source_y1, source_x0:source_x1]
    return expanded


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    deduped: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _curve_descriptor_for_index(
    curve_analysis: FigureCurveAnalysis | None,
    series_index: int,
) -> CurveDescriptor | None:
    if curve_analysis is None or not curve_analysis.curves:
        return None
    ordered = sorted(curve_analysis.curves, key=lambda curve: (curve.order_from_top, -curve.confidence))
    if series_index >= len(ordered):
        return None
    return ordered[series_index]


def _digitize_single_curve(
    *,
    image_path: Path,
    output_dir: Path,
    settings: Settings,
    plot_bbox: Tuple[int, int, int, int],
    x_axis: AxisCalibration,
    y_axis: AxisCalibration,
    curve_mask: np.ndarray,
    curve_columns: np.ndarray,
    curve_rows: np.ndarray,
    multi_series_detected: bool,
    series_count_estimate: int,
    shared_flagged_reasons: Sequence[str],
    requires_manual_review: bool,
    descriptor: CurveDescriptor | None,
    series_index: int,
) -> DigitizedCurve:
    plot_x0, plot_y0, plot_x1, plot_y1 = plot_bbox
    flagged: List[str] = list(shared_flagged_reasons)
    local_review_required = bool(requires_manual_review)

    if len(curve_columns) / max(1, curve_mask.shape[1]) < settings.min_curve_coverage:
        flagged.append("Weak curve coverage")

    global_x = curve_columns + plot_x0
    global_y = curve_rows + plot_y0
    raw_two_theta = [x_axis.to_value(float(value)) for value in global_x]
    raw_intensity = [max(0.0, y_axis.to_value(float(value))) for value in global_y]

    processed_x, processed_y, baseline, peaks = process_curve(
        raw_two_theta,
        raw_intensity,
        step_deg=settings.interpolation_step_deg,
        smoothing_window=settings.smoothing_window,
        smoothing_polyorder=settings.smoothing_polyorder,
    )
    if processed_x.size >= 2:
        two_theta_span = float(processed_x[-1] - processed_x[0])
        if two_theta_span < 2.0 or two_theta_span > _MAX_PXRD_TWO_THETA_SPAN:
            flagged.append("Suspicious 2theta span")
            local_review_required = True

    overlay_similarity = compute_overlay_similarity(curve_mask, curve_columns, curve_rows)
    reliability_score = compute_reliability(
        overlay_similarity=overlay_similarity,
        x_axis=x_axis,
        y_axis=y_axis,
        flagged_reasons=flagged,
    )
    series_label = _derive_series_label(descriptor, series_index)
    replot_basename = "replot-{index:02d}-{slug}.png".format(
        index=series_index + 1,
        slug=slugify(series_label, fallback="curve"),
    )
    replot_path = output_dir / replot_basename
    replot_x, replot_y = _build_absolute_replot_curve(
        raw_two_theta,
        raw_intensity,
        processed_x,
        settings=settings,
    )
    save_replot(
        replot_x.tolist(),
        replot_y.tolist(),
        replot_path,
        title="{title} | {label}".format(title=image_path.stem, label=series_label),
        ylabel="Intensity (raw)",
    )

    return DigitizedCurve(
        status="review_required" if local_review_required else "accepted",
        reason=None,
        series_index=series_index,
        series_order_from_top=series_index + 1,
        series_label=series_label,
        material_name=descriptor.material_name if descriptor else None,
        sample_name=descriptor.sample_name if descriptor else None,
        visible_color_hint=descriptor.visible_color if descriptor else None,
        semantic_confidence=(float(descriptor.confidence) if descriptor else None),
        plot_bbox=plot_bbox,
        x_axis=x_axis,
        y_axis=y_axis,
        multi_series_detected=multi_series_detected,
        series_count_estimate=series_count_estimate,
        raw_two_theta=list(map(float, raw_two_theta)),
        raw_intensity=list(map(float, raw_intensity)),
        processed_two_theta=processed_x.astype(float).tolist(),
        processed_intensity=processed_y.astype(float).tolist(),
        baseline=baseline.astype(float).tolist(),
        peaks=peaks,
        overlay_similarity=float(overlay_similarity),
        reliability_score=float(reliability_score),
        flagged_reasons=flagged,
        replot_image_path=str(replot_path),
    )


def _derive_series_label(descriptor: CurveDescriptor | None, series_index: int) -> str:
    if descriptor is not None:
        for value in (descriptor.label, descriptor.sample_name, descriptor.material_name):
            if value:
                return value
    return "curve-{index:02d}".format(index=series_index + 1)


def _build_absolute_replot_curve(
    raw_two_theta: Sequence[float],
    raw_intensity: Sequence[float],
    processed_two_theta: Sequence[float] | np.ndarray,
    *,
    settings: Settings,
) -> Tuple[np.ndarray, np.ndarray]:
    raw_x = np.asarray(raw_two_theta, dtype=float)
    raw_y = np.asarray(raw_intensity, dtype=float)
    target_x = np.asarray(processed_two_theta, dtype=float)
    if raw_x.size == 0 or raw_y.size == 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    aligned_raw_y = align_intensity_to_x_grid(raw_x, raw_y, target_x)
    if target_x.size and aligned_raw_y.size == target_x.size:
        smoothed_raw_y = smooth_intensity(
            aligned_raw_y,
            settings.smoothing_window,
            settings.smoothing_polyorder,
        )
        return target_x.astype(float), smoothed_raw_y.astype(float)

    order = np.argsort(raw_x)
    return raw_x[order].astype(float), raw_y[order].astype(float)


def _maybe_assign_legend_to_curves(
    *,
    image_path: Path,
    series_results: List[DigitizedCurve],
    openai_service: object | None,
    caption: str,
    context: str,
) -> Optional[LegendAssignment]:
    """Ask AI to match legend/inline labels to tracked curves and apply them."""

    if openai_service is None or not series_results:
        return None
    if not hasattr(openai_service, "assign_legend_to_curves"):
        return None
    from .openai_service import OpenAIServiceError

    current_curves = [
        {
            "order_from_top": series.series_order_from_top,
            "visible_color": series.visible_color_hint,
            "label": series.series_label,
        }
        for series in series_results
    ]

    try:
        assignment = openai_service.assign_legend_to_curves(
            image_path,
            current_curves=current_curves,
            caption=caption,
            context=context,
        )
    except OpenAIServiceError:
        return None

    applied = 0
    for entry in assignment.assignments:
        if entry.confidence < 0.6:
            continue
        idx = int(entry.assigned_curve_index)
        if idx < 0 or idx >= len(series_results):
            continue
        target = series_results[idx]
        if entry.legend_text:
            target.series_label = entry.legend_text
            target.material_name = target.material_name or entry.legend_text
        if entry.visible_color:
            target.visible_color_hint = entry.visible_color
        applied += 1

    if applied == 0 and assignment.overall_confidence < 0.5 and not assignment.assignments:
        return None
    return assignment


def _maybe_verify_and_repair_curves(
    *,
    image_path: Path,
    plot_pixels: np.ndarray,
    series_results: List[DigitizedCurve],
    openai_service: object | None,
    caption: str,
    settings: Settings,
) -> None:
    """Ask AI to verify each replot and run a targeted repair when it fails.

    Repairs are bounded: at most one retry per curve. Causes we can mechanically
    repair (baseline, peak_miss) are fixed in place; structural causes (tracking,
    calibration, wrong_curve) just flag the curve for manual review.
    """

    if openai_service is None or not series_results:
        return
    if not hasattr(openai_service, "verify_replot"):
        return
    from .openai_service import OpenAIServiceError

    try:
        crop_data_url = encode_image_object_to_data_url(plot_pixels)
    except Exception:
        return
    crop_height, crop_width = plot_pixels.shape[:2]

    for series in series_results:
        if not series.replot_image_path:
            continue
        replot_path = Path(series.replot_image_path)
        if not replot_path.exists():
            continue

        verification = _call_replot_verifier(
            openai_service=openai_service,
            crop_data_url=crop_data_url,
            replot_path=replot_path,
            crop_width=crop_width,
            crop_height=crop_height,
            series=series,
            caption=caption,
        )
        if verification is None:
            continue

        series.replot_verification = verification
        if verification.faithful:
            continue

        cause = verification.likely_cause
        repaired = False
        if cause == ReplotFailureCause.BASELINE:
            repaired = _repair_curve_series(
                series,
                settings=settings,
                image_path=image_path,
                als_lam_override=1.0e8,
            )
        elif cause == ReplotFailureCause.PEAK_MISS:
            repaired = _repair_curve_series(
                series,
                settings=settings,
                image_path=image_path,
                peak_prominence_scale=0.5,
            )

        if repaired and series.replot_image_path:
            reverified = _call_replot_verifier(
                openai_service=openai_service,
                crop_data_url=crop_data_url,
                replot_path=Path(series.replot_image_path),
                crop_width=crop_width,
                crop_height=crop_height,
                series=series,
                caption=caption,
            )
            if reverified is not None:
                series.replot_verification = reverified

        final = series.replot_verification
        if final is None or not final.faithful:
            reason = "Replot verification failed"
            if final is not None and final.likely_cause is not None:
                reason = "{base} ({cause})".format(base=reason, cause=final.likely_cause.value)
            if reason not in series.flagged_reasons:
                series.flagged_reasons.append(reason)
            series.status = "review_required"
    return


def _call_replot_verifier(
    *,
    openai_service: object,
    crop_data_url: str,
    replot_path: Path,
    crop_width: int,
    crop_height: int,
    series: DigitizedCurve,
    caption: str,
) -> Optional[ReplotVerification]:
    from .openai_service import OpenAIServiceError

    try:
        replot_data_url = encode_image_to_data_url(replot_path)
        return openai_service.verify_replot(
            crop_image_data_url=crop_data_url,
            replot_image_data_url=replot_data_url,
            crop_width=int(crop_width),
            crop_height=int(crop_height),
            series_label=series.series_label,
            processed_peaks_summary=_format_peak_summary(series.peaks),
            caption=caption,
        )
    except OpenAIServiceError:
        return None


def _format_peak_summary(peaks: Sequence[object]) -> str:
    parts: List[str] = []
    for peak in peaks[:8]:
        two_theta = getattr(peak, "two_theta_deg", None)
        intensity = getattr(peak, "intensity_note", None)
        if two_theta is None:
            continue
        parts.append("{deg:.2f}°@{intensity}".format(
            deg=float(two_theta),
            intensity=intensity or "?",
        ))
    return ", ".join(parts) if parts else "none"


def _repair_curve_series(
    series: DigitizedCurve,
    *,
    settings: Settings,
    image_path: Path,
    als_lam_override: float = 1.0e7,
    peak_prominence_scale: float = 1.0,
) -> bool:
    if not series.raw_two_theta or not series.raw_intensity:
        return False
    processed_x, processed_y, baseline, peaks = process_curve(
        series.raw_two_theta,
        series.raw_intensity,
        step_deg=settings.interpolation_step_deg,
        smoothing_window=settings.smoothing_window,
        smoothing_polyorder=settings.smoothing_polyorder,
        als_lam=als_lam_override,
        peak_prominence_scale=peak_prominence_scale,
    )
    series.processed_two_theta = processed_x.astype(float).tolist()
    series.processed_intensity = processed_y.astype(float).tolist()
    series.baseline = baseline.astype(float).tolist()
    series.peaks = list(peaks)
    if series.replot_image_path:
        replot_x, replot_y = _build_absolute_replot_curve(
            series.raw_two_theta,
            series.raw_intensity,
            processed_x,
            settings=settings,
        )
        save_replot(
            replot_x.tolist(),
            replot_y.tolist(),
            Path(series.replot_image_path),
            title="{title} | {label} (retry)".format(
                title=image_path.stem,
                label=series.series_label or "curve",
            ),
            ylabel="Intensity (raw)",
        )
    series.replot_retry_count += 1
    return True


def _draw_curve_polyline(
    draw: object,
    global_x: np.ndarray,
    global_y: np.ndarray,
    *,
    color: Tuple[int, int, int],
) -> None:
    polyline = list(zip(global_x.tolist(), global_y.tolist()))
    if len(polyline) <= 1:
        return

    segments: List[List[Tuple[float, float]]] = [[polyline[0]]]
    for previous_point, current_point in zip(polyline, polyline[1:]):
        if (current_point[0] - previous_point[0]) > 3:
            segments.append([current_point])
            continue
        segments[-1].append(current_point)
    for segment in segments:
        if len(segment) > 1:
            draw.line(segment, fill=color, width=2)
        marker_step = max(1, len(segment) // 24)
        for point_x, point_y in segment[::marker_step]:
            draw.ellipse(
                (point_x - 2, point_y - 2, point_x + 2, point_y + 2),
                fill=color,
                outline=(255, 255, 255),
            )


def _safe_slope_denominator(slope: float) -> float:
    if abs(float(slope)) >= 1e-9:
        return float(slope)
    return 1e-9 if slope >= 0 else -1e-9


def detect_plot_bbox(pixels: np.ndarray) -> Tuple[int, int, int, int]:
    import cv2

    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape

    frame_bbox = _detect_black_frame_bbox(gray)
    if frame_bbox is not None:
        return _snap_bbox_to_frame(gray, frame_bbox)

    # Prefer the inner black chart frame when it exists.
    dark_mask = (gray < 80).astype(np.uint8) * 255
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(dark_mask, connectivity=8)
    border_candidates: List[Tuple[float, Tuple[int, int, int, int]]] = []
    for index in range(1, component_count):
        x, y, w, h, area = stats[index]
        if w < width * 0.45 or h < height * 0.35:
            continue
        fill_ratio = area / max(1.0, float(w * h))
        if fill_ratio < 0.01 or fill_ratio > 0.14:
            continue
        center_x = x + (w / 2.0)
        center_y = y + (h / 2.0)
        center_bias = 1.0 - (
            abs(center_x - (width / 2.0)) / max(1.0, width / 2.0)
            + abs(center_y - (height / 2.0)) / max(1.0, height / 2.0)
        ) * 0.5
        score = (w * h) * (0.6 + center_bias)
        border_candidates.append((score, (x, y, x + w, y + h)))

    if border_candidates:
        border_candidates.sort(key=lambda item: item[0], reverse=True)
        return _snap_bbox_to_frame(gray, border_candidates[0][1])

    edges = cv2.Canny(gray, 50, 150)
    closed = cv2.dilate(edges, np.ones((5, 5), dtype=np.uint8), iterations=1)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_threshold = height * width * 0.08

    candidates: List[Tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < area_threshold:
            continue
        if w < width * 0.35 or h < height * 0.2:
            continue
        if w * h > height * width * 0.95:
            continue
        candidates.append((x, y, x + w, y + h))

    if candidates:
        best_box = max(candidates, key=lambda box: (box[2] - box[0]) * (box[3] - box[1]))
        return _snap_bbox_to_frame(gray, best_box)

    return _snap_bbox_to_frame(gray, (
        int(width * 0.12),
        int(height * 0.08),
        int(width * 0.95),
        int(height * 0.82),
    ))


def _snap_bbox_to_frame(gray: np.ndarray, bbox: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Shrink the bounding box until its edges land on mostly dark pixels (the chart border)."""
    x0, y0, x1, y1 = bbox
    threshold = 120
    density_req = 0.40 
    
    # Shrink top
    for new_y0 in range(y0, min(y1 - 10, y0 + int((y1 - y0) * 0.15))):
        if (gray[new_y0, x0:x1] < threshold).mean() >= density_req:
            y0 = new_y0
            break
            
    # Shrink bottom
    for new_y1 in range(y1, max(y0 + 10, y1 - int((y1 - y0) * 0.15)), -1):
        if (gray[new_y1 - 1, x0:x1] < threshold).mean() >= density_req:
            y1 = new_y1
            break
            
    # Shrink left
    for new_x0 in range(x0, min(x1 - 10, x0 + int((x1 - x0) * 0.15))):
        if (gray[y0:y1, new_x0] < threshold).mean() >= density_req:
            x0 = new_x0
            break
            
    # Shrink right
    for new_x1 in range(x1, max(x0 + 10, x1 - int((x1 - x0) * 0.15)), -1):
        if (gray[y0:y1, new_x1 - 1] < threshold).mean() >= density_req:
            x1 = new_x1
            break
            
    return (x0, y0, x1, y1)


def _detect_black_frame_bbox(gray: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """Detect a rectangular black frame by keeping only long horizontal/vertical strokes."""

    import cv2

    height, width = gray.shape
    if height < 20 or width < 20:
        return None

    dark_mask = (gray < 80).astype(np.uint8) * 255
    horizontal_kernel = np.ones((3, max(24, width // 16)), dtype=np.uint8)
    vertical_kernel = np.ones((max(24, height // 16), 3), dtype=np.uint8)

    horizontal = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, vertical_kernel)
    frame_mask = cv2.bitwise_or(horizontal, vertical)
    if not frame_mask.any():
        return None

    frame_mask = cv2.morphologyEx(frame_mask, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8), iterations=1)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(frame_mask, connectivity=8)

    candidates: List[Tuple[float, Tuple[int, int, int, int]]] = []
    for index in range(1, component_count):
        x, y, w, h, area = stats[index]
        if w < width * 0.4 or h < height * 0.3:
            continue
        if area < max(200, int(width * height * 0.0015)):
            continue
        fill_ratio = area / max(1.0, float(w * h))
        if fill_ratio > 0.22:
            continue

        component = labels == index
        subcomponent = component[y:y + h, x:x + w]
        row_density = subcomponent.mean(axis=1)
        col_density = subcomponent.mean(axis=0)

        row_threshold = max(0.45, float(row_density.max()) * 0.65)
        col_threshold = max(0.45, float(col_density.max()) * 0.65)
        row_indices = np.where(row_density >= row_threshold)[0]
        col_indices = np.where(col_density >= col_threshold)[0]
        if row_indices.size < 2 or col_indices.size < 2:
            continue

        candidate_x0 = x + int(col_indices.min())
        candidate_y0 = y + int(row_indices.min())
        candidate_x1 = x + int(col_indices.max()) + 1
        candidate_y1 = y + int(row_indices.max()) + 1
        candidate_width = candidate_x1 - candidate_x0
        candidate_height = candidate_y1 - candidate_y0
        if candidate_width < width * 0.4 or candidate_height < height * 0.3:
            continue

        row_score = float(row_density[row_indices].mean())
        col_score = float(col_density[col_indices].mean())
        center_x = candidate_x0 + (candidate_width / 2.0)
        center_y = candidate_y0 + (candidate_height / 2.0)
        center_bias = 1.0 - (
            abs(center_x - (width / 2.0)) / max(1.0, width / 2.0)
            + abs(center_y - (height / 2.0)) / max(1.0, height / 2.0)
        ) * 0.5
        score = (
            candidate_width
            * candidate_height
            * (0.5 + center_bias)
            * (0.5 + row_score)
            * (0.5 + col_score)
            * (0.8 + fill_ratio)
        )
        candidates.append((score, (candidate_x0, candidate_y0, candidate_x1, candidate_y1)))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def extract_axis_entries(
    pixels: np.ndarray,
    plot_bbox: Tuple[int, int, int, int],
    *,
    axis: str,
    ocr_backend: OCRBackend,
) -> List[Tuple[float, float]]:
    import cv2

    from .text import parse_numeric_token

    x0, y0, x1, y1 = plot_bbox
    height, width = pixels.shape[:2]
    plot_height = max(1, y1 - y0)
    plot_width = max(1, x1 - x0)

    if axis == "x":
        inside = max(18, int(plot_height * 0.08))
        below = max(30, int(height * 0.12))
        band_y0 = max(0, y1 - inside)
        band_y1 = min(height, y1 + below)
        band = pixels[band_y0:band_y1, x0:x1]
        offset_x, offset_y = x0, band_y0
    else:
        left = max(40, int(width * 0.16))
        inside = max(18, int(plot_width * 0.08))
        band_x0 = max(0, x0 - left)
        band_x1 = min(width, x0 + inside)
        band = pixels[y0:y1, band_x0:band_x1]
        offset_x, offset_y = band_x0, y0

    if band.size == 0:
        return []

    raw_entries: List[Tuple[str, float, float]] = []
    gray = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inverted = 255 - otsu
    variants = [
        (gray, 1.0, 1.0),
        (otsu, 1.0, 1.0),
        (
            cv2.resize(otsu, None, fx=2.2, fy=2.2, interpolation=cv2.INTER_CUBIC),
            2.2,
            2.2,
        ),
        (
            cv2.resize(inverted, None, fx=2.2, fy=2.2, interpolation=cv2.INTER_CUBIC),
            2.2,
            2.2,
        ),
    ]

    for variant, scale_x, scale_y in variants:
        for token in ocr_backend.detect_tokens(variant, min_confidence=0.28):
            left, top, right, bottom = token.bbox
            if axis == "x":
                pixel_position = offset_x + ((left + right) / 2.0) / scale_x
                token_center_y = offset_y + ((top + bottom) / 2.0) / scale_y
            else:
                pixel_position = offset_y + ((top + bottom) / 2.0) / scale_y
                token_center_y = 0.0
            raw_entries.append((token.text, pixel_position, token_center_y))

    entries: List[Tuple[float, float, float]] = []
    for text, pixel_position, token_center_y in raw_entries:
        value = parse_numeric_token(text)
        if value is None:
            continue
        entries.append((pixel_position, value, token_center_y))

    if axis == "x":
        entries = _select_dominant_x_axis_entries(entries)

    deduped: List[Tuple[float, float]] = []
    for pixel_position, value, _ in sorted(entries, key=lambda item: item[0]):
        if deduped and abs(deduped[-1][0] - pixel_position) < 8:
            continue
        deduped.append((pixel_position, value))
    if axis == "x":
        deduped = repair_x_axis_entries(deduped)
    return deduped


def _select_dominant_x_axis_entries(
    entries: Sequence[Tuple[float, float, float]],
) -> List[Tuple[float, float, float]]:
    """Keep the vertically dominant x-axis tick cluster and discard lower text noise."""

    if len(entries) < 3:
        return list(entries)

    ordered = sorted(entries, key=lambda item: item[2])
    clusters: List[List[Tuple[float, float, float]]] = []
    current_cluster: List[Tuple[float, float, float]] = [ordered[0]]
    y_gap_threshold = 24.0

    for entry in ordered[1:]:
        if abs(entry[2] - current_cluster[-1][2]) <= y_gap_threshold:
            current_cluster.append(entry)
            continue
        clusters.append(current_cluster)
        current_cluster = [entry]
    clusters.append(current_cluster)

    viable_clusters = [cluster for cluster in clusters if len(cluster) >= 2]
    if not viable_clusters:
        return list(entries)

    chosen_cluster = max(
        viable_clusters,
        key=lambda cluster: (
            len(cluster),
            -float(np.median([entry[2] for entry in cluster])),
            float(np.median([entry[0] for entry in cluster])),
        ),
    )
    return sorted(chosen_cluster, key=lambda item: item[0])


def fit_axis_calibration(
    entries: Sequence[Tuple[float, float]],
    *,
    source: str,
) -> Optional[AxisCalibration]:
    if len(entries) < 2:
        return None
    pixels = np.asarray([pixel for pixel, _ in entries], dtype=float)
    values = np.asarray([value for _, value in entries], dtype=float)
    if np.unique(values).size < 2:
        return None
    if source.endswith("-x"):
        if not np.all(np.isfinite(values)):
            return None
        if np.min(values) < -5.0 or np.max(values) > _MAX_PXRD_TWO_THETA_SPAN:
            return None
        if np.ptp(values) > _MAX_PXRD_TWO_THETA_SPAN:
            return None

    slope, intercept, inlier_count = _fit_line_ransac(pixels, values)
    if slope is None or intercept is None:
        slope_np, intercept_np = np.polyfit(pixels, values, 1)
        slope, intercept = float(slope_np), float(intercept_np)
        inlier_count = len(entries)

    return AxisCalibration(
        slope=float(slope),
        intercept=float(intercept),
        tick_count=int(inlier_count),
        source=source,
    )


def _fit_line_ransac(
    pixels: np.ndarray,
    values: np.ndarray,
    *,
    iterations: int = 64,
    inlier_tolerance_frac: float = 0.04,
) -> Tuple[Optional[float], Optional[float], int]:
    """Robust 1-D line fit that rejects OCR outliers.

    Uses all C(n,2) pairs when n is small, otherwise a random subset. Returns
    the candidate line with the most inliers, with residuals re-fit via
    ordinary least squares on the inlier subset.
    """

    n = len(pixels)
    if n < 2:
        return None, None, 0
    if n == 2:
        return _ols_line(pixels, values) + (2,)

    value_range = float(np.ptp(values))
    if value_range <= 0:
        return _ols_line(pixels, values) + (n,)
    tolerance = max(0.5, value_range * inlier_tolerance_frac)

    rng = np.random.default_rng(0)
    pair_indices: List[Tuple[int, int]] = []
    if n <= 12:
        for i in range(n):
            for j in range(i + 1, n):
                pair_indices.append((i, j))
    else:
        seen: set = set()
        while len(pair_indices) < iterations:
            i, j = rng.integers(0, n, size=2)
            if i == j:
                continue
            key = (int(min(i, j)), int(max(i, j)))
            if key in seen:
                continue
            seen.add(key)
            pair_indices.append(key)

    best_inliers: Optional[np.ndarray] = None
    for i, j in pair_indices:
        dx = pixels[j] - pixels[i]
        if abs(dx) < 1e-6:
            continue
        slope = (values[j] - values[i]) / dx
        intercept = values[i] - slope * pixels[i]
        residuals = np.abs((slope * pixels + intercept) - values)
        inliers = residuals <= tolerance
        if best_inliers is None or inliers.sum() > best_inliers.sum():
            best_inliers = inliers
            if inliers.sum() == n:
                break

    if best_inliers is None or best_inliers.sum() < 2:
        slope, intercept = _ols_line(pixels, values)
        return slope, intercept, n

    in_pixels = pixels[best_inliers]
    in_values = values[best_inliers]
    slope, intercept = _ols_line(in_pixels, in_values)
    return slope, intercept, int(best_inliers.sum())


def _ols_line(pixels: np.ndarray, values: np.ndarray) -> Tuple[float, float]:
    slope, intercept = np.polyfit(pixels, values, 1)
    return float(slope), float(intercept)


def _is_plausible_two_theta_calibration(
    calibration: AxisCalibration,
    *,
    plot_width: int,
) -> bool:
    """Reject OCR fits that imply an impossible PXRD 2θ span.

    RapidOCR can hallucinate long digit runs for tick labels. If those values
    are accepted as-is, the x-axis calibration can expand a normal crop into a
    multi-hundred-thousand-degree range and blow up interpolation memory.
    """

    if not np.isfinite(calibration.slope) or not np.isfinite(calibration.intercept):
        return False
    if calibration.slope <= 0.0:
        return False

    implied_span = calibration.slope * max(1, plot_width)
    return implied_span <= _MAX_PXRD_TWO_THETA_SPAN


def estimate_series_count(plot_pixels: np.ndarray) -> Tuple[bool, int]:
    import cv2

    hsv = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
    non_white = np.any(plot_pixels < 245, axis=2)
    colored = non_white & (hsv[:, :, 1] > 40)
    if colored.any():
        hue_bins = np.bincount(((hsv[:, :, 0] // 15)[colored]).astype(int), minlength=12)
        active = int((hue_bins > max(20, colored.sum() * 0.05)).sum())
        return (active > 1, max(1, active))

    binary = build_curve_mask(plot_pixels)
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(binary)
    wide_components = 0
    for index in range(1, component_count):
        x, y, width, height, area = stats[index]
        if area < 40:
            continue
        if width > binary.shape[1] * 0.45:
            wide_components += 1
    estimate = max(1, wide_components)
    return (wide_components > 1, estimate)


def build_curve_mask(plot_pixels: np.ndarray, *, text_mask: Optional[np.ndarray] = None) -> np.ndarray:
    import cv2

    gray = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        11,
    )

    # Color-first augmentation: when the plot contains significant saturated
    # pixels, the grayscale adaptive threshold can miss pale/thin colored
    # traces (e.g. light cyan, yellow). OR-in a saturation gate so those
    # pixels still make it into the curve mask.
    hsv = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    saturated_fraction = float((saturation >= 30).sum()) / max(1, saturation.size)
    if saturated_fraction >= 0.05:
        color_mask = ((saturation >= 30) & (value <= 245)).astype(np.uint8) * 255
        binary = cv2.bitwise_or(binary, color_mask)

    curve = binary.copy()
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((25, 1), dtype=np.uint8))
    vertical = cv2.subtract(vertical, _saturated_color_mask(plot_pixels))
    
    # Only remove vertical lines near the left and right edges to avoid deleting sharp PXRD peaks
    border_zone_x = max(10, int(curve.shape[1] * 0.05))
    vertical_mask = np.zeros_like(curve)
    vertical_mask[:, :border_zone_x] = vertical[:, :border_zone_x]
    vertical_mask[:, -border_zone_x:] = vertical[:, -border_zone_x:]
    curve = cv2.subtract(curve, vertical_mask)
    if text_mask is not None and text_mask.shape == curve.shape:
        curve = cv2.subtract(curve, text_mask)
    curve = cv2.subtract(curve, detect_frame_like_components(binary))

    horizontal_line = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((1, max(40, curve.shape[1] // 8)), dtype=np.uint8))
    horizontal_line = cv2.dilate(horizontal_line, np.ones((7, 1), dtype=np.uint8), iterations=1)
    horizontal_mask = np.zeros_like(curve)
    border_zone_y = int(curve.shape[0] * 0.12)
    horizontal_mask[:border_zone_y, :] = horizontal_line[:border_zone_y, :]
    horizontal_mask[-border_zone_y:, :] = horizontal_line[-border_zone_y:, :]
    curve = cv2.subtract(curve, horizontal_mask)

    edge_margin_rows = max(3, curve.shape[0] // 80)
    curve[:edge_margin_rows, :] = 0
    curve[-edge_margin_rows:, :] = 0
    
    # Remove small isolated noise dots without eroding thin 1-pixel wide sharp peaks
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(curve, connectivity=8)
    mask_to_keep = stats[:, cv2.CC_STAT_AREA] >= 15
    mask_to_keep[0] = False
    curve = mask_to_keep[labels].astype(np.uint8) * 255

    # Mask out inset figures (embedded molecular graphics) before curve tracing
    inset_mask = _detect_inset_figure_mask(plot_pixels, curve)
    if inset_mask is not None and inset_mask.any():
        curve = cv2.subtract(curve, inset_mask)

    curve = cv2.morphologyEx(curve, cv2.MORPH_CLOSE, np.ones((7, 3), dtype=np.uint8))
    return curve


def _detect_inset_figure_mask(plot_pixels: np.ndarray, curve_mask: np.ndarray) -> Optional[np.ndarray]:
    """Detect embedded inset graphics (e.g. molecular structures) inside the plot area.

    These appear as large rectangular regions filled with complex colour patterns —
    completely unlike a simple 1-D diffraction curve.  We find candidate rectangles
    whose interiors have high colour diversity and a high proportion of saturated
    pixels, then return a dilated mask so the tracer never follows their borders.
    """
    import cv2

    height, width = plot_pixels.shape[:2]
    if height < 30 or width < 30:
        return None

    hsv = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2GRAY)

    # Find all contours in the curve mask (borders show up as connected blobs)
    contours, _ = cv2.findContours(curve_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    inset_mask = np.zeros((height, width), dtype=np.uint8)
    min_inset_fraction = 0.06  # inset must be at least 6% of the plot area in each dimension
    min_h = int(height * min_inset_fraction)
    min_w = int(width * min_inset_fraction)

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < min_w or h < min_h:
            continue
        # Must be a reasonably square-ish / compact block (not a thin horizontal curve)
        aspect = w / max(1, h)
        if aspect > 5.0 or aspect < 0.2:
            continue
        # Ignore very large regions that span most of the plot (those ARE the axis box)
        if w > width * 0.85 or h > height * 0.85:
            continue

        # Sample the interior of the bounding rect from the original pixels
        pad = 2
        roi_y0, roi_y1 = max(0, y + pad), min(height, y + h - pad)
        roi_x0, roi_x1 = max(0, x + pad), min(width, x + w - pad)
        if roi_y1 <= roi_y0 or roi_x1 <= roi_x0:
            continue

        roi_hsv = hsv[roi_y0:roi_y1, roi_x0:roi_x1]
        roi_gray = gray[roi_y0:roi_y1, roi_x0:roi_x1]
        roi_pixels = (roi_y1 - roi_y0) * (roi_x1 - roi_x0)

        # Heuristic 1: high colour saturation fraction (molecular ball-and-stick models are vivid)
        saturated_fraction = float((roi_hsv[:, :, 1] > 60).sum()) / max(1, roi_pixels)

        # Heuristic 2: many distinct hues → complex image, not a monochrome curve
        hue_values = roi_hsv[:, :, 0][roi_hsv[:, :, 1] > 60]
        unique_hues = len(np.unique((hue_values // 10).astype(np.uint8))) if hue_values.size > 0 else 0

        # Heuristic 3: local gradient variance (insets have textured interiors)
        grad_x = cv2.Sobel(roi_gray, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(roi_gray, cv2.CV_32F, 0, 1, ksize=3)
        gradient_std = float(np.std(np.sqrt(grad_x ** 2 + grad_y ** 2)))

        is_inset = (
            saturated_fraction > 0.25
            and unique_hues >= 3
            and gradient_std > 15.0
        )
        if is_inset:
            # Dilate slightly so the border of the inset is also removed
            cv2.rectangle(inset_mask, (x, y), (x + w, y + h), 255, -1)

    if not inset_mask.any():
        return None

    # Shrink the mask a little at the edges so we don't accidentally eat diffraction
    # peaks that happen to sit near an inset.
    kernel = np.ones((5, 5), dtype=np.uint8)
    inset_mask = cv2.erode(inset_mask, kernel, iterations=2)
    inset_mask = cv2.dilate(inset_mask, np.ones((9, 9), dtype=np.uint8), iterations=1)
    return inset_mask


def detect_text_mask(plot_pixels: np.ndarray, ocr_backend: OCRBackend) -> Tuple[np.ndarray, List[Tuple[int, int, int, int]]]:
    return _detect_ocr_token_mask(plot_pixels, ocr_backend, alnum_only=True)


def _detect_ocr_token_mask(
    plot_pixels: np.ndarray,
    ocr_backend: OCRBackend,
    *,
    alnum_only: bool,
) -> Tuple[np.ndarray, List[Tuple[int, int, int, int]]]:
    import cv2

    gray = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2GRAY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    enlarged_variants = [
        (cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC), 2.0, 2.0),
        (cv2.resize(otsu, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC), 2.0, 2.0),
        (cv2.resize(255 - otsu, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC), 2.0, 2.0),
    ]

    height, width = gray.shape
    mask = np.zeros((height, width), dtype=np.uint8)
    boxes: List[Tuple[int, int, int, int]] = []

    for enlarged, scale_x, scale_y in enlarged_variants:
        for token in ocr_backend.detect_tokens(enlarged, min_confidence=0.35):
            cleaned = (token.text or "").strip()
            if not cleaned:
                continue
            if alnum_only:
                if not any(character.isalnum() for character in cleaned):
                    continue
            elif not any(character.isalpha() for character in cleaned):
                continue

            left, top, right, bottom = token.bbox
            x0 = int(max(0, left / scale_x - 4))
            y0 = int(max(0, top / scale_y - 3))
            x1 = int(min(width, right / scale_x + 4))
            y1 = int(min(height, bottom / scale_y + 3))
            if (x1 - x0) > width * 0.85 or (y1 - y0) > height * 0.18:
                continue
            if boxes and any(
                abs(existing[0] - x0) <= 6
                and abs(existing[1] - y0) <= 6
                and abs(existing[2] - x1) <= 6
                and abs(existing[3] - y1) <= 6
                for existing in boxes
            ):
                continue
            mask[y0:y1, x0:x1] = 255
            boxes.append((x0, y0, x1, y1))

    if boxes:
        mask = cv2.dilate(mask, np.ones((5, 9), dtype=np.uint8), iterations=1)
    return mask, boxes


def detect_non_curve_boxes(
    plot_pixels: np.ndarray,
    *,
    curve_mask: np.ndarray,
    primary_curve_mask: np.ndarray,
    curve_columns: np.ndarray,
    curve_rows: np.ndarray,
    ocr_backend: OCRBackend,
    ai_regions: Optional[Sequence[ArtifactRegion]] = None,
) -> Tuple[List[Tuple[int, int, int, int]], np.ndarray]:
    import cv2

    if plot_pixels.size == 0:
        return [], np.zeros((0, 0), dtype=np.uint8)

    foreground_mask = _build_plot_foreground_mask(plot_pixels)
    frame_mask = detect_frame_like_components(foreground_mask)
    token_mask, token_boxes = _detect_ocr_token_mask(plot_pixels, ocr_backend, alnum_only=True)
    curve_support_mask = _build_curve_support_mask(
        curve_mask,
        primary_curve_mask,
        curve_columns,
        curve_rows,
    )

    artifact_mask = cv2.bitwise_or(foreground_mask, frame_mask)
    artifact_mask = cv2.bitwise_or(artifact_mask, token_mask)
    artifact_mask = cv2.subtract(
        artifact_mask,
        cv2.dilate(curve_support_mask, np.ones((5, 5), dtype=np.uint8), iterations=1),
    )
    artifact_mask = cv2.morphologyEx(
        artifact_mask,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
        iterations=1,
    )
    if ai_regions:
        ai_prior_mask = _build_ai_artifact_mask(
            ai_regions,
            semantic_mask=cv2.bitwise_or(cv2.bitwise_or(foreground_mask, frame_mask), token_mask),
            curve_support_mask=curve_support_mask,
            bounds=artifact_mask.shape,
        )
        artifact_mask = cv2.bitwise_or(artifact_mask, ai_prior_mask)
        artifact_mask = cv2.morphologyEx(
            artifact_mask,
            cv2.MORPH_CLOSE,
            np.ones((3, 3), dtype=np.uint8),
            iterations=1,
        )

    component_boxes = _component_boxes_from_mask(artifact_mask)
    merged_boxes = _merge_boxes(
        [
            *component_boxes,
            *token_boxes,
            *[tuple(map(int, region.bbox)) for region in ai_regions or []],
        ],
        gap_x=max(8, plot_pixels.shape[1] // 70),
        gap_y=max(6, plot_pixels.shape[0] // 70),
        bounds=artifact_mask.shape,
    )
    return merged_boxes, artifact_mask


def _should_request_ai_non_curve_assistance(
    settings: Settings,
    *,
    x_axis: Optional[AxisCalibration],
    overlay_similarity: float,
    flagged_reasons: Sequence[str],
    multi_series_detected: bool,
    artifact_mask: np.ndarray,
    openai_service: object | None,
) -> bool:
    if openai_service is None or not settings.ai_non_curve_assist_enabled:
        return False

    artifact_fraction = 0.0
    if artifact_mask.size:
        artifact_fraction = float(np.count_nonzero(artifact_mask)) / float(artifact_mask.size)

    if x_axis is None:
        return True
    if multi_series_detected:
        return True
    if overlay_similarity < settings.ai_non_curve_assist_overlay_threshold:
        return True
    if artifact_fraction > settings.ai_non_curve_assist_mask_fraction_threshold:
        return True
    return any(
        "Weak curve coverage" in reason or "Suspicious 2theta span" in reason
        for reason in flagged_reasons
    )


def _request_ai_non_curve_regions(
    plot_pixels: np.ndarray,
    *,
    openai_service: object | None,
    overlay_similarity: float,
    flagged_reasons: Sequence[str],
    existing_box_count: int,
    artifact_mask: np.ndarray,
) -> List[ArtifactRegion]:
    from .openai_service import OpenAIServiceError

    if openai_service is None or plot_pixels.size == 0:
        return []

    artifact_fraction = 0.0
    if artifact_mask.size:
        artifact_fraction = float(np.count_nonzero(artifact_mask)) / float(artifact_mask.size)

    try:
        assistance = openai_service.assist_non_curve_regions(
            image_data_url=encode_image_object_to_data_url(plot_pixels),
            image_width=int(plot_pixels.shape[1]),
            image_height=int(plot_pixels.shape[0]),
            overlay_similarity=float(overlay_similarity),
            flagged_reasons=list(flagged_reasons),
            existing_box_count=int(existing_box_count),
            artifact_fraction=float(artifact_fraction),
        )
    except OpenAIServiceError:
        return []

    width = int(plot_pixels.shape[1])
    height = int(plot_pixels.shape[0])
    normalized: List[ArtifactRegion] = []
    for region in assistance.regions:
        x0, y0, x1, y1 = map(int, region.bbox)
        x0 = max(0, min(width, x0))
        y0 = max(0, min(height, y0))
        x1 = max(0, min(width, x1))
        y1 = max(0, min(height, y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        normalized.append(region.model_copy(update={"bbox": [x0, y0, x1, y1]}))
    return normalized


def _build_ai_artifact_mask(
    ai_regions: Sequence[ArtifactRegion],
    *,
    semantic_mask: np.ndarray,
    curve_support_mask: np.ndarray,
    bounds: Tuple[int, int],
) -> np.ndarray:
    import cv2

    height, width = bounds
    if not ai_regions or height <= 0 or width <= 0:
        return np.zeros((height, width), dtype=np.uint8)

    curve_exclusion = cv2.dilate(curve_support_mask, np.ones((5, 5), dtype=np.uint8), iterations=1)
    ai_mask = np.zeros((height, width), dtype=np.uint8)

    for region in ai_regions:
        x0, y0, x1, y1 = map(int, region.bbox)
        x0 = max(0, min(width, x0))
        y0 = max(0, min(height, y0))
        x1 = max(0, min(width, x1))
        y1 = max(0, min(height, y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue

        seeded_roi = cv2.subtract(semantic_mask[y0:y1, x0:x1], curve_exclusion[y0:y1, x0:x1])
        if np.count_nonzero(seeded_roi) >= max(12, int((x1 - x0) * (y1 - y0) * 0.02)):
            ai_mask[y0:y1, x0:x1] = cv2.bitwise_or(ai_mask[y0:y1, x0:x1], seeded_roi)
            continue

        if region.confidence < 0.55:
            continue
        ai_mask[y0:y1, x0:x1] = 255

    ai_mask = cv2.subtract(ai_mask, curve_exclusion)
    ai_mask = cv2.morphologyEx(
        ai_mask,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
        iterations=1,
    )
    return ai_mask


def _build_plot_foreground_mask(plot_pixels: np.ndarray) -> np.ndarray:
    import cv2

    gray = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2GRAY)
    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        11,
    )
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    edges = cv2.Canny(gray, 40, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8), iterations=1)
    dark = (gray < 232).astype(np.uint8) * 255

    foreground = cv2.bitwise_or(adaptive, otsu)
    foreground = cv2.bitwise_or(foreground, edges)
    foreground = cv2.bitwise_and(foreground, dark)
    foreground = cv2.morphologyEx(
        foreground,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
        iterations=1,
    )
    return foreground


def _build_curve_support_mask(
    curve_mask: np.ndarray,
    primary_curve_mask: np.ndarray,
    curve_columns: np.ndarray,
    curve_rows: np.ndarray,
) -> np.ndarray:
    import cv2

    if curve_mask.size == 0:
        return curve_mask

    height, width = curve_mask.shape
    seed_mask = np.zeros_like(curve_mask)

    if curve_columns.size and curve_rows.size:
        points = np.column_stack([curve_columns.astype(int), curve_rows.astype(int)]).reshape(-1, 1, 2)
        if len(points) > 1:
            thickness = max(5, int(round(max(5.0, height * 0.025))))
            cv2.polylines(seed_mask, [points], isClosed=False, color=255, thickness=thickness)

    seed_mask = cv2.bitwise_or(
        seed_mask,
        cv2.dilate(primary_curve_mask, np.ones((9, 9), dtype=np.uint8), iterations=1),
    )
    seed_mask = cv2.dilate(seed_mask, np.ones((5, 5), dtype=np.uint8), iterations=1)

    component_count, labels, _, _ = cv2.connectedComponentsWithStats(curve_mask, connectivity=8)
    support_mask = np.zeros_like(curve_mask)
    for index in range(1, component_count):
        component_pixels = labels == index
        if not np.any(component_pixels & (seed_mask > 0)):
            continue
        support_mask[component_pixels] = 255

    support_mask = cv2.dilate(support_mask, np.ones((5, 5), dtype=np.uint8), iterations=1)
    return support_mask


def _component_boxes_from_mask(mask: np.ndarray) -> List[Tuple[int, int, int, int]]:
    import cv2

    if mask.size == 0:
        return []

    height, width = mask.shape
    min_area = max(10, int(round(height * width * 0.00008)))
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes: List[Tuple[int, int, int, int]] = []

    for index in range(1, component_count):
        x, y, component_width, component_height, area = stats[index]
        if component_width <= 1 or component_height <= 1:
            continue

        touches_border = (
            x <= max(3, int(width * 0.05))
            or y <= max(3, int(height * 0.05))
            or (x + component_width) >= width - max(3, int(width * 0.05))
            or (y + component_height) >= height - max(3, int(height * 0.05))
        )
        fill_ratio = area / max(1.0, float(component_width * component_height))

        if area < min_area:
            if not touches_border:
                continue
            if area < max(4, min_area // 2) and max(component_width, component_height) < 8:
                continue

        if fill_ratio < 0.02 and not touches_border:
            continue

        boxes.append((x, y, x + component_width, y + component_height))
    return boxes


def _merge_boxes(
    boxes: Sequence[Tuple[int, int, int, int]],
    *,
    gap_x: int,
    gap_y: int,
    bounds: Tuple[int, int],
) -> List[Tuple[int, int, int, int]]:
    if not boxes:
        return []

    height, width = bounds
    pending = [tuple(map(int, box)) for box in boxes]
    merged = True
    while merged:
        merged = False
        next_pending: List[Tuple[int, int, int, int]] = []
        while pending:
            current = pending.pop()
            index = 0
            while index < len(pending):
                other = pending[index]
                if not _boxes_are_close(current, other, gap_x=gap_x, gap_y=gap_y):
                    index += 1
                    continue
                current = (
                    min(current[0], other[0]),
                    min(current[1], other[1]),
                    max(current[2], other[2]),
                    max(current[3], other[3]),
                )
                pending.pop(index)
                merged = True
            next_pending.append(current)
        pending = next_pending

    deduped: List[Tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in sorted(pending, key=lambda item: (item[1], item[0], item[2], item[3])):
        padded = (
            max(0, x0 - 2),
            max(0, y0 - 2),
            min(width, x1 + 2),
            min(height, y1 + 2),
        )
        if deduped and any(
            abs(existing[0] - padded[0]) <= 2
            and abs(existing[1] - padded[1]) <= 2
            and abs(existing[2] - padded[2]) <= 2
            and abs(existing[3] - padded[3]) <= 2
            for existing in deduped
        ):
            continue
        deduped.append(padded)
    return deduped


def _boxes_are_close(
    box_a: Tuple[int, int, int, int],
    box_b: Tuple[int, int, int, int],
    *,
    gap_x: int,
    gap_y: int,
) -> bool:
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    return not (
        ax1 + gap_x < bx0
        or bx1 + gap_x < ax0
        or ay1 + gap_y < by0
        or by1 + gap_y < ay0
    )


def _extract_artifact_contours(mask: np.ndarray) -> List[List[Tuple[int, int]]]:
    import cv2

    if mask.size == 0:
        return []

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons: List[List[Tuple[int, int]]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 8.0:
            continue
        epsilon = max(1.5, 0.01 * cv2.arcLength(contour, True))
        approx = cv2.approxPolyDP(contour, epsilon, True)
        polygon = [(int(point[0][0]), int(point[0][1])) for point in approx]
        if polygon:
            polygons.append(polygon)
    polygons.sort(key=lambda item: min(point[1] for point in item))
    return polygons


def detect_frame_like_components(binary_mask: np.ndarray) -> np.ndarray:
    import cv2

    height, width = binary_mask.shape
    frame_mask = np.zeros_like(binary_mask)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)
    line_thickness = max(8, min(height, width) // 25)

    for index in range(1, component_count):
        x, y, component_width, component_height, area = stats[index]
        fill_ratio = area / max(1.0, float(component_width * component_height))
        horizontal_border = (
            component_width >= width * 0.7
            and component_height <= line_thickness
            and (y <= height * 0.2 or (y + component_height) >= height * 0.8)
            and fill_ratio >= 0.45
        )
        vertical_border = (
            component_height >= height * 0.7
            and component_width <= line_thickness
            and (x <= width * 0.12 or (x + component_width) >= width * 0.88)
            and fill_ratio >= 0.45
        )
        if not horizontal_border and not vertical_border:
            continue
        if area < max(width, height) * 0.25:
            continue
        frame_mask[labels == index] = 255

    return frame_mask


def repair_x_axis_entries(entries: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if len(entries) < 2:
        return list(entries)

    repaired: List[Tuple[float, float]] = []
    fallback_step = infer_typical_step([value for _, value in entries])

    for pixel_position, value in sorted(entries, key=lambda item: item[0]):
        repaired_value = float(value)
        if repaired:
            previous_pixel, previous_value = repaired[-1]
            expected_value = None
            step_guess = fallback_step
            if len(repaired) >= 2:
                prior_pixel, prior_value = repaired[-2]
                pixel_delta = previous_pixel - prior_pixel
                value_delta = previous_value - prior_value
                if pixel_delta > 1 and value_delta > 0:
                    step_guess = value_delta
                    slope = value_delta / pixel_delta
                    expected_value = previous_value + slope * (pixel_position - previous_pixel)

            keep_observed = repaired_value > previous_value
            if keep_observed and expected_value is not None:
                tolerance = max(5.0, abs(step_guess or 0.0) * 0.35)
                keep_observed = abs(repaired_value - expected_value) <= tolerance

            if not keep_observed:
                repaired_value = choose_increasing_tick_value(
                    repaired_value,
                    previous_value=previous_value,
                    expected_value=expected_value,
                    step_guess=step_guess,
                )
                if step_guess is not None and step_guess > 0:
                    snapped = previous_value + step_guess
                    if abs(repaired_value - snapped) <= max(1.5, step_guess * 0.15):
                        repaired_value = snapped
        repaired.append((float(pixel_position), float(repaired_value)))

    strictly_increasing: List[Tuple[float, float]] = []
    for pixel_position, value in repaired:
        if strictly_increasing and value <= strictly_increasing[-1][1]:
            continue
        strictly_increasing.append((pixel_position, value))
    return strictly_increasing


def infer_typical_step(values: Sequence[float]) -> Optional[float]:
    positive_deltas = [curr - prev for prev, curr in zip(values, values[1:]) if curr > prev]
    if not positive_deltas:
        return None
    rounded = [round(delta, 4) for delta in positive_deltas]
    return float(max(rounded))


def choose_increasing_tick_value(
    value: float,
    *,
    previous_value: float,
    expected_value: Optional[float],
    step_guess: Optional[float],
) -> float:
    observed_value = float(value)
    candidates = {observed_value}
    if 0 < value < 10:
        candidates.update({value * 10.0, value * 100.0})
    candidates.update({value + 10.0, value + 20.0, value + 40.0, value + 60.0, value + 80.0})
    if step_guess is not None and step_guess > 0:
        candidates.update(
            {
                previous_value + step_guess,
                previous_value + 2.0 * step_guess,
                previous_value + 3.0 * step_guess,
            }
        )
    if expected_value is not None:
        candidates.update(
            {
                round(expected_value),
                round(expected_value / 2.0) * 2.0,
                round(expected_value / 5.0) * 5.0,
                round(expected_value / 10.0) * 10.0,
            }
        )

    increasing = [candidate for candidate in candidates if candidate > previous_value]
    if not increasing:
        return observed_value
    if expected_value is None and step_guess is None:
        return min(increasing)
    best_candidate = min(
        increasing,
        key=lambda candidate: (
            0.0 if expected_value is None else abs(candidate - expected_value),
            0.0 if step_guess is None else abs((candidate - previous_value) - step_guess),
            abs(candidate - observed_value) * 0.15,
            candidate,
        ),
    )
    return float(best_candidate)


def _iter_row_runs(active_rows: np.ndarray, *, gap_tolerance: int = 2) -> List[np.ndarray]:
    if active_rows.size == 0:
        return []

    runs: List[np.ndarray] = []
    start_index = 0
    for index, gap in enumerate(np.diff(active_rows), start=1):
        if gap <= gap_tolerance:
            continue
        runs.append(active_rows[start_index:index])
        start_index = index
    runs.append(active_rows[start_index:])
    return [run for run in runs if run.size > 0]


def _select_top_row(active_rows: np.ndarray) -> float:
    """Return a robust y-coordinate of the topmost (highest-intensity) edge.

    PXRD peaks are tall filled bodies from baseline to peak apex.  The plain
    median sits in the middle of that body and underestimates peak heights.
    We instead locate the topmost contiguous run of active pixels (smallest
    row index = highest position in image = highest intensity) and return the
    mean of its top 20 %, which is robust to isolated noise pixels at the very
    top while still tracking the true peak apex rather than the body centre.
    """
    if active_rows.size == 0:
        return 0.0
    row_runs = _iter_row_runs(active_rows, gap_tolerance=2)
    valid_runs = [r for r in row_runs if r.size >= 2]
    if not valid_runs:
        return float(active_rows[0])
    topmost_run = min(valid_runs, key=lambda r: int(r[0]))
    top_n = max(1, topmost_run.size // 5)
    return float(np.mean(topmost_run[:top_n]))


def _track_primary_curve_mask(
    cleaned_mask: np.ndarray,
    *,
    hsv_pixels: Optional[np.ndarray],
    target_hue: Optional[float],
    bootstrap: bool,
) -> np.ndarray:
    """Globally-optimal column-wise curve tracker via Viterbi DP.

    For each column we enumerate row-runs as candidate states and score them
    with an emission term (color, darkness, top-bias, thickness). Neighboring
    columns are linked with an asymmetric smoothness term (ascending jumps
    are cheaper than descending ones to accommodate sharp PXRD peaks). A
    forward pass computes the best cumulative score; backtracking recovers
    the row-run selected in each column.
    """

    height, width = cleaned_mask.shape
    selected_mask = np.zeros_like(cleaned_mask)
    if height == 0 or width == 0:
        return selected_mask

    max_jump = max(12.0, height * 0.08)
    top_bias_weight = 0.75 if bootstrap else 0.35

    column_runs: List[List[np.ndarray]] = []
    column_reps: List[np.ndarray] = []
    column_emissions: List[np.ndarray] = []

    for column in range(width):
        active_rows = np.where(cleaned_mask[:, column] > 0)[0]
        if active_rows.size == 0:
            column_runs.append([])
            column_reps.append(np.asarray([], dtype=float))
            column_emissions.append(np.asarray([], dtype=float))
            continue

        row_runs = _iter_row_runs(active_rows, gap_tolerance=2)
        reps = np.empty(len(row_runs), dtype=float)
        emissions = np.empty(len(row_runs), dtype=float)

        for run_index, row_run in enumerate(row_runs):
            representative_row = _select_top_row(row_run)
            thickness_bonus = min(0.35, row_run.size / 12.0)
            top_bias = -top_bias_weight * (representative_row / max(1.0, height))

            color_score = 0.0
            if not bootstrap and hsv_pixels is not None:
                color_score = score_curve_row(
                    hsv_pixels,
                    column=column,
                    row=int(round(representative_row)),
                    target_hue=target_hue,
                    previous_row=None,
                    max_jump=max_jump,
                )

            reps[run_index] = representative_row
            emissions[run_index] = color_score + thickness_bonus + top_bias

        column_runs.append(row_runs)
        column_reps.append(reps)
        column_emissions.append(emissions)

    best_score = np.full(width, -np.inf, dtype=float)
    best_run_index = np.full(width, -1, dtype=np.int32)
    back_pointer_column = np.full(width, -1, dtype=np.int32)
    back_pointer_run = np.full(width, -1, dtype=np.int32)

    for column in range(width):
        reps = column_reps[column]
        if reps.size == 0:
            continue
        emissions = column_emissions[column]

        for run_index in range(reps.size):
            representative_row = reps[run_index]
            local_emission = float(emissions[run_index])

            # No-predecessor start hypothesis
            start_score = local_emission
            if not bootstrap and hsv_pixels is not None and start_score < 0.45:
                start_score = -np.inf
            candidate_score = start_score
            candidate_prev_col = -1
            candidate_prev_run = -1

            # Look back over a bounded window for predecessors. Most real
            # curves are continuous, so 24 columns is ample and keeps the
            # DP near-linear in width.
            look_back_limit = max(1, min(column, 24))
            for prev_column in range(column - 1, column - 1 - look_back_limit, -1):
                prev_reps = column_reps[prev_column]
                if prev_reps.size == 0:
                    continue
                prev_scores = best_score[prev_column]
                if not np.isfinite(prev_scores).any():
                    continue

                vertical_deltas = np.abs(prev_reps - representative_row)
                ascending = prev_reps > representative_row
                effective_max = np.where(ascending, max_jump * 3.0, max_jump)
                continuity_penalty = vertical_deltas / np.maximum(1.0, effective_max)
                hard_break = vertical_deltas > effective_max * 1.25
                continuity_penalty = continuity_penalty + np.where(hard_break, 0.5, 0.0)

                jump_limit = np.where(ascending, max_jump * 3.5, max_jump * 1.4)
                feasible = vertical_deltas <= jump_limit

                gap_width = max(0, column - prev_column - 1)
                gap_penalty = min(0.4, gap_width / 20.0)

                transition_scores = (
                    prev_scores
                    + local_emission
                    - continuity_penalty
                    - gap_penalty
                )
                transition_scores = np.where(feasible, transition_scores, -np.inf)
                if not np.isfinite(transition_scores).any():
                    # If the most recent column has no feasible predecessor,
                    # earlier columns will almost certainly be too far.
                    if prev_column == column - 1:
                        continue
                    else:
                        break

                best_prev_run = int(np.argmax(transition_scores))
                best_prev_score = float(transition_scores[best_prev_run])
                if best_prev_score > candidate_score:
                    candidate_score = best_prev_score
                    candidate_prev_col = prev_column
                    candidate_prev_run = best_prev_run

                # Continuous neighbor found; earlier columns are implicit via
                # best_score transitivity, so we can stop.
                if candidate_prev_col == column - 1:
                    break

            if candidate_score > best_score[column]:
                best_score[column] = candidate_score
                best_run_index[column] = run_index
                back_pointer_column[column] = candidate_prev_col
                back_pointer_run[column] = candidate_prev_run

    finite_columns = np.where(np.isfinite(best_score))[0]
    if finite_columns.size == 0:
        return selected_mask

    terminal_column = int(finite_columns[np.argmax(best_score[finite_columns])])
    current_column = terminal_column
    current_run = int(best_run_index[current_column])
    while current_column >= 0 and current_run >= 0:
        run = column_runs[current_column][current_run]
        selected_mask[run, current_column] = 255
        prev_col = int(back_pointer_column[current_column])
        prev_run = int(back_pointer_run[current_column])
        if prev_col < 0 or prev_run < 0:
            break
        current_column = prev_col
        current_run = prev_run

    if selected_mask.any():
        import cv2

        selected_mask = cv2.morphologyEx(selected_mask, cv2.MORPH_CLOSE, np.ones((9, 1), dtype=np.uint8))
        selected_mask = cv2.dilate(selected_mask, np.ones((3, 1), dtype=np.uint8), iterations=1)
    return selected_mask


def select_primary_curve_mask(
    curve_mask: np.ndarray,
    *,
    plot_pixels: Optional[np.ndarray] = None,
) -> np.ndarray:
    import cv2

    if curve_mask.size == 0:
        return curve_mask

    vertical = cv2.morphologyEx(curve_mask, cv2.MORPH_OPEN, np.ones((12, 2), dtype=np.uint8))
    if plot_pixels is not None and plot_pixels.shape[:2] == curve_mask.shape:
        vertical = cv2.subtract(vertical, _saturated_color_mask(plot_pixels))
    cleaned_mask = cv2.subtract(curve_mask, vertical)
    cleaned_mask = cv2.subtract(cleaned_mask, detect_frame_like_components(cleaned_mask))
    if not cleaned_mask.any():
        return curve_mask

    hsv_pixels: Optional[np.ndarray] = None
    if plot_pixels is not None and plot_pixels.shape[:2] == cleaned_mask.shape:
        hsv_pixels = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)

    bootstrap_mask = _track_primary_curve_mask(
        cleaned_mask,
        hsv_pixels=None,
        target_hue=None,
        bootstrap=True,
    )
    if not bootstrap_mask.any():
        return cleaned_mask

    bootstrap_columns, bootstrap_rows = extract_curve_trace(bootstrap_mask, plot_pixels=plot_pixels)
    bootstrap_score = _curve_anchor_score(
        bootstrap_columns,
        bootstrap_rows,
        width=bootstrap_mask.shape[1],
        height=bootstrap_mask.shape[0],
    )

    target_hue = None
    if plot_pixels is not None:
        target_hue = estimate_target_hue(plot_pixels, bootstrap_mask)

    locked_mask = _lock_curve_components(
        cleaned_mask,
        bootstrap_mask,
        hsv_pixels=hsv_pixels,
        target_hue=target_hue,
    )

    refined_mask = _track_primary_curve_mask(
        locked_mask,
        hsv_pixels=hsv_pixels,
        target_hue=target_hue,
        bootstrap=False,
    )
    if refined_mask.any():
        retracked_mask = _retrack_primary_curve_by_majority_color(
            cleaned_mask,
            refined_mask,
            plot_pixels=plot_pixels,
            hsv_pixels=hsv_pixels,
        )
        return _prefer_curve_mask(
            bootstrap_mask,
            retracked_mask,
            plot_pixels=plot_pixels,
            bootstrap_score=bootstrap_score,
        )

    if locked_mask is not cleaned_mask:
        fallback_refined_mask = _track_primary_curve_mask(
            cleaned_mask,
            hsv_pixels=hsv_pixels,
            target_hue=target_hue,
            bootstrap=False,
        )
        if fallback_refined_mask.any():
            retracked_mask = _retrack_primary_curve_by_majority_color(
                cleaned_mask,
                fallback_refined_mask,
                plot_pixels=plot_pixels,
                hsv_pixels=hsv_pixels,
            )
            return _prefer_curve_mask(
                bootstrap_mask,
                retracked_mask,
                plot_pixels=plot_pixels,
                bootstrap_score=bootstrap_score,
            )

    return bootstrap_mask


def estimate_target_hue(plot_pixels: np.ndarray, curve_mask: np.ndarray) -> Optional[float]:
    import cv2

    if plot_pixels.size == 0 or curve_mask.size == 0:
        return None

    hsv = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
    active = curve_mask > 0
    # Tighten color detection: curves usually have distinct saturation.
    # We ignore very dark pixels (V < 140) which are usually black text or labels.
    saturated = active & (hsv[:, :, 1] >= 45) & (hsv[:, :, 2] >= 140) & (hsv[:, :, 2] <= 245)
    if not saturated.any():
        return None

    height, width = curve_mask.shape
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(curve_mask, connectivity=8)
    best_hue: Optional[float] = None
    best_score = float("-inf")

    for index in range(1, component_count):
        x, y, component_width, component_height, area = stats[index]
        component_center_y = y + (component_height / 2.0)
        if component_center_y > height * 0.62:
            continue
        component_pixels = labels == index
        component_saturated = component_pixels & saturated
        if component_saturated.sum() < 12:
            continue

        hue_values = hsv[:, :, 0][component_saturated].astype(int)
        histogram = np.bincount(hue_values, minlength=180)
        if histogram.sum() == 0:
            continue

        coverage = component_width / max(1.0, float(width))
        top_bias = 1.0 - (y / max(1.0, float(height)))
        density = component_saturated.sum() / max(1.0, float(area))
        score = (2.4 * coverage) + (0.7 * top_bias) + (0.5 * density)
        if score > best_score:
            best_score = score
            best_hue = float(np.argmax(histogram))

    if best_hue is not None:
        return best_hue

    hue_values = hsv[:, :, 0][saturated].astype(int)
    histogram = np.bincount(hue_values, minlength=180)
    if histogram.sum() == 0:
        return None
    return float(np.argmax(histogram))


def hue_distance(hue_a: float, hue_b: float) -> float:
    delta = abs(float(hue_a) - float(hue_b))
    return min(delta, 180.0 - delta)


def score_curve_row(
    hsv_pixels: np.ndarray,
    *,
    column: int,
    row: int,
    target_hue: Optional[float],
    previous_row: Optional[float],
    max_jump: float,
) -> float:
    y0 = max(0, row - 1)
    y1 = min(hsv_pixels.shape[0], row + 2)
    x0 = max(0, column - 1)
    x1 = min(hsv_pixels.shape[1], column + 2)
    sample = hsv_pixels[y0:y1, x0:x1]
    mean_hue = float(sample[:, :, 0].mean())
    mean_sat = float(sample[:, :, 1].mean())
    mean_val = float(sample[:, :, 2].mean())

    color_score = 0.0
    if target_hue is not None and mean_sat >= 20:
        color_score = 1.0 - (hue_distance(mean_hue, target_hue) / 90.0)
    elif mean_sat < 20:
        color_score = -0.2

    continuity_score = 0.0
    if previous_row is not None:
        continuity_score = -abs(float(row) - float(previous_row)) / max(1.0, max_jump)

    darkness_bonus = max(0.0, (220.0 - mean_val) / 255.0)
    return (2.2 * color_score) + (1.8 * continuity_score) + (0.25 * darkness_bonus)


def _lock_curve_components(
    cleaned_mask: np.ndarray,
    bootstrap_mask: np.ndarray,
    *,
    hsv_pixels: Optional[np.ndarray],
    target_hue: Optional[float],
) -> np.ndarray:
    """Keep only same-color components that overlap the bootstrap-selected curve.

    This prevents the tracker from jumping to a different region that shares the
    curve's color but is not actually part of the selected series.
    """

    if hsv_pixels is None or target_hue is None:
        return cleaned_mask

    color_mask = _build_target_hue_mask(hsv_pixels, target_hue)
    if not color_mask.any():
        return cleaned_mask

    import cv2

    candidate_mask = cv2.bitwise_and(cleaned_mask, color_mask)
    if not candidate_mask.any():
        return cleaned_mask

    seed_mask = cv2.dilate(bootstrap_mask, np.ones((7, 7), dtype=np.uint8), iterations=1)
    component_count, labels, _, _ = cv2.connectedComponentsWithStats(candidate_mask, connectivity=8)
    locked_mask = np.zeros_like(candidate_mask)

    for index in range(1, component_count):
        component_pixels = labels == index
        if np.any(component_pixels & (seed_mask > 0)):
            locked_mask[component_pixels] = 255

    return locked_mask if locked_mask.any() else candidate_mask


def _build_target_hue_mask(hsv_pixels: np.ndarray, target_hue: float) -> np.ndarray:
    hue = hsv_pixels[:, :, 0].astype(float)
    sat = hsv_pixels[:, :, 1]
    val = hsv_pixels[:, :, 2]
    distance = np.abs(hue - float(target_hue))
    distance = np.minimum(distance, 180.0 - distance)
    mask = (sat >= 28) & (val <= 250) & (distance <= _TARGET_HUE_DISTANCE_LIMIT)
    return mask.astype(np.uint8) * 255


def _curve_anchor_score(
    curve_columns: np.ndarray,
    curve_rows: np.ndarray,
    *,
    width: int,
    height: int,
) -> float:
    if curve_columns.size == 0 or curve_rows.size == 0:
        return float("-inf")

    quality = _trace_quality(curve_columns, width)
    anchor_count = min(8, curve_rows.size)
    anchor_level = float(np.median(curve_rows[:anchor_count]))
    anchor_penalty = 0.15 * (anchor_level / max(1.0, float(height)))
    return quality - anchor_penalty


def _prefer_curve_mask(
    reference_mask: np.ndarray,
    candidate_mask: np.ndarray,
    *,
    plot_pixels: Optional[np.ndarray],
    bootstrap_score: float,
) -> np.ndarray:
    if candidate_mask.size == 0 or not candidate_mask.any():
        return reference_mask

    candidate_columns, candidate_rows = extract_curve_trace(candidate_mask, plot_pixels=plot_pixels)
    candidate_score = _curve_anchor_score(
        candidate_columns,
        candidate_rows,
        width=candidate_mask.shape[1],
        height=candidate_mask.shape[0],
    )
    if candidate_score > bootstrap_score + 0.01:
        return candidate_mask
    return reference_mask


def _saturated_color_mask(pixels: np.ndarray) -> np.ndarray:
    import cv2

    if pixels.size == 0:
        return np.zeros(pixels.shape[:2], dtype=np.uint8)

    hsv_pixels = cv2.cvtColor(pixels, cv2.COLOR_RGB2HSV)
    mask = (hsv_pixels[:, :, 1] >= 35) & (hsv_pixels[:, :, 2] <= 245)
    if not mask.any():
        return np.zeros(pixels.shape[:2], dtype=np.uint8)
    mask = cv2.dilate(mask.astype(np.uint8) * 255, np.ones((3, 3), dtype=np.uint8), iterations=1)
    return mask


def _curve_color_purity(hsv_pixels: np.ndarray, curve_mask: np.ndarray, target_hue: float) -> float:
    if hsv_pixels.size == 0 or curve_mask.size == 0 or target_hue is None:
        return 0.0

    active = curve_mask > 0
    saturated = active & (hsv_pixels[:, :, 1] >= 35) & (hsv_pixels[:, :, 2] <= 245)
    if not saturated.any():
        return 0.0

    hue = hsv_pixels[:, :, 0].astype(float)
    distance = np.abs(hue - float(target_hue))
    distance = np.minimum(distance, 180.0 - distance)
    aligned = saturated & (distance <= _TARGET_HUE_DISTANCE_LIMIT)
    return float(aligned.sum() / max(1, saturated.sum()))


def _retrack_primary_curve_by_majority_color(
    cleaned_mask: np.ndarray,
    curve_mask: np.ndarray,
    *,
    plot_pixels: Optional[np.ndarray],
    hsv_pixels: Optional[np.ndarray],
) -> np.ndarray:
    import cv2

    if plot_pixels is None or hsv_pixels is None or curve_mask.size == 0 or not curve_mask.any():
        return curve_mask

    target_hue = estimate_target_hue(plot_pixels, curve_mask)
    if target_hue is None:
        return curve_mask

    current_columns, _ = extract_curve_trace(curve_mask, plot_pixels=plot_pixels)
    current_quality = _trace_quality(current_columns, curve_mask.shape[1])
    current_purity = _curve_color_purity(hsv_pixels, curve_mask, target_hue)
    if current_quality >= 0.95 and current_purity >= 0.95:
        return curve_mask

    color_mask = _build_target_hue_mask(hsv_pixels, target_hue)
    if not color_mask.any():
        return curve_mask

    retrace_source = cv2.bitwise_and(cleaned_mask, color_mask)
    if not retrace_source.any():
        return curve_mask

    retracked_mask = _track_primary_curve_mask(
        retrace_source,
        hsv_pixels=hsv_pixels,
        target_hue=target_hue,
        bootstrap=False,
    )
    if not retracked_mask.any():
        return curve_mask

    retracked_columns, _ = extract_curve_trace(retracked_mask, plot_pixels=plot_pixels)
    retracked_quality = _trace_quality(retracked_columns, retracked_mask.shape[1])
    retracked_purity = _curve_color_purity(hsv_pixels, retracked_mask, target_hue)

    if retracked_quality >= current_quality + 0.05:
        return retracked_mask
    if retracked_purity >= current_purity + 0.03 and retracked_quality >= current_quality - 0.02:
        return retracked_mask
    return curve_mask


def _trace_quality(curve_columns: np.ndarray, width: int) -> float:
    """Score how continuous and span-wide a traced curve is."""

    if curve_columns.size == 0:
        return float("-inf")

    x_values = np.unique(np.asarray(np.round(curve_columns), dtype=int))
    coverage = x_values.size / max(1.0, float(width))
    if x_values.size < 2:
        return coverage - 1.0

    gaps = np.diff(x_values)
    gap_threshold = max(4, int(width * 0.03))
    large_gap_count = int((gaps > gap_threshold).sum())
    max_gap = float(gaps.max())
    return coverage - (0.18 * large_gap_count) - min(0.8, max_gap / max(1.0, float(width)))


def _remove_top_margin_artifacts(curve_mask: np.ndarray, margin_rows: int) -> np.ndarray:
    """Remove compact top annotations without clipping tall curve peaks."""

    if curve_mask.size == 0 or margin_rows <= 0:
        return curve_mask

    import cv2

    height, width = curve_mask.shape
    if height == 0 or width == 0:
        return curve_mask

    top_band_limit = min(height, int(margin_rows + max(8, height * 0.06)))
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(curve_mask, connectivity=8)
    cleaned = curve_mask.copy()

    for index in range(1, component_count):
        x, y, component_width, component_height, area = stats[index]
        if y >= margin_rows:
            continue
        if component_width >= width * 0.55:
            continue
        component_bottom = y + component_height
        if component_bottom > top_band_limit:
            continue

        fill_ratio = area / max(1.0, float(component_width * component_height))
        compact_text_like = (
            component_height <= max(14, height * 0.08)
            or fill_ratio >= 0.12
        )
        if compact_text_like:
            cleaned[labels == index] = 0

    return cleaned


def extract_curve_trace(
    curve_mask: np.ndarray,
    *,
    plot_pixels: Optional[np.ndarray] = None,
    obstacle_mask: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Trace a PXRD curve using top-down ray casting.

    For every column in the mask, a ray is fired from the top of the plot
    downward.  The ray stops at the **first** active (curve-coloured) pixel it
    encounters.  This gives us the *apex* of each peak rather than the mid-line,
    and it is naturally immune to artefacts that sit below the curve (inset
    images, axis lines, etc.).

    A small noise guard is applied: if only an isolated single-pixel blob is
    detected near the very top but then there is a gap of many empty rows before
    the next active region, we skip that isolated pixel and continue downward so
    that stray noise dots don't hijack the trace.
    """
    width = curve_mask.shape[1]
    height = curve_mask.shape[0]
    x_values: List[int] = []
    y_values: List[float] = []

    # Build a color-refined version of the mask when pixel data is available
    refined_mask = curve_mask.copy()
    target_hue = None
    if plot_pixels is not None and plot_pixels.shape[:2] == curve_mask.shape:
        import cv2
        hsv_pixels = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
        target_hue = estimate_target_hue(plot_pixels, curve_mask)
        if target_hue is not None:
            color_mask = _build_target_hue_mask(hsv_pixels, target_hue)
            if color_mask.any():
                refined_mask = cv2.bitwise_and(curve_mask, color_mask)
                if not refined_mask.any():
                    refined_mask = curve_mask  # fallback if filter is too strict

    # Noise guard: ignore isolated pixels right at the top if there is a large
    # gap below them before the main signal starts.
    noise_guard_rows = max(3, height // 30)
    min_cluster_size = 3

    # --- VECTORIZED BEAM TRACING ---
    # Apply obstacle mask to refined_mask: make obstacle regions zero
    if obstacle_mask is not None and obstacle_mask.shape == refined_mask.shape:
        working_mask = np.where(obstacle_mask > 0, 0, refined_mask).astype(np.uint8)
    else:
        working_mask = refined_mask

    # Top-margin guard: remove compact annotation fragments at the top, but
    # keep real PXRD peaks that rise into this band and continue downward.
    top_margin = int(height * 0.08)
    working_mask = _remove_top_margin_artifacts(working_mask, top_margin)

    # BLACK PIXEL FILTER:
    # If we have detected that the target curve has a specific COLOR (target_hue is not None),
    # then we can safely treat pure black pixels (usually text/annotations) as transparent.
    # This lets the beam "see through" legend text to find a colored curve behind it.
    # If the target curve itself is black/grey, we do NOT apply this filter.
    if target_hue is not None and plot_pixels is not None and plot_pixels.shape[:2] == working_mask.shape:
        import cv2
        gray = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2GRAY)
        working_mask[gray < 35] = 0

    # Find the row index of the FIRST non-zero pixel in each column.
    # np.argmax returns the index of the first maximum. Since mask is binary (0/255),
    # it finds the first hit.
    # Note: If a column has no non-zero pixels, argmax returns 0.
    # We must mask out columns that have no signal.
    has_signal = np.any(working_mask > 0, axis=0)
    first_hits = np.argmax(working_mask > 0, axis=0)

    # Filtering step: apply the noise guard logic across all columns at once.
    # We check if there are at least min_cluster_size pixels in the next noise_guard_rows.
    # This is a bit trickier to vectorize perfectly but we can use a moving sum.
    from scipy import ndimage
    kernel = np.ones((noise_guard_rows + 1, 1), dtype=int)
    nearby_counts = ndimage.convolve((working_mask > 0).astype(int), kernel, mode='constant', cval=0)

    # For each first_hit, get the nearby count at that exact location
    cols = np.arange(width)
    counts_at_hits = nearby_counts[first_hits, cols]
    is_valid_hit = has_signal & (counts_at_hits >= min_cluster_size)

    # Final arrays
    x_arr = cols[is_valid_hit].astype(float)
    y_arr = first_hits[is_valid_hit].astype(float)

    if x_arr.size == 0:
        return np.asarray([]), np.asarray([])

    # ── Spike repair: remove short rectangular jumps caused by legend lines ──
    y_arr = _repair_legend_spikes(x_arr, y_arr, height=height, width=width)

    # Fill small horizontal gaps by linear interpolation
    max_gap = max(4.0, width * 0.03)
    segments: List[Tuple[np.ndarray, np.ndarray]] = []
    start_index = 0
    for index, gap in enumerate(np.diff(x_arr), start=1):
        if gap <= max_gap:
            continue
        segments.append((x_arr[start_index:index], y_arr[start_index:index]))
        start_index = index
    segments.append((x_arr[start_index:], y_arr[start_index:]))

    dense_x_parts: List[np.ndarray] = []
    dense_y_parts: List[np.ndarray] = []
    for segment_x, segment_y in segments:
        if segment_x.size == 0:
            continue
        if segment_x.size == 1:
            dense_x_parts.append(segment_x)
            dense_y_parts.append(segment_y)
            continue
        full_x = np.arange(int(segment_x.min()), int(segment_x.max()) + 1, dtype=float)
        full_y = np.interp(full_x, segment_x, segment_y)
        dense_x_parts.append(full_x)
        dense_y_parts.append(full_y)

    if not dense_x_parts:
        return x_arr, y_arr
    return np.concatenate(dense_x_parts), np.concatenate(dense_y_parts)


def _repair_legend_spikes(
    x_arr: np.ndarray,
    y_arr: np.ndarray,
    *,
    height: int,
    width: int,
    jump_fraction: float = 0.18,
    max_artifact_fraction: float = 0.07,
) -> np.ndarray:
    """Remove short rectangular upward spikes caused by legend indicator lines.

    In pixel coordinates (y=0 at top), a legend line makes the beam stop at a
    very SMALL y for a short run of columns.  The signature of an artifact vs.
    a real PXRD peak is:

    * **Abruptness**: The y value drops sharply (large negative gradient) then
      immediately recovers (large positive gradient) — a rectangular profile.
    * **Width**: The anomalous run spans < ``max_artifact_fraction`` of the
      plot width (legend lines are very short; real peaks span many columns).
    * **Magnitude**: The deviation from the local median is > ``jump_fraction``
      of the plot height.

    Detected runs are repaired by linear interpolation between the two
    neighbouring valid values.
    """
    n = len(y_arr)
    if n < 6:
        return y_arr

    y = y_arr.copy()

    # Robust local baseline via wide rolling median (ignores short spikes)
    half_win = max(8, n // 8)
    local_med = np.array([
        np.median(y[max(0, i - half_win): min(n, i + half_win)])
        for i in range(n)
    ])

    # Pixels that are anomalously HIGH in the image (small row value)
    threshold_px = height * jump_fraction
    is_high = (local_med - y) > threshold_px  # y much smaller than median

    # Gradient: detect abrupt transitions (legend lines have step edges)
    grad = np.abs(np.gradient(y))
    edge_thresh = height * 0.06  # a sharp jump of ≥ 6% image height per column

    max_artifact_cols = max(3, int(width * max_artifact_fraction))

    # Label contiguous anomalous regions
    from scipy import ndimage as _ndi
    labeled, n_regions = _ndi.label(is_high)

    for rid in range(1, n_regions + 1):
        idx = np.where(labeled == rid)[0]
        if idx.size == 0 or idx.size > max_artifact_cols:
            continue  # too wide → likely a real feature

        # Check that at least one edge of the region is abrupt
        left_edge = idx[0]
        right_edge = idx[-1]
        left_grad = grad[left_edge] if left_edge < n else 0.0
        right_grad = grad[right_edge] if right_edge < n else 0.0
        if max(left_grad, right_grad) < edge_thresh:
            continue  # smooth transition → probably a real peak shoulder
        if float(np.ptp(y[idx])) > max(4.0, height * 0.035):
            continue  # sloped/pointed run → likely a real narrow PXRD peak

        # Interpolate across the artifact run
        li = left_edge - 1
        ri = right_edge + 1
        if li < 0 or ri >= n:
            continue  # can't interpolate at boundary

        left_y = y[li]
        right_y = y[ri]
        for j, col in enumerate(idx):
            t = (j + 1) / (len(idx) + 1)
            y[col] = left_y + t * (right_y - left_y)

    return y



def compute_overlay_similarity(
    curve_mask: np.ndarray,
    curve_columns: np.ndarray,
    curve_rows: np.ndarray,
) -> float:
    """Fraction of curve-mask pixels covered by a distance-tolerant rendering.

    A thin polyline rasterized against a thick mask always yields a tiny IoU,
    so the previous thickness=1 implementation was effectively meaningless.
    We instead rasterize the polyline, dilate it to the estimated curve
    thickness, and report the fraction of the mask that lies inside that
    distance tolerance — a Chamfer-style proxy for trace quality.
    """

    import cv2

    if curve_columns.size == 0 or curve_mask.size == 0:
        return 0.0

    height, width = curve_mask.shape
    rendered = np.zeros_like(curve_mask)
    points = np.column_stack([curve_columns.astype(int), curve_rows.astype(int)]).reshape(-1, 1, 2)
    if len(points) > 1:
        cv2.polylines(rendered, [points], isClosed=False, color=255, thickness=1)
    else:
        rendered[int(curve_rows[0]), int(curve_columns[0])] = 255

    # Dilate to approximately match curve thickness so the comparison is
    # tolerant of sub-pixel apex shifts but still punishes off-curve tracking.
    radius = max(2, min(height, width) // 120)
    kernel = np.ones((radius * 2 + 1, radius * 2 + 1), dtype=np.uint8)
    rendered_dilated = cv2.dilate(rendered, kernel, iterations=1)

    mask_binary = curve_mask > 0
    covered = mask_binary & (rendered_dilated > 0)
    mask_pixels = int(mask_binary.sum())
    if mask_pixels == 0:
        return 0.0
    return float(covered.sum()) / float(mask_pixels)


def compute_reliability(
    *,
    overlay_similarity: float,
    x_axis: AxisCalibration,
    y_axis: AxisCalibration,
    flagged_reasons: Sequence[str],
) -> float:
    calibration_bonus = min(0.15, 0.03 * (x_axis.tick_count + y_axis.tick_count))
    penalty = 0.1 * len(flagged_reasons)
    score = overlay_similarity + calibration_bonus - penalty
    return max(0.0, min(1.0, score))


def save_replot(
    x_values: Sequence[float],
    y_values: Sequence[float],
    path: Path,
    *,
    title: str,
    ylabel: str = "Intensity (norm.)",
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(x_values, y_values, color="#1f4b99", linewidth=1.5)
    axis.set_xlabel("2theta (deg)")
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(path, dpi=200)
    plt.close(figure)
