#!/usr/bin/env python3
"""Create minimal baseline and Skill Codex homes for paired evaluation."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from run_trigger_eval import atomic_write_json, file_sha256


ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENT_SCHEMA_VERSION = "1.0.0"
DEFAULT_IMAGE = "verify-engineering-work-codex:0.144.0-alpha.4"
SKILL_FILES = (
    "SKILL.md",
    "VERSION",
    "agents/openai.yaml",
    "references/eval-rubric.md",
    "references/evidence-report.md",
    "references/risk-matrix.md",
    "scripts/score_eval.py",
)


def copy_skill(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for relative in SKILL_FILES:
        candidate = source / relative
        if not candidate.exists():
            if relative == "SKILL.md":
                raise ValueError(f"Skill source is missing SKILL.md: {source}")
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if candidate.is_file():
            shutil.copy2(candidate, target)
        else:
            raise ValueError(f"unsupported Skill source entry: {candidate}")


def create_environment(
    output_root: Path,
    skill_source: Path,
    skill_name: str,
    image: str,
) -> dict:
    if output_root.exists():
        raise ValueError(f"output directory already exists: {output_root}")
    if not skill_source.is_dir():
        raise ValueError(f"Skill source is not a directory: {skill_source}")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}-", dir=output_root.parent)
    )
    try:
        baseline = temporary / "baseline-home"
        skill = temporary / "skill-home"
        (baseline / "skills").mkdir(parents=True)
        (skill / "skills").mkdir(parents=True)
        copy_skill(skill_source, skill / "skills" / skill_name)
        manifest = {
            "schema_version": ENVIRONMENT_SCHEMA_VERSION,
            "codex_image": image,
            "skill_name": skill_name,
            "skill_body_sha256": file_sha256(skill_source / "SKILL.md"),
            "baseline_home": "baseline-home",
            "skill_home": "skill-home",
            "auth_mode": "runtime-read-only-mount",
        }
        atomic_write_json(temporary / "environment.json", manifest)
        temporary.replace(output_root)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--skill-source", type=Path, default=ROOT)
    parser.add_argument("--skill-name", default="verify-engineering-work")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()
    try:
        manifest = create_environment(
            args.output_root.resolve(),
            args.skill_source.resolve(),
            args.skill_name,
            args.image,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
