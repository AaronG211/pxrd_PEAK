import numpy as np
from types import SimpleNamespace

from pxrd_fetcher.digitize import (
    _digitize_single_curve,
    _request_ai_non_curve_regions,
    build_curve_mask,
    detect_text_mask,
    detect_non_curve_boxes,
    detect_plot_bbox,
    estimate_target_hue,
    extract_curve_trace,
    _select_dominant_x_axis_entries,
    _trace_quality,
    _curve_anchor_score,
    _lock_curve_components,
    _remove_top_margin_artifacts,
    _repair_legend_spikes,
    _retrack_primary_curve_by_majority_color,
    repair_x_axis_entries,
    select_primary_curve_mask,
)
from pxrd_fetcher.schemas import ArtifactAssistance, ArtifactKind, ArtifactRegion, AxisCalibration


def test_select_primary_curve_mask_prefers_top_spanning_curve():
    mask = np.zeros((120, 200), dtype=np.uint8)
    mask[20:23, 10:190] = 255
    mask[55:58, 12:188] = 255
    mask[92:95, 15:185] = 255
    mask[:, 170:172] = 255

    primary = select_primary_curve_mask(mask)
    rows = np.where(primary > 0)[0]

    assert rows.size > 0
    assert rows.mean() < 35


def test_select_primary_curve_mask_skips_thin_top_border_band():
    mask = np.zeros((160, 220), dtype=np.uint8)
    mask[4:7, 5:215] = 255
    x_values = np.arange(10, 210)
    y_values = (28 + 6 * np.sin(np.linspace(0, np.pi, x_values.size))).astype(int)
    for x_value, y_value in zip(x_values, y_values):
        mask[y_value - 1:y_value + 2, x_value] = 255

    primary = select_primary_curve_mask(mask)
    rows = np.where(primary > 0)[0]

    assert rows.size > 0
    assert rows.mean() > 20


def test_select_primary_curve_mask_keeps_single_series_continuous_across_arch():
    mask = np.zeros((180, 260), dtype=np.uint8)
    x_values = np.arange(10, 250)
    top_curve = (96 - 42 * np.exp(-((x_values - 120) / 34.0) ** 2)).astype(int)
    lower_curve = (122 + 8 * np.sin(np.linspace(0, 1.5 * np.pi, x_values.size))).astype(int)

    for x_value, top_row, lower_row in zip(x_values, top_curve, lower_curve):
        mask[top_row - 1:top_row + 2, x_value] = 255
        mask[lower_row - 1:lower_row + 2, x_value] = 255

    primary = select_primary_curve_mask(mask)
    active_columns = np.unique(np.where(primary > 0)[1])

    assert active_columns.size >= 220
    assert active_columns.min() <= 13
    assert active_columns.max() >= 246


def test_select_primary_curve_mask_keeps_left_edge_peak():
    mask = np.zeros((140, 240), dtype=np.uint8)
    x_values = np.arange(0, 220)
    top_curve = (
        18
        + 10 * np.exp(-((x_values - 5) / 2.2) ** 2)
        + 2 * np.sin(np.linspace(0, 1.8 * np.pi, x_values.size))
    ).astype(int)
    lower_curve = (78 + 6 * np.sin(np.linspace(0, 1.2 * np.pi, x_values.size))).astype(int)

    for x_value, top_row, lower_row in zip(x_values, top_curve, lower_curve):
        mask[top_row - 1:top_row + 2, x_value] = 255
        mask[lower_row - 1:lower_row + 2, x_value] = 255

    primary = select_primary_curve_mask(mask)
    active_columns = np.unique(np.where(primary > 0)[1])
    rows = np.where(primary > 0)[0]

    assert active_columns.size > 0
    assert active_columns.min() == 0
    assert np.any(primary[:, :5] > 0)
    assert rows.mean() < 40


