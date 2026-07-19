"""v2 fully-automatic pipeline: PDF in, trusted curves or explicit rejections out.

No human-review state exists. Every decision is backed by quantified evidence
recorded in the per-figure result JSON.
"""

from __future__ import annotations

import csv
import functools
import json
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from ..config import Settings
from ..ocr import OCRBackend, get_ocr_backend
from ..openai_service import OpenAIService, OpenAIServiceError
from ..utils import derive_paper_id, detect_doi_like_name, ensure_dir
from .calibrate import (
    _AGREEMENT_TOL_DEG,
    apply_arbitration,
    consensus_calibration,
    fits_agree,
)
from .curves import curve_is_computed
from .extract import build_plot_mask, extract_series
from .llm import VisionLLM
from .report import write_html_report
from .schemas import (
    CalibrationEvidence,
    CurveAnchor,
    FigureAnalysis,
    FigureResult,
    PaperResult,
    RunReport,
    SeriesResult,
)
from .vision import (
    BBox,
    detect_plot_frame,
    detect_x_tick_pixels,
    draw_grid_overlay,
    finalize_frame,
    render_overlay,
    select_plot_frame,
)

print = functools.partial(print, flush=True)  # progress must survive pipes

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_PAGE_LLM_MAX_DIM = 1600   # page images are downscaled before the locate call


class AutoPipelineError(RuntimeError):
    pass


