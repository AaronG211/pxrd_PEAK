from pathlib import Path

import pytest

from pxrd_fetcher.config import Settings
from pxrd_fetcher.preprocess import extract_pdf_candidates
from pxrd_fetcher.schemas import PagePxrdScreening

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PAPERS = [
    REPO_ROOT / "sample_papers" / "10.1002_adma.202206706.pdf",
    REPO_ROOT / "sample_papers" / "10.1002_adfm.202207749.pdf",
    REPO_ROOT / "sample_papers" / "10.1002_adma.202204894.pdf",
    REPO_ROOT / "sample_papers" / "10.1002_adfm.202315548.pdf",
]


@pytest.mark.integration
@pytest.mark.parametrize("pdf_path", SAMPLE_PAPERS)
def test_sample_pdf_preprocessing_builds_page_artifacts(pdf_path, tmp_path):
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    assert fitz  # keep lint quiet

    # sample_papers/ holds copyrighted journal PDFs and is gitignored, so it is
    # absent from a fresh clone — skip rather than error when the fixture PDF
    # is not present locally.
    if not pdf_path.exists():
        pytest.skip(f"sample PDF not available: {pdf_path.name}")

    settings = Settings(
        CHATGPT_API_KEY="x",
        CHATGPT_MODEL="gpt-5.4-2026-03-05",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "db.sqlite",
    )

    metadata, candidates = extract_pdf_candidates(
        pdf_path,
        tmp_path / pdf_path.stem,
        settings,
        page_screen_fn=lambda page_path, page_text, page_number: PagePxrdScreening(
            has_pxrd_graph=True,
            confidence=1.0,
            reason="integration test",
        ),
    )

    assert metadata.paper_id
    page_dir = tmp_path / pdf_path.stem / "pages"
    assert any(page_dir.glob("*.png"))
    for candidate in candidates:
        assert Path(candidate.crop_image_path).exists()
        assert candidate.axis_label_text