def test_select_primary_curve_mask_prefers_top_colored_series():
    mask = np.zeros((180, 260), dtype=np.uint8)
    plot_pixels = np.full((180, 260, 3), 255, dtype=np.uint8)
    x_values = np.arange(12, 248)
    top_curve = (52 + 8 * np.sin(np.linspace(0, 2 * np.pi, x_values.size))).astype(int)
    middle_curve = (102 + 10 * np.sin(np.linspace(0, 1.4 * np.pi, x_values.size))).astype(int)

    for x_value, top_row, middle_row in zip(x_values, top_curve, middle_curve):
        mask[top_row - 1:top_row + 2, x_value] = 255
        mask[middle_row - 1:middle_row + 2, x_value] = 255
        plot_pixels[top_row - 1:top_row + 2, x_value] = np.array([92, 72, 160], dtype=np.uint8)
        plot_pixels[middle_row - 1:middle_row + 2, x_value] = np.array([225, 72, 72], dtype=np.uint8)

    primary = select_primary_curve_mask(mask, plot_pixels=plot_pixels)
    rows = np.where(primary > 0)[0]

    assert rows.size > 0
    assert rows.mean() < 80


def test_retrack_primary_curve_by_majority_color_removes_off_color_spur():
    import cv2

    mask = np.zeros((150, 240), dtype=np.uint8)
    plot_pixels = np.full((150, 240, 3), 255, dtype=np.uint8)

    blue = np.array([88, 74, 168], dtype=np.uint8)
    teal = np.array([66, 165, 154], dtype=np.uint8)

    x_values = np.arange(18, 220)
    y_values = (54 + 6 * np.sin(np.linspace(0, 2 * np.pi, x_values.size))).astype(int)
    for x_value, y_value in zip(x_values, y_values):
        mask[y_value - 1:y_value + 2, x_value] = 255
        plot_pixels[y_value - 1:y_value + 2, x_value] = blue

    mask[22:70, 112:118] = 255
    plot_pixels[22:70, 112:118] = teal

    hsv_pixels = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
    retracked = _retrack_primary_curve_by_majority_color(
        mask,
        mask,
        plot_pixels=plot_pixels,
        hsv_pixels=hsv_pixels,
    )

    assert retracked.sum() > 0
    assert np.count_nonzero(retracked[22:70, 112:118]) < np.count_nonzero(mask[22:70, 112:118])
    assert np.unique(np.where(retracked > 0)[1]).min() <= 20
    assert np.unique(np.where(retracked > 0)[1]).max() >= 215


def test_lock_curve_components_keeps_seeded_same_color_component_only():
    mask = np.zeros((120, 220), dtype=np.uint8)
    plot_pixels = np.full((120, 220, 3), 255, dtype=np.uint8)
    curve_color = np.array([58, 84, 164], dtype=np.uint8)

    x_values = np.arange(20, 170)
    y_values = (34 + 4 * np.sin(np.linspace(0, 2 * np.pi, x_values.size))).astype(int)
    for x_value, y_value in zip(x_values, y_values):
        mask[y_value - 1:y_value + 2, x_value] = 255
        plot_pixels[y_value - 1:y_value + 2, x_value] = curve_color

    mask[70:78, 184:212] = 255
    plot_pixels[70:78, 184:212] = curve_color

    bootstrap_mask = np.zeros_like(mask)
    bootstrap_mask[31:39, 24:58] = 255

    locked = _lock_curve_components(
        mask,
        bootstrap_mask,
        hsv_pixels=None,
        target_hue=None,
    )
    assert np.array_equal(locked, mask)

    import cv2

    hsv_pixels = cv2.cvtColor(plot_pixels, cv2.COLOR_RGB2HSV)
    target_hue = estimate_target_hue(plot_pixels, bootstrap_mask)
    assert target_hue is not None

    locked = _lock_curve_components(
        mask,
        bootstrap_mask,
        hsv_pixels=hsv_pixels,
        target_hue=target_hue,
    )

    assert locked[70:78, 184:212].sum() == 0
    assert locked[31:39, 24:58].sum() > 0
    assert locked[:, 20:170].sum() > 0


def test_repair_x_axis_entries_fixes_truncated_last_tick():
    repaired = repair_x_axis_entries([(208.5, 20.0), (378.0, 40.0), (548.0, 60.0), (712.5, 8.0)])

    assert [value for _, value in repaired] == [20.0, 40.0, 60.0, 80.0]


