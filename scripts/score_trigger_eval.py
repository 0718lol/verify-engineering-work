#!/usr/bin/env python3
"""Score observed implicit Skill activation against a labeled benchmark."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


REQUIRED_EXPERIMENT_FIELDS = ("skill_version", "model", "harness", "skill_commit")


class TriggerEvaluationError(ValueError):
    pass


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TriggerEvaluationError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TriggerEvaluationError(f"{path} must contain a JSON object")
    return value


def indexed_rows(rows: object, field: str, boolean_field: str) -> dict[str, dict]:
    if not isinstance(rows, list) or not rows:
        raise TriggerEvaluationError(f"{field} must be a non-empty array")
    indexed = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TriggerEvaluationError(f"{field}[{index}] must be an object")
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id.strip():
            raise TriggerEvaluationError(f"{field}[{index}].id must be a non-empty string")
        if row_id in indexed:
            raise TriggerEvaluationError(f"{field}: duplicate id {row_id}")
        if not isinstance(row.get(boolean_field), bool):
            raise TriggerEvaluationError(f"{row_id}: {boolean_field} must be boolean")
        indexed[row_id] = row
    return indexed


def wilson_interval(successes: int, count: int) -> list[float]:
    if count == 0:
        return [0.0, 0.0]
    z = 1.959963984540054
    rate = successes / count
    denominator = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def ratio(successes: int, count: int) -> dict:
    return {
        "value": successes / count if count else None,
        "confidence_interval_95": wilson_interval(successes, count) if count else None,
        "cases": count,
    }


def score(
    benchmark: dict,
    observations: dict,
    minimum_cases: int = 40,
    allow_subset: bool = False,
) -> dict:
    expected = indexed_rows(benchmark.get("cases"), "cases", "expected_trigger")
    observed = indexed_rows(observations.get("observations"), "observations", "triggered")
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if extra or (missing and not allow_subset):
        raise TriggerEvaluationError(f"observation IDs differ; missing={missing}, extra={extra}")
    if allow_subset:
        expected = {case_id: expected[case_id] for case_id in observed}

    true_positive = true_negative = false_positive = false_negative = 0
    false_positive_ids = []
    false_negative_ids = []
    for case_id, case in expected.items():
        wanted = case["expected_trigger"]
        actual = observed[case_id]["triggered"]
        if wanted and actual:
            true_positive += 1
        elif not wanted and not actual:
            true_negative += 1
        elif not wanted and actual:
            false_positive += 1
            false_positive_ids.append(case_id)
        else:
            false_negative += 1
            false_negative_ids.append(case_id)

    count = len(expected)
    benchmark_count = len(benchmark["cases"])
    experiment = observations.get("experiment")
    if experiment is not None and not isinstance(experiment, dict):
        raise TriggerEvaluationError("experiment must be an object when provided")
    experiment = experiment or {}
    missing_metadata = [
        field for field in REQUIRED_EXPERIMENT_FIELDS if experiment.get(field) in (None, "")
    ]
    invalid_metadata = [
        field
        for field in REQUIRED_EXPERIMENT_FIELDS
        if experiment.get(field) not in (None, "")
        and (not isinstance(experiment[field], str) or not experiment[field].strip())
    ]
    return {
        "benchmark_version": benchmark.get("version"),
        "experiment": experiment,
        "quality_gate": {
            "cases": count,
            "benchmark_cases": benchmark_count,
            "complete_coverage": count == benchmark_count,
            "minimum_cases": minimum_cases,
            "sample_size_sufficient": count >= minimum_cases,
            "missing_experiment_metadata": missing_metadata,
            "invalid_experiment_metadata": invalid_metadata,
            "publication_ready": (
                count == benchmark_count
                and count >= minimum_cases
                and not missing_metadata
                and not invalid_metadata
            ),
        },
        "confusion_matrix": {
            "true_positive": true_positive,
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
        },
        "metrics": {
            "recall": ratio(true_positive, true_positive + false_negative),
            "precision": ratio(true_positive, true_positive + false_positive),
            "specificity": ratio(true_negative, true_negative + false_positive),
            "accuracy": ratio(true_positive + true_negative, count),
        },
        "false_positive_ids": false_positive_ids,
        "false_negative_ids": false_negative_ids,
    }


def self_test() -> None:
    benchmark = {
        "version": "test",
        "cases": [
            {"id": "yes", "expected_trigger": True},
            {"id": "no", "expected_trigger": False},
        ],
    }
    observations = {
        "observations": [
            {"id": "yes", "triggered": True},
            {"id": "no", "triggered": False},
        ]
    }
    result = score(benchmark, observations, minimum_cases=2)
    assert result["metrics"]["accuracy"]["value"] == 1
    assert result["confusion_matrix"]["false_positive"] == 0
    try:
        score(benchmark, {"observations": observations["observations"][:1]}, minimum_cases=2)
    except TriggerEvaluationError:
        pass
    else:
        raise AssertionError("missing observations must be rejected")
    pilot = score(
        benchmark,
        {"observations": observations["observations"][:1]},
        minimum_cases=2,
        allow_subset=True,
    )
    assert pilot["quality_gate"]["complete_coverage"] is False
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", nargs="?", type=Path)
    parser.add_argument("observations", nargs="?", type=Path)
    parser.add_argument("--minimum-cases", type=int, default=40)
    parser.add_argument("--allow-subset", action="store_true", help="Score a non-publishable pilot subset")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.benchmark is None or args.observations is None:
            parser.error("benchmark and observations are required unless --self-test is used")
        if args.minimum_cases < 1:
            raise TriggerEvaluationError("--minimum-cases must be at least 1")
        result = score(
            load_json(args.benchmark),
            load_json(args.observations),
            minimum_cases=args.minimum_cases,
            allow_subset=args.allow_subset,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except TriggerEvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
