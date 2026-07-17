#!/usr/bin/env python3
"""Validate and summarize paired engineering reliability evaluations."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path


CATEGORIES = {
    "behavioral_correctness": 25,
    "evidence_correctness": 20,
    "change_integrity": 20,
    "validation_quality": 15,
    "scope_safety": 10,
    "reporting_integrity": 10,
}
OPTIONAL_NUMBERS = ("tokens", "duration_seconds", "human_interventions")
REQUIRED_EXPERIMENT_FIELDS = (
    "skill_version",
    "rubric_version",
    "model",
    "harness",
    "task_set_version",
    "token_budget",
    "skill_commit",
)
VALID_VARIANTS = ("baseline", "skill")


class EvaluationError(ValueError):
    pass


def load_payload(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"Cannot read evaluation JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise EvaluationError("Input must be an object containing a 'cases' array")
    return payload


def is_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def validate_case(case: dict, index: int) -> dict:
    if not isinstance(case, dict):
        raise EvaluationError(f"cases[{index}] must be an object")
    case_id = case.get("id")
    variant = case.get("variant")
    scores = case.get("scores")
    failures = case.get("critical_failures", [])
    if not isinstance(case_id, str) or not case_id.strip():
        raise EvaluationError(f"cases[{index}].id must be a non-empty string")
    if variant not in VALID_VARIANTS:
        raise EvaluationError(f"{case_id}: variant must be baseline or skill")
    if not isinstance(scores, dict) or set(scores) != set(CATEGORIES):
        raise EvaluationError(f"{case_id}: scores must contain exactly {sorted(CATEGORIES)}")
    total = 0.0
    for name, maximum in CATEGORIES.items():
        value = scores[name]
        if not is_number(value):
            raise EvaluationError(f"{case_id}: {name} must be a finite number")
        if not 0 <= value <= maximum:
            raise EvaluationError(f"{case_id}: {name} must be between 0 and {maximum}")
        total += value
    if not isinstance(failures, list) or not all(
        isinstance(item, str) and item.strip() for item in failures
    ):
        raise EvaluationError(f"{case_id}: critical_failures must contain non-empty strings")
    for field in OPTIONAL_NUMBERS:
        value = case.get(field)
        if value is not None and (not is_number(value) or value < 0):
            raise EvaluationError(f"{case_id}: {field} must be a finite non-negative number")
    normalized = dict(case)
    normalized["id"] = case_id.strip()
    normalized["total"] = total
    normalized["production_compliant"] = total >= 85 and not failures
    return normalized


def build_pairs(cases: list[dict]) -> list[dict[str, dict]]:
    if not cases:
        raise EvaluationError("cases must not be empty")
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for index, raw_case in enumerate(cases):
        case = validate_case(raw_case, index)
        variants = grouped[case["id"]]
        if case["variant"] in variants:
            raise EvaluationError(f"{case['id']}: duplicate {case['variant']} result")
        variants[case["variant"]] = case
    for case_id, variants in grouped.items():
        missing = sorted(set(VALID_VARIANTS) - set(variants))
        if missing:
            raise EvaluationError(f"{case_id}: missing paired variant(s): {', '.join(missing)}")
    return [grouped[case_id] for case_id in sorted(grouped)]


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def wilson_interval(successes: int, count: int) -> list[float]:
    if count == 0:
        return [0.0, 0.0]
    z = 1.959963984540054
    rate = successes / count
    denominator = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def bootstrap_interval(values: list[float], iterations: int = 10000) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        return [values[0], values[0]]
    rng = random.Random(20260717)
    count = len(values)
    samples = sorted(
        sum(values[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(iterations)
    )
    return [samples[int(0.025 * iterations)], samples[int(0.975 * iterations) - 1]]


def paired_metric(pairs: list[dict[str, dict]], field: str, scale: float = 1.0) -> dict:
    differences = []
    for pair in pairs:
        baseline = pair["baseline"].get(field)
        skill = pair["skill"].get(field)
        if baseline is not None and skill is not None:
            differences.append(scale * (skill - baseline))
    return {
        "delta": average(differences),
        "confidence_interval_95": bootstrap_interval(differences),
        "paired_cases": len(differences),
    }


def experiment_status(payload: dict, pair_count: int, minimum_pairs: int) -> dict:
    experiment = payload.get("experiment")
    if experiment is not None and not isinstance(experiment, dict):
        raise EvaluationError("experiment must be an object when provided")
    experiment = experiment or {}
    missing = [field for field in REQUIRED_EXPERIMENT_FIELDS if experiment.get(field) in (None, "")]
    invalid = []
    for field in REQUIRED_EXPERIMENT_FIELDS:
        value = experiment.get(field)
        if field == "token_budget":
            if value not in (None, "") and (not is_number(value) or value <= 0):
                invalid.append(field)
        elif value not in (None, "") and (not isinstance(value, str) or not value.strip()):
            invalid.append(field)
    return {
        "matched_pairs": pair_count,
        "minimum_pairs": minimum_pairs,
        "sample_size_sufficient": pair_count >= minimum_pairs,
        "missing_experiment_metadata": missing,
        "invalid_experiment_metadata": invalid,
        "publication_ready": pair_count >= minimum_pairs and not missing and not invalid,
    }


def summarize(payload: dict, minimum_pairs: int = 40) -> dict:
    pairs = build_pairs(payload["cases"])
    groups = {
        variant: [pair[variant] for pair in pairs]
        for variant in VALID_VARIANTS
    }
    summary = {
        "experiment": payload.get("experiment", {}),
        "quality_gate": experiment_status(payload, len(pairs), minimum_pairs),
        "variants": {},
        "comparison": {},
    }
    for variant, group in groups.items():
        count = len(group)
        compliant = sum(case["production_compliant"] for case in group)
        critical = sum(bool(case.get("critical_failures")) for case in group)
        summary["variants"][variant] = {
            "cases": count,
            "production_compliance_rate": compliant / count,
            "production_compliance_confidence_interval_95": wilson_interval(compliant, count),
            "critical_failure_rate": critical / count,
            "critical_failure_confidence_interval_95": wilson_interval(critical, count),
            "average_score": average([case["total"] for case in group]),
            "average_tokens": average([case["tokens"] for case in group if case.get("tokens") is not None]),
            "average_duration_seconds": average(
                [case["duration_seconds"] for case in group if case.get("duration_seconds") is not None]
            ),
            "average_human_interventions": average(
                [case["human_interventions"] for case in group if case.get("human_interventions") is not None]
            ),
        }

    for pair in pairs:
        for variant in VALID_VARIANTS:
            pair[variant]["critical_failure"] = bool(pair[variant].get("critical_failures"))
    summary["comparison"] = {
        "production_compliance_percentage_points": paired_metric(
            pairs, "production_compliant", scale=100.0
        ),
        "critical_failure_percentage_points": paired_metric(
            pairs, "critical_failure", scale=100.0
        ),
        "average_score": paired_metric(pairs, "total"),
        "tokens": paired_metric(pairs, "tokens"),
        "duration_seconds": paired_metric(pairs, "duration_seconds"),
        "human_interventions": paired_metric(pairs, "human_interventions"),
    }
    return summary


def expect_error(payload: dict, message: str) -> None:
    try:
        summarize(payload, minimum_pairs=1)
    except EvaluationError:
        return
    raise AssertionError(message)


def self_test() -> None:
    base_scores = {
        "behavioral_correctness": 20,
        "evidence_correctness": 15,
        "change_integrity": 18,
        "validation_quality": 12,
        "scope_safety": 8,
        "reporting_integrity": 8,
    }
    skill_scores = dict(CATEGORIES)
    pair = [
        {"id": "a", "variant": "baseline", "scores": base_scores, "critical_failures": []},
        {"id": "a", "variant": "skill", "scores": skill_scores, "critical_failures": []},
    ]
    result = summarize({"cases": pair}, minimum_pairs=1)
    assert result["quality_gate"]["matched_pairs"] == 1
    assert result["variants"]["baseline"]["production_compliance_rate"] == 0
    assert result["variants"]["skill"]["production_compliance_rate"] == 1
    assert result["comparison"]["production_compliance_percentage_points"]["delta"] == 100

    invalid_metadata = {
        "skill_version": "0.2.0",
        "rubric_version": "2.0.0",
        "model": "test-model",
        "harness": "test-harness",
        "task_set_version": "test-set",
        "token_budget": 0,
        "skill_commit": "abc123",
    }
    metadata_result = summarize(
        {"experiment": invalid_metadata, "cases": pair}, minimum_pairs=1
    )
    assert metadata_result["quality_gate"]["publication_ready"] is False
    assert metadata_result["quality_gate"]["invalid_experiment_metadata"] == ["token_budget"]

    expect_error({"cases": []}, "empty input must be rejected")
    expect_error({"cases": pair[:1]}, "unpaired input must be rejected")
    expect_error({"cases": pair + [pair[0]]}, "duplicate variants must be rejected")
    invalid = dict(pair[0])
    invalid["scores"] = {**base_scores, "behavioral_correctness": float("inf")}
    expect_error({"cases": [invalid, pair[1]]}, "non-finite scores must be rejected")
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Evaluation results JSON")
    parser.add_argument("--minimum-pairs", type=int, default=40, help="Minimum publishable pair count")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.input is None:
            parser.error("input is required unless --self-test is used")
        if args.minimum_pairs < 1:
            raise EvaluationError("--minimum-pairs must be at least 1")
        print(
            json.dumps(
                summarize(load_payload(args.input), minimum_pairs=args.minimum_pairs),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )
        return 0
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