def test_repair_x_axis_entries_fixes_partial_double_digit_tick():
    repaired = repair_x_axis_entries([(208.5, 20.0), (378.0, 40.0), (557.5, 0.0), (712.5, 68.0)])

    assert [value for _, value in repaired] == [20.0, 40.0, 60.0, 80.0]


def test_select_dominant_x_axis_entries_drops_lower_noise_cluster():
    entries = [
        (261.0, 20.0, 98.0),
        (515.5, 40.0, 98.5),
        (771.5, 60.0, 99.0),
        (1012.0, 8.0, 98.2),
        (367.5, 0.0, 154.0),
    ]

    selected = _select_dominant_x_axis_entries(entries)

    assert [value for _, value, _ in selected] == [20.0, 40.0, 60.0, 8.0]


def test_select_dominant_x_axis_entries_keeps_tick_line_over_axis_label():
    entries = [
        (134.5, 5.0, 67.0),
        (342.0, 10.0, 67.0),
        (548.5, 15.0, 67.0),
        (755.5, 20.0, 67.0),
        (962.5, 25.0, 67.0),
        (1170.5, 30.0, 67.0),
        (640.0, 2.0, 116.0),
        (263.5, 15.0, 142.0),
    ]

    selected = _select_dominant_x_axis_entries(entries)

    assert [value for _, value, _ in selected] == [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]


def test_trace_quality_penalizes_fragmented_traces():
    continuous = np.arange(0, 120, dtype=float)
    fragmented = np.concatenate([np.arange(0, 40), np.arange(80, 120)]).astype(float)

    assert _trace_quality(continuous, 120) > _trace_quality(fragmented, 120)


def test_curve_anchor_score_prefers_earlier_start():
    columns = np.arange(0, 100, dtype=float)
    bootstrap_rows = np.full(100, 130.0, dtype=float)
    candidate_rows = np.full(100, 205.0, dtype=float)

    bootstrap_score = _curve_anchor_score(columns, bootstrap_rows, width=980, height=728)
    candidate_score = _curve_anchor_score(columns, candidate_rows, width=980, height=728)

    assert bootstrap_score > candidate_score


def test_build_curve_mask_respects_text_mask():
    plot_pixels = np.full((120, 160, 3), 255, dtype=np.uint8)
    plot_pixels[16:24, 10:150] = 0
    plot_pixels[76:88, 10:150] = 0
    text_mask = np.zeros((120, 160), dtype=np.uint8)
    text_mask[12:28, 0:160] = 255

    curve_mask = build_curve_mask(plot_pixels, text_mask=text_mask)

    assert curve_mask[18:22, 20:140].sum() == 0
    assert curve_mask[78:86, 20:140].sum() > 0


def test_detect_text_mask_marks_numeric_peak_label():
    plot_pixels = np.full((120, 180, 3), 255, dtype=np.uint8)

    class FakeOCR:
        def detect_tokens(self, image, min_confidence=0.0):
            height, width = image.shape[:2]
            scale_x = width / plot_pixels.shape[1]
            scale_y = height / plot_pixels.shape[0]
            return [
                SimpleNamespace(
                    text="(100)",
                    bbox=(
                        int(24 * scale_x),
                        int(8 * scale_y),
                        int(70 * scale_x),
                        int(20 * scale_y),
                    ),
                )
            ]

    text_mask, boxes = detect_text_mask(plot_pixels, FakeOCR())

    assert boxes
    assert text_mask[14, 42] == 255


def test_build_curve_mask_removes_numeric_peak_label_from_curve_pixels():
    plot_pixels = np.full((120, 180, 3), 255, dtype=np.uint8)
    plot_pixels[8:26, 24:70] = 0
    plot_pixels[70:74, 10:170] = 0
    text_mask = np.zeros((120, 180), dtype=np.uint8)
    text_mask[4:30, 18:76] = 255

    curve_mask = build_curve_mask(plot_pixels, text_mask=text_mask)

    assert curve_mask[8:26, 24:70].sum() == 0
    assert curve_mask[70:74, 20:160].sum() > 0


