"""Review artifact generation."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, List

from .storage import Database
from .utils import ensure_dir, load_json, relative_to


def generate_review_html(database: Database, run_id: int, run_dir: Path) -> Path:
    """Render a simple HTML report for a run."""

    bundle = database.get_run_bundle(run_id)
    run = bundle["run"]
    figures = bundle["figures"]
    papers = {paper.id: paper for paper in bundle["papers"]}

    cards: List[str] = []
    for figure in figures:
        paper = papers.get(figure.paper_db_id)
        digitization = load_json(figure.digitization_json, {})
        metadata = load_json(figure.metadata_json, {})
        classification = load_json(figure.classification_json, {})
        flagged = digitization.get("flagged_reasons", [])
        series = digitization.get("series", [])
        curve_analysis = digitization.get("curve_analysis") or {}
        series_summary = ", ".join(
            "{label} [{status}]".format(
                label=(item.get("series_label") or item.get("series_key") or "curve"),
                status=item.get("status") or "unknown",
            )
            for item in series
        ) or "None"

        cards.append(
            """
            <section class="card">
              <div class="meta">
                <div><strong>Paper</strong>: {paper_id}</div>
                <div><strong>Figure</strong>: {figure_label}</div>
                <div><strong>Status</strong>: <span class="badge {badge_class}">{status}</span></div>
                <div><strong>PXRD Confidence</strong>: {confidence:.2f}</div>
                <div><strong>Reliability</strong>: {reliability:.2f}</div>
                <div><strong>Caption</strong>: {caption}</div>
                <div><strong>Material</strong>: {material}</div>
                <div><strong>Sample</strong>: {sample}</div>
                <div><strong>AI Curve Count</strong>: {ai_curve_count}</div>
                <div><strong>Extracted Series</strong>: {series_summary}</div>
                <div><strong>Notes</strong>: {notes}</div>
                <div><strong>Flags</strong>: {flags}</div>
                <div><strong>Reason</strong>: {reason}</div>
              </div>
              <div class="images">
                {crop_html}
                {annotated_html}
                {series_replots}
              </div>
            </section>
            """.format(
                paper_id=html.escape(getattr(paper, "paper_id", "unknown")),
                figure_label=html.escape(figure.figure_label or figure.figure_key),
                badge_class="warn" if figure.review_required else "ok",
                status=html.escape(figure.status),
                confidence=float(figure.confidence),
                reliability=float(figure.reliability_score),
                caption=html.escape(figure.caption or "N/A"),
                material=html.escape(metadata.get("material_name") or "N/A"),
                sample=html.escape(metadata.get("sample_name") or "N/A"),
                ai_curve_count=html.escape(str(curve_analysis.get("curve_count") or "N/A")),
                series_summary=html.escape(series_summary),
                notes=html.escape(metadata.get("notes") or "N/A"),
                flags=html.escape(", ".join(flagged) or "None"),
                reason=html.escape(classification.get("reason") or digitization.get("reason") or "N/A"),
                crop_html=_image_html(run_dir, figure.crop_image_path, "Figure crop"),
                annotated_html=_image_html(
                    run_dir,
                    digitization.get("annotated_image_path", ""),
                    "Annotated calibration",
                ),
                series_replots="".join(
                    _image_html(
                        run_dir,
                        item.get("replot_image_path", ""),
                        "Replot - {label}".format(
                            label=item.get("series_label")
                            or item.get("series_key")
                            or "curve"
                        ),
                    )
                    for item in series
                ),
            )
        )

    html_path = ensure_dir(run_dir) / "review.html"
    html_path.write_text(
        """
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <title>PXRD Fetcher Review - Run {run_id}</title>
          <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; background: #f6f8fb; color: #1d2939; }}
            h1 {{ margin-bottom: 0.5rem; }}
            .summary {{ margin-bottom: 2rem; color: #475467; }}
            .card {{ background: white; border-radius: 14px; padding: 1rem; margin-bottom: 1.25rem; box-shadow: 0 10px 30px rgba(16, 24, 40, 0.08); }}
            .meta {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.5rem 1rem; margin-bottom: 1rem; }}
            .images {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }}
            figure {{ margin: 0; }}
            img {{ max-width: 100%; border-radius: 10px; border: 1px solid #d0d5dd; background: white; }}
            .badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; color: white; font-size: 0.85rem; }}
            .badge.ok {{ background: #087443; }}
            .badge.warn {{ background: #b54708; }}
          </style>
        </head>
        <body>
          <h1>PXRD Fetcher Review</h1>
          <p class="summary">Run {run_id} | status: {status} | source: {source}</p>
          {cards}
        </body>
        </html>
        """.format(
            run_id=run.id,
            status=html.escape(run.status),
            source=html.escape(run.source_path),
            cards="\n".join(cards) or "<p>No figures were stored for this run.</p>",
        ),
        encoding="utf-8",
    )
    database.attach_review_html(run_id, html_path)
    return html_path


def _image_html(run_dir: Path, image_path_value, label: str) -> str:
    if not image_path_value:
        return ""
    image_path = Path(str(image_path_value))
    if image_path.name in {"", "."}:
        return ""
    if not image_path.exists():
        return ""
    return (
        '<figure><figcaption>{label}</figcaption><img src="{src}" alt="{alt}"></figure>'
    ).format(
        label=html.escape(label),
        src=html.escape(relative_to(run_dir, image_path)),
        alt=html.escape(label),
    )
