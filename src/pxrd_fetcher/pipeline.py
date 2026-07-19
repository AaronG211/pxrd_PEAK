"""Pipeline orchestration for PXRD Fetcher."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Dict, List

from .config import Settings
from .digitize import DigitizationError, digitize_figure
from .exporter import export_run, write_series_csv
from .ocr import get_ocr_backend
from .openai_service import OpenAIService, OpenAIServiceError
from .postprocess import align_intensity_to_x_grid
from .preprocess import extract_pdf_candidates, iter_pdf_paths
from .review import generate_review_html
from .schemas import (
    DigitizedCurve,
    FigureCurveAnalysis,
    FigureDigitizationResult,
    MetadataExtraction,
    PagePxrdScreening,
    PipelineFigureResult,
    PxrdClassification,
)
from .storage import Database
from .text import has_two_theta_degree_label, keyword_score
from .utils import command_exists, dump_json, ensure_dir


class PipelineError(RuntimeError):
    """Raised when a run cannot proceed."""


def run_pipeline(target: Path, settings: Settings) -> Dict[str, object]:
    """Execute the end-to-end local pipeline."""

    try:
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )
    except ImportError:  # pragma: no cover - optional UX dependency
        progress_context = nullcontext(None)
    else:
        progress_context = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )

    if not target.exists():
        raise PipelineError("Input path does not exist: {path}".format(path=target))

    _assert_runtime_dependencies()
    settings.prepare_directories()

    database = Database(settings.resolve_database_path())
    database.create_all()
    run = database.create_run(target)
    run_dir = ensure_dir(settings.resolve_output_dir() / "run-{run_id:05d}".format(run_id=run.id))
    openai_service = OpenAIService(settings)

    pdf_paths = list(iter_pdf_paths(target))
    if not pdf_paths:
        database.finish_run(run.id, status="failed", note="No PDFs found.")
        raise PipelineError("No PDF files were found at the requested path.")

    stats = {
        "run_id": run.id,
        "pdf_count": len(pdf_paths),
        "candidate_count": 0,
        "pxrd_count": 0,
        "accepted_count": 0,
        "accepted_series_count": 0,
        "review_required_count": 0,
        "skipped_count": 0,
    }

    try:
        with progress_context as progress:
            overall_task = None
            if progress is not None:
                overall_task = progress.add_task(
                    "[bold blue]Processing PDFs...",
                    total=len(pdf_paths),
                )

            for pdf_path in pdf_paths:
                if progress is not None and overall_task is not None:
                    progress.update(
                        overall_task,
                        description="[bold blue]Processing {name}...".format(name=pdf_path.name),
                    )
                paper_output_dir = ensure_dir(run_dir / pdf_path.stem.replace("/", "_"))

                metadata, candidates = extract_pdf_candidates(
                    pdf_path,
                    paper_output_dir,
                    settings,
                    page_screen_fn=lambda page_path, page_text, page_number: _screen_page(
                        openai_service,
                        page_path,
                        page_text,
                        page_number,
                    ),
                )
                paper = database.create_paper(run.id, metadata.model_dump())
                stats["candidate_count"] += len(candidates)
                if progress:
                    if not candidates:
                        progress.console.print("  [yellow]⚠ No PXRD candidates extracted from this paper.[/yellow]")
                    else:
                        progress.console.print("  [green]✓ Extracted {n} candidates.[/green]".format(n=len(candidates)))

                candidate_task = None
                if progress is not None and candidates:
                    candidate_task = progress.add_task(
                        "  [cyan]Analyzing figures...",
                        total=len(candidates),
                    )

                for candidate in candidates:
                    if progress is not None and candidate_task is not None:
                        progress.update(
                            candidate_task,
                            description="  [cyan]Digitizing {id}...".format(id=candidate.candidate_id),
                        )
                    figure_output_dir = ensure_dir(paper_output_dir / candidate.candidate_id)
                    classification = _classify_candidate(openai_service, candidate)
                    if progress:
                        if not classification.is_pxrd:
                            progress.console.print("  [red]✗ Rejected {id}: {reason}[/red]".format(
                                id=candidate.candidate_id, reason=classification.reason
                            ))
                        else:
                            progress.console.print("  [green]✓ Accepted {id} as PXRD.[/green]".format(
                                id=candidate.candidate_id
                            ))
                    metadata_extraction = MetadataExtraction(
                        figure_label=candidate.figure_label or classification.figure_label
                    )
                    curve_analysis: FigureCurveAnalysis | None = None

                    if classification.is_pxrd:
                        stats["pxrd_count"] += 1
                        try:
                            metadata_extraction = openai_service.extract_metadata(
                                candidate.caption,
                                candidate.context,
                                classification.figure_label or candidate.figure_label,
                            )
                        except OpenAIServiceError as exc:
                            metadata_extraction.notes = "Metadata extraction fallback: {err}".format(err=exc)

                        if metadata_extraction.figure_label is None:
                            metadata_extraction.figure_label = (
                                classification.figure_label or candidate.figure_label
                            )
                        curve_analysis = _analyze_curve_structure(
                            openai_service,
                            candidate,
                            figure_label=metadata_extraction.figure_label,
                            likely_series_count=classification.likely_series_count,
                        )
                        if (
                            curve_analysis is not None
                            and len(curve_analysis.curves) == 1
                            and metadata_extraction.series_label is None
                        ):
                            metadata_extraction.series_label = curve_analysis.curves[0].label
                        digitization = _digitize_candidate(
                            candidate,
                            figure_output_dir,
                            settings,
                            openai_service,
                            curve_analysis=curve_analysis,
                            likely_series_count=classification.likely_series_count,
                        )
                        for series in digitization.series:
                            _annotate_peak_delta(metadata_extraction, series)
                        _write_series_artifacts(
                            metadata.paper_id,
                            candidate.candidate_id,
                            figure_output_dir,
                            digitization,
                        )
                    else:
                        digitization = _build_skipped_digitization(candidate, classification.reason)

                    result = PipelineFigureResult(
                        candidate=candidate,
                        classification=classification,
                        metadata=metadata_extraction,
                        digitization=digitization,
                    )
                    database.store_figure_result(
                        run_id=run.id,
                        paper_db_id=paper.id,
                        result=result,
                    )
                    stats["accepted_series_count"] += sum(
                        1 for series in digitization.series if series.status == "accepted"
                    )

                    if digitization.status == "accepted":
                        stats["accepted_count"] += 1
                    elif digitization.status == "review_required":
                        stats["review_required_count"] += 1
                    else:
                        stats["skipped_count"] += 1

                    if progress is not None and candidate_task is not None:
                        progress.advance(candidate_task)

                if progress is not None and candidate_task is not None:
                    progress.remove_task(candidate_task)

                if progress is not None and overall_task is not None:
                    progress.advance(overall_task)

        csv_path, json_path = export_run(database, run.id, run_dir)
        review_path = run_dir / "review.html"
        note = dump_json(
            {
                **stats,
                "review_html": str(review_path),
                "accepted_csv": str(csv_path),
                "summary_json": str(json_path),
            }
        )
        final_status = "completed_with_review" if stats["review_required_count"] else "completed"
        database.finish_run(run.id, status=final_status, note=note)
        review_path = generate_review_html(database, run.id, run_dir)
        return {
            **stats,
            "review_html": review_path,
            "accepted_csv": csv_path,
            "summary_json": json_path,
            "status": final_status,
        }
    except Exception as exc:
        database.finish_run(run.id, status="failed", note=str(exc))
        raise


def regenerate_review(run_id: int, settings: Settings) -> Path:
    database = Database(settings.resolve_database_path())
    database.create_all()
    run = database.get_run(run_id)
    if run is None:
        raise PipelineError("Run {run_id} does not exist.".format(run_id=run_id))
    run_dir = ensure_dir(settings.resolve_output_dir() / "run-{run_id:05d}".format(run_id=run_id))
    return generate_review_html(database, run_id, run_dir)


def regenerate_export(run_id: int, settings: Settings):
    database = Database(settings.resolve_database_path())
    database.create_all()
    if database.get_run(run_id) is None:
        raise PipelineError("Run {run_id} does not exist.".format(run_id=run_id))
    run_dir = ensure_dir(settings.resolve_output_dir() / "run-{run_id:05d}".format(run_id=run_id))
    return export_run(database, run_id, run_dir)


def _assert_runtime_dependencies() -> None:
    missing = [
        name
        for name in ("pdftoppm",)
        if not command_exists(name)
    ]
    if missing:
        raise PipelineError(
            "Missing native dependencies: {deps}. Install them with Homebrew before running the pipeline.".format(
                deps=", ".join(missing)
            )
        )


def _classify_candidate(openai_service: OpenAIService, candidate) -> PxrdClassification:
    local_score = keyword_score("{caption}\n{context}".format(
        caption=candidate.caption,
        context=candidate.context,
    ))
    try:
        return openai_service.classify_pxrd(
            Path(candidate.crop_image_path),
            candidate.caption,
            candidate.context,
            local_score,
        )
    except OpenAIServiceError as exc:
        if local_score >= 0.8:
            return PxrdClassification(
                is_pxrd=True,
                confidence=min(0.85, local_score),
                reason="OpenAI classification unavailable; falling back to strong keyword evidence: {err}".format(
                    err=exc
                ),
                figure_label=candidate.figure_label,
                likely_series_count=1,
            )
        return PxrdClassification(
            is_pxrd=False,
            confidence=max(0.0, 1.0 - local_score),
            reason="OpenAI classification unavailable and local evidence was weak: {err}".format(err=exc),
            figure_label=candidate.figure_label,
            likely_series_count=0,
        )


def _screen_page(
    openai_service: OpenAIService,
    page_path: Path,
    page_text: str,
    page_number: int,
) -> PagePxrdScreening:
    try:
        return openai_service.screen_pxrd_page(page_path, page_text, page_number)
    except OpenAIServiceError as exc:
        combined_text = page_text
        local_score = keyword_score(combined_text)
        has_axis_hint = has_two_theta_degree_label(combined_text)
        if local_score < 0.78 and not has_axis_hint:
            try:
                from PIL import Image

                ocr_text = get_ocr_backend().extract_text(
                    Image.open(page_path).convert("RGB"),
                    min_confidence=0.28,
                )
            except Exception:
                ocr_text = ""
            combined_text = "\n".join(part for part in (page_text, ocr_text) if part)
            local_score = keyword_score(combined_text)
            has_axis_hint = has_two_theta_degree_label(combined_text)
        return PagePxrdScreening(
            has_pxrd_graph=local_score >= 0.78 or has_axis_hint,
            confidence=max(local_score, 0.8 if has_axis_hint else local_score),
            reason="OpenAI page screening unavailable; using local fallback: {err}".format(err=exc),
        )


def _digitize_candidate(
    candidate,
    figure_output_dir: Path,
    settings: Settings,
    openai_service: OpenAIService,
    *,
    curve_analysis: FigureCurveAnalysis | None,
    likely_series_count: int,
) -> FigureDigitizationResult:
    try:
        return digitize_figure(
            Path(candidate.crop_image_path),
            figure_output_dir,
            settings,
            openai_service=openai_service,
            curve_analysis=curve_analysis,
            likely_series_count=likely_series_count,
            caption=getattr(candidate, "caption", None),
            context=getattr(candidate, "context", None),
        )
    except DigitizationError as exc:
        return FigureDigitizationResult(
            status="review_required",
            reason=str(exc),
            plot_bbox=candidate.bbox,
            flagged_reasons=[str(exc)],
            series=[],
        )


def _build_skipped_digitization(candidate, reason: str) -> FigureDigitizationResult:
    return FigureDigitizationResult(
        status="skipped",
        reason=reason or "Figure was not classified as PXRD.",
        plot_bbox=candidate.bbox,
        series=[],
    )


def _analyze_curve_structure(
    openai_service: OpenAIService,
    candidate,
    *,
    figure_label: str | None,
    likely_series_count: int,
) -> FigureCurveAnalysis | None:
    try:
        return openai_service.analyze_pxrd_curves(
            Path(candidate.crop_image_path),
            candidate.caption,
            candidate.context,
            figure_label,
            likely_series_count,
        )
    except OpenAIServiceError:
        return None


def _write_series_artifacts(
    paper_id: str,
    figure_id: str,
    figure_output_dir: Path,
    digitization: FigureDigitizationResult,
) -> None:
    accepted_series = [
        series
        for series in digitization.series
        if series.status == "accepted" and series.processed_two_theta
    ]

    for index, series in enumerate(digitization.series, start=1):
        if not series.series_key:
            series.series_key = "{figure_id}-s{index:02d}".format(
                figure_id=figure_id,
                index=index,
            )
        if not series.series_label:
            series.series_label = "curve-{index:02d}".format(index=index)

    for series in accepted_series:
        aligned_raw_values = align_intensity_to_x_grid(
            series.raw_two_theta,
            series.raw_intensity,
            series.processed_two_theta,
        )
        raw_values = (
            aligned_raw_values.astype(float).tolist()
            if aligned_raw_values.size == len(series.processed_two_theta)
            else list(series.processed_intensity)
        )
        if len(accepted_series) == 1:
            csv_path = figure_output_dir / "accepted.csv"
        else:
            csv_path = figure_output_dir / "{series_key}.csv".format(
                series_key=series.series_key or "series",
            )
        write_series_csv(
            csv_path,
            paper_id=paper_id,
            figure_id=figure_id,
            series_id=series.series_key or "series",
            series_label=series.series_label,
            two_theta_values=series.processed_two_theta,
            raw_values=raw_values,
            normalized_values=series.processed_intensity,
        )
        series.csv_path = str(csv_path)


def _annotate_peak_delta(metadata: MetadataExtraction, digitization: DigitizedCurve) -> None:
    reported = [peak.two_theta_deg for peak in metadata.reported_peaks if peak.two_theta_deg is not None]
    extracted = [peak.two_theta_deg for peak in digitization.peaks if peak.two_theta_deg is not None]
    if not reported or not extracted:
        return
    deltas: List[float] = []
    for expected in reported:
        deltas.append(min(abs(expected - actual) for actual in extracted))
    digitization.peak_position_delta_deg = float(sum(deltas) / len(deltas))
    if digitization.peak_position_delta_deg > 0.5:
        digitization.flagged_reasons.append("Reported peaks do not align closely with extracted peaks")