def run_auto(target: Path, settings: Settings, *, out_name: Optional[str] = None) -> RunReport:
    if not target.exists():
        raise AutoPipelineError(f"Input path does not exist: {target}")

    pdfs = [target] if target.is_file() else sorted(target.rglob("*.pdf"))
    if not pdfs:
        raise AutoPipelineError("No PDF files found.")

    run_dir = ensure_dir(
        settings.resolve_output_dir() / (out_name or f"auto-{time.strftime('%Y%m%d-%H%M%S')}")
    )
    # Vision-reasoning calls (panel location, anchor tracing) routinely exceed
    # the v1 default of 60s; a short timeout turns slow successes into retries.
    if settings.openai_timeout_seconds < 240.0:
        settings.openai_timeout_seconds = 240.0
    service = OpenAIService(settings)
    llm = VisionLLM(service)
    ocr = get_ocr_backend(settings.ocr_backend)

    report = RunReport(run_dir=str(run_dir))
    for pdf_path in pdfs:
        print(f"[auto] paper: {pdf_path.name}")
        paper = process_paper(pdf_path, run_dir, llm, ocr, settings)
        report.papers.append(paper)

    (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
    write_html_report(report, run_dir / "report.html")
    _write_master_csv(report, run_dir / "accepted_series.csv")
    print(
        f"[auto] done: {report.n_accepted} figures accepted, "
        f"{report.n_rejected} rejected -> {run_dir}"
    )
    return report


# --------------------------------------------------------------------------


def process_paper(
    pdf_path: Path,
    run_dir: Path,
    llm: VisionLLM,
    ocr: OCRBackend,
    settings: Settings,
) -> PaperResult:
    import fitz

    paper_id = derive_paper_id(pdf_path)
    paper_dir = ensure_dir(run_dir / paper_id)
    doc = fitz.open(pdf_path)
    paper = PaperResult(
        paper_id=paper_id,
        source_path=str(pdf_path),
        doi=detect_doi_like_name(pdf_path),
        title=_first_title(doc),
    )

    for page_index, page in enumerate(doc):
        page_no = page_index + 1
        page_small = _render_page(page, dpi=150)
        if max(page_small.size) > _PAGE_LLM_MAX_DIM:
            page_small, _ = _downscale(page_small, _PAGE_LLM_MAX_DIM)
        page_text = page.get_text("text")
        small_path = paper_dir / f"page-{page_no:03d}.png"
        page_small.save(small_path)

        try:
            panels = llm.locate_panels(small_path, page_text)
        except OpenAIServiceError as exc:
            print(f"  [warn] page {page_no} locate failed: {exc}")
            continue
        if not panels.panels:
            small_path.unlink(missing_ok=True)
            continue
        print(f"  page {page_no}: {len(panels.panels)} panel(s)")

        for p_idx, panel in enumerate(panels.panels, start=1):
            figure_id = f"{paper_id}-p{page_no:03d}-f{p_idx:02d}"
            fig_dir = ensure_dir(paper_dir / figure_id)
            t0 = time.time()
            crop, bbox, clip_pts = _render_panel(page, panel)
            crop_path = fig_dir / "crop.png"
            crop.save(crop_path)

            result = FigureResult(
                figure_id=figure_id,
                paper_id=paper_id,
                page_number=page_no,
                panel_bbox=bbox,
                crop_path=str(crop_path),
                figure_label=panel.panel_label,
            )
            try:
                _process_figure(result, crop, fig_dir, llm, ocr, settings,
                                page_text, page=page, clip_pts=clip_pts)
                # A reframe must never turn an otherwise-usable figure into a
                # rejection: if expanding the crop pulled in a neighbouring
                # panel and broke extraction, fall back to the original tight
                # crop and process it straight (no reframe).
                if result.status == "rejected" and result.panel_bbox != bbox:
                    crop.save(crop_path)
                    fallback = FigureResult(
                        figure_id=figure_id, paper_id=paper_id, page_number=page_no,
                        panel_bbox=bbox, crop_path=str(crop_path),
                        figure_label=panel.panel_label,
                    )
                    _process_figure(fallback, crop, fig_dir, llm, ocr, settings,
                                    page_text)
                    if fallback.status != "rejected":
                        result = fallback
            except OpenAIServiceError as exc:
                result.status = "rejected"
                result.reject_reason = f"LLM unavailable: {exc}"
            except Exception as exc:  # noqa: BLE001 — one figure must never kill a run
                result.status = "rejected"
                result.reject_reason = f"internal error: {exc}"
            result.elapsed_seconds = round(time.time() - t0, 2)
            (fig_dir / "result.json").write_text(result.model_dump_json(indent=2))
            paper.figures.append(result)
            print(
                f"    {figure_id}: {result.status}"
                + (f" ({result.reject_reason})" if result.reject_reason else
                   f" conf={result.confidence:.2f} series={sum(1 for s in result.series if s.status=='accepted')}")
            )

    doc.close()
    return paper


def _process_figure(
    result: FigureResult,
    crop: Image.Image,
    fig_dir: Path,
    llm: VisionLLM,
    ocr: OCRBackend,
    settings: Settings,
    page_text: str,
    page=None,
    clip_pts: Optional[Tuple[float, float, float, float]] = None,
) -> None:
    # -- 1. Deep analysis ---------------------------------------------------
    analysis = llm.analyze_figure(crop, caption="", context=page_text[:1500])
    result.analysis = analysis
    if not analysis.is_pxrd:
        result.status = "rejected"
        result.reject_reason = f"not PXRD: {analysis.reason}"
        return

    # -- 1b. Reframe a clipped / offset crop straight from the PDF ----------
    # locate_panels sometimes returns a box that is shifted off the real plot:
    # it cuts the target frame on one side (truncating high-2θ data) and pulls
    # in a neighbouring panel on the other. The LLM plot_box tells us where the
    # axes frame actually sits inside the crop; when it touches a crop edge or
    # leaves a huge opposite margin we re-render from the PDF, recentred on the
    # frame with proper label margins, then re-read the corrected crop. Bounded
    # to 2 passes so a genuinely edge-filling plot can't loop.
    if page is not None and clip_pts is not None:
        for _ in range(2):
            pb = analysis.plot_box
            if pb is None or not _needs_reframe(pb):
                break
            crop, bbox, clip_pts = _render_region(page, _reframe_clip(clip_pts, pb))
            crop.save(fig_dir / "crop.png")
            result.panel_bbox = bbox
            analysis = llm.analyze_figure(crop, caption="", context=page_text[:1500])
            result.analysis = analysis
            if not analysis.is_pxrd:
                result.status = "rejected"
                result.reject_reason = f"not PXRD after reframe: {analysis.reason}"
                return

    crop_rgb = np.asarray(crop.convert("RGB"))
    # Only measured experimental traces are digitised. Stick/Bragg rows and
    # model-derived curves (simulated, calculated, Pawley/Rietveld refined,
    # difference residuals) are not real data, so they are dropped before
    # tracing — never extracted, never counted in the figure decision.
    real_curves = [
        c for c in analysis.curves
        if not c.is_stick_pattern
        and not curve_is_computed(c.sample_state, c.label, c.material_name)
    ]
    if not real_curves:
        result.status = "rejected"
        result.reject_reason = "no traceable curves (only sticks / simulated / none)"
        return

    # -- 2+3. Plot frame and consensus calibration ---------------------------
    # Frame candidates come from independent strategies; the first one whose
    # calibration clears the trust bar wins. The bar itself never moves.
    frame_candidates: List[BBox] = []
    primary = choose_frame(crop_rgb, analysis, ocr)
    if primary is not None:
        frame_candidates.append(primary)
    hint = _hint_box(analysis)
    if analysis.plot_box is not None:
        alt = _refine_frame_near(crop_rgb, analysis.plot_box)
        if alt is not None:
            alt = finalize_frame(crop_rgb, alt, hint=hint)
            if alt not in frame_candidates:
                frame_candidates.append(alt)
    whole = detect_plot_frame(crop_rgb)
    if whole is not None:
        whole = finalize_frame(crop_rgb, whole, hint=hint)
        if whole not in frame_candidates:
            frame_candidates.append(whole)
    if not frame_candidates:
        result.status = "rejected"
        result.reject_reason = "plot frame not detectable"
        return

    frame = frame_candidates[0]
    calib = None
    for cand in frame_candidates:
        tick_px = detect_x_tick_pixels(crop_rgb, cand)
        ocr_pairs = _ocr_axis_pairs(crop_rgb, cand, ocr)
        attempt = consensus_calibration(
            frame=cand,
            tick_pixels=tick_px,
            llm_tick_values=analysis.x_tick_values,
            ocr_pairs=ocr_pairs,
        )
        if calib is None or (attempt.fit is not None and calib.fit is None):
            frame, calib = cand, attempt
        if attempt.fit is not None and attempt.status == "consensus":
            frame, calib = cand, attempt
            break
        if attempt.fit is not None:
            break
    if calib.fit is None and calib.status in ("disagreement", "weak_single", "failed"):
        calib = _retry_calibration(crop_rgb, frame, calib, llm)
    result.calibration = calib
    result.frame_bbox = tuple(frame)
    if calib.fit is None:
        result.status = "rejected"
        result.reject_reason = f"calibration not trusted ({calib.status}: {'; '.join(calib.notes)})"
        return
    x0, y0, x1, y1 = frame

    # -- 4. Anchor polylines --------------------------------------------------
    grid_img = draw_grid_overlay(crop_rgb, frame)
    curves_desc = "\n".join(
        f"  {i+1}. [{c.color}] {c.label or c.material_name or 'unnamed'}"
        + (f" ({c.sample_state})" if c.sample_state else "")
        for i, c in enumerate(real_curves)
    )
    anchors = llm.trace_anchors(grid_img, curves_desc, len(real_curves))
    if not anchors.curves:
        result.status = "rejected"
        result.reject_reason = "model returned no anchor polylines"
        return

    # -- 5. Snap, verify, decide per series -----------------------------------
    plot_rgb = crop_rgb[y0:y1, x0:x1]
    text_boxes = _ocr_text_boxes_in_plot(plot_rgb, ocr)
    plot_mask = build_plot_mask(plot_rgb, text_boxes)

    matched = _match_anchors_to_specs(anchors.curves, real_curves)
    traces_for_overlay = []
    window = _two_theta_window(calib, frame)
    for s_idx, (anchor, spec) in enumerate(matched, start=1):
        series_id = f"{result.figure_id}-s{s_idx:02d}"
        series, raw_y_px = extract_series(
            plot_rgb, anchor, spec, calib.fit, plot_mask,
            frame_x0=x0, series_id=series_id,
            two_theta_window=window,
            interpolation_step_deg=settings.interpolation_step_deg,
            smoothing_window=settings.smoothing_window,
            smoothing_polyorder=settings.smoothing_polyorder,
        )
        series.anchor_points = [(p.x_frac, p.y_frac) for p in anchor.points]
        if series.status == "accepted":
            csv_path = fig_dir / f"{series_id}.csv"
            _write_series_csv(csv_path, result, series)
            series.csv_path = str(csv_path)
        if raw_y_px is not None:
            tag = "" if series.status == "accepted" else " [REJECTED]"
            traces_for_overlay.append((raw_y_px, (series.label or series.series_id) + tag))
        result.series.append(series)

    # Curves the model failed to trace must count against the figure rather
    # than vanish silently. Matching is by order_from_top value, so "missing"
    # means a spec no anchor claimed — not a positional count difference.
    matched_spec_ids = {id(spec) for _, spec in matched if spec is not None}
    s_next = len(matched)
    for spec in real_curves:
        if id(spec) in matched_spec_ids:
            continue
        s_next += 1
        result.series.append(SeriesResult(
            series_id=f"{result.figure_id}-s{s_next:02d}",
            label=spec.label,
            color=spec.color,
            status="rejected",
            reject_reason="model returned no anchor polyline for this curve",
        ))

    # -- 6. Figure-level decision ----------------------------------------------
    accepted = [s for s in result.series if s.status == "accepted"]
    overlay_path = fig_dir / "overlay.png"
    if traces_for_overlay:
        render_overlay(crop_rgb, frame, traces_for_overlay, str(overlay_path))
        result.overlay_path = str(overlay_path)

    if not accepted:
        result.status = "rejected"
        result.reject_reason = "all series failed fidelity verification"
        return
    result.status = "accepted" if len(accepted) == len(result.series) else "partial"
    result.confidence = round(float(np.mean([s.confidence for s in accepted])), 4)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _render_page(page, dpi: int) -> Image.Image:
    import fitz

    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def _downscale(image: Image.Image, max_dim: int) -> Tuple[Image.Image, float]:
    w, h = image.size
    if max(w, h) <= max_dim:
        return image, 1.0
    ratio = max_dim / max(w, h)
    return image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS), 1.0 / ratio


