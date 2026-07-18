#!/usr/bin/env python3
"""Verify that an engineering Pilot retains its intended unsolved baseline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from prepare_paired_eval import load_spec


def command_passes(
    command: list[str],
    workspace: Path,
    timeout: int,
) -> bool:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(workspace)
    completed = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
        check=False,
    )
    return completed.returncode == 0


def calibrate(spec_path: Path, timeout: int = 30) -> dict:
    version, tasks = load_spec(spec_path)
    results = []
    for task in tasks:
        public_passes = command_passes(
            task["verification"]["public_command"],
            task["template"],
            timeout,
        )
        held_out_passes = command_passes(
            [sys.executable, str(task["verification"]["hidden_test"])],
            task["template"],
            timeout,
        )
        expected = task["calibration"]
        results.append(
            {
                "id": task["id"],
                "public_passes": public_passes,
                "held_out_passes": held_out_passes,
                "matches_expected": (
                    public_passes == expected["public_passes"]
                    and held_out_passes == expected["held_out_passes"]
                ),
            }
        )
    return {
        "task_set_version": version,
        "summary": {
            "tasks": len(results),
            "public_passes": sum(result["public_passes"] for result in results),
            "held_out_passes": sum(
                result["held_out_passes"] for result in results
            ),
            "calibration_mismatches": sum(
                not result["matches_expected"] for result in results
            ),
        },
        "tasks": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_spec", type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--allow-verification-commands", action="store_true")
    args = parser.parse_args()
    try:
        if args.timeout < 1:
            raise ValueError("--timeout must be positive")
        if not args.allow_verification_commands:
            raise ValueError(
                "calibration executes commands from the task set; "
                "pass --allow-verification-commands"
            )
        result = calibrate(args.task_spec.resolve(), args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["summary"]["calibration_mismatches"] == 0 else 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
