#!/usr/bin/env python3
"""Run a read-only Codex implicit-trigger benchmark and record observations."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


DETECTION_SCHEMA_VERSION = "2.0.0"
KNOWN_NEGATIVE_OBSERVABLE_CLI_VERSIONS = {"codex-cli 0.144.0-alpha.4"}


def load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark must contain a non-empty cases array")
    return cases


def selected_cases(cases: list[dict], ids: set[str] | None, limit: int | None) -> list[dict]:
    selected = [case for case in cases if ids is None or case.get("id") in ids]
    if ids is not None:
        missing = ids - {case.get("id") for case in selected}
        if missing:
            raise ValueError(f"unknown case IDs: {sorted(missing)}")
    return selected[:limit] if limit is not None else selected


def event_names_skill(event: dict, skill_name: str) -> bool:
    containers = [event]
    item = event.get("item")
    if isinstance(item, dict):
        containers.append(item)
    for container in containers:
        event_type = str(container.get("type", "")).lower()
        if "skill" not in event_type:
            continue
        for field in ("skill_name", "skill", "name", "id"):
            value = container.get(field)
            if isinstance(value, str) and value == skill_name:
                return True
    return False


def event_reads_skill_body(event: dict, skill_name: str) -> bool:
    marker = f"skills/{skill_name}/SKILL.md"
    containers = [event]
    item = event.get("item")
    if isinstance(item, dict):
        containers.append(item)
    for container in containers:
        for field in ("command", "arguments", "input", "path", "uri", "file", "filename"):
            value = container.get(field)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and marker in value.replace("\\", "/"):
                return True
    return False


def parse_events(
    stdout: str,
    skill_name: str,
    cli_version: str,
    allow_unverified_negative: bool = False,
) -> dict:
    structured_event = False
    body_read = False
    turn_completed = False
    usage = {}
    valid_events = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        valid_events += 1
        structured_event = structured_event or event_names_skill(event, skill_name)
        body_read = body_read or event_reads_skill_body(event, skill_name)
        if event.get("type") == "turn.completed":
            turn_completed = True
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]

    if structured_event:
        triggered = True
        status = "triggered"
        method = "structured_skill_event"
    elif body_read:
        triggered = True
        status = "triggered"
        method = "skill_body_read"
    elif turn_completed and (
        cli_version in KNOWN_NEGATIVE_OBSERVABLE_CLI_VERSIONS or allow_unverified_negative
    ):
        triggered = False
        status = "not_triggered"
        method = (
            "verified_absence"
            if cli_version in KNOWN_NEGATIVE_OBSERVABLE_CLI_VERSIONS
            else "user_accepted_unverified_absence"
        )
    else:
        triggered = None
        status = "indeterminate"
        method = "unobservable_harness"

    return {
        "triggered": triggered,
        "detection_status": status,
        "detection_method": method,
        "turn_completed": turn_completed,
        "valid_events": valid_events,
        "usage": usage,
    }


def codex_version(environment: dict[str, str]) -> str:
    completed = subprocess.run(
        ["codex", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError("cannot determine Codex CLI version")
    return completed.stdout.strip().splitlines()[-1]


def self_test() -> None:
    skill_name = "verify-engineering-work"
    known_version = next(iter(KNOWN_NEGATIVE_OBSERVABLE_CLI_VERSIONS))
    body_read = json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "command": "sed -n 1,200p /tmp/.agents/skills/verify-engineering-work/SKILL.md",
            },
        }
    )
    structured = json.dumps(
        {"type": "skill.invoked", "skill_name": "verify-engineering-work"}
    )
    completed = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}})

    assert parse_events(body_read + "\n" + completed, skill_name, "future")["triggered"] is True
    assert parse_events(structured + "\n" + completed, skill_name, "future")["detection_method"] == "structured_skill_event"
    assert parse_events(completed, skill_name, known_version)["triggered"] is False
    assert parse_events(completed, skill_name, "future")["triggered"] is None
    assert parse_events(completed, skill_name, "future", allow_unverified_negative=True)["triggered"] is False
    assert parse_events("", skill_name, known_version)["triggered"] is None
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", nargs="?", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--skill-name", default="verify-engineering-work")
    parser.add_argument("--skill-version", default="0.2.1")
    parser.add_argument("--skill-commit", default="")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--ids", help="Comma-separated benchmark IDs")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--logs-dir", type=Path)
    parser.add_argument(
        "--allow-unverified-negative",
        action="store_true",
        help="Treat completed runs without positive evidence as negative on unknown CLI versions",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.benchmark is None or args.output is None:
        parser.error("benchmark and output are required unless --self-test is used")
    if args.codex_home is None or args.workspace is None:
        parser.error("--codex-home and --workspace are required")

    ids = {item.strip() for item in args.ids.split(",") if item.strip()} if args.ids else None
    cases = selected_cases(load_cases(args.benchmark), ids, args.limit)
    if not cases:
        raise ValueError("no benchmark cases selected")
    if args.logs_dir:
        args.logs_dir.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(args.codex_home.resolve())
    cli_version = codex_version(environment)
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
        detection = parse_events(
            completed.stdout,
            args.skill_name,
            cli_version,
            allow_unverified_negative=args.allow_unverified_negative,
        )
        observations.append(
            {
                "id": case_id,
                "triggered": detection["triggered"],
                "detection_status": detection["detection_status"],
                "detection_method": detection["detection_method"],
                "turn_completed": detection["turn_completed"],
                "valid_events": detection["valid_events"],
                "input_tokens": detection["usage"].get("input_tokens"),
                "output_tokens": detection["usage"].get("output_tokens"),
            }
        )

    payload = {
        "experiment": {
            "skill_version": args.skill_version,
            "model": args.model,
            "harness": f"{cli_version}-read-only",
            "skill_commit": args.skill_commit,
            "detection_schema_version": DETECTION_SCHEMA_VERSION,
            "negative_detection_verified": cli_version
            in KNOWN_NEGATIVE_OBSERVABLE_CLI_VERSIONS,
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
