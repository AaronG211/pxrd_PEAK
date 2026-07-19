from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from pxrd_fetcher.preprocess import (
    TextBlock,
    _build_whole_bbox_fallback_panel,
    _extract_panel_candidates,
    _find_theta_axis_label_evidence,
    extract_pdf_candidates,
)
from pxrd_fetcher.schemas import PagePxrdScreening


def test_theta_axis_evidence_can_come_from_pdf_text():
    crop = Image.new("RGB", (300, 240), "white")
    bbox = (20, 40, 280, 200)
    text_blocks = [
        TextBlock(bbox=(40, 202, 240, 228), text="2θ (degree)"),
        TextBlock(bbox=(40, 10, 200, 30), text="Figure 3 PXRD"),
    ]
    fake_ocr = SimpleNamespace(extract_text=lambda *args, **kwargs: "")

    text, source = _find_theta_axis_label_evidence(
        crop_image=crop,
        bbox=bbox,
        text_blocks=text_blocks,
        ocr_backend=fake_ocr,
    )

    assert "degree" in text
    assert source == "pdf_text"


def test_page_filter_can_remove_all_candidates(tmp_path):
    pdf_path = Path("sample_papers/10.1002_adfm.202207749.pdf")

    from pxrd_fetcher.config import Settings

    settings = Settings(
        CHATGPT_API_KEY="x",
        CHATGPT_MODEL="gpt-5.4-2026-03-05",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "db.sqlite",
    )

    metadata, candidates = extract_pdf_candidates(
        pdf_path,
        tmp_path / "paper",
        settings,
        page_screen_fn=lambda page_path, page_text, page_number: PagePxrdScreening(
            has_pxrd_graph=False,
            confidence=0.0,
            reason="test",
        ),
    )

    assert metadata.paper_id
    assert candidates == []


def test_extract_panel_candidates_splits_multi_panel_chart():
    crop = Image.new("RGB", (420, 320), "white")
    draw = ImageDraw.Draw(crop)
    draw.rectangle((30, 30, 180, 130), outline="black", width=3)
    draw.rectangle((220, 165, 390, 275), outline="black", width=3)
    text_blocks = [
        TextBlock(bbox=(40, 132, 170, 154), text="2θ (degree)"),
        TextBlock(bbox=(230, 278, 375, 300), text="20 (degree)"),
    ]
    fake_ocr = SimpleNamespace(extract_text=lambda *args, **kwargs: "")

    panel_specs = _extract_panel_candidates(
        crop_image=crop,
        parent_bbox=(0, 0, 420, 320),
        text_blocks=text_blocks,
        ocr_backend=fake_ocr,
    )

    assert len(panel_specs) == 2
    assert all("degree" in axis_text for _, axis_text, _ in panel_specs)


def test_whole_bbox_fallback_is_blocked_for_multi_chart_crop():
    crop = Image.new("RGB", (420, 320), "white")
    draw = ImageDraw.Draw(crop)
    draw.rectangle((30, 30, 180, 130), outline="black", width=3)
    draw.rectangle((220, 165, 390, 275), outline="black", width=3)
    text_blocks = [
        TextBlock(bbox=(40, 132, 170, 154), text="2θ (degree)"),
        TextBlock(bbox=(230, 278, 375, 300), text="20 (degree)"),
    ]
    fake_ocr = SimpleNamespace(extract_text=lambda *args, **kwargs: "")

    fallback_panel = _build_whole_bbox_fallback_panel(
        crop_image=crop,
        bbox=(0, 0, 420, 320),
        text_blocks=text_blocks,
        ocr_backend=fake_ocr,
    )

    assert fallback_panel is None