def test_digitize_single_curve_replot_uses_absolute_raw_intensity(monkeypatch, tmp_path):
    from pxrd_fetcher import digitize as digitize_module
    from pxrd_fetcher.config import Settings

    captured = {}

    monkeypatch.setattr(
        digitize_module,
        "process_curve",
        lambda x_values, y_values, **kwargs: (
            np.asarray([0.0, 1.0, 2.0], dtype=float),
            np.asarray([0.0, 100.0, 0.0], dtype=float),
            np.asarray([20.0, 20.0, 20.0], dtype=float),
            [],
        ),
    )

    def fake_save_replot(x_values, y_values, path, *, title, ylabel="Intensity (norm.)"):
        captured["x_values"] = list(x_values)
        captured["y_values"] = list(y_values)
        captured["ylabel"] = ylabel

    monkeypatch.setattr(digitize_module, "save_replot", fake_save_replot)

    settings = Settings(
        CHATGPT_API_KEY="x",
        CHATGPT_MODEL="gpt-5.4-2026-03-05",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "db.sqlite",
        smoothing_window=99,
    )
    curve_mask = np.zeros((100, 3), dtype=np.uint8)
    curve_columns = np.asarray([0.0, 1.0, 2.0], dtype=float)
    curve_rows = np.asarray([10.0, 40.0, 70.0], dtype=float)
    curve_mask[curve_rows.astype(int), curve_columns.astype(int)] = 255

    result = _digitize_single_curve(
        image_path=tmp_path / "crop.png",
        output_dir=tmp_path,
        settings=settings,
        plot_bbox=(0, 0, 3, 100),
        x_axis=AxisCalibration(slope=1.0, intercept=0.0, tick_count=2, source="test-x"),
        y_axis=AxisCalibration(slope=-1.0, intercept=100.0, tick_count=2, source="test-y"),
        curve_mask=curve_mask,
        curve_columns=curve_columns,
        curve_rows=curve_rows,
        multi_series_detected=False,
        series_count_estimate=1,
        shared_flagged_reasons=[],
        requires_manual_review=False,
        descriptor=None,
        series_index=0,
    )

    assert result.processed_intensity == [0.0, 100.0, 0.0]
    assert captured["y_values"] == [90.0, 60.0, 30.0]
    assert captured["ylabel"] == "Intensity (raw)"


def test_build_curve_mask_removes_top_frame_line():
    plot_pixels = np.full((120, 160, 3), 255, dtype=np.uint8)
    plot_pixels[0:4, :] = 0
    plot_pixels[52:56, 10:150] = 0

    curve_mask = build_curve_mask(plot_pixels)

    assert curve_mask[0:4, :].sum() == 0
    assert curve_mask[52:56, 20:140].sum() > 0


def test_detect_plot_bbox_prefers_inner_dark_frame():
    plot_pixels = np.full((180, 220, 3), 255, dtype=np.uint8)
    plot_pixels[8:24, 0:20] = 0
    plot_pixels[35:39, 28:192] = 0
    plot_pixels[135:139, 28:192] = 0
    plot_pixels[35:139, 28:32] = 0
    plot_pixels[35:139, 188:192] = 0

    bbox = detect_plot_bbox(plot_pixels)

    assert bbox[0] >= 20
    assert bbox[1] >= 30
    assert bbox[2] <= 195
    assert bbox[3] <= 142


def test_detect_plot_bbox_ignores_attached_dark_blob():
    plot_pixels = np.full((180, 220, 3), 255, dtype=np.uint8)
    plot_pixels[35:39, 30:190] = 0
    plot_pixels[135:139, 30:190] = 0
    plot_pixels[35:139, 30:34] = 0
    plot_pixels[35:139, 186:190] = 0

    yy, xx = np.ogrid[:180, :220]
    blob = (xx - 12) ** 2 + (yy - 18) ** 2 <= 9 ** 2
    plot_pixels[blob] = 0
    plot_pixels[18:40, 21:30] = 0

    bbox = detect_plot_bbox(plot_pixels)

    assert bbox[0] >= 28
    assert bbox[1] >= 33
    assert bbox[2] <= 192
    assert bbox[3] <= 141