_PANEL_TARGET_WIDTH = 1500   # render each panel near this pixel width
_PANEL_MIN_DPI = 220
_PANEL_MAX_DPI = 900


_REFRAME_EDGE = 0.03    # plot_box within this of a crop edge ⇒ frame is clipped
_REFRAME_MARGIN = 0.42  # plot_box leaves this much empty on one side ⇒ offset/neighbour


def _render_region(page, clip_pts: Tuple[float, float, float, float]
                   ) -> Tuple[Image.Image, BBox, Tuple[float, float, float, float]]:
    """Render an arbitrary page region (PDF points) at adaptive DPI.

    Small panels rendered as part of a full 300dpi page are too coarse for
    tick marks and tick-label OCR; clipping at the PDF level lets every panel
    arrive at working resolution. Returns the image, its 300dpi-page bbox for
    traceability, and the clamped clip in PDF points (so a reframe pass can
    re-derive page coordinates from the next plot_box).
    """

    import fitz

    rect = page.rect
    clip = fitz.Rect(
        max(0.0, min(clip_pts[0], rect.width)),
        max(0.0, min(clip_pts[1], rect.height)),
        max(0.0, min(clip_pts[2], rect.width)),
        max(0.0, min(clip_pts[3], rect.height)),
    )
    dpi = 72.0 * _PANEL_TARGET_WIDTH / max(1.0, clip.width)
    dpi = max(_PANEL_MIN_DPI, min(_PANEL_MAX_DPI, dpi))
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    crop = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    # panel_bbox is recorded in 300dpi page coordinates for traceability.
    to300 = 300.0 / 72.0
    bbox = (
        int(clip.x0 * to300), int(clip.y0 * to300),
        int(clip.x1 * to300), int(clip.y1 * to300),
    )
    return crop, bbox, (clip.x0, clip.y0, clip.x1, clip.y1)


