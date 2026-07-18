#!/usr/bin/env python3
"""Validate Skill metadata, evaluation assets, and local documentation links."""

from __future__ import annotations

import json
import re
from pathlib import Path

from prepare_paired_eval import directory_manifest, load_spec
from run_paired_eval import RUBRIC_VERSION

from run_trigger_eval import (
    load_compatibility,
    negative_detection_observable,
    parse_events,
    project_version,
)
from score_trigger_eval import load_json, score


ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "verify-engineering-work"
FRONTMATTER = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
LOCAL_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_frontmatter() -> dict[str, str]:
    content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = FRONTMATTER.match(content)
    require(match is not None, "SKILL.md must start with YAML frontmatter")
    fields = {}
    for line in match.group("body").splitlines():
        key, separator, value = line.partition(":")
        require(bool(separator), f"invalid frontmatter line: {line!r}")
        key = key.strip()
        require(key not in fields, f"duplicate frontmatter field: {key}")
        fields[key] = value.strip()
    return fields


def validate_skill_metadata() -> None:
    fields = parse_frontmatter()
    require(set(fields) == {"name", "description"}, "frontmatter must contain only name and description")
    require(fields["name"] == SKILL_NAME, f"skill name must be {SKILL_NAME}")
    require(bool(re.fullmatch(r"[a-z0-9-]{1,64}", fields["name"])), "skill name must be hyphen-case")
    description = fields["description"]
    require(0 < len(description) <= 1024, "description must contain 1-1024 characters")
    require("<" not in description and ">" not in description, "description cannot contain angle brackets")

    agent = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    require("allow_implicit_invocation: true" in agent, "implicit invocation must remain enabled")
    require(f"${SKILL_NAME}" in agent, "default prompt must reference the Skill name")


def validate_trigger_assets() -> None:
    benchmark_path = ROOT / "references" / "trigger-benchmark-v1.json"
    boundary_benchmark_path = ROOT / "references" / "trigger-benchmark-v2.json"
    pilot_path = ROOT / "evals" / "trigger-pilot-2026-07-17.json"
    expanded_pilot_path = ROOT / "evals" / "trigger-pilot-2026-07-18-reclassified.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    cases = benchmark.get("cases")
    require(isinstance(cases, list) and len(cases) == 40, "trigger benchmark must contain 40 cases")
    ids = [case.get("id") for case in cases]
    require(len(set(ids)) == len(ids), "trigger benchmark IDs must be unique")
    positives = sum(case.get("expected_trigger") is True for case in cases)
    negatives = sum(case.get("expected_trigger") is False for case in cases)
    require((positives, negatives) == (20, 20), "trigger benchmark must be balanced 20/20")
    require(
        all(isinstance(case.get("prompt"), str) and case["prompt"].strip() for case in cases),
        "every trigger case must have a prompt",
    )
    boundary_benchmark = json.loads(
        boundary_benchmark_path.read_text(encoding="utf-8")
    )
    boundary_cases = boundary_benchmark.get("cases")
    require(
        boundary_benchmark.get("version") == "2.0.0",
        "boundary trigger benchmark version must be 2.0.0",
    )
    require(
        isinstance(boundary_cases, list) and len(boundary_cases) == 40,
        "boundary trigger benchmark must contain 40 cases",
    )
    boundary_ids = [case.get("id") for case in boundary_cases]
    require(
        len(set(boundary_ids)) == len(boundary_ids),
        "boundary trigger benchmark IDs must be unique",
    )
    require(
        sum(case.get("expected_trigger") is True for case in boundary_cases) == 20
        and sum(case.get("expected_trigger") is False for case in boundary_cases) == 20,
        "boundary trigger benchmark must be balanced 20/20",
    )
    require(
        all(
            boundary_cases[index].get("expected_trigger")
            != boundary_cases[index + 1].get("expected_trigger")
            for index in range(len(boundary_cases) - 1)
        ),
        "boundary trigger benchmark must interleave positive and negative cases",
    )
    require(
        all(
            case.get("difficulty") == "boundary"
            and isinstance(case.get("category"), str)
            and case["category"].strip()
            and isinstance(case.get("prompt"), str)
            and case["prompt"].strip()
            for case in boundary_cases
        ),
        "every boundary trigger case must contain difficulty, category, and prompt",
    )

    pilot = score(benchmark, load_json(pilot_path), allow_subset=True)
    require(pilot["quality_gate"]["cases"] == 8, "checked-in Pilot must contain 8 observations")
    require(pilot["quality_gate"]["indeterminate_cases"] == 0, "checked-in Pilot must remain scorable")
    require(pilot["quality_gate"]["publication_ready"] is False, "Pilot must remain non-publishable")
    require(
        pilot["confusion_matrix"]
        == {"true_positive": 4, "true_negative": 4, "false_positive": 0, "false_negative": 0},
        "checked-in Pilot confusion matrix changed unexpectedly",
    )

    expanded = score(benchmark, load_json(expanded_pilot_path), allow_subset=True)
    require(expanded["quality_gate"]["cases"] == 13, "expanded Pilot must contain 13 observations")
    require(expanded["quality_gate"]["indeterminate_cases"] == 0, "expanded Pilot must remain scorable")
    require(expanded["quality_gate"]["publication_ready"] is False, "expanded Pilot must remain non-publishable")
    require(
        expanded["confusion_matrix"]
        == {"true_positive": 7, "true_negative": 6, "false_positive": 0, "false_negative": 0},
        "expanded Pilot confusion matrix changed unexpectedly",
    )
    require(
        expanded["experiment"].get("reclassified_from_detection_schema_version") == "2.1.0"
        and expanded["experiment"].get("detection_schema_version") == "2.2.0",
        "expanded Pilot must preserve detection reclassification provenance",
    )


