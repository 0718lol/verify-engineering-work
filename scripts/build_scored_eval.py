#!/usr/bin/env python3
"""Merge blinded rubric scores with paired-run provenance for score_eval.py."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from run_trigger_eval import atomic_write_json
from score_eval import validate_case


def load_object(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def merge(run_payload: dict, mapping_payload: dict, scores_payload: dict) -> dict:
    runs = run_payload.get("runs")
    mapping_rows = mapping_payload.get("mapping")
    score_rows = scores_payload.get("scores")
    if not isinstance(runs, list) or not runs:
        raise ValueError("run payload must contain runs")
    if not isinstance(mapping_rows, list) or not isinstance(score_rows, list):
        raise ValueError("mapping and scores payloads must contain arrays")

    mapping = {}
    for row in mapping_rows:
        if not isinstance(row, dict) or not isinstance(row.get("blind_id"), str):
            raise ValueError("invalid mapping row")
        if row["blind_id"] in mapping:
            raise ValueError(f"duplicate mapping blind_id: {row['blind_id']}")
        mapping[row["blind_id"]] = row
    scores = {}
    for row in score_rows:
        if not isinstance(row, dict) or not isinstance(row.get("blind_id"), str):
            raise ValueError("invalid score row")
        if row["blind_id"] in scores:
            raise ValueError(f"duplicate score blind_id: {row['blind_id']}")
        scores[row["blind_id"]] = row

    cases = []
    for index, run in enumerate(runs):
        blind = run.get("blind_id") if isinstance(run, dict) else None
        if blind not in mapping or blind not in scores:
            raise ValueError(f"missing mapping or score for run: {blind}")
        map_row = mapping[blind]
        score_row = scores[blind]
        case = {
            "id": map_row.get("task_id"),
            "variant": map_row.get("variant"),
            "scores": score_row.get("scores"),
            "critical_failures": score_row.get("critical_failures", []),
            "tokens": (
                run["input_tokens"] + run["output_tokens"]
                if isinstance(run.get("input_tokens"), (int, float))
                and isinstance(run.get("output_tokens"), (int, float))
                else None
            ),
            "duration_seconds": run.get("duration_seconds"),
            "human_interventions": score_row.get("human_interventions", 0),
            "blind_id": blind,
        }
        validate_case(case, index)
        cases.append(case)
    if set(mapping) != {run["blind_id"] for run in runs}:
        raise ValueError("mapping contains rows not present in run payload")
    if set(scores) != {run["blind_id"] for run in runs}:
        raise ValueError("scores contain rows not present in run payload")
    return {"experiment": run_payload.get("experiment", {}), "cases": cases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_output", type=Path)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("blind_scores", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        result = merge(
            load_object(args.run_output, "run output"),
            load_object(args.mapping, "mapping"),
            load_object(args.blind_scores, "blind scores"),
        )
        atomic_write_json(args.output, result)
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
