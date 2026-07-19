from pathlib import Path

import numpy as np
from pxrd_fetcher.config import Settings
from pxrd_fetcher.digitize import digitize_figure
from pxrd_fetcher.pipeline import _annotate_peak_delta, _screen_page
from pxrd_fetcher.openai_service import OpenAIServiceError
from pxrd_fetcher.schemas import (
    ArtifactAssistance,
    ArtifactKind,
    ArtifactRegion,
    DigitizedCurve,
    MetadataExtraction,
    PagePxrdScreening,
    ReportedPeak,
)


class FailingOpenAIService:
    def screen_pxrd_page(self, page_path, page_text, page_number):
        raise OpenAIServiceError("temporary error")


def test_page_screen_falls_back_to_local_keywords(tmp_path):
    page_path = tmp_path / "page.png"
    page_path.write_bytes(b"fake")

    screening = _screen_page(
        FailingOpenAIService(),
        page_path,
        "PXRD pattern with 2 theta (degree) axis",
        4,
    )

    assert screening.has_pxrd_graph is True
    assert "fallback" in screening.reason.lower()


def test_annotate_peak_delta_only_adds_warning(monkeypatch):
    metadata = MetadataExtraction(
        reported_peaks=[ReportedPeak(two_theta_deg=10.0), ReportedPeak(two_theta_deg=20.0)]
    )
    digitization = DigitizedCurve(
        status="accepted",
        plot_bbox=(0, 0, 10, 10),
        peaks=[ReportedPeak(two_theta_deg=10.8), ReportedPeak(two_theta_deg=21.2)],
    )

    _annotate_peak_delta(metadata, digitization)

    assert digitization.status == "accepted"
    assert digitization.peak_position_delta_deg is not None
    assert "Reported peaks do not align closely with extracted peaks" in digitization.flagged_reasons


