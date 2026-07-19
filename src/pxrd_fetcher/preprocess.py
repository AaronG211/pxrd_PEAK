"""PDF ingestion and figure-candidate extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import Settings
from .ocr import OCRBackend, OCREngineError, get_ocr_backend
from .schemas import FigureCandidate, PagePxrdScreening, PaperMetadata
from .text import (
    extract_figure_label,
    has_two_theta_degree_label,
    keyword_score,
    merge_context_lines,
)
from .utils import derive_paper_id, detect_doi_like_name, ensure_dir

CAPTION_RE = re.compile(r"^\s*(fig(?:ure)?\.?\s*\d+[a-z]?)", re.IGNORECASE)


class PreprocessError(RuntimeError):
    """Raised when PDF ingestion fails."""


@dataclass
class TextBlock:
    bbox: Tuple[int, int, int, int]
    text: str


PageScreenFn = Callable[[Path, str, int], PagePxrdScreening]
PanelSpec = Tuple[Tuple[int, int, int, int], str, Optional[str]]


def iter_pdf_paths(target: Path) -> List[Path]:
    """Return sorted PDF files from a path."""

    if target.is_file():
        return [target]
    return sorted(path for path in target.rglob("*.pdf") if path.is_file())


def extract_pdf_candidates(
    pdf_path: Path,
    paper_output_dir: Path,
    settings: Settings,
    *,
    page_screen_fn: Optional[PageScreenFn] = None,
) -> Tuple[PaperMetadata, List[FigureCandidate]]:
    """Extract likely figure crops from a PDF."""

    try:
        import fitz
        import numpy as np
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - import guard
        raise PreprocessError(
            "Missing PDF dependencies. Install PyMuPDF, Pillow, and NumPy."
        ) from exc

    if not pdf_path.exists():
        raise PreprocessError("PDF does not exist: {path}".format(path=pdf_path))

    ensure_dir(paper_output_dir)
    page_dir = ensure_dir(paper_output_dir / "pages")
    crop_dir = ensure_dir(paper_output_dir / "crops")
    try:
        ocr_backend = get_ocr_backend(settings.ocr_backend)
    except OCREngineError as exc:
        raise PreprocessError(str(exc)) from exc

    try:
        document = fitz.open(pdf_path)
    except Exception as exc:  # pragma: no cover - file parsing
        raise PreprocessError("Could not read PDF: {path}".format(path=pdf_path)) from exc

    paper_id = derive_paper_id(pdf_path)
    first_page = document[0]
    first_page_text = first_page.get_text("text")
    metadata = PaperMetadata(
        paper_id=paper_id,
        source_path=str(pdf_path),
        doi=detect_doi_like_name(pdf_path),
        title=_extract_title(first_page_text, pdf_path.stem),
        year=_extract_year(first_page_text),
    )

    candidates: List[FigureCandidate] = []
    scale = settings.render_dpi / 72.0

    for page_index, page in enumerate(document):
        image = _render_page(page, settings.render_dpi)
        page_path = page_dir / "page-{page:03d}.png".format(page=page_index + 1)
        image.save(page_path)

        page_dict = page.get_text("dict")
        text_blocks = _extract_text_blocks(page_dict, scale)
        page_text = merge_context_lines(block.text for block in text_blocks)
        page_screen = _screen_page(
            page_path=page_path,
            page_text=page_text,
            page_number=page_index + 1,
            page_screen_fn=page_screen_fn,
        )
        # DEBUG LOG
        if not page_screen.has_pxrd_graph:
            print("  [debug] Page {n} skipped by AI screening.".format(n=page_index+1))
            continue
        
        image_blocks = _extract_image_boxes(page_dict, scale)
        text_blocks = _extract_text_blocks(page_dict, scale)
        print("  [debug] Page {n}: found {i} images, {t} text blocks.".format(
            n=page_index+1, i=len(image_blocks), t=len(text_blocks)
        ))
        
        page_array = np.asarray(image)
        caption_blocks = _find_caption_blocks(text_blocks)
        candidate_specs = _build_candidate_specs(
            caption_blocks=caption_blocks,
            image_blocks=image_blocks,
            page_shape=page_array.shape,
            page_pixels=page_array,
            text_blocks=text_blocks,
        )

        emitted_specs: List[PanelSpec] = []
        candidate_index = 0
        for bbox, caption_block in candidate_specs:
            crop = image.crop(bbox)
            panel_specs = _extract_panel_candidates(
                crop_image=crop,
                parent_bbox=bbox,
                text_blocks=text_blocks,
                ocr_backend=ocr_backend,
            )

            if not panel_specs:
                fallback_panel = _build_whole_bbox_fallback_panel(
                    crop_image=crop,
                    bbox=bbox,
                    text_blocks=text_blocks,
                    ocr_backend=ocr_backend,
                )
                if fallback_panel is not None:
                    panel_specs = [fallback_panel]

            for panel_bbox, axis_label_text, axis_label_source in panel_specs:
                if any(_bbox_iou(panel_bbox, existing_bbox) >= 0.68 for existing_bbox, _, _ in emitted_specs):
                    continue
                emitted_specs.append((panel_bbox, axis_label_text, axis_label_source))

                candidate_index += 1
                panel_crop = image.crop(panel_bbox)
                crop_name = "page-{page:03d}-figure-{idx:02d}.png".format(
                    page=page_index + 1,
                    idx=candidate_index,
                )
                crop_path = crop_dir / crop_name
                panel_crop.save(crop_path)

                if caption_block is not None:
                    caption = caption_block.text
                    context = _gather_context(text_blocks, caption_block)
                    figure_label = extract_figure_label(caption_block.text)
                else:
                    caption = ""
                    context = _gather_bbox_context(text_blocks, panel_bbox)
                    figure_label = None

                combined_text = "{caption}\n{context}\n{axis}".format(
                    caption=caption,
                    context=context,
                    axis=axis_label_text,
                )

                candidates.append(
                    FigureCandidate(
                        candidate_id="{paper}-p{page:03d}-f{idx:02d}".format(
                            paper=paper_id,
                            page=page_index + 1,
                            idx=candidate_index,
                        ),
                        page_number=page_index + 1,
                        bbox=panel_bbox,
                        page_image_path=str(page_path),
                        crop_image_path=str(crop_path),
                        caption=caption,
                        context=context,
                        figure_label=figure_label,
                        keyword_score=keyword_score(combined_text),
                        axis_label_text=axis_label_text,
                        axis_label_source=axis_label_source,
                    )
                )

    document.close()
    return metadata, candidates


def _render_page(page: object, dpi: int):
    import fitz
    from PIL import Image

    scale = dpi / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)


def _extract_text_blocks(page_dict: Dict[str, object], scale: float) -> List[TextBlock]:
    blocks: List[TextBlock] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = []
        for line in block.get("lines", []):
            spans = [span.get("text", "") for span in line.get("spans", [])]
            lines.append("".join(spans))
        text = merge_context_lines(lines)
        if not text:
            continue
        bbox = _scale_bbox(block.get("bbox", (0, 0, 0, 0)), scale)
        blocks.append(TextBlock(bbox=bbox, text=text))
    return blocks


def _extract_image_boxes(page_dict: Dict[str, object], scale: float) -> List[Tuple[int, int, int, int]]:
    boxes: List[Tuple[int, int, int, int]] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = _scale_bbox(block.get("bbox", (0, 0, 0, 0)), scale)
        boxes.append(bbox)
    return boxes


def _scale_bbox(raw_bbox: Sequence[float], scale: float) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = raw_bbox
    return (
        int(round(x0 * scale)),
        int(round(y0 * scale)),
        int(round(x1 * scale)),
        int(round(y1 * scale)),
    )


def _find_caption_blocks(blocks: Iterable[TextBlock]) -> List[TextBlock]:
    return [block for block in blocks if CAPTION_RE.match(block.text)]


def _screen_page(
    *,
    page_path: Path,
    page_text: str,
    page_number: int,
    page_screen_fn: Optional[PageScreenFn],
) -> PagePxrdScreening:
    if page_screen_fn is not None:
        return page_screen_fn(page_path, page_text, page_number)

    combined_text = page_text
    local_score = keyword_score(combined_text)
    if local_score < 0.72 and not has_two_theta_degree_label(combined_text):
        try:
            from PIL import Image

            ocr_text = get_ocr_backend().extract_text(Image.open(page_path).convert("RGB"), min_confidence=0.28)
        except Exception:
            ocr_text = ""
        combined_text = merge_context_lines([page_text, ocr_text])

    local_score = keyword_score(combined_text)
    return PagePxrdScreening(
        has_pxrd_graph=local_score >= 0.72 or has_two_theta_degree_label(combined_text),
        confidence=max(local_score, 0.8 if has_two_theta_degree_label(combined_text) else local_score),
        reason="Local keyword fallback page screening.",
    )


def _build_candidate_specs(
    *,
    caption_blocks: Sequence[TextBlock],
    image_blocks: Sequence[Tuple[int, int, int, int]],
    page_shape: Tuple[int, int, int],
    page_pixels,
    text_blocks: Sequence[TextBlock],
) -> List[Tuple[Tuple[int, int, int, int], Optional[TextBlock]]]:
    raw_specs: List[Tuple[Tuple[int, int, int, int], Optional[TextBlock]]] = []
    for caption_block in caption_blocks:
        bbox = _find_candidate_bbox(
            caption_block=caption_block,
            image_blocks=image_blocks,
            page_shape=page_shape,
            page_pixels=page_pixels,
        )
        raw_specs.append((bbox, caption_block))

    for image_bbox in image_blocks:
        raw_specs.append((
            _expand_image_bbox(image_bbox, page_shape),
            _find_nearest_caption_block(caption_blocks, image_bbox),
        ))

    deduped: List[Tuple[Tuple[int, int, int, int], Optional[TextBlock]]] = []
    for bbox, caption_block in raw_specs:
        if any(_bbox_iou(bbox, existing_bbox) >= 0.72 for existing_bbox, _ in deduped):
            continue
        deduped.append((bbox, caption_block))
    return deduped


def _find_candidate_bbox(
    *,
    caption_block: TextBlock,
    image_blocks: Sequence[Tuple[int, int, int, int]],
    page_shape: Tuple[int, int, int],
    page_pixels,
) -> Tuple[int, int, int, int]:
    page_height, page_width = page_shape[:2]
    cap_x0, cap_y0, cap_x1, _ = caption_block.bbox

    overlapping_images = []
    for box in image_blocks:
        x0, y0, x1, y1 = box
        horizontal_overlap = max(0, min(x1, cap_x1) - max(x0, cap_x0))
        if y1 > cap_y0 + 50:
            continue
        if horizontal_overlap <= 0 and abs(((x0 + x1) / 2) - ((cap_x0 + cap_x1) / 2)) > page_width * 0.35:
            continue
        overlapping_images.append(box)

    if overlapping_images:
        x0 = max(0, min(box[0] for box in overlapping_images) - 20)
        y0 = max(0, min(box[1] for box in overlapping_images) - 20)
        x1 = min(page_width, max(box[2] for box in overlapping_images) + 20)
        y1 = min(page_height, max(box[3] for box in overlapping_images) + 20)
        return (x0, y0, x1, y1)

    top = max(0, cap_y0 - int(page_height * 0.42))
    bottom = max(top + 80, cap_y0 - 10)
    left = int(page_width * 0.05)
    right = int(page_width * 0.95)
    base_left = left
    base_top = top

    candidate = page_pixels[top:bottom, left:right]
    mask = candidate.mean(axis=2) < 250
    if mask.any():
        ys, xs = mask.nonzero()
        left = max(0, base_left + int(xs.min()) - 20)
        top = max(0, base_top + int(ys.min()) - 20)
        right = min(page_width, base_left + int(xs.max()) + 40)
        bottom = min(page_height, base_top + int(ys.max()) + 40)
    return (left, top, right, bottom)


def _extract_panel_candidates(
    *,
    crop_image,
    parent_bbox: Tuple[int, int, int, int],
    text_blocks: Sequence[TextBlock],
    ocr_backend: OCRBackend,
) -> List[PanelSpec]:
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - import guard
        return []

    crop_pixels = np.asarray(crop_image)
    panel_boxes = _detect_chart_boxes(crop_pixels)
    panel_specs: List[PanelSpec] = []
    parent_x0, parent_y0, _, _ = parent_bbox

    for local_box in panel_boxes:
        expanded_local_box = _expand_panel_bbox(local_box, crop_pixels.shape[:2])
        global_box = _offset_bbox(expanded_local_box, parent_x0, parent_y0)
        panel_crop = crop_image.crop(expanded_local_box)
        axis_label_text, axis_label_source = _find_theta_axis_label_evidence(
            crop_image=panel_crop,
            bbox=global_box,
            text_blocks=text_blocks,
            ocr_backend=ocr_backend,
        )
        if not axis_label_text:
            continue
        panel_specs.append((global_box, axis_label_text, axis_label_source))

    deduped: List[PanelSpec] = []
    for panel_spec in sorted(panel_specs, key=lambda item: (item[0][1], item[0][0])):
        bbox = panel_spec[0]
        # Use a more aggressive IOU for deduplication (0.50) to merge overlapping candidates
        if any(_bbox_iou(bbox, existing_bbox) >= 0.50 for existing_bbox, _, _ in deduped):
            continue
        deduped.append(panel_spec)
    return deduped


def _build_whole_bbox_fallback_panel(
    *,
    crop_image,
    bbox: Tuple[int, int, int, int],
    text_blocks: Sequence[TextBlock],
    ocr_backend: OCRBackend,
) -> Optional[PanelSpec]:
    """Fallback only when the crop appears to contain a single chart.

    Without this guard, a large multi-panel figure can be kept as one PXRD
    candidate as soon as any inset or nearby panel contains a 2theta-like
    label, which then allows unrelated charts in the same crop to slip through.
    """

    axis_label_text, axis_label_source = _find_theta_axis_label_evidence(
        crop_image=crop_image,
        bbox=bbox,
        text_blocks=text_blocks,
        ocr_backend=ocr_backend,
    )
    if not axis_label_text:
        return None

    try:
        import numpy as np
    except ImportError:  # pragma: no cover - import guard
        return (bbox, axis_label_text, axis_label_source)

    crop_pixels = np.asarray(crop_image)
    chart_boxes = _detect_chart_boxes(crop_pixels)
    if len(chart_boxes) > 1:
        return None
    if len(chart_boxes) == 1:
        local_box = _expand_panel_bbox(chart_boxes[0], crop_pixels.shape[:2])
        panel_bbox = _offset_bbox(local_box, bbox[0], bbox[1])
        return (panel_bbox, axis_label_text, axis_label_source)
    return (bbox, axis_label_text, axis_label_source)


def _expand_image_bbox(
    bbox: Tuple[int, int, int, int],
    page_shape: Tuple[int, int, int],
) -> Tuple[int, int, int, int]:
    page_height, page_width = page_shape[:2]
    x0, y0, x1, y1 = bbox
    return (
        max(0, x0 - 18),
        max(0, y0 - 18),
        min(page_width, x1 + 18),
        min(page_height, y1 + max(60, int((y1 - y0) * 0.12))),
    )


def _detect_chart_boxes(crop_pixels) -> List[Tuple[int, int, int, int]]:
    import cv2
    import numpy as np

    gray = cv2.cvtColor(crop_pixels, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 40, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    height, width = gray.shape
    area_threshold = height * width * 0.03
    max_area = height * width * 0.78
    candidates: List[Tuple[int, int, int, int]] = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < area_threshold or area > max_area:
            continue
        if w < width * 0.18 or h < height * 0.12:
            continue
        aspect = w / max(1, h)
        if not 0.6 <= aspect <= 4.8:
            continue
        frame_signal = _frame_signal(gray[y:y + h, x:x + w])
        if frame_signal < 0.13:
            continue
        candidates.append((x, y, x + w, y + h))

    deduped: List[Tuple[int, int, int, int]] = []
    for bbox in sorted(candidates, key=lambda box: ((box[3] - box[1]) * (box[2] - box[0])), reverse=True):
        if any(_bbox_iou(bbox, existing) >= 0.72 for existing in deduped):
            continue
        deduped.append(bbox)
    return deduped


def _frame_signal(gray_region) -> float:
    import numpy as np

    if gray_region.size == 0:
        return 0.0
    height, width = gray_region.shape
    border = max(2, min(height, width) // 40)
    top = gray_region[:border, :]
    bottom = gray_region[-border:, :]
    left = gray_region[:, :border]
    right = gray_region[:, -border:]

    # Calculate dark pixel ratio for each side independently
    t_sig = float((top < 200).mean())
    b_sig = float((bottom < 200).mean())
    l_sig = float((left < 200).mean())
    r_sig = float((right < 200).mean())

    # PXRD plots often have ONLY a bottom axis (X-axis).
    # We require at least one strong side (usually bottom) or a moderate combination.
    max_side = max(t_sig, b_sig, l_sig, r_sig)
    avg_side = (t_sig + b_sig + l_sig + r_sig) / 4.0

    # If any single side is very strong (like a clear X-axis), that's a good signal.
    # Or if the average is decent.
    return max(max_side * 0.8, avg_side * 2.0)


def _expand_panel_bbox(
    bbox: Tuple[int, int, int, int],
    crop_shape: Tuple[int, int],
) -> Tuple[int, int, int, int]:
    crop_height, crop_width = crop_shape[:2]
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    return (
        max(0, x0 - int(width * 0.04)),
        max(0, y0 - int(height * 0.04)),
        min(crop_width, x1 + int(width * 0.04)),
        min(crop_height, y1 + max(20, int(height * 0.18))),
    )


def _offset_bbox(
    bbox: Tuple[int, int, int, int],
    offset_x: int,
    offset_y: int,
) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return (x0 + offset_x, y0 + offset_y, x1 + offset_x, y1 + offset_y)


def _find_nearest_caption_block(
    caption_blocks: Sequence[TextBlock],
    bbox: Tuple[int, int, int, int],
) -> Optional[TextBlock]:
    x0, _, x1, y1 = bbox
    candidates: List[Tuple[int, TextBlock]] = []
    for caption_block in caption_blocks:
        cap_x0, cap_y0, cap_x1, _ = caption_block.bbox
        horizontal_overlap = max(0, min(x1, cap_x1) - max(x0, cap_x0))
        if horizontal_overlap <= 0:
            continue
        distance = cap_y0 - y1
        if -40 <= distance <= 260:
            candidates.append((abs(distance), caption_block))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _gather_context(blocks: Sequence[TextBlock], caption_block: TextBlock) -> str:
    cap_x0, cap_y0, cap_x1, cap_y1 = caption_block.bbox
    nearby = []
    for block in blocks:
        if block is caption_block:
            continue
        x0, y0, x1, y1 = block.bbox
        if y1 < cap_y0 - 250 or y0 > cap_y1 + 250:
            continue
        if max(0, min(x1, cap_x1) - max(x0, cap_x0)) <= 0:
            continue
        nearby.append(block.text)
    return merge_context_lines(nearby)


def _gather_bbox_context(blocks: Sequence[TextBlock], bbox: Tuple[int, int, int, int]) -> str:
    x0, y0, x1, y1 = bbox
    nearby = []
    for block in blocks:
        bx0, by0, bx1, by1 = block.bbox
        if by1 < y0 - 160 or by0 > y1 + 220:
            continue
        if max(0, min(x1, bx1) - max(x0, bx0)) <= 0:
            continue
        nearby.append(block.text)
    return merge_context_lines(nearby)


def _find_theta_axis_label_evidence(
    *,
    crop_image,
    bbox: Tuple[int, int, int, int],
    text_blocks: Sequence[TextBlock],
    ocr_backend: OCRBackend,
) -> Tuple[str, Optional[str]]:
    text_evidence = _gather_axis_text_blocks(text_blocks, bbox)
    if has_two_theta_degree_label(text_evidence):
        return text_evidence, "pdf_text"

    ocr_evidence = _ocr_axis_label(crop_image, ocr_backend)
    if has_two_theta_degree_label(ocr_evidence):
        return ocr_evidence, "ocr"

    combined = merge_context_lines([text_evidence, ocr_evidence])
    if has_two_theta_degree_label(combined):
        return combined, "mixed"
    
    print("  [debug] No 2theta evidence for candidate at {bbox}. Text: '{t}', OCR: '{o}'".format(
        bbox=bbox, t=text_evidence[:50], o=ocr_evidence[:50]
    ))
    return "", None


def _gather_axis_text_blocks(
    text_blocks: Sequence[TextBlock],
    bbox: Tuple[int, int, int, int],
) -> str:
    x0, _, x1, y1 = bbox
    matches = []
    for block in text_blocks:
        bx0, by0, bx1, by1 = block.bbox
        horizontal_overlap = max(0, min(x1, bx1) - max(x0, bx0))
        if horizontal_overlap <= max(18, (x1 - x0) * 0.12):
            continue
        if by0 < y1 - 40 or by1 > y1 + 180:
            continue
        matches.append(block.text)
    return merge_context_lines(matches)


def _ocr_axis_label(crop_image, ocr_backend: OCRBackend) -> str:
    from PIL import ImageOps

    width, height = crop_image.size
    if width < 40 or height < 40:
        return ""

    regions = [
        crop_image.crop((0, int(height * 0.68), width, height)),
        crop_image.crop((0, int(height * 0.55), width, height)),
        crop_image,
    ]
    texts = []
    for region in regions:
        grayscale = ImageOps.grayscale(region)
        grayscale = ImageOps.autocontrast(grayscale)
        enlarged = grayscale.resize((max(1, grayscale.width * 2), max(1, grayscale.height * 2)))
        inverted = ImageOps.invert(enlarged)
        for variant in (grayscale, enlarged, inverted):
            try:
                text = ocr_backend.extract_text(variant, min_confidence=0.28)
            except Exception:
                text = ""
            if text:
                texts.append(text)
    return merge_context_lines(texts)


def _bbox_iou(box_a: Tuple[int, int, int, int], box_b: Tuple[int, int, int, int]) -> float:
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)
    if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
        return 0.0
    intersection = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1, (bx1 - bx0) * (by1 - by0))
    union = area_a + area_b - intersection
    return float(intersection / union)


def _extract_title(first_page_text: str, fallback: str) -> str:
    lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]
    for line in lines[:10]:
        if len(line) > 20 and not line.lower().startswith("doi"):
            return line
    return fallback


def _extract_year(text: str):
    match = re.search(r"\b(19|20)\d{2}\b", text)
    if not match:
        return None
    return int(match.group(0))
