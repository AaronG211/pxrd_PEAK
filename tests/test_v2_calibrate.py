"""Unit tests for v2 consensus calibration."""

import numpy as np
import pytest

from pxrd_fetcher.v2.calibrate import (
    consensus_calibration,
    fit_from_pairs,
    pair_ticks_with_values,
    plausible,
)
from pxrd_fetcher.v2.schemas import AxisFit
from pxrd_fetcher.v2.vision import _regularize_tick_grid

FRAME = (20, 10, 620, 410)  # 600 px wide plot


def _pixels(start, stop, n):
    return list(np.linspace(start, stop, n))


class TestPairing:
    def test_exact_count_pairs_one_to_one(self):
        px = _pixels(20, 620, 9)
        vals = [5, 10, 15, 20, 25, 30, 35, 40, 45]
        paired = pair_ticks_with_values(px, vals)
        assert paired is not None
        assert paired[0] == px
        assert paired[1] == vals

    def test_extra_cv_ticks_extends_arithmetic_values(self):
        # CV found 11 marks (incl. minor), LLM read 6 labels with step 10
        px = _pixels(20, 620, 11)
        vals = [0, 10, 20, 30, 40, 50]
        paired = pair_ticks_with_values(px, vals)
        assert paired is not None
        assert len(paired[0]) == len(paired[1]) == 11

    def test_unpairable_returns_none(self):
        assert pair_ticks_with_values([10, 50], [1, 2, 3, 4, 5, 6, 7]) is None


class TestFit:
    def test_outlier_is_rejected(self):
        px = _pixels(20, 620, 7)
        vals = [5, 10, 15, 20, 25, 30, 35]
        vals[3] = 99.0  # corrupted OCR read
        fit = fit_from_pairs(px, vals, "test")
        assert fit is not None
        assert fit.rmse_deg < 0.1

    def test_plausibility_rejects_reversed_axis(self):
        fit = AxisFit(slope=-0.05, intercept=50, n_points=5, rmse_deg=0.0, method="t")
        assert not plausible(fit, FRAME)

    def test_plausibility_rejects_huge_span(self):
        fit = AxisFit(slope=1.0, intercept=0, n_points=5, rmse_deg=0.0, method="t")
        assert not plausible(fit, FRAME)  # 600 deg span


class TestConsensus:
    def test_two_agreeing_sources_give_consensus(self):
        px = _pixels(20, 620, 9)
        vals = [5, 10, 15, 20, 25, 30, 35, 40, 45]
        ocr = list(zip(px, vals))
        ev = consensus_calibration(
            frame=FRAME, tick_pixels=px, llm_tick_values=vals, ocr_pairs=ocr
        )
        assert ev.status == "consensus"
        assert ev.fit is not None
        assert ev.agreement_deg < 0.01

    def test_disagreeing_sources_fail_loudly(self):
        px = _pixels(20, 620, 9)
        vals = [5, 10, 15, 20, 25, 30, 35, 40, 45]
        wrong_ocr = list(zip(px, [v * 2 for v in vals]))  # OCR misread scale
        ev = consensus_calibration(
            frame=FRAME, tick_pixels=px, llm_tick_values=vals, ocr_pairs=wrong_ocr
        )
        assert ev.status == "disagreement"
        assert ev.fit is None

    def test_strong_single_source_accepted(self):
        px = _pixels(20, 620, 9)
        vals = [5, 10, 15, 20, 25, 30, 35, 40, 45]
        ev = consensus_calibration(
            frame=FRAME, tick_pixels=px, llm_tick_values=vals, ocr_pairs=[]
        )
        assert ev.status == "single_method"
        assert ev.fit is not None

    def test_no_evidence_fails(self):
        ev = consensus_calibration(
            frame=FRAME, tick_pixels=[], llm_tick_values=[], ocr_pairs=[]
        )
        assert ev.status == "failed"
        assert ev.fit is None


class TestTickGrid:
    def test_missing_ticks_are_filled(self):
        full = list(np.linspace(100, 900, 9))
        detected = [p for i, p in enumerate(full) if i not in (3, 6)]
        regular = _regularize_tick_grid(detected)
        assert len(regular) == 9
        np.testing.assert_allclose(regular, full, atol=1.0)

    def test_irregular_spacing_is_discarded(self):
        assert _regularize_tick_grid([100, 130, 300, 310, 700]) == []

    def test_too_few_ticks_discarded(self):
        assert _regularize_tick_grid([100, 200]) == []


class TestMinorTickPairing:
    def test_minor_ticks_resolved_by_ocr_points(self):
        # 9 tick marks but only 5 printed labels (minor ticks halve the step):
        px = list(np.linspace(100, 884, 9))
        vals = [5, 15, 25, 35, 45]
        # OCR caught just two labels — enough to arbitrate, not to fit alone
        ocr = [(100.0, 5.0), (296.0, 15.0)]
        ev = consensus_calibration(
            frame=(80, 10, 900, 410), tick_pixels=px, llm_tick_values=vals,
            ocr_pairs=ocr,
        )
        assert ev.fit is not None
        # slope must reflect 10 deg per 2 ticks => 5 deg per 98px
        assert abs(ev.fit.slope - (10 / 196.0)) < 0.002

    def test_ambiguous_without_ocr_fails_safely(self):
        px = list(np.linspace(100, 884, 8))   # 8 ticks
        vals = [10, 20, 30, 40]               # 4 labels — k=2 has two offsets
        ev = consensus_calibration(
            frame=(80, 10, 900, 410), tick_pixels=px, llm_tick_values=vals,
            ocr_pairs=[],
        )
        assert ev.fit is None
