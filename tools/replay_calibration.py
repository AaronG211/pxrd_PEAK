"""Replay frame selection + calibration offline for every figure in run dirs.

Uses saved crops and the analysis stored in result.json — zero API calls.
Usage: python tools/replay_calibration.py outputs/auto-test-01 [more dirs...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from pxrd_fetcher.ocr import get_ocr_backend
from pxrd_fetcher.v2.calibrate import consensus_calibration
from pxrd_fetcher.v2.pipeline import _ocr_axis_pairs, choose_frame
from pxrd_fetcher.v2.schemas import FigureAnalysis
from pxrd_fetcher.v2.vision import detect_x_tick_pixels


def replay(result_file: Path, ocr) -> None:
    d = json.loads(result_file.read_text())
    if not d.get("analysis") or not d["analysis"].get("is_pxrd"):
        return
    crop_path = d["crop_path"]
    img = np.asarray(Image.open(crop_path).convert("RGB"))
    analysis = FigureAnalysis.model_validate(d["analysis"])
    frame = choose_frame(img, analysis, ocr)
    if frame is None:
        print(f"{d['figure_id']}: NO FRAME")
        return
    ticks = detect_x_tick_pixels(img, frame)
    pairs = _ocr_axis_pairs(img, frame, ocr)
    ev = consensus_calibration(
        frame=frame,
        tick_pixels=ticks,
        llm_tick_values=d["analysis"].get("x_tick_values", []),
        ocr_pairs=pairs,
    )
    fit_txt = ""
    if ev.fit:
        x0, _, x1, _ = frame
        fit_txt = f" span={ev.fit.to_deg(x0):.1f}..{ev.fit.to_deg(x1):.1f} rmse={ev.fit.rmse_deg:.3f}"
    print(
        f"{d['figure_id']}: frame={frame} ticks={len(ticks)} ocr={len(pairs)} "
        f"llm={d['analysis'].get('x_tick_values')} -> {ev.status}{fit_txt}"
        + (f" | {'; '.join(ev.notes)}" if ev.notes else "")
    )


def main(run_dirs):
    ocr = get_ocr_backend("rapidocr")
    for run_dir in run_dirs:
        for rf in sorted(Path(run_dir).rglob("result.json")):
            try:
                replay(rf, ocr)
            except Exception as exc:  # noqa: BLE001
                print(f"{rf}: ERROR {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
