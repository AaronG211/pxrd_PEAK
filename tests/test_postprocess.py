import numpy as np

from pxrd_fetcher.postprocess import (
    align_intensity_to_x_grid,
    baseline_asls,
    detect_pxrd_peaks,
    interpolate_curve,
    normalize_intensity,
    process_curve,
)


def test_baseline_correction_and_normalization():
    x = np.linspace(2, 30, 500)
    baseline = 10 + 0.05 * x
    peaks = 30 * np.exp(-((x - 6.8) ** 2) / 0.08) + 45 * np.exp(-((x - 12.4) ** 2) / 0.12)
    y = baseline + peaks

    estimated = baseline_asls(y)
    normalized = normalize_intensity(y - estimated)

    assert estimated.shape == y.shape
    assert float(normalized.max()) == 100.0


def test_process_curve_detects_expected_peak_region():
    x = np.linspace(2, 30, 500)
    y = 5 + 60 * np.exp(-((x - 6.8) ** 2) / 0.04) + 35 * np.exp(-((x - 12.5) ** 2) / 0.09)

    grid_x, smoothed, baseline, peaks = process_curve(
        x,
        y,
        step_deg=0.02,
        smoothing_window=11,
        smoothing_polyorder=3,
    )

    assert len(grid_x) == len(smoothed)
    assert len(baseline) == len(y)
    assert any(abs((peak.two_theta_deg or 0.0) - 6.8) < 0.3 for peak in peaks)


def test_detect_pxrd_peaks_returns_highest_peaks_first():
    x = np.linspace(2, 20, 200)
    y = 90 * np.exp(-((x - 4.5) ** 2) / 0.05) + 40 * np.exp(-((x - 10.0) ** 2) / 0.1)

    peaks = detect_pxrd_peaks(x, y)

    assert peaks
    assert abs((peaks[0].two_theta_deg or 0.0) - 4.5) < 0.3


def test_interpolate_curve_caps_pathological_point_counts():
    x = np.asarray([0.0, 1_000_000.0], dtype=float)
    y = np.asarray([0.0, 100.0], dtype=float)

    grid_x, grid_y = interpolate_curve(x, y, step_deg=0.02)

    assert len(grid_x) == 20_000
    assert len(grid_y) == 20_000
    assert grid_x[0] == 0.0
    assert grid_x[-1] == 1_000_000.0


def test_align_intensity_to_x_grid_preserves_raw_trend_on_processed_grid():
    source_x = np.asarray([5.0, 10.0, 15.0], dtype=float)
    source_y = np.asarray([90.0, 60.0, 30.0], dtype=float)
    target_x = np.asarray([5.0, 7.5, 10.0, 12.5, 15.0], dtype=float)

    aligned = align_intensity_to_x_grid(source_x, source_y, target_x)

    assert aligned.tolist() == [90.0, 75.0, 60.0, 45.0, 30.0]