def test_extract_curve_trace_prefers_consistent_curve_color():
    curve_mask = np.zeros((120, 140), dtype=np.uint8)
    plot_pixels = np.full((120, 140, 3), 255, dtype=np.uint8)

    x_values = np.arange(10, 130)
    y_values = (22 + 5 * np.sin(np.linspace(0, 2 * np.pi, x_values.size))).astype(int)
    for x_value, y_value in zip(x_values, y_values):
        curve_mask[y_value - 1:y_value + 2, x_value] = 255
        plot_pixels[y_value - 1:y_value + 2, x_value] = np.array([58, 84, 164], dtype=np.uint8)

    curve_mask[18:65, 70:72] = 255
    plot_pixels[18:65, 70:72] = np.array([0, 170, 170], dtype=np.uint8)

    xs, ys = extract_curve_trace(curve_mask, plot_pixels=plot_pixels)
    target_index = int(np.where(xs == 70)[0][0])

    assert abs(float(ys[target_index]) - float(y_values[60])) <= 3.0


def test_extract_curve_trace_keeps_peak_that_reaches_top_margin():
    curve_mask = np.zeros((140, 180), dtype=np.uint8)
    x_values = np.arange(10, 170)
    y_values = (
        92
        - 86 * np.exp(-((x_values - 36) / 5.0) ** 2)
        - 34 * np.exp(-((x_values - 96) / 12.0) ** 2)
    ).astype(int)
    for x_value, y_value in zip(x_values, y_values):
        curve_mask[y_value:96, x_value] = 255

    top_artifact_x = slice(118, 145)
    curve_mask[5:16, top_artifact_x] = 255

    cleaned = _remove_top_margin_artifacts(curve_mask, int(curve_mask.shape[0] * 0.08))
    xs, ys = extract_curve_trace(cleaned)

    peak_index = int(np.argmin(np.abs(xs - 36)))
    assert ys[peak_index] <= 10
    assert cleaned[5:16, top_artifact_x].sum() == 0


def test_repair_legend_spikes_repairs_flat_artifact_but_keeps_pointed_peak():
    x_arr = np.arange(100, dtype=float)
    y_arr = np.full(100, 82.0, dtype=float)
    y_arr[25:32] = 12.0
    y_arr[60:67] = np.array([70.0, 50.0, 24.0, 8.0, 24.0, 50.0, 70.0])

    repaired = _repair_legend_spikes(x_arr, y_arr, height=120, width=100)

    assert repaired[28] > 70
    assert repaired[63] == 8.0


def test_estimate_target_hue_prefers_wide_curve_over_narrow_annotation():
    curve_mask = np.zeros((120, 140), dtype=np.uint8)
    plot_pixels = np.full((120, 140, 3), 255, dtype=np.uint8)

    x_values = np.arange(8, 132)
    y_values = np.full(x_values.shape, 24, dtype=int)
    for x_value, y_value in zip(x_values, y_values):
        curve_mask[y_value - 1:y_value + 2, x_value] = 255
        plot_pixels[y_value - 1:y_value + 2, x_value] = np.array([58, 84, 164], dtype=np.uint8)

    curve_mask[20:100, 70:72] = 255
    plot_pixels[20:100, 70:72] = np.array([0, 170, 170], dtype=np.uint8)

    hue = estimate_target_hue(plot_pixels, curve_mask)

    assert hue is not None
    assert 105 <= hue <= 125


def test_estimate_target_hue_ignores_low_axis_like_component():
    curve_mask = np.zeros((140, 160), dtype=np.uint8)
    plot_pixels = np.full((140, 160, 3), 255, dtype=np.uint8)

    curve_mask[28:32, 10:150] = 255
    plot_pixels[28:32, 10:150] = np.array([58, 84, 164], dtype=np.uint8)

    curve_mask[118:128, 10:150] = 255
    plot_pixels[118:128, 10:150] = np.array([0, 170, 170], dtype=np.uint8)

    hue = estimate_target_hue(plot_pixels, curve_mask)

    assert hue is not None
    assert 105 <= hue <= 125


