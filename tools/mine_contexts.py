"""Text-mine literature context for PXRD curves into the contexts schema.

One API call per paper: pre-chunked inputs (first-page header, XRD methods
sentences, per-figure caption candidates, per-curve color/label table) go in;
structured paper/figure/curve context comes out and is written to pxrd.db
(papers UPDATE, figures UPDATE, contexts INSERT OR REPLACE) with provenance.

The curve color is the join key between unnamed curves and captions
("solvothermal (black), Hg lamp (orange)"). The model is told to fill only
text-supported fields and cite where each curve's context came from.

Usage: python tools/mine_contexts.py outputs/pxrd.db --model gpt-5.4 --limit 20
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional

import fitz
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pxrd_fetcher.config import get_settings  # noqa: E402
from pxrd_fetcher.openai_service import OpenAIService  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ---- structured output schema ------------------------------------------------
class PaperFacts(BaseModel):
    title: Optional[str] = None
    instrument: Optional[str] = None
    radiation: Optional[str] = None
    wavelength_angstrom: Optional[float] = None
    voltage_kv: Optional[float] = None
    current_ma: Optional[float] = None
    xrd_geometry: Optional[str] = None
    scan_range: Optional[str] = None
    scan_step: Optional[str] = None
    scan_speed: Optional[str] = None


class FigureContext(BaseModel):
    figure_id: str
    caption_text: Optional[str] = None
    figure_purpose: Optional[str] = None       # phase_id/refinement/stability_test/time_series/comparison/in_situ
    measurement_condition: Optional[str] = None


class CurveContext(BaseModel):
    series_id: str
    canonical_material: Optional[str] = None
    material_class: Optional[str] = None
    compound_alias: Optional[str] = None
    chemical_formula: Optional[str] = None
    metal_center: Optional[str] = None
    linkage_type: Optional[str] = None
    building_blocks: Optional[str] = None
    topology_net: Optional[str] = None
    curve_role: Optional[str] = None
    sim_model: Optional[str] = None
    reference_material: Optional[str] = None
    jcpds_card: Optional[str] = None
    space_group: Optional[str] = None
    unit_cell_a: Optional[float] = None
    unit_cell_b: Optional[float] = None
    unit_cell_c: Optional[float] = None
    unit_cell_alpha: Optional[float] = None
    unit_cell_beta: Optional[float] = None
    unit_cell_gamma: Optional[float] = None
    rwp: Optional[float] = None
    rp: Optional[float] = None
    ccdc_number: Optional[str] = None
    crystallite_size_nm: Optional[float] = None
    d_spacing_angstrom: Optional[float] = None
    hkl_peaks: Optional[str] = None            # JSON: [{"hkl":"100","two_theta":4.7}]
    sample_form: Optional[str] = None
    composite_components: Optional[str] = None
    guest_or_solvate: Optional[str] = None
    substrate: Optional[str] = None
    synthesis_method: Optional[str] = None
    synthesis_temp_c: Optional[float] = None
    synthesis_duration_h: Optional[float] = None
    solvent: Optional[str] = None
    solvent_ratio: Optional[str] = None
    catalyst: Optional[str] = None
    synthesis_atmosphere: Optional[str] = None
    synthesis_stage: Optional[str] = None
    activation_method: Optional[str] = None
    treatment_type: Optional[str] = None
    treatment_agent: Optional[str] = None
    treatment_concentration: Optional[str] = None
    treatment_temp_c: Optional[float] = None
    treatment_duration_h: Optional[float] = None
    series_variable: Optional[str] = None
    series_value: Optional[str] = None
    measurement_state: Optional[str] = None
    extraction_source: Optional[str] = None    # "caption p3" / "methods" / "body p5"
    extraction_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    notes: Optional[str] = None


class PaperExtraction(BaseModel):
    paper: PaperFacts
    figures: List[FigureContext]
    curves: List[CurveContext]


SYSTEM = (
    "You extract structured literature context for powder-XRD curves that were "
    "digitised from figures in a chemistry paper. You are given: the paper's "
    "first-page header, XRD-related methods sentences, and for each figure its "
    "digitised curves (series_id, color, in-figure label, material, state) plus "
    "caption candidates from that page.\n"
    "Rules:\n"
    "- Fill ONLY fields the given text supports; leave everything else null. "
    "Never guess or use outside knowledge about these materials.\n"
    "- Match curves to caption text via their COLOR and label (captions often "
    "say 'solvothermal (black), Hg lamp (orange)').\n"
    "- curve_role: experimental | simulated | pawley | rietveld | difference | "
    "reference. sim_model: stacking mode (AA/AB/eclipsed/staggered), space "
    "group, or topology of a simulated model.\n"
    "- When a multi-curve figure sweeps one variable (time, ratio, treatment, "
    "temperature), set series_variable once (same string for all its curves) "
    "and series_value per curve.\n"
    "- Numbers: convert temperatures to Celsius, durations to hours, "
    "wavelengths to Angstrom.\n"
    "- measurement_state is ONLY for in-situ/operando measurement conditions "
    "(applied pressure, electrochemical potential, measurement temperature). "
    "It is NOT the curve role, NOT the series value, NOT the sample state — "
    "leave it null for ordinary ex-situ room-temperature patterns.\n"
    "- Per curve set extraction_source ('caption p3' / 'methods' / 'header') "
    "and extraction_confidence (0-1).\n"
    "- Return every figure_id given; return curves only when you can fill at "
    "least one content field."
)

CAP_RE = re.compile(r"(Figure\s?\d+[.:|]?\s)", re.I)
XRD_SENT = re.compile(
    r"[^.]*?(?:PXRD|XRD|X-?ray diffraction|diffractometer|Cu\s?K|synchrotron|"
    r"wavelength|radiation)[^.]*\.", re.I)


def paper_input(pid: str, con: sqlite3.Connection) -> Optional[str]:
    pdf = ROOT / "sample_papers" / f"{pid}.pdf"
    if not pdf.exists():
        return None
    doc = fitz.open(pdf)
    header = doc[0].get_text("text").replace("\n", " ")[:600]
    full = " ".join(doc[i].get_text("text").replace("\n", " ")
                    for i in range(len(doc)))

    methods, seen = [], set()
    for m in XRD_SENT.finditer(full):
        s = m.group(0).strip()
        if 40 < len(s) < 500 and re.search(
                r"diffractometer|Cu\s?K|radiation|wavelength|recorded|collected|"
                r"measured|performed|scan", s, re.I):
            if s[:60] not in seen:
                seen.add(s[:60])
                methods.append(s)
    parts = [f"PAPER {pid}", f"HEADER: {header}",
             "XRD METHODS SENTENCES:\n" + "\n".join(f"- {s}" for s in methods[:8])]

    figs = con.execute(
        "SELECT figure_id, page_number FROM figures WHERE paper_id=? "
        "ORDER BY figure_id", (pid,)).fetchall()
    for fid, pno in figs:
        curves = con.execute(
            "SELECT series_id, color, label, material_name, sample_state "
            "FROM curves WHERE figure_id=?", (fid,)).fetchall()
        rows = "\n".join(
            f"  {sid} | color={c or '?'} | label={l or '?'} | "
            f"material={m or '?'} | state={s or '?'}"
            for sid, c, l, m, s in curves)
        page_text = doc[pno - 1].get_text("text").replace("\n", " ")
        caps = []
        for m in CAP_RE.finditer(page_text):
            cap = page_text[m.start():m.start() + 700]
            if re.search(r"PXRD|XRD|diffract", cap, re.I):
                caps.append(cap)
        cap_block = "\n".join(f"  CANDIDATE: {c}" for c in caps[:3]) or "  (none found)"
        parts.append(f"FIGURE {fid} (page {pno})\nCURVES:\n{rows}\n"
                     f"CAPTION CANDIDATES:\n{cap_block}")
    doc.close()
    return "\n\n".join(parts)


CURVE_COLS = [f for f in CurveContext.model_fields if f != "series_id"]


def write_result(con: sqlite3.Connection, pid: str, res: PaperExtraction,
                 model_id: str) -> tuple[int, int]:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    p = res.paper
    con.execute(
        "UPDATE papers SET title=COALESCE(?,title), instrument=?, radiation=?, "
        "wavelength_angstrom=?, voltage_kv=?, current_ma=?, xrd_geometry=?, "
        "scan_range=?, scan_step=?, scan_speed=? WHERE paper_id=?",
        (p.title, p.instrument, p.radiation, p.wavelength_angstrom,
         p.voltage_kv, p.current_ma, p.xrd_geometry, p.scan_range,
         p.scan_step, p.scan_speed, pid))

    valid_figs = {r[0] for r in con.execute(
        "SELECT figure_id FROM figures WHERE paper_id=?", (pid,))}
    n_f = 0
    for f in res.figures:
        if f.figure_id not in valid_figs:
            continue
        con.execute(
            "UPDATE figures SET caption_text=?, figure_purpose=?, "
            "measurement_condition=? WHERE figure_id=?",
            (f.caption_text, f.figure_purpose, f.measurement_condition,
             f.figure_id))
        n_f += 1

    valid_curves = {r[0] for r in con.execute(
        "SELECT series_id FROM curves WHERE paper_id=?", (pid,))}
    n_c = 0
    cols = ", ".join(["series_id"] + CURVE_COLS + ["extracted_by", "extracted_at"])
    marks = ",".join("?" * (len(CURVE_COLS) + 3))
    for c in res.curves:
        if c.series_id not in valid_curves:
            continue
        vals = [c.series_id] + [getattr(c, k) for k in CURVE_COLS] + [model_id, now]
        con.execute(f"INSERT OR REPLACE INTO contexts({cols}) VALUES ({marks})",
                    vals)
        n_c += 1
    con.commit()
    return n_f, n_c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("--model", default="gpt-5.4")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    settings = get_settings()
    settings.chatgpt_model = args.model
    if settings.openai_timeout_seconds < 240.0:
        settings.openai_timeout_seconds = 240.0
    svc = OpenAIService(settings)

    con = sqlite3.connect(args.db)
    papers = [r[0] for r in con.execute(
        "SELECT paper_id FROM papers ORDER BY paper_id LIMIT ?", (args.limit,))]

    t0 = time.time()
    in_chars = out_rows = 0
    for i, pid in enumerate(papers, 1):
        text = paper_input(pid, con)
        if text is None:
            print(f"[{i}/{len(papers)}] {pid}: pdf missing, skipped", flush=True)
            continue
        in_chars += len(text)
        try:
            res = svc._parse_response(
                PaperExtraction, system_prompt=SYSTEM, user_text=text)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(papers)}] {pid}: ERROR {exc}", flush=True)
            continue
        n_f, n_c = write_result(con, pid, res, args.model)
        out_rows += n_c
        print(f"[{i}/{len(papers)}] {pid}: figures={n_f} curve-contexts={n_c} "
              f"radiation={res.paper.radiation!r}", flush=True)

    dt = time.time() - t0
    print(f"\nDONE {len(papers)} papers in {dt/60:.1f} min | "
          f"contexts written: {out_rows} | "
          f"input ~{in_chars/4/1000:.0f}k tokens (chars/4 estimate)")
    con.close()


if __name__ == "__main__":
    main()
