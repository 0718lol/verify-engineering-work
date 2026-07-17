#!/usr/bin/env python3
"""Run a read-only Codex implicit-trigger benchmark and record observations."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark must contain a non-empty cases array")
    return cases


def parse_events(stdout: str, skill_name: str) -> tuple[bool, dict]:
    triggered = False
    usage = {}
    marker = f"skills/{skill_name}/SKILL.md"
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if item.get("type") == "command_execution" and marker in item.get("command", ""):
            triggered = True
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return triggered, usage


def selected_cases(cases: list[dict], ids: set[str] | None, limit: int | None) -> list[dict]:
    selected = [case for case in cases if ids is None or case.get("id") in ids]
    if ids is not None:
        missing = ids - {case.get("id") for case in selected}
        if missing:
            raise ValueError(f"unknown case IDs: {sorted(missing)}")
    return selected[:limit] if limit is not None else selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--skill-name", default="verify-engineering-work")
    parser.add_argument("--skill-version", default="0.2.0")
    parser.add_argument("--skill-commit", default="")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--ids", help="Comma-separated benchmark IDs")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--logs-dir", type=Path)
    args = parser.parse_args()

    ids = {item.strip() for item in args.ids.split(",") if item.strip()} if args.ids else None
    cases = selected_cases(load_cases(args.benchmark), ids, args.limit)
    if not cases:
        raise ValueError("no benchmark cases selected")
    if args.logs_dir:
        args.logs_dir.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(args.codex_home.resolve())
    observations = []
    suffix = "\n\nOnly assess this request in read-only mode. Do not perform external operations."
    for index, case in enumerate(cases, start=1):
        case_id = case["id"]
        print(f"[{index}/{len(cases)}] {case_id}", file=sys.stderr, flush=True)
        command = [
            "codex",
            "-a",
            "never",
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "-C",
            str(args.workspace.resolve()),
            case["prompt"] + suffix,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{case_id}: timed out after {args.timeout}s") from exc
        if args.logs_dir:
            (args.logs_dir / f"{case_id}.stdout.jsonl").write_text(
                completed.stdout, encoding="utf-8"
            )
            (args.logs_dir / f"{case_id}.stderr.log").write_text(
                completed.stderr, encoding="utf-8"
            )
        if completed.returncode != 0:
            raise RuntimeError(f"{case_id}: Codex exited with {completed.returncode}")
        triggered, usage = parse_events(completed.stdout, args.skill_name)
        observations.append(
            {
                "id": case_id,
                "triggered": triggered,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            }
        )

    payload = {
        "experiment": {
            "skill_version": args.skill_version,
            "model": args.model,
            "harness": "codex-cli-read-only",
            "skill_commit": args.skill_commit,
        },
        "observations": observations,
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(observations)} observations to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
