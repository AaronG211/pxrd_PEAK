"""Regression tests for pipeline helpers fixed after the July 2026 review.

Each test encodes a bug that shipped: anchors paired by list index (mislabeling
curves when the model skips one), and the 2θ output window derived from CV tick
pixels even when the chosen calibration came from OCR (spurious ticks cropping
correctly-calibrated data).
"""

from __future__ import annotations

from pxrd_fetcher.v2.calibrate import fit_from_pairs
from pxrd_fetcher.v2.pipeline import _match_anchors_to_specs, _two_theta_window
from pxrd_fetcher.v2.schemas import (
    AnchorPoint,
    CalibrationEvidence,
    CurveAnchor,
    CurveSpec,
)


def _anchor(order: int) -> CurveAnchor:
    return CurveAnchor(
        order_from_top=order,
        points=[AnchorPoint(x_frac=0.1, y_frac=0.5), AnchorPoint(x_frac=0.9, y_frac=0.5)],
    )


def _spec(order: int, label: str) -> CurveSpec:
    return CurveSpec(order_from_top=order, label=label, color="black")


class TestMatchAnchorsToSpecs:
    def test_skipped_middle_curve_does_not_shift_identities(self):
        """Model returns anchors for curves 1 and 3 only: curve 3's anchor must
        keep curve 3's identity, not inherit curve 2's (the index-zip bug)."""
        anchors = [_anchor(1), _anchor(3)]
        specs = [_spec(1, "pristine"), _spec(2, "treated"), _spec(3, "simulated-ref")]
        matched = _match_anchors_to_specs(anchors, specs)
        assert matched[0][1].label == "pristine"
        assert matched[1][1].label == "simulated-ref"

    def test_unknown_anchor_order_gets_no_spec(self):
        anchors = [_anchor(5)]
        specs = [_spec(1, "a"), _spec(2, "b")]
        matched = _match_anchors_to_specs(anchors, specs)
        assert matched[0][1] is None

    def test_duplicate_anchor_orders_do_not_share_a_spec(self):
        anchors = [_anchor(1), _anchor(1)]
        specs = [_spec(1, "a"), _spec(2, "b")]
        matched = _match_anchors_to_specs(anchors, specs)
        assert matched[0][1].label == "a"
        assert matched[1][1] is None


class TestTwoThetaWindow:
    def test_ocr_backed_fit_ignores_disagreeing_ticks(self):
        """Three spurious CVticks failed pairing; the chosen fit is OCR-based.
        The window must come from the OCR support, not the spurious ticks."""
        frame = (0, 0, 1000, 800)
        ocr_pairs = [(50.0, 5.0), (500.0, 55.0), (950.0, 105.0)]
        ocr_fit = fit_from_pairs([p for p, _ in ocr_pairs],
                                 [v for _, v in ocr_pairs], "ocr_pairs")
        calib = CalibrationEvidence(
            tick_pixels=[300.0, 500.0, 700.0],   # spurious interior strokes
            ocr_pairs=ocr_pairs,
            candidates=[ocr_fit],
            fit=ocr_fit,
        )
        lo, hi = _two_theta_window(calib, frame)
        # spurious ticks would crop to ~[32.5, 77.5]; OCR support keeps ~5-105
        assert lo < 10.0
        assert hi > 100.0

    def test_agreeing_tick_fit_still_contributes(self):
        frame = (0, 0, 1000, 800)
        ticks = [100.0, 300.0, 500.0, 700.0, 900.0]
        cv_fit = fit_from_pairs(ticks, [10.0, 20.0, 30.0, 40.0, 50.0],
                                "cv_ticks+llm_values")
        calib = CalibrationEvidence(
            tick_pixels=ticks, ocr_pairs=[], candidates=[cv_fit], fit=cv_fit,
        )
        lo, hi = _two_theta_window(calib, frame)
        assert 0.2 <= lo < 10.0
        assert 50.0 < hi <= 65.0
