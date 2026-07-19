"""SQLite persistence helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from .postprocess import align_intensity_to_x_grid
from .schemas import PipelineFigureResult
from .utils import dump_json, load_json, now_utc


class RunRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_path: str
    status: str = "running"
    note: str = ""
    created_at: datetime = Field(default_factory=now_utc)
    finished_at: Optional[datetime] = None


class PaperRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runrecord.id", index=True)
    paper_id: str = Field(index=True)
    source_path: str
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    metadata_json: str = "{}"


class FigureRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runrecord.id", index=True)
    paper_db_id: int = Field(foreign_key="paperrecord.id", index=True)
    figure_key: str = Field(index=True)
    page_number: int
    figure_label: Optional[str] = None
    caption: str = ""
    context: str = ""
    bbox_json: str = "[]"
    page_image_path: str = ""
    crop_image_path: str = ""
    status: str = "candidate"
    keyword_score: float = 0.0
    confidence: float = 0.0
    reliability_score: float = 0.0
    review_required: bool = False
    flagged_reasons_json: str = "[]"
    classification_json: str = "{}"
    metadata_json: str = "{}"
    digitization_json: str = "{}"


class SeriesRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    figure_id: int = Field(foreign_key="figurerecord.id", index=True)
    series_key: str = Field(index=True)
    label: Optional[str] = None
    status: str = "accepted"
    multi_series_detected: bool = False
    raw_point_count: int = 0
    processed_point_count: int = 0
    csv_path: Optional[str] = None
    replot_image_path: Optional[str] = None
    summary_json: str = "{}"


class PointRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    series_id: int = Field(foreign_key="seriesrecord.id", index=True)
    order_index: int
    two_theta_deg: float
    intensity_raw: float
    intensity_norm: float


class ReviewRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runrecord.id", index=True)
    figure_id: int = Field(foreign_key="figurerecord.id", index=True)
    status: str
    summary: str = ""
    html_path: Optional[str] = None


class Database:
    """Thin persistence layer."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.engine = create_engine(
            "sqlite:///{path}".format(path=database_path),
            connect_args={"check_same_thread": False},
        )

    def create_all(self) -> None:
        SQLModel.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            yield session

    def create_run(self, source_path: Path) -> RunRecord:
        run = RunRecord(source_path=str(source_path))
        with self.session() as session:
            session.add(run)
            session.commit()
            session.refresh(run)
        return run

    def finish_run(self, run_id: int, *, status: str, note: str = "") -> RunRecord:
        with self.session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None
            run.status = status
            run.note = note
            run.finished_at = now_utc()
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def create_paper(self, run_id: int, metadata: Dict[str, object]) -> PaperRecord:
        paper = PaperRecord(
            run_id=run_id,
            paper_id=str(metadata["paper_id"]),
            source_path=str(metadata["source_path"]),
            doi=metadata.get("doi"),
            title=metadata.get("title"),
            year=metadata.get("year"),
            metadata_json=dump_json(metadata),
        )
        with self.session() as session:
            session.add(paper)
            session.commit()
            session.refresh(paper)
        return paper

    def store_figure_result(
        self,
        *,
        run_id: int,
        paper_db_id: int,
        result: PipelineFigureResult,
    ) -> FigureRecord:
        figure = FigureRecord(
            run_id=run_id,
            paper_db_id=paper_db_id,
            figure_key=result.candidate.candidate_id,
            page_number=result.candidate.page_number,
            figure_label=result.metadata.figure_label or result.classification.figure_label,
            caption=result.candidate.caption,
            context=result.candidate.context,
            bbox_json=dump_json(result.candidate.bbox),
            page_image_path=result.candidate.page_image_path,
            crop_image_path=result.candidate.crop_image_path,
            status=result.digitization.status,
            keyword_score=result.candidate.keyword_score,
            confidence=result.classification.confidence,
            reliability_score=result.digitization.reliability_score,
            review_required=result.digitization.status == "review_required",
            flagged_reasons_json=dump_json(result.digitization.flagged_reasons),
            classification_json=result.classification.model_dump_json(indent=2),
            metadata_json=result.metadata.model_dump_json(indent=2),
            digitization_json=result.digitization.summary_json(),
        )

        with self.session() as session:
            session.add(figure)
            session.commit()
            session.refresh(figure)

            for series_result in result.digitization.series:
                series = SeriesRecord(
                    figure_id=figure.id,
                    series_key=series_result.series_key
                    or "{key}-s{index:02d}".format(
                        key=result.candidate.candidate_id,
                        index=series_result.series_order_from_top,
                    ),
                    label=series_result.series_label
                    or result.metadata.series_label
                    or "curve-{index:02d}".format(index=series_result.series_order_from_top),
                    status=series_result.status,
                    multi_series_detected=result.digitization.multi_series_detected,
                    raw_point_count=len(series_result.raw_two_theta),
                    processed_point_count=len(series_result.processed_two_theta),
                    csv_path=series_result.csv_path,
                    replot_image_path=series_result.replot_image_path,
                    summary_json=series_result.summary_json(),
                )
                session.add(series)
                session.commit()
                session.refresh(series)

                aligned_raw_values = align_intensity_to_x_grid(
                    series_result.raw_two_theta,
                    series_result.raw_intensity,
                    series_result.processed_two_theta,
                )
                raw_values = (
                    aligned_raw_values.astype(float).tolist()
                    if aligned_raw_values.size == len(series_result.processed_two_theta)
                    else list(series_result.processed_intensity)
                )
                for index, (two_theta, raw_intensity, norm_intensity) in enumerate(
                    zip(
                        series_result.processed_two_theta,
                        raw_values,
                        series_result.processed_intensity,
                    )
                ):
                    session.add(
                        PointRecord(
                            series_id=series.id,
                            order_index=index,
                            two_theta_deg=float(two_theta),
                            intensity_raw=float(raw_intensity),
                            intensity_norm=float(norm_intensity),
                        )
                    )

            review = ReviewRecord(
                run_id=run_id,
                figure_id=figure.id,
                status="review_required" if figure.review_required else "auto_accepted",
                summary="; ".join(result.digitization.flagged_reasons) or result.classification.reason,
            )
            session.add(review)
            session.commit()
            session.refresh(figure)
            return figure

    def attach_review_html(self, run_id: int, html_path: Path) -> None:
        with self.session() as session:
            reviews = session.exec(
                select(ReviewRecord).where(ReviewRecord.run_id == run_id)
            ).all()
            for review in reviews:
                review.html_path = str(html_path)
                session.add(review)
            session.commit()

    def get_run(self, run_id: int) -> Optional[RunRecord]:
        with self.session() as session:
            return session.get(RunRecord, run_id)

    def get_run_bundle(self, run_id: int) -> Dict[str, object]:
        with self.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise ValueError("Run {run_id} not found.".format(run_id=run_id))
            papers = session.exec(select(PaperRecord).where(PaperRecord.run_id == run_id)).all()
            figures = session.exec(select(FigureRecord).where(FigureRecord.run_id == run_id)).all()
            reviews = session.exec(select(ReviewRecord).where(ReviewRecord.run_id == run_id)).all()
            series = session.exec(
                select(SeriesRecord).where(SeriesRecord.figure_id.in_([figure.id for figure in figures] or [-1]))
            ).all()
            points = session.exec(
                select(PointRecord).where(PointRecord.series_id.in_([row.id for row in series] or [-1]))
            ).all()

        grouped_points: Dict[int, List[PointRecord]] = {}
        for point in points:
            grouped_points.setdefault(point.series_id, []).append(point)

        return {
            "run": run,
            "papers": papers,
            "figures": figures,
            "reviews": reviews,
            "series": series,
            "points_by_series": grouped_points,
        }

    def list_exportable_series(self, run_id: int) -> List[Dict[str, object]]:
        bundle = self.get_run_bundle(run_id)
        figure_map = {figure.id: figure for figure in bundle["figures"]}
        items: List[Dict[str, object]] = []
        for series in bundle["series"]:
            figure = figure_map.get(series.figure_id)
            if series.status != "accepted" or figure is None:
                continue
            points = sorted(
                bundle["points_by_series"].get(series.id, []),
                key=lambda item: item.order_index,
            )
            items.append(
                {
                    "series": series,
                    "figure": figure,
                    "points": points,
                    "summary": load_json(series.summary_json, {}),
                }
            )
        return items
