"""LLM semantic layer for the v2 pipeline.

The model is asked only for things models are reliably good at: finding
panels, reading printed numbers/labels, counting and naming curves, and
sketching coarse curve shapes. Pixel precision is never demanded — the CV
layer owns that. Reuses the v1 OpenAIService for retry + disk caching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ..openai_service import OpenAIService, OpenAIServiceError
from ..utils import encode_image_object_to_data_url, encode_image_to_data_url
from .schemas import (
    CalibrationArbitration,
    CurveAnchorSet,
    FigureAnalysis,
    PagePanels,
    TickReading,
)

__all__ = ["VisionLLM", "OpenAIServiceError"]


class VisionLLM:
    def __init__(self, service: OpenAIService):
        self._svc = service

    # -- Stage 1: locate PXRD panels on a rendered page --------------------

    def locate_panels(self, page_image_path: Path, page_text: str) -> PagePanels:
        prompt = (
            "Find every powder X-ray diffraction (PXRD/XRD) plot panel on this page.\n"
            "A PXRD panel is a 2D line plot whose x-axis is 2-theta (2θ, degrees, "
            "Bragg angle). Include each individual panel of multi-panel figures "
            "separately. EXCLUDE: SEM/TEM images, photographs, adsorption isotherms, "
            "IR/Raman/NMR/UV-Vis spectra, TGA curves, schematics, and structures.\n\n"
            "For each panel return its bounding box in FRACTIONS of the page "
            "(x0,y0 top-left, x1,y1 bottom-right, 0..1). The box must include the "
            "full plot frame plus its axis tick labels and axis titles, but as "
            "little surrounding content as possible. Also return the panel letter "
            "label if printed (a, b, c...).\n\n"
            f"Page text excerpt (may help identify figures):\n{(page_text or '')[:2500]}"
        )
        return self._svc._parse_response(
            PagePanels,
            system_prompt=(
                "You locate PXRD diffraction plot panels on scientific paper pages. "
                "Return strict JSON. Fractional coordinates, full axis labels included "
                "in each box. If the page has no PXRD plot, return an empty list."
            ),
            user_text=prompt,
            image_data_url=encode_image_to_data_url(page_image_path),
        )

    # -- Stage 2: one-shot deep analysis of one panel crop ------------------

    def analyze_figure(self, crop, caption: str, context: str) -> FigureAnalysis:
        prompt = (
            "Analyze this figure crop from a chemistry paper.\n\n"
            "1. is_pxrd: is it a powder XRD diffractogram (x-axis = 2θ in degrees)? "
            "Plots of adsorption, spectroscopy, TGA etc. are NOT PXRD.\n"
            "1b. plot_box: the bounding box of the PXRD plot's AXES FRAME only "
            "(the rectangle delimited by the axis lines, excluding tick labels, "
            "axis titles, and any neighboring drawings/panels), in fractions of "
            "this image (x0,y0 top-left to x1,y1 bottom-right).\n"
            "2. curves: list every REAL diffraction trace, ordered top to bottom at "
            "their left end. For each: the printed label exactly as written (legend "
            "or inline text), material name, sample state (experimental/simulated/"
            "calcined/...), and its line color as a simple English word. "
            "Mark stick/bar reference patterns with is_stick_pattern=true. "
            "Do NOT count Bragg-position tick rows or axis lines as curves.\n"
            "3. x_tick_values: ALL numeric tick labels printed along the x-axis, "
            "left to right, exactly as printed (e.g. [5,10,15,20,25,30]).\n"
            "4. x_axis_title / y_axis_title exactly as printed; x_unit_is_degrees.\n"
            "5. grayscale_only: true when all curves are black/gray.\n"
            "6. has_inset: true if a smaller plot is embedded inside.\n\n"
            f"Caption: {caption or 'N/A'}\n"
            f"Context: {(context or 'N/A')[:1500]}"
        )
        return self._svc._parse_response(
            FigureAnalysis,
            system_prompt=(
                "You analyze PXRD figure crops. Return strict JSON. Read tick labels "
                "and curve labels EXACTLY as printed; never invent values that are "
                "not visible. Curves ordered top to bottom."
            ),
            user_text=prompt,
            image_data_url=encode_image_object_to_data_url(crop),
        )

    # -- Stage 3: read the x-axis strip (calibration values) ----------------

    def read_axis_strip(self, strip_image: np.ndarray) -> TickReading:
        prompt = (
            "This image is the x-axis region of a PXRD plot (bottom axis strip, "
            "magnified). Read the numeric tick labels from LEFT to RIGHT and return "
            "them in order in tick_values. Return every visible numeric label, "
            "including the first and last. Also return the axis title text."
        )
        return self._svc._parse_response(
            TickReading,
            system_prompt=(
                "You read axis tick labels from magnified plot images. Return strict "
                "JSON. Only report numbers actually visible; preserve order."
            ),
            user_text=prompt,
            image_data_url=encode_image_object_to_data_url(strip_image),
        )

    # -- Stage 4: coarse anchor polylines ------------------------------------

    def trace_anchors(
        self,
        grid_image: np.ndarray,
        curves_description: str,
        n_curves: int,
    ) -> CurveAnchorSet:
        prompt = (
            "This PXRD figure crop has a red 10x10 reference grid drawn over the "
            "plot box; the grid spans the plot interior with fractional labels 0.0 "
            "to 1.0 on both axes (x: left→right, y: TOP→bottom, so y=0.0 is the top "
            "edge of the plot box and y=1.0 the bottom edge).\n\n"
            f"Trace ALL {n_curves} diffraction curve(s), ordered top to bottom:\n"
            f"{curves_description}\n\n"
            "For each curve return a polyline of 40-80 points (x_frac, y_frac) "
            "following the visible trace from its left end to its right end. "
            "CRITICAL accuracy requirements:\n"
            "- Use the red gridlines to estimate coordinates precisely.\n"
            "- Capture every visible peak: at each sharp peak place a point at the "
            "apex plus points on both flanks.\n"
            "- Between peaks follow the baseline level of THAT curve (stacked "
            "curves have different baseline heights — do not drift to a neighbor).\n"
            "- Densify points around peaks; sparse points are fine on flat regions."
        )
        return self._svc._parse_response(
            CurveAnchorSet,
            system_prompt=(
                "You digitize curve shapes from plots into fractional-coordinate "
                "polylines. Return strict JSON. y_frac is measured DOWNWARD from "
                "the top of the plot box. Peaks must not be skipped."
            ),
            user_text=prompt,
            image_data_url=encode_image_object_to_data_url(grid_image),
        )

    # -- Stage 5: arbitration when calibration is ambiguous ------------------

    def arbitrate_axis(
        self,
        strip_image: np.ndarray,
        candidates_desc: str,
    ) -> CalibrationArbitration:
        prompt = (
            "This is a magnified x-axis strip of a PXRD plot. Our automatic "
            "calibration produced conflicting readings:\n"
            f"{candidates_desc}\n\n"
            "Read the axis carefully and report: the numeric value of the FIRST "
            "(leftmost) tick label, the LAST (rightmost) tick label, and the full "
            "ordered list of tick label values. Only report what is printed."
        )
        return self._svc._parse_response(
            CalibrationArbitration,
            system_prompt=(
                "You resolve conflicting axis readings by carefully reading "
                "magnified tick labels. Return strict JSON."
            ),
            user_text=prompt,
            image_data_url=encode_image_object_to_data_url(strip_image),
        )
