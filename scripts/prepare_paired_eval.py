#!/usr/bin/env python3
"""Prepare clean baseline/Skill Git workspaces from immutable task templates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from run_trigger_eval import atomic_write_json, file_sha256, stable_sha256


PREPARATION_SCHEMA_VERSION = "1.0.0"
VARIANTS = ("baseline", "skill")
FIXED_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Evaluation Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Evaluation Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


def load_spec(path: Path) -> tuple[str, list[dict]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read task specification {path}: {exc}") from exc
    version = payload.get("version") if isinstance(payload, dict) else None
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("task specification must contain a version")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task specification must contain tasks")

    normalized = []
    ids = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"tasks[{index}] must be an object")
        task_id = task.get("id")
        category = task.get("category")
        prompt = task.get("prompt")
        template_value = task.get("template")
        verification = task.get("verification")
        calibration = task.get("calibration")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
            raise ValueError(f"tasks[{index}].id must be filesystem-safe")
        if task_id in ids:
            raise ValueError(f"duplicate task ID: {task_id}")
        if not isinstance(category, str) or not category.strip():
            raise ValueError(f"{task_id}: category must be non-empty")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{task_id}: prompt must be non-empty")
        if not isinstance(template_value, str) or not template_value.strip():
            raise ValueError(f"{task_id}: template must be a path")
        template = Path(template_value)
        if not template.is_absolute():
            template = path.parent / template
        template = template.resolve()
        if not template.is_dir():
            raise ValueError(f"{task_id}: template directory does not exist: {template}")
        if not isinstance(verification, dict):
            raise ValueError(f"{task_id}: verification must be an object")
        public_command = verification.get("public_command")
        hidden_test_value = verification.get("hidden_test")
        if (
            not isinstance(public_command, list)
            or not public_command
            or not all(isinstance(part, str) and part for part in public_command)
        ):
            raise ValueError(f"{task_id}: public_command must be a string array")
        if not isinstance(hidden_test_value, str) or not hidden_test_value.strip():
            raise ValueError(f"{task_id}: hidden_test must be a path")
        hidden_test = Path(hidden_test_value)
        if not hidden_test.is_absolute():
            hidden_test = path.parent / hidden_test
        hidden_test = hidden_test.resolve()
        if not hidden_test.is_file():
            raise ValueError(f"{task_id}: hidden test does not exist: {hidden_test}")
        if (
            not isinstance(calibration, dict)
            or not isinstance(calibration.get("public_passes"), bool)
            or not isinstance(calibration.get("held_out_passes"), bool)
        ):
            raise ValueError(f"{task_id}: calibration must contain two booleans")
        ids.add(task_id)
        normalized.append(
            {
                "id": task_id,
                "category": category.strip(),
                "prompt": prompt.strip(),
                "template": template,
                "verification": {
                    "public_command": public_command,
                    "hidden_test": hidden_test,
                },
                "calibration": {
                    "public_passes": calibration["public_passes"],
                    "held_out_passes": calibration["held_out_passes"],
                },
            }
        )
    return version.strip(), normalized


def directory_manifest(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if ".git" in path.relative_to(root).parts:
            raise ValueError(f"fixture templates cannot contain .git data: {path}")
        if path.is_symlink():
            raise ValueError(f"fixture templates cannot contain symlinks: {path}")
        if path.is_file():
            files[relative] = file_sha256(path)
    if not files:
        raise ValueError(f"fixture template must contain files: {root}")
    return files


def run_git(arguments: list[str], cwd: Path, environment: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed in {cwd}: {detail}")
    return completed.stdout.strip()


def create_seed_repository(template: Path, seed: Path, task_id: str) -> str:
    shutil.copytree(template, seed)
    run_git(["init", "-q", "--initial-branch=main"], seed)
    run_git(["add", "--all"], seed)
    run_git(
        ["commit", "-q", "-m", f"Create {task_id} evaluation fixture"],
        seed,
        FIXED_GIT_ENV,
    )
    return run_git(["rev-parse", "HEAD"], seed)


def prepare(spec_path: Path, output_root: Path) -> dict:
    if output_root.exists():
        raise ValueError(f"output directory already exists: {output_root}")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    version, tasks = load_spec(spec_path)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}-", dir=output_root.parent)
    )
    seeds_root = Path(tempfile.mkdtemp(prefix="paired-eval-seeds-"))
    try:
        generated_tasks = []
        manifest_tasks = []
        for task in tasks:
            task_id = task["id"]
            template_files = directory_manifest(task["template"])
            seed = seeds_root / task_id
            head = create_seed_repository(task["template"], seed, task_id)
            workspaces = {}
            for variant in VARIANTS:
                destination = temporary / "workspaces" / task_id / variant
                destination.parent.mkdir(parents=True, exist_ok=True)
                run_git(
                    ["clone", "-q", "--no-hardlinks", str(seed), str(destination)],
                    seeds_root,
                )
                cloned_head = run_git(["rev-parse", "HEAD"], destination)
                if cloned_head != head:
                    raise RuntimeError(f"{task_id}: cloned workspace HEAD differs")
                workspaces[variant] = str(destination.relative_to(temporary))
            hidden_destination = (
                temporary / "verification" / task_id / "hidden_test.py"
            )
            hidden_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(task["verification"]["hidden_test"], hidden_destination)
            generated_tasks.append(
                {
                    "id": task_id,
                    "category": task["category"],
                    "prompt": task["prompt"],
                    "workspaces": workspaces,
                    "verification": {
                        "public_command": task["verification"]["public_command"],
                        "hidden_test": str(
                            hidden_destination.relative_to(temporary)
                        ),
                        "hidden_test_sha256": file_sha256(hidden_destination),
                    },
                }
            )
            manifest_tasks.append(
                {
                    "id": task_id,
                    "category": task["category"],
                    "template_sha256": stable_sha256(template_files),
                    "template_files": template_files,
                    "fixture_commit": head,
                    "hidden_test_sha256": file_sha256(
                        task["verification"]["hidden_test"]
                    ),
                    "calibration": task["calibration"],
                }
            )

        generated = {
            "version": version,
            "preparation_schema_version": PREPARATION_SCHEMA_VERSION,
            "source_task_set_sha256": file_sha256(spec_path),
            "tasks": generated_tasks,
        }
        task_set_path = temporary / "paired-tasks.json"
        atomic_write_json(task_set_path, generated)
        manifest = {
            "schema_version": PREPARATION_SCHEMA_VERSION,
            "task_set_version": version,
            "source_task_set_sha256": file_sha256(spec_path),
            "generated_task_set_sha256": file_sha256(task_set_path),
            "tasks": manifest_tasks,
        }
        manifest["manifest_sha256"] = stable_sha256(manifest)
        atomic_write_json(temporary / "preparation-manifest.json", manifest)
        temporary.replace(output_root)
        return manifest
    finally:
        shutil.rmtree(seeds_root, ignore_errors=True)
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_spec", type=Path)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    try:
        manifest = prepare(args.task_spec.resolve(), args.output_root.resolve())
        print(
            json.dumps(
                {
                    "output_root": str(args.output_root.resolve()),
                    "tasks": len(manifest["tasks"]),
                    "manifest_sha256": manifest["manifest_sha256"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