def test_digitize_auto_accepts_successful_warning_only_pxrd(monkeypatch, tmp_path):
    from PIL import Image
    from pxrd_fetcher import digitize as digitize_module

    class FakeBackend:
        name = "fake"

        def detect_tokens(self, image, *, min_confidence: float = 0.0):
            return []

        def extract_text(self, image, *, min_confidence: float = 0.0):
            return ""

    image_path = tmp_path / "crop.png"
    Image.new("RGB", (20, 20), color="white").save(image_path)

    monkeypatch.setattr(digitize_module, "get_ocr_backend", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr(digitize_module, "detect_plot_bbox", lambda pixels: (0, 0, 20, 20))
    monkeypatch.setattr(
        digitize_module,
        "extract_axis_entries",
        lambda pixels, plot_bbox, axis, ocr_backend: (
            [(0.0, 2.0), (19.0, 22.0)] if axis == "x" else [(0.0, 100.0), (19.0, 0.0)]
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "detect_text_mask",
        lambda plot_pixels, ocr_backend: (np.zeros((20, 20), dtype=np.uint8), []),
    )
    monkeypatch.setattr(digitize_module, "estimate_series_count", lambda plot_pixels: (True, 1))
    monkeypatch.setattr(
        digitize_module,
        "build_curve_mask",
        lambda plot_pixels, text_mask=None: np.ones((20, 20), dtype=np.uint8) * 255,
    )
    monkeypatch.setattr(
        digitize_module,
        "select_primary_curve_mask",
        lambda curve_mask, plot_pixels=None: curve_mask,
    )
    monkeypatch.setattr(
        digitize_module,
        "extract_curve_trace",
        lambda primary_curve_mask, plot_pixels=None, obstacle_mask=None: (
            np.asarray(list(range(24)), dtype=float),
            np.asarray(list(range(24)), dtype=float),
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "compute_overlay_similarity",
        lambda curve_mask, curve_columns, curve_rows: 0.05,
    )
    monkeypatch.setattr(
        digitize_module,
        "process_curve",
        lambda x_values, y_values, **kwargs: (
            np.asarray([2.0, 12.0], dtype=float),
            np.asarray([10.0, 20.0], dtype=float),
            np.asarray([0.0, 0.0], dtype=float),
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

    output_dir = tmp_path / "digitize"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = digitize_figure(image_path, output_dir, settings)

    assert result.status == "accepted"
    assert "Multiple series detected" in result.flagged_reasons


def test_digitize_requires_review_without_confirmed_x_axis(monkeypatch, tmp_path):
    from PIL import Image
    from pxrd_fetcher import digitize as digitize_module

    class FakeBackend:
        name = "fake"

        def detect_tokens(self, image, *, min_confidence: float = 0.0):
            return []

        def extract_text(self, image, *, min_confidence: float = 0.0):
            return ""

    image_path = tmp_path / "crop.png"
    Image.new("RGB", (20, 20), color="white").save(image_path)

    monkeypatch.setattr(digitize_module, "get_ocr_backend", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr(digitize_module, "detect_plot_bbox", lambda pixels: (0, 0, 20, 20))
    monkeypatch.setattr(
        digitize_module,
        "detect_text_mask",
        lambda plot_pixels, ocr_backend: (np.zeros((20, 20), dtype=np.uint8), []),
    )
    monkeypatch.setattr(digitize_module, "estimate_series_count", lambda plot_pixels: (False, 1))
    monkeypatch.setattr(
        digitize_module,
        "build_curve_mask",
        lambda plot_pixels, text_mask=None: np.ones((20, 20), dtype=np.uint8) * 255,
    )
    monkeypatch.setattr(
        digitize_module,
        "select_primary_curve_mask",
        lambda curve_mask, plot_pixels=None: curve_mask,
    )
    monkeypatch.setattr(
        digitize_module,
        "extract_curve_trace",
        lambda primary_curve_mask, plot_pixels=None, obstacle_mask=None: (
            np.asarray(list(range(24)), dtype=float),
            np.asarray(list(range(24)), dtype=float),
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "compute_overlay_similarity",
        lambda curve_mask, curve_columns, curve_rows: 0.75,
    )
    monkeypatch.setattr(
        digitize_module,
        "process_curve",
        lambda x_values, y_values, **kwargs: (
            np.asarray([0.0, 1.0], dtype=float),
            np.asarray([10.0, 20.0], dtype=float),
            np.asarray([0.0, 0.0], dtype=float),
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

    output_dir = tmp_path / "digitize"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = digitize_figure(image_path, output_dir, settings)

    assert result.status == "review_required"
    assert "Weak x-axis calibration" in result.flagged_reasons


def test_digitize_ai_non_curve_assist_falls_back_cleanly_on_service_error(monkeypatch, tmp_path):
    from PIL import Image
    from pxrd_fetcher import digitize as digitize_module

    class FakeBackend:
        name = "fake"

        def detect_tokens(self, image, *, min_confidence: float = 0.0):
            return []

        def extract_text(self, image, *, min_confidence: float = 0.0):
            return ""

    class FailingAssistService:
        def assist_non_curve_regions(self, **kwargs):
            raise OpenAIServiceError("assist failed")

    image_path = tmp_path / "crop.png"
    Image.new("RGB", (20, 20), color="white").save(image_path)

    monkeypatch.setattr(digitize_module, "get_ocr_backend", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr(digitize_module, "detect_plot_bbox", lambda pixels: (0, 0, 20, 20))
    monkeypatch.setattr(
        digitize_module,
        "extract_axis_entries",
        lambda pixels, plot_bbox, axis, ocr_backend: (
            [(0.0, 2.0), (19.0, 22.0)] if axis == "x" else [(0.0, 100.0), (19.0, 0.0)]
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "detect_text_mask",
        lambda plot_pixels, ocr_backend: (np.zeros((20, 20), dtype=np.uint8), []),
    )
    monkeypatch.setattr(digitize_module, "estimate_series_count", lambda plot_pixels: (True, 1))
    monkeypatch.setattr(
        digitize_module,
        "build_curve_mask",
        lambda plot_pixels, text_mask=None: np.ones((20, 20), dtype=np.uint8) * 255,
    )
    monkeypatch.setattr(
        digitize_module,
        "select_primary_curve_mask",
        lambda curve_mask, plot_pixels=None: curve_mask,
    )
    monkeypatch.setattr(
        digitize_module,
        "extract_curve_trace",
        lambda primary_curve_mask, plot_pixels=None, obstacle_mask=None: (
            np.asarray(list(range(24)), dtype=float),
            np.asarray(list(range(24)), dtype=float),
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "compute_overlay_similarity",
        lambda curve_mask, curve_columns, curve_rows: 0.4,
    )
    monkeypatch.setattr(
        digitize_module,
        "process_curve",
        lambda x_values, y_values, **kwargs: (
            np.asarray([2.0, 12.0], dtype=float),
            np.asarray([10.0, 20.0], dtype=float),
            np.asarray([0.0, 0.0], dtype=float),
            [],
        ),
    )
    monkeypatch.setattr(
        digitize_module,
        "detect_non_curve_boxes",
        lambda *args, **kwargs: ([], np.zeros((20, 20), dtype=np.uint8)),
    )

    settings = Settings(
        CHATGPT_API_KEY="x",
        CHATGPT_MODEL="gpt-5.4-2026-03-05",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "db.sqlite",
    )

    output_dir = tmp_path / "digitize"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = digitize_figure(
        image_path,
        output_dir,
        settings,
        openai_service=FailingAssistService(),
    )

    assert result.status == "accepted"
    assert result.ai_non_curve_assisted is False
    assert result.ai_non_curve_regions == []
