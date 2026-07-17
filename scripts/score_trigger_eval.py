#!/usr/bin/env python3
"""Score observed implicit Skill activation against a labeled benchmark."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


REQUIRED_EXPERIMENT_FIELDS = (
    "skill_version",
    "model",
    "harness",
    "skill_commit",
    "detection_schema_version",
    "negative_detection_verified",
)


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


def indexed_rows(
    rows: object,
    field: str,
    boolean_field: str,
    allow_indeterminate: bool = False,
) -> dict[str, dict]:
    if not isinstance(rows, list) or not rows:
        raise TriggerEvaluationError(f"{field} must be a non-empty array")
    indexed = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TriggerEvaluationError(f"{field}[{index}] must be an object")
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id.strip():
            raise TriggerEvaluationError(f"{field}[{index}].id must be a non-empty string")
        row_id = row_id.strip()
        if row_id in indexed:
            raise TriggerEvaluationError(f"{field}: duplicate id {row_id}")
        if boolean_field not in row:
            raise TriggerEvaluationError(f"{row_id}: missing {boolean_field}")
        value = row[boolean_field]
        if not isinstance(value, bool) and not (allow_indeterminate and value is None):
            suffix = "boolean or null" if allow_indeterminate else "boolean"
            raise TriggerEvaluationError(f"{row_id}: {boolean_field} must be {suffix}")
        if field == "observations" and "detection_status" in row:
            expected_status = {True: "triggered", False: "not_triggered", None: "indeterminate"}[value]
            if row["detection_status"] != expected_status:
                raise TriggerEvaluationError(
                    f"{row_id}: detection_status must be {expected_status} when triggered is {value}"
                )
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
    observed = indexed_rows(
        observations.get("observations"),
        "observations",
        "triggered",
        allow_indeterminate=True,
    )
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if extra or (missing and not allow_subset):
        raise TriggerEvaluationError(f"observation IDs differ; missing={missing}, extra={extra}")
    if allow_subset:
        expected = {case_id: expected[case_id] for case_id in observed}

    indeterminate_ids = sorted(
        case_id for case_id in expected if observed[case_id]["triggered"] is None
    )
    scorable_expected = {
        case_id: case for case_id, case in expected.items() if case_id not in indeterminate_ids
    }
    true_positive = true_negative = false_positive = false_negative = 0
    false_positive_ids = []
    false_negative_ids = []
    for case_id, case in scorable_expected.items():
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

    observation_count = len(expected)
    count = len(scorable_expected)
    benchmark_count = len(benchmark["cases"])
    experiment = observations.get("experiment")
    if experiment is not None and not isinstance(experiment, dict):
        raise TriggerEvaluationError("experiment must be an object when provided")
    experiment = experiment or {}
    missing_metadata = [
        field for field in REQUIRED_EXPERIMENT_FIELDS if experiment.get(field) in (None, "")
    ]
    invalid_metadata = []
    for field in REQUIRED_EXPERIMENT_FIELDS:
        value = experiment.get(field)
        if value in (None, ""):
            continue
        if field == "negative_detection_verified":
            if not isinstance(value, bool):
                invalid_metadata.append(field)
        elif not isinstance(value, str) or not value.strip():
            invalid_metadata.append(field)
    negative_detection_verified = experiment.get("negative_detection_verified") is True
    return {
        "benchmark_version": benchmark.get("version"),
        "experiment": experiment,
        "quality_gate": {
            "cases": count,
            "observations": observation_count,
            "benchmark_cases": benchmark_count,
            "complete_coverage": observation_count == benchmark_count,
            "complete_scorable_coverage": count == benchmark_count,
            "indeterminate_cases": len(indeterminate_ids),
            "minimum_cases": minimum_cases,
            "sample_size_sufficient": count >= minimum_cases,
            "missing_experiment_metadata": missing_metadata,
            "invalid_experiment_metadata": invalid_metadata,
            "negative_detection_verified": negative_detection_verified,
            "publication_ready": (
                observation_count == benchmark_count
                and count == benchmark_count
                and count >= minimum_cases
                and not missing_metadata
                and not invalid_metadata
                and negative_detection_verified
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
        "indeterminate_ids": indeterminate_ids,
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
    indeterminate = score(
        benchmark,
        {
            "observations": [
                {"id": "yes", "triggered": True},
                {"id": "no", "triggered": None},
            ]
        },
        minimum_cases=1,
    )
    assert indeterminate["quality_gate"]["complete_coverage"] is True
    assert indeterminate["quality_gate"]["complete_scorable_coverage"] is False
    assert indeterminate["quality_gate"]["publication_ready"] is False
    assert indeterminate["indeterminate_ids"] == ["no"]
    try:
        score(
            benchmark,
            {"observations": [{"id": "yes"}, {"id": "no", "triggered": False}]},
            minimum_cases=1,
        )
    except TriggerEvaluationError:
        pass
    else:
        raise AssertionError("missing triggered values must be rejected")
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