def test_detect_non_curve_boxes_marks_axes_and_labels_without_boxing_curve():
    plot_pixels = np.full((160, 240, 3), 255, dtype=np.uint8)
    curve_mask = np.zeros((160, 240), dtype=np.uint8)

    plot_pixels[4:7, 10:230] = 0
    plot_pixels[4:140, 10:13] = 0
    plot_pixels[136:139, 10:230] = 0

    plot_pixels[10:24, 28:54] = 0
    plot_pixels[144:154, 104:140] = 0

    x_values = np.arange(12, 228)
    curve_rows = (112 - 44 * np.exp(-((x_values - 126) / 30.0) ** 2)).astype(int)
    for x_value, y_value in zip(x_values, curve_rows):
        curve_mask[y_value - 2:y_value + 3, x_value] = 255
        plot_pixels[y_value - 2:y_value + 3, x_value] = 0

    fake_ocr = SimpleNamespace(
        detect_tokens=lambda image, min_confidence=0.0: [
            SimpleNamespace(text="(100)", bbox=(28, 10, 54, 24)),
            SimpleNamespace(text="20", bbox=(104, 144, 124, 154)),
        ]
    )

    boxes, artifact_mask = detect_non_curve_boxes(
        plot_pixels,
        curve_mask=curve_mask,
        primary_curve_mask=curve_mask,
        curve_columns=x_values.astype(float),
        curve_rows=curve_rows.astype(float),
        ocr_backend=fake_ocr,
    )

    assert boxes
    assert any(box[1] <= 8 and box[0] <= 14 for box in boxes)
    assert any(box[1] >= 110 for box in boxes)

    peak_x = int(x_values[len(x_values) // 2])
    peak_y = int(curve_rows[len(curve_rows) // 2])
    assert artifact_mask[peak_y, peak_x] == 0
    assert not any(box[0] < 70 and box[2] > 170 and box[1] < 40 and box[3] > 120 for box in boxes)


def test_detect_non_curve_boxes_can_be_seeded_by_ai_regions():
    plot_pixels = np.full((120, 180, 3), 255, dtype=np.uint8)
    curve_mask = np.zeros((120, 180), dtype=np.uint8)
    x_values = np.arange(10, 170)
    curve_rows = np.full(x_values.shape, 70, dtype=int)
    for x_value, y_value in zip(x_values, curve_rows):
        curve_mask[y_value - 1:y_value + 2, x_value] = 255

    fake_ocr = SimpleNamespace(detect_tokens=lambda image, min_confidence=0.0: [])
    ai_regions = [
        ArtifactRegion(
            kind=ArtifactKind.PEAK_LABEL,
            confidence=0.96,
            bbox=[18, 10, 52, 28],
            note="(100)",
        )
    ]

    boxes, artifact_mask = detect_non_curve_boxes(
        plot_pixels,
        curve_mask=curve_mask,
        primary_curve_mask=curve_mask,
        curve_columns=x_values.astype(float),
        curve_rows=curve_rows.astype(float),
        ocr_backend=fake_ocr,
        ai_regions=ai_regions,
    )

    assert boxes
    assert any(box[0] <= 20 and box[1] <= 12 and box[2] >= 50 and box[3] >= 26 for box in boxes)
    assert np.count_nonzero(artifact_mask[10:28, 18:52]) > 0


def test_request_ai_non_curve_regions_clamps_and_filters_boxes():
    class FakeService:
        def assist_non_curve_regions(self, **kwargs):
            return ArtifactAssistance(
                regions=[
                    ArtifactRegion(kind=ArtifactKind.TEXT, confidence=0.8, bbox=[-4, 6, 24, 22]),
                    ArtifactRegion(kind=ArtifactKind.OTHER, confidence=0.9, bbox=[5, 5, 6, 6]),
                ],
                summary="demo",
            )

    plot_pixels = np.full((40, 50, 3), 255, dtype=np.uint8)
    artifact_mask = np.zeros((40, 50), dtype=np.uint8)

    regions = _request_ai_non_curve_regions(
        plot_pixels,
        openai_service=FakeService(),
        overlay_similarity=0.5,
        flagged_reasons=["Weak curve coverage"],
        existing_box_count=0,
        artifact_mask=artifact_mask,
    )

    assert len(regions) == 1
    assert regions[0].bbox == [0, 6, 24, 22]