def _render_panel(page, panel) -> Tuple[Image.Image, BBox, Tuple[float, float, float, float]]:
    """Initial crop for a located panel: its box plus a fraction of padding, so
    neighbours stay out while slightly-short LLM boxes still get healed."""

    rect = page.rect
    pw, ph = rect.width, rect.height
    bw = max(1e-3, (panel.x1 - panel.x0) * pw)
    bh = max(1e-3, (panel.y1 - panel.y0) * ph)
    pad_x = bw * 0.12
    pad_y = bh * 0.12
    return _render_region(page, (
        panel.x0 * pw - pad_x,
        panel.y0 * ph - pad_y,
        panel.x1 * pw + pad_x,
        panel.y1 * ph + pad_y,
    ))


def _needs_reframe(pb) -> bool:
    """A plot_box worth re-cropping: the frame is shoved off one edge of an
    axis (the opposite side cut) or it leaves a large empty margin on one side
    (offset box / neighbouring panel pulled in).

    A frame clipped on BOTH edges of an axis fills the crop on that axis — it
    is already complete there, and expanding would only engulf neighbours — so
    that is not, by itself, a reason to reframe (XOR, not OR)."""

    e = _REFRAME_EDGE
    x_clip = (pb.x0 <= e) != (pb.x1 >= 1 - e)
    y_clip = (pb.y0 <= e) != (pb.y1 >= 1 - e)
    offset = (pb.x0 >= _REFRAME_MARGIN or pb.y0 >= _REFRAME_MARGIN
              or (1 - pb.x1) >= _REFRAME_MARGIN or (1 - pb.y1) >= _REFRAME_MARGIN)
    return x_clip or y_clip or offset


