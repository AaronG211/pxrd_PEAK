"""Static HTML gallery report for a v2 run — informational only, no actions."""

from __future__ import annotations

import html
from pathlib import Path

from .schemas import RunReport

_CSS = """
body{font-family:-apple-system,Helvetica,sans-serif;margin:24px;background:#fafafa}
h1{font-size:20px} h2{font-size:16px;margin-top:28px}
.fig{display:flex;gap:16px;background:#fff;border:1px solid #ddd;border-radius:8px;
     padding:12px;margin:10px 0;align-items:flex-start}
.fig img{max-width:520px;max-height:340px;border:1px solid #eee}
.meta{font-size:13px;line-height:1.5}
.ok{color:#0a7d28;font-weight:600}.bad{color:#b00020;font-weight:600}
.part{color:#a06000;font-weight:600}
.evidence{color:#666;font-size:12px}
table.summary{border-collapse:collapse;font-size:13px}
table.summary td,table.summary th{border:1px solid #ccc;padding:4px 10px}
"""


def write_html_report(report: RunReport, out_path: Path) -> Path:
    run_dir = Path(report.run_dir)
    parts = [
        "<!doctype html><meta charset='utf-8'><title>PXRD auto run</title>",
        f"<style>{_CSS}</style>",
        "<h1>PXRD Fetcher — fully automatic run</h1>",
        "<table class='summary'><tr><th>papers</th><th>figures accepted</th>"
        f"<th>figures rejected</th></tr><tr><td>{len(report.papers)}</td>"
        f"<td>{report.n_accepted}</td><td>{report.n_rejected}</td></tr></table>",
    ]
    for paper in report.papers:
        parts.append(f"<h2>{html.escape(paper.paper_id)}</h2>")
        if paper.title:
            parts.append(f"<div class='meta'>{html.escape(paper.title[:160])}</div>")
        for fig in paper.figures:
            cls = {"accepted": "ok", "partial": "part"}.get(fig.status, "bad")
            img_src = fig.overlay_path or fig.crop_path
            rel = _rel(run_dir, Path(img_src)) if img_src else None
            img_tag = f"<a href='{rel}'><img src='{rel}'></a>" if rel else ""
            series_rows = "".join(
                "<li>{lab} — <span class='{c}'>{st}</span>"
                " <span class='evidence'>conf={conf:.2f} snap={snap:.2f}"
                " prec={prec:.2f} rec={rec:.2f}{rr}</span></li>".format(
                    lab=html.escape(s.label or s.series_id),
                    c="ok" if s.status == "accepted" else "bad",
                    st=s.status,
                    conf=s.confidence,
                    snap=s.evidence.snap_rate,
                    prec=s.evidence.pixel_precision,
                    rec=s.evidence.pixel_recall,
                    rr=f" | {html.escape(s.reject_reason)}" if s.reject_reason else "",
                )
                for s in fig.series
            )
            calib = fig.calibration
            calib_txt = (
                f"calibration: {calib.status}"
                + (f", rmse={calib.fit.rmse_deg:.3f}°" if calib.fit else "")
                + (f", agreement={calib.agreement_deg:.2f}°" if calib.agreement_deg is not None else "")
            )
            parts.append(
                f"<div class='fig'>{img_tag}<div class='meta'>"
                f"<b>{html.escape(fig.figure_id)}</b> — <span class='{cls}'>{fig.status}</span>"
                + (f" ({html.escape(fig.reject_reason)})" if fig.reject_reason else "")
                + f"<br>{calib_txt}<ul>{series_rows}</ul></div></div>"
            )
    out_path.write_text("\n".join(parts))
    return out_path


def _rel(base: Path, target: Path) -> str:
    try:
        return str(target.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(target)
