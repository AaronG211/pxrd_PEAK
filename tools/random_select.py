"""Draw a reproducible random sample for manual accuracy auditing.

Selects N_GROUPS x N_PER_GROUP distinct figures (no overlap) from every
accepted/partial figure in a run, splits them into group folders, and copies
each figure's 3-panel qa_montage.png (crop | overlay | replot) in — overlay is
the most diagnostic view for judging trace accuracy, since it shows exactly
where the extracted trace departs from the original ink. Each folder also gets
a review.csv with an empty verdict column to fill in while auditing.

Usage: python tools/random_select.py <run_dir> <out_dir> [--groups 5] [--per-group 50] [--seed 42]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--groups", type=int, default=5)
    ap.add_argument("--per-group", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    pool = []
    for rf in sorted(args.run_dir.rglob("result.json")):
        d = json.loads(rf.read_text())
        if d["status"] not in ("accepted", "partial"):
            continue
        fig_dir = rf.parent
        montage = fig_dir / "qa_montage.png"
        if not montage.exists():
            continue
        pool.append({
            "figure_id": d["figure_id"],
            "paper_id": d["paper_id"],
            "status": d["status"],
            "confidence": d.get("confidence", 0),
            "n_series": sum(1 for s in d.get("series", []) if s.get("status") == "accepted"),
            "montage": montage,
        })

    need = args.groups * args.per_group
    if need > len(pool):
        raise SystemExit(f"need {need} figures but pool only has {len(pool)}")

    rng = random.Random(args.seed)
    sample = rng.sample(pool, need)   # sample WITHOUT replacement -> 5 disjoint groups
    rng.shuffle(sample)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for g in range(args.groups):
        group_items = sample[g * args.per_group:(g + 1) * args.per_group]
        group_dir = args.out_dir / f"batch_{g+1}"
        group_dir.mkdir(exist_ok=True)
        rows = []
        for item in group_items:
            dest = group_dir / f"{item['figure_id']}.png"
            shutil.copy2(item["montage"], dest)
            row = {
                "figure_id": item["figure_id"],
                "paper_id": item["paper_id"],
                "status": item["status"],
                "confidence": round(item["confidence"], 4),
                "n_series": item["n_series"],
                "verdict": "",          # fill in: correct / minor_issue / wrong
                "notes": "",
            }
            rows.append(row)
        all_rows.extend(rows)
        with (group_dir / "review.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"batch_{g+1}: {len(rows)} figures -> {group_dir}")

    with (args.out_dir / "all_samples.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["batch"] + list(all_rows[0].keys()))
        w.writeheader()
        for g in range(args.groups):
            for row in all_rows[g * args.per_group:(g + 1) * args.per_group]:
                w.writerow({"batch": g + 1, **row})

    print(f"\ntotal sampled: {len(sample)} (seed={args.seed}, pool={len(pool)})")
    print(f"summary manifest: {args.out_dir / 'all_samples.csv'}")


if __name__ == "__main__":
    main()
