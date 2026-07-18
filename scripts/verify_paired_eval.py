#!/usr/bin/env python3
"""Run public and hidden machine checks for completed paired evaluations."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from run_paired_eval import git_capture
from run_trigger_eval import atomic_write_json, file_sha256, write_log


VERIFICATION_SCHEMA_VERSION = "1.0.0"
VARIANTS = ("baseline", "skill")


def load_json(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def load_verification_tasks(path: Path) -> tuple[str, dict[str, dict]]:
    payload = load_json(path, "prepared task set")
    version = payload.get("version")
    rows = payload.get("tasks")
    if not isinstance(version, str) or not isinstance(rows, list) or not rows:
        raise ValueError("prepared task set must contain version and tasks")
    tasks = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(f"tasks[{index}] is invalid")
        task_id = row["id"]
        workspaces = row.get("workspaces")
        verification = row.get("verification")
        if task_id in tasks:
            raise ValueError(f"duplicate task ID: {task_id}")
        if not isinstance(workspaces, dict) or set(workspaces) != set(VARIANTS):
            raise ValueError(f"{task_id}: invalid workspaces")
        if not isinstance(verification, dict):
            raise ValueError(f"{task_id}: missing verification")
        public_command = verification.get("public_command")
        hidden_test = verification.get("hidden_test")
        hidden_test_sha256 = verification.get("hidden_test_sha256")
        if (
            not isinstance(public_command, list)
            or not public_command
            or not all(isinstance(part, str) and part for part in public_command)
        ):
            raise ValueError(f"{task_id}: invalid public command")
        if not isinstance(hidden_test, str):
            raise ValueError(f"{task_id}: hidden test is missing")
        hidden_test_path = Path(hidden_test)
        if not hidden_test_path.is_absolute():
            hidden_test_path = path.parent / hidden_test_path
        hidden_test_path = hidden_test_path.resolve()
        if not hidden_test_path.is_file():
            raise ValueError(f"{task_id}: hidden test is missing")
        if (
            not isinstance(hidden_test_sha256, str)
            or file_sha256(hidden_test_path) != hidden_test_sha256
        ):
            raise ValueError(f"{task_id}: hidden test hash does not match")
        resolved_workspaces = {}
        for variant, raw_path in workspaces.items():
            if not isinstance(raw_path, str):
                raise ValueError(f"{task_id}: invalid {variant} workspace")
            workspace = Path(raw_path)
            if not workspace.is_absolute():
                workspace = path.parent / workspace
            resolved_workspaces[variant] = workspace.resolve()
        tasks[task_id] = {
            "category": row.get("category", "uncategorized"),
            "workspaces": resolved_workspaces,
            "public_command": public_command,
            "hidden_test": hidden_test_path,
        }
    return version, tasks


def execute_check(
    command: list[str],
    workspace: Path,
    timeout: int,
    log_prefix: Path,
) -> dict:
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(workspace)
        if not existing_pythonpath
        else str(workspace) + os.pathsep + existing_pythonpath
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = None
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        timed_out = True
    duration = time.monotonic() - started
    stdout_meta = write_log(Path(str(log_prefix) + ".stdout.log"), stdout, False)
    stderr_meta = write_log(Path(str(log_prefix) + ".stderr.log"), stderr, False)
    return {
        "passed": returncode == 0 and not timed_out,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "stdout_sha256": stdout_meta["sha256"],
        "stderr_sha256": stderr_meta["sha256"],
        "log_redactions": stdout_meta["redactions"] + stderr_meta["redactions"],
    }


def verify(
    task_set_path: Path,
    run_output_path: Path,
    artifacts_dir: Path,
    timeout: int,
) -> dict:
    task_set_version, tasks = load_verification_tasks(task_set_path)
    run_payload = load_json(run_output_path, "paired run output")
    runs = run_payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("paired run output must contain completed runs")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    cases = []
    seen = set()
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("run rows must be objects")
        task_id = run.get("id")
        variant = run.get("variant")
        blind_id = run.get("blind_id")
        key = (task_id, variant)
        if task_id not in tasks or variant not in VARIANTS or not isinstance(blind_id, str):
            raise ValueError(f"invalid completed run: {key}")
        if key in seen:
            raise ValueError(f"duplicate completed run: {key}")
        seen.add(key)
        task = tasks[task_id]
        workspace = task["workspaces"][variant]
        before = git_capture(workspace)
        if before["status"] != run.get("workspace_status"):
            raise ValueError(f"{blind_id}: workspace differs from the run checkpoint")

        public = execute_check(
            task["public_command"],
            workspace,
            timeout,
            artifacts_dir / f"{blind_id}.public",
        )
        after_public = git_capture(workspace)
        if after_public["status"] != before["status"]:
            raise ValueError(f"{blind_id}: public verification modified the workspace")
        hidden = execute_check(
            [sys.executable, str(task["hidden_test"])],
            workspace,
            timeout,
            artifacts_dir / f"{blind_id}.hidden",
        )
        after = git_capture(workspace)
        if after["status"] != before["status"]:
            raise ValueError(f"{blind_id}: verification modified the workspace")
        cases.append(
            {
                "id": task_id,
                "category": task["category"],
                "variant": variant,
                "blind_id": blind_id,
                "public": public,
                "hidden": hidden,
                "machine_verification_passed": public["passed"] and hidden["passed"],
            }
        )
        atomic_write_json(
            artifacts_dir / "checkpoint.json",
            {
                "schema_version": VERIFICATION_SCHEMA_VERSION,
                "completed_cases": len(cases),
                "cases": cases,
            },
        )
    return {
        "experiment": {
            "schema_version": VERIFICATION_SCHEMA_VERSION,
            "task_set_version": task_set_version,
            "task_set_sha256": file_sha256(task_set_path),
            "run_output_sha256": file_sha256(run_output_path),
        },
        "summary": {
            "cases": len(cases),
            "passed": sum(case["machine_verification_passed"] for case in cases),
            "failed": sum(not case["machine_verification_passed"] for case in cases),
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared_task_set", type=Path)
    parser.add_argument("run_output", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--allow-verification-commands", action="store_true")
    args = parser.parse_args()
    try:
        if args.timeout < 1:
            raise ValueError("--timeout must be positive")
        if not args.allow_verification_commands:
            raise ValueError(
                "verification executes commands from the task set; "
                "pass --allow-verification-commands"
            )
        if args.output.exists():
            raise ValueError(f"output already exists: {args.output}")
        result = verify(
            args.prepared_task_set.resolve(),
            args.run_output.resolve(),
            args.artifacts_dir.resolve(),
            args.timeout,
        )
        atomic_write_json(args.output.resolve(), result)
        print(
            f"verified {result['summary']['cases']} runs: "
            f"{result['summary']['passed']} passed, "
            f"{result['summary']['failed']} failed",
            file=sys.stderr,
        )
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
