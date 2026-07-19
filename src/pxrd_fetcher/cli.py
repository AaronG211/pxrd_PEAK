"""CLI entrypoints."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings, get_settings
from .doctor import build_doctor_report
from .pipeline import PipelineError, regenerate_export, regenerate_review, run_pipeline

app = typer.Typer(add_completion=False, no_args_is_help=True, help="PXRD figure extraction CLI.")


@app.command()
def doctor(
    probe_openai: bool = typer.Option(
        True,
        "--probe-openai/--no-probe-openai",
        help="Probe the configured OpenAI model for multimodal structured-output support.",
    )
) -> None:
    """Verify runtime prerequisites."""

    settings = get_settings()
    report = build_doctor_report(settings, probe_openai=probe_openai)
    for check in report.checks:
        typer.echo("[{status}] {name}: {detail}".format(
            status=check.status.value.upper(),
            name=check.name,
            detail=check.detail,
        ))
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def run(
    pdf_or_dir: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
) -> None:
    """Run the local PXRD extraction pipeline."""

    settings = get_settings()
    try:
        summary = run_pipeline(pdf_or_dir, settings)
    except Exception as exc:
        typer.echo("Pipeline failed: {err}".format(err=exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(
        "Run {run_id} finished with status {status}. Accepted={accepted} Review={review}".format(
            run_id=summary["run_id"],
            status=summary["status"],
            accepted=summary["accepted_count"],
            review=summary["review_required_count"],
        )
    )
    typer.echo("Review HTML: {path}".format(path=summary["review_html"]))
    typer.echo("Accepted CSV: {path}".format(path=summary["accepted_csv"]))
    typer.echo("Summary JSON: {path}".format(path=summary["summary_json"]))


@app.command()
def auto(
    pdf_or_dir: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    out_name: str = typer.Option(None, "--out", help="Output directory name under outputs/."),
) -> None:
    """Run the fully-automatic v2 pipeline (accept/reject only, no review)."""

    from .v2 import AutoPipelineError, run_auto

    settings = get_settings()
    try:
        report = run_auto(pdf_or_dir, settings, out_name=out_name)
    except AutoPipelineError as exc:
        typer.echo("Auto pipeline failed: {err}".format(err=exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(
        "Accepted figures: {ok} | Rejected: {bad} | Report: {dir}/report.html".format(
            ok=report.n_accepted, bad=report.n_rejected, dir=report.run_dir
        )
    )


@app.command()
def review(run_id: int = typer.Argument(..., help="Run identifier from a previous `run`.")) -> None:
    """Regenerate the review HTML bundle for a run."""

    settings = get_settings()
    try:
        html_path = regenerate_review(run_id, settings)
    except Exception as exc:
        typer.echo("Review generation failed: {err}".format(err=exc), err=True)
        raise typer.Exit(code=1)
    typer.echo("Review HTML: {path}".format(path=html_path))


@app.command()
def export(run_id: int = typer.Argument(..., help="Run identifier from a previous `run`.")) -> None:
    """Export accepted series for a run."""

    settings = get_settings()
    try:
        csv_path, json_path = regenerate_export(run_id, settings)
    except Exception as exc:
        typer.echo("Export failed: {err}".format(err=exc), err=True)
        raise typer.Exit(code=1)
    typer.echo("Accepted CSV: {path}".format(path=csv_path))
    typer.echo("Summary JSON: {path}".format(path=json_path))


def main() -> None:
    """Console-script entrypoint."""

    app()


if __name__ == "__main__":
    main()
