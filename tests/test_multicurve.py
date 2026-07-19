from pathlib import Path
from types import SimpleNamespace

import sys

import numpy as np

from pxrd_fetcher.config import Settings
from pxrd_fetcher.digitize import digitize_figure
from pxrd_fetcher.pipeline import _write_series_artifacts
from pxrd_fetcher.schemas import CurveDescriptor, DigitizedCurve, FigureCurveAnalysis, FigureDigitizationResult


def test_digitize_figure_returns_multiple_series_with_ai_labels(monkeypatch, tmp_path):
    from PIL import Image
    from pxrd_fetcher import digitize as digitize_module

    class FakeBackend:
        name = "fake"

        def detect_tokens(self, image, *, min_confidence: float = 0.0):
            return []

        def extract_text(self, image, *, min_confidence: float = 0.0):
            return ""

    image_path = tmp_path / "crop.png"
    Image.new("RGB", (60, 40), color="white").save(image_path)
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace())

    top_mask = np.zeros((40, 60), dtype=np.uint8)
    top_mask[8:11, :] = 255
    bottom_mask = np.zeros((40, 60), dtype=np.uint8)
    bottom_mask[24:27, :] = 255

    monkeypatch.setattr(digitize_module, "get_ocr_backend", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr(digitize_module, "detect_plot_bbox", lambda pixels: (0, 0, 60, 40))
    monkeypatch.setattr(
        digitize_module,
        "extract_axis_entries",
        lambda pixels, plot_bbox, axis, ocr_backend: (
            [(0.0, 5.0), (59.0, 65.0)] if axis == "x" else [(0.0, 100.0), (39.0, 0.0)]
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "detect_text_mask",
        lambda plot_pixels, ocr_backend: (np.zeros((40, 60), dtype=np.uint8), []),
    )
    monkeypatch.setattr(digitize_module, "estimate_series_count", lambda plot_pixels: (True, 2))
    monkeypatch.setattr(
        digitize_module,
        "build_curve_mask",
        lambda plot_pixels, text_mask=None: np.ones((40, 60), dtype=np.uint8) * 255,
    )
    monkeypatch.setattr(digitize_module, "detect_obstacle_bboxes", lambda plot_pixels: None)
    monkeypatch.setattr(
        digitize_module,
        "_extract_ranked_curve_traces",
        lambda curve_mask, plot_pixels, requested_count, min_curve_coverage, descriptors=None, obstacle_mask=None: [
            (top_mask, np.arange(0, 60, dtype=float), np.full(60, 9.0, dtype=float)),
            (bottom_mask, np.arange(0, 60, dtype=float), np.full(60, 25.0, dtype=float)),
        ],
    )
    monkeypatch.setattr(
        digitize_module,
        "detect_non_curve_boxes",
        lambda *args, **kwargs: ([], np.zeros((40, 60), dtype=np.uint8)),
    )
    monkeypatch.setattr(digitize_module, "_extract_artifact_contours", lambda mask: [])
    monkeypatch.setattr(
        digitize_module,
        "compute_overlay_similarity",
        lambda curve_mask, curve_columns, curve_rows: 0.91 if curve_rows.mean() < 12 else 0.87,
    )
    monkeypatch.setattr(
        digitize_module,
        "process_curve",
        lambda x_values, y_values, **kwargs: (
            np.asarray([5.0, 25.0, 45.0], dtype=float),
            np.asarray([12.0, 34.0, 18.0], dtype=float),
            np.asarray([0.0, 0.0, 0.0], dtype=float),
            [],
        ),
    )

    settings = Settings(
        CHATGPT_API_KEY="x",
        CHATGPT_MODEL="gpt-5.4-2026-03-05",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "db.sqlite",
    )

    result = digitize_figure(
        image_path,
        tmp_path / "digitize",
        settings,
        curve_analysis=FigureCurveAnalysis(
            curve_count=2,
            curves=[
                CurveDescriptor(order_from_top=1, label="pristine", confidence=0.92),
                CurveDescriptor(order_from_top=2, label="cycled", confidence=0.89),
            ],
        ),
        likely_series_count=2,
    )

    assert result.status == "accepted"
    assert result.multi_series_detected is True
    assert len(result.series) == 2
    assert [series.series_label for series in result.series] == ["pristine", "cycled"]
    assert all(Path(series.replot_image_path).exists() for series in result.series)
    assert Path(result.annotated_image_path).exists()


def test_write_series_artifacts_writes_one_csv_per_series(tmp_path):
    digitization = FigureDigitizationResult(
        status="accepted",
        plot_bbox=(0, 0, 10, 10),
        series=[
            DigitizedCurve(
                status="accepted",
                plot_bbox=(0, 0, 10, 10),
                series_order_from_top=1,
                series_label="pristine",
                processed_two_theta=[10.0, 20.0],
                processed_intensity=[0.5, 1.0],
                raw_intensity=[0.5, 1.0],
            ),
            DigitizedCurve(
                status="accepted",
                plot_bbox=(0, 0, 10, 10),
                series_order_from_top=2,
                series_label="cycled",
                processed_two_theta=[10.0, 20.0],
                processed_intensity=[0.2, 0.8],
                raw_intensity=[0.2, 0.8],
            ),
        ],
    )

    _write_series_artifacts("paper-1", "fig-1", tmp_path, digitization)

    csv_paths = [Path(series.csv_path) for series in digitization.series]
    assert all(path.exists() for path in csv_paths)
    assert csv_paths[0].name == "fig-1-s01.csv"
    assert csv_paths[1].name == "fig-1-s02.csv"
    assert "series_label" in csv_paths[0].read_text(encoding="utf-8")


def test_digitize_figure_rejects_weak_secondary_trace_when_ai_says_single_curve(monkeypatch, tmp_path):
    from PIL import Image
    from pxrd_fetcher import digitize as digitize_module

    class FakeBackend:
        name = "fake"

        def detect_tokens(self, image, *, min_confidence: float = 0.0):
            return []

        def extract_text(self, image, *, min_confidence: float = 0.0):
            return ""

    image_path = tmp_path / "crop-single.png"
    Image.new("RGB", (80, 50), color="white").save(image_path)
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace())

    primary_mask = np.zeros((50, 80), dtype=np.uint8)
    primary_mask[24:27, :] = 255
    weak_secondary_mask = np.zeros((50, 80), dtype=np.uint8)
    weak_secondary_mask[2:8, 6:16] = 255
    text_mask = np.zeros((50, 80), dtype=np.uint8)
    text_mask[0:10, 4:18] = 255

    monkeypatch.setattr(digitize_module, "get_ocr_backend", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr(digitize_module, "detect_plot_bbox", lambda pixels: (0, 0, 80, 50))
    monkeypatch.setattr(
        digitize_module,
        "extract_axis_entries",
        lambda pixels, plot_bbox, axis, ocr_backend: (
            [(0.0, 5.0), (79.0, 45.0)] if axis == "x" else [(0.0, 100.0), (49.0, 0.0)]
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "detect_text_mask",
        lambda plot_pixels, ocr_backend: (text_mask, []),
    )
    monkeypatch.setattr(digitize_module, "estimate_series_count", lambda plot_pixels: (True, 2))
    monkeypatch.setattr(
        digitize_module,
        "build_curve_mask",
        lambda plot_pixels, text_mask=None: np.ones((50, 80), dtype=np.uint8) * 255,
    )
    monkeypatch.setattr(digitize_module, "detect_obstacle_bboxes", lambda plot_pixels: None)
    monkeypatch.setattr(
        digitize_module,
        "_extract_ranked_curve_traces",
        lambda curve_mask, plot_pixels, requested_count, min_curve_coverage, descriptors=None, obstacle_mask=None: [
            (primary_mask, np.arange(0, 80, dtype=float), np.full(80, 25.0, dtype=float)),
            (weak_secondary_mask, np.arange(6, 16, dtype=float), np.full(10, 4.0, dtype=float)),
        ],
    )
    monkeypatch.setattr(
        digitize_module,
        "detect_non_curve_boxes",
        lambda *args, **kwargs: ([], np.zeros((50, 80), dtype=np.uint8)),
    )
    monkeypatch.setattr(digitize_module, "_extract_artifact_contours", lambda mask: [])
    monkeypatch.setattr(
        digitize_module,
        "compute_overlay_similarity",
        lambda curve_mask, curve_columns, curve_rows: 0.9,
    )
    monkeypatch.setattr(
        digitize_module,
        "process_curve",
        lambda x_values, y_values, **kwargs: (
            np.asarray([5.0, 20.0, 35.0], dtype=float),
            np.asarray([15.0, 30.0, 12.0], dtype=float),
            np.asarray([0.0, 0.0, 0.0], dtype=float),
            [],
        ),
    )

    settings = Settings(
        CHATGPT_API_KEY="x",
        CHATGPT_MODEL="gpt-5.4-2026-03-05",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "db.sqlite",
    )

    result = digitize_figure(
        image_path,
        tmp_path / "digitize-single",
        settings,
        curve_analysis=FigureCurveAnalysis(
            curve_count=1,
            confidence=0.96,
            curves=[CurveDescriptor(order_from_top=1, label="single-trace", confidence=0.9)],
        ),
        likely_series_count=2,
    )

    assert len(result.series) == 1
    assert result.series[0].series_label == "single-trace"
    assert any("Rejected" in reason for reason in result.flagged_reasons)