def _reframe_clip(clip_pts: Tuple[float, float, float, float], pb
                  ) -> Tuple[float, float, float, float]:
    """New clip (PDF points) recentred on the frame the LLM reported.

    A side clipped while its opposite is not expands generously to reveal the
    true axis extent; the other sides tighten to fixed label margins (room for
    tick labels, axis titles and the panel letter) so neighbours are trimmed.
    An axis clipped on both edges already fills the crop, so its extent is left
    untouched rather than blown outward into the neighbouring panels.
    `_render_region` clamps the result to the page."""

    x0p, y0p, x1p, y1p = clip_pts
    W, H = x1p - x0p, y1p - y0p
    fx0, fy0 = x0p + pb.x0 * W, y0p + pb.y0 * H
    fx1, fy1 = x0p + pb.x1 * W, y0p + pb.y1 * H
    fw, fh = max(1.0, fx1 - fx0), max(1.0, fy1 - fy0)

    e = _REFRAME_EDGE
    expand = 0.65                            # reveal clipped axis on this side
    ml, mr, mt, mb = 0.26, 0.14, 0.14, 0.22  # label margins (fraction of frame)

    if pb.x0 <= e and pb.x1 >= 1 - e:        # frame fills width — keep extent
        nx0, nx1 = x0p, x1p
    else:
        left = expand if pb.x0 <= e else ml
        right = expand if pb.x1 >= 1 - e else mr
        nx0, nx1 = fx0 - fw * left, fx1 + fw * right

    if pb.y0 <= e and pb.y1 >= 1 - e:        # frame fills height — keep extent
        ny0, ny1 = y0p, y1p
    else:
        top = expand if pb.y0 <= e else mt
        bot = expand if pb.y1 >= 1 - e else mb
        ny0, ny1 = fy0 - fh * top, fy1 + fh * bot

    return (nx0, ny0, nx1, ny1)