def validate_version_and_compatibility() -> None:
    version = project_version()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    require(f"当前版本：`{version}`" in readme, "README version must match VERSION")

    compatibility = load_compatibility()
    require(
        compatibility.get("schema_version") == "1.0.0",
        "compatibility schema_version must be 1.0.0",
    )
    versions = compatibility["codex_cli_versions"]
    require(bool(versions), "compatibility matrix must contain at least one CLI version")
    for cli_version, entry in versions.items():
        require(isinstance(entry, dict), f"{cli_version}: compatibility entry must be an object")
        require(
            negative_detection_observable(cli_version, compatibility),
            f"{cli_version}: checked-in fixture must verify negative detection",
        )
        triggered_path = ROOT / str(entry.get("triggered_fixture", ""))
        not_triggered_path = ROOT / str(entry.get("not_triggered_fixture", ""))
        require(triggered_path.is_file(), f"{cli_version}: triggered fixture is missing")
        require(not_triggered_path.is_file(), f"{cli_version}: not-triggered fixture is missing")
        triggered = parse_events(
            triggered_path.read_text(encoding="utf-8"),
            SKILL_NAME,
            negative_observable=True,
        )
        not_triggered = parse_events(
            not_triggered_path.read_text(encoding="utf-8"),
            SKILL_NAME,
            negative_observable=True,
        )
        require(triggered["triggered"] is True, f"{cli_version}: triggered fixture was not detected")
        require(
            not_triggered["triggered"] is False,
            f"{cli_version}: not-triggered fixture was not detected",
        )


def validate_engineering_assets() -> None:
    task_set_path = ROOT / "references" / "engineering-task-pilot-v1.json"
    version, tasks = load_spec(task_set_path)
    require(version == "engineering-pilot-v1", "engineering Pilot version changed")
    require(len(tasks) == 8, "engineering Pilot must contain 8 tasks")
    require(
        len({task["category"] for task in tasks}) == 8,
        "engineering Pilot categories must be distinct",
    )
    for task in tasks:
        template_files = directory_manifest(task["template"])
        require(
            any(path.startswith("tests/") for path in template_files),
            f"{task['id']}: fixture must contain public tests",
        )
        require(
            task["verification"]["hidden_test"].is_file(),
            f"{task['id']}: hidden verification is missing",
        )

    rubric = (ROOT / "references" / "eval-rubric.md").read_text(encoding="utf-8")
    require(
        f"Rubric version: `{RUBRIC_VERSION}`." in rubric,
        "paired runner and rubric versions must agree",
    )
    schema_paths = sorted((ROOT / "schemas").glob("*.schema.json"))
    require(len(schema_paths) == 4, "exactly four evaluation schemas are required")
    for schema_path in schema_paths:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        require(
            schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
            f"{schema_path.name}: unsupported JSON Schema draft",
        )
        require(schema.get("type") == "object", f"{schema_path.name}: root must be an object")


def validate_local_links() -> None:
    for document in (ROOT / "README.md", ROOT / "SKILL.md"):
        content = document.read_text(encoding="utf-8")
        for raw_target in LOCAL_LINK.findall(content):
            target = raw_target.strip().split("#", 1)[0]
            if not target or target.startswith(("http://", "https://")):
                continue
            require((document.parent / target).exists(), f"broken local link in {document.name}: {raw_target}")


def main() -> None:
    validate_skill_metadata()
    validate_trigger_assets()
    validate_engineering_assets()
    validate_version_and_compatibility()
    validate_local_links()
    print("repository validation passed")


if __name__ == "__main__":
    main()
