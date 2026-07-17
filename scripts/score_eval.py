#!/usr/bin/env python3
"""Validate and summarize blinded engineering reliability evaluation results."""

from __future__ import annotations

import argparse
import json
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
VALID_VARIANTS = {"baseline", "skill"}


class EvaluationError(ValueError):
    pass


def load_cases(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"Cannot read evaluation JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise EvaluationError("Input must be an object containing a 'cases' array")
    return payload["cases"]


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
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvaluationError(f"{case_id}: {name} must be numeric")
        if not 0 <= value <= maximum:
            raise EvaluationError(f"{case_id}: {name} must be between 0 and {maximum}")
        total += value
    if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures):
        raise EvaluationError(f"{case_id}: critical_failures must be an array of strings")
    for field in OPTIONAL_NUMBERS:
        value = case.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            raise EvaluationError(f"{case_id}: {field} must be a non-negative number")
    normalized = dict(case)
    normalized["total"] = total
    normalized["production_compliant"] = total >= 85 and not failures
    return normalized


def average(cases: list[dict], field: str) -> float | None:
    values = [case[field] for case in cases if case.get(field) is not None]
    return sum(values) / len(values) if values else None


def summarize(cases: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for index, case in enumerate(cases):
        groups[case["variant"]].append(validate_case(case, index))

    summary = {"variants": {}, "comparison": {}}
    for variant in sorted(groups):
        group = groups[variant]
        count = len(group)
        compliant = sum(case["production_compliant"] for case in group)
        critical = sum(bool(case.get("critical_failures")) for case in group)
        summary["variants"][variant] = {
            "cases": count,
            "production_compliance_rate": compliant / count,
            "critical_failure_rate": critical / count,
            "average_score": average(group, "total"),
            "average_tokens": average(group, "tokens"),
            "average_duration_seconds": average(group, "duration_seconds"),
            "average_human_interventions": average(group, "human_interventions"),
        }

    if VALID_VARIANTS.issubset(summary["variants"]):
        baseline = summary["variants"]["baseline"]
        skill = summary["variants"]["skill"]
        summary["comparison"] = {
            "production_compliance_percentage_point_delta": 100
            * (skill["production_compliance_rate"] - baseline["production_compliance_rate"]),
            "critical_failure_percentage_point_delta": 100
            * (skill["critical_failure_rate"] - baseline["critical_failure_rate"]),
            "average_score_delta": skill["average_score"] - baseline["average_score"],
        }
    return summary


def self_test() -> None:
    base_scores = {
        "behavioral_correctness": 20,
        "evidence_correctness": 15,
        "change_integrity": 18,
        "validation_quality": 12,
        "scope_safety": 8,
        "reporting_integrity": 8,
    }
    skill_scores = {name: maximum for name, maximum in CATEGORIES.items()}
    result = summarize(
        [
            {"id": "a", "variant": "baseline", "scores": base_scores, "critical_failures": []},
            {"id": "a", "variant": "skill", "scores": skill_scores, "critical_failures": []},
        ]
    )
    assert result["variants"]["baseline"]["production_compliance_rate"] == 0
    assert result["variants"]["skill"]["production_compliance_rate"] == 1
    assert result["comparison"]["production_compliance_percentage_point_delta"] == 100

    critical_case = {
        "id": "critical",
        "variant": "skill",
        "scores": skill_scores,
        "critical_failures": ["fabricated API"],
    }
    assert summarize([critical_case])["variants"]["skill"]["production_compliance_rate"] == 0

    invalid_case = {
        "id": "invalid",
        "variant": "baseline",
        "scores": {**base_scores, "behavioral_correctness": 26},
        "critical_failures": [],
    }
    try:
        summarize([invalid_case])
    except EvaluationError:
        pass
    else:
        raise AssertionError("out-of-range scores must be rejected")
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Evaluation results JSON")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.input is None:
            parser.error("input is required unless --self-test is used")
        print(json.dumps(summarize(load_cases(args.input)), ensure_ascii=False, indent=2))
        return 0
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