def _two_theta_window(calib: CalibrationEvidence, frame: BBox) -> Optional[Tuple[float, float]]:
    """Trusted 2θ output window derived from where calibration evidence lives.

    Data may extend a bit past the outermost labeled ticks, but a trace far
    beyond them is neighbor-panel content or a hallucinated extension. The
    frame itself is a hard bound: nothing exists outside the drawn axis.
    """

    if calib.fit is None:
        return None
    frame_lo = calib.fit.to_deg(frame[0])
    frame_hi = calib.fit.to_deg(frame[2])

    # Window support must come from evidence that actually backs the CHOSEN
    # fit. Unpaired CV tick pixels can be spurious (legend stubs, interior
    # strokes); when the fit is OCR-based, letting those ticks set the window
    # crops correctly-calibrated data to a wrong sub-range.
    px: List[float] = []
    for cand in calib.candidates:
        if fits_agree(cand, calib.fit, frame) > _AGREEMENT_TOL_DEG:
            continue
        if cand.method == "cv_ticks+llm_values" and len(calib.tick_pixels) >= 2:
            px.extend(calib.tick_pixels)
        elif cand.method == "ocr_pairs" and len(calib.ocr_pairs) >= 2:
            px.extend(p for p, _ in calib.ocr_pairs)
    if calib.fit.method == "arbitrated" and len(calib.tick_pixels) >= 2:
        px.extend(calib.tick_pixels)          # arbitration re-paired the ticks
    px = sorted(set(px))
    if len(px) < 2:
        return (max(0.2, frame_lo), min(130.0, frame_hi))
    gap = float(np.median(np.diff(px))) if len(px) > 2 else (px[-1] - px[0])
    lo = calib.fit.to_deg(px[0] - 1.25 * gap)
    hi = calib.fit.to_deg(px[-1] + 1.25 * gap)
    return (max(0.2, lo, frame_lo), min(130.0, hi, frame_hi))


def choose_frame(crop_rgb: np.ndarray, analysis: FigureAnalysis, ocr: OCRBackend) -> Optional[BBox]:
    """Frame choice chain: evidence-scored selection, then hint-local detect,
    then whole-crop detect. Shared by the pipeline and offline replay tools so
    they can never diverge.

    Candidate frames are scored by axis evidence (ticks, numeric labels,
    curve ink, LLM hint) — long lines alone are meaningless in multi-panel
    crops full of drawings and neighboring plots.
    """

    numeric_tokens = _ocr_numeric_token_centers(crop_rgb, ocr)
    hint = _hint_box(analysis)
    frame = select_plot_frame(crop_rgb, hint=hint, numeric_token_xs=numeric_tokens)
    if frame is None and analysis.plot_box is not None:
        frame = _refine_frame_near(crop_rgb, analysis.plot_box)
    if frame is None:
        frame = detect_plot_frame(crop_rgb)
    if frame is None:
        return None
    # Adapt to the figure's real axis topology: borderless edges are derived
    # from the bottom axis run + LLM hint + tick evidence, never from
    # arbitrary ink extents.
    return finalize_frame(crop_rgb, frame, hint=hint)


def _hint_box(analysis: FigureAnalysis) -> Optional[Tuple[float, float, float, float]]:
    if analysis.plot_box is None:
        return None
    pb = analysis.plot_box
    return (pb.x0, pb.y0, pb.x1, pb.y1)


def _ocr_numeric_token_centers(crop_rgb: np.ndarray, ocr: OCRBackend) -> List[Tuple[float, float]]:
    """Centers of OCR tokens that look numeric — frame-selection evidence."""

    try:
        tokens = ocr.detect_tokens(Image.fromarray(crop_rgb), min_confidence=0.35)
    except Exception:
        return []
    centers: List[Tuple[float, float]] = []
    for tok in tokens:
        text = tok.text.strip().replace("°", "").replace("º", "")
        if not text or sum(ch.isdigit() for ch in text) < max(1, len(text) // 2):
            continue
        x0, y0, x1, y1 = tok.bbox
        centers.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return centers


def _refine_frame_near(crop_rgb: np.ndarray, plot_box) -> Optional[BBox]:
    """Run frame detection inside an expanded neighborhood of the LLM plot box."""

    h, w = crop_rgb.shape[:2]
    mx = int(w * 0.05)
    my = int(h * 0.05)
    rx0 = max(0, int(plot_box.x0 * w) - mx)
    ry0 = max(0, int(plot_box.y0 * h) - my)
    rx1 = min(w, int(plot_box.x1 * w) + mx)
    ry1 = min(h, int(plot_box.y1 * h) + my)
    if rx1 - rx0 < 60 or ry1 - ry0 < 40:
        return None
    sub = crop_rgb[ry0:ry1, rx0:rx1]
    frame = detect_plot_frame(sub)
    if frame is None:
        return None
    fx0, fy0, fx1, fy1 = frame
    return (fx0 + rx0, fy0 + ry0, fx1 + rx0, fy1 + ry0)


def _ocr_axis_pairs(crop_rgb: np.ndarray, frame: BBox, ocr: OCRBackend) -> List[Tuple[float, float]]:
    """OCR numeric tick labels just below the bottom axis → (pixel_x, value).

    Tokens are clustered into text rows; the topmost row holding at least two
    numbers is the tick-label row (deeper rows are the axis title or the next
    panel and must not leak in).
    """

    h, w = crop_rgb.shape[:2]
    x0, _, x1, y1 = frame
    band_lo = min(h - 1, y1 + 2)
    band_hi = min(h, y1 + max(26, int(h * 0.16)))
    if band_hi - band_lo < 8:
        return []
    band = crop_rgb[band_lo:band_hi, :]
    # Upscale for small fonts.
    band_img = Image.fromarray(band).resize((band.shape[1] * 2, band.shape[0] * 2), Image.LANCZOS)
    try:
        tokens = ocr.detect_tokens(band_img, min_confidence=0.35)
    except Exception:
        return []
    numeric: List[Tuple[float, float, float]] = []  # (cx, cy, value)
    for tok in tokens:
        text = tok.text.strip().replace("º", "").replace("°", "")
        if not _NUM_RE.match(text):
            continue
        bx0, by0, bx1, by1 = tok.bbox
        cx = (bx0 + bx1) / 4.0  # /2 for center, /2 for the 2x upscale
        cy = (by0 + by1) / 4.0
        if x0 - 20 <= cx <= x1 + 20:
            numeric.append((cx, cy, float(text)))
    if not numeric:
        return []

    # Cluster by row (y center) and keep the topmost row with ≥2 numbers.
    numeric.sort(key=lambda t: t[1])
    row_tol = max(6.0, (band_hi - band_lo) * 0.25)
    rows: List[List[Tuple[float, float, float]]] = []
    for item in numeric:
        if rows and abs(item[1] - rows[-1][-1][1]) <= row_tol:
            rows[-1].append(item)
        else:
            rows.append([item])
    row = next((r for r in rows if len(r) >= 2), None)
    if row is None:
        return []

    pairs = sorted(((cx, val) for cx, _, val in row), key=lambda p: p[0])
    # Values along an x axis must be strictly increasing; drop violations.
    cleaned: List[Tuple[float, float]] = []
    for px_, val in pairs:
        if cleaned and val <= cleaned[-1][1]:
            continue
        cleaned.append((px_, val))
    return cleaned


def _ocr_text_boxes_in_plot(plot_rgb: np.ndarray, ocr: OCRBackend) -> List[BBox]:
    try:
        tokens = ocr.detect_tokens(Image.fromarray(plot_rgb), min_confidence=0.45)
    except Exception:
        return []
    boxes = []
    for tok in tokens:
        x0, y0, x1, y1 = tok.bbox
        # Pad slightly; text strokes bleed beyond OCR boxes.
        boxes.append((x0 - 2, y0 - 2, x1 + 2, y1 + 2))
    return boxes


def _retry_calibration(crop_rgb, frame: BBox, calib: CalibrationEvidence, llm: VisionLLM):
    """Magnified-strip arbitration when initial calibration is untrusted."""

    h, w = crop_rgb.shape[:2]
    x0, _, x1, y1 = frame
    lo = max(0, y1 - 10)
    hi = min(h, y1 + max(30, int(h * 0.12)))
    strip = crop_rgb[lo:hi, max(0, x0 - 30):min(w, x1 + 30)]
    strip_big = np.asarray(
        Image.fromarray(strip).resize((strip.shape[1] * 3, strip.shape[0] * 3), Image.LANCZOS)
    )
    desc = (
        f"candidate tick value lists: LLM={calib.tick_values}, "
        f"OCR={[v for _, v in calib.ocr_pairs]}; "
        f"CV found {len(calib.tick_pixels)} tick marks"
    )
    try:
        arb = llm.arbitrate_axis(strip_big, desc)
    except OpenAIServiceError:
        return calib
    # The model reports its own confidence in the magnified re-read; a
    # low-confidence guess must not overwrite evidence-based calibration —
    # quarantining beats confidently applying a hedge.
    if arb.confidence < 0.4:
        calib.notes.append(
            f"arbitration not applied (model confidence {arb.confidence:.2f})"
        )
        return calib
    values = arb.tick_values or [
        v for v in (arb.first_tick_value, arb.last_tick_value) if v is not None
    ]
    if len(values) >= 2:
        calib = apply_arbitration(calib, frame=frame, arbitrated_values=values)
    return calib


def _match_anchors_to_specs(anchors: List[CurveAnchor], specs):
    """Pair anchor polylines with curve specs by their order_from_top VALUE.

    Pairing by list index silently mislabels curves whenever the model skips
    one: anchors [1, 3] against specs [1, 2, 3] would hand curve 3's anchor to
    curve 2's identity. Matching on the declared value keeps identities
    honest; an anchor whose value has no spec proceeds unlabeled (spec=None)
    rather than stealing a neighbor's label."""

    by_order: dict = {}
    for s in specs:
        by_order.setdefault(s.order_from_top, s)
    matched = []
    used = set()
    for anchor in sorted(anchors, key=lambda a: a.order_from_top):
        spec = by_order.get(anchor.order_from_top)
        if spec is not None and id(spec) in used:
            spec = None                      # duplicate order value from model
        if spec is not None:
            used.add(id(spec))
        matched.append((anchor, spec))
    return matched


def _write_series_csv(path: Path, result: FigureResult, series) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "paper_id", "figure_id", "series_id", "series_label",
            "two_theta_deg", "relative_intensity", "two_theta_uncertainty_deg",
        ])
        for tt, iv in zip(series.two_theta_deg, series.intensity_norm):
            writer.writerow([
                result.paper_id, result.figure_id, series.series_id,
                series.label or "", tt, iv, series.two_theta_uncertainty_deg,
            ])


def _write_master_csv(report: RunReport, path: Path) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "paper_id", "figure_id", "series_id", "series_label", "status",
            "confidence", "two_theta_deg", "relative_intensity",
            "two_theta_uncertainty_deg",
        ])
        for paper in report.papers:
            for fig in paper.figures:
                for s in fig.series:
                    if s.status != "accepted":
                        continue
                    for tt, iv in zip(s.two_theta_deg, s.intensity_norm):
                        writer.writerow([
                            paper.paper_id, fig.figure_id, s.series_id,
                            s.label or "", s.status, s.confidence,
                            tt, iv, s.two_theta_uncertainty_deg,
                        ])


def _first_title(doc) -> Optional[str]:
    try:
        text = doc[0].get_text("text")
    except Exception:
        return None
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 20 and not line.lower().startswith("doi"):
            return line
    return None
