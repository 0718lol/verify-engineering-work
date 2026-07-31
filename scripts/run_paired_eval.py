#!/usr/bin/env python3
"""Run resumable, blinded baseline/Skill engineering task pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from run_trigger_eval import (
    atomic_write_json,
    codex_version,
    file_sha256,
    project_version,
    stable_sha256,
    write_log,
)


RUNNER_SCHEMA_VERSION = "1.2.0"
RUBRIC_VERSION = "3.1.0"
VARIANTS = ("baseline", "skill")
CODEX_HOME_CONTROL_PATHS = {
    "AGENTS.md",
    "auth.json",
    "config.toml",
    "instructions.md",
    "plugins",
    "rules",
    "skills",
    "vendor_imports",
}


def load_task_set(path: Path) -> tuple[str, list[dict]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read task set {path}: {exc}") from exc
    version = payload.get("version") if isinstance(payload, dict) else None
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("task set must contain a non-empty version")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task set must contain a non-empty tasks array")

    normalized = []
    ids = set()
    workspaces = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"tasks[{index}] must be an object")
        task_id = task.get("id")
        prompt = task.get("prompt")
        variants = task.get("workspaces")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
            raise ValueError(f"tasks[{index}].id must be filesystem-safe")
        if task_id in ids:
            raise ValueError(f"duplicate task ID: {task_id}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{task_id}: prompt must be non-empty")
        if not isinstance(variants, dict) or set(variants) != set(VARIANTS):
            raise ValueError(f"{task_id}: workspaces must contain baseline and skill")
        resolved = {}
        for variant in VARIANTS:
            raw_path = variants[variant]
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError(f"{task_id}: {variant} workspace must be a path")
            workspace = Path(raw_path).expanduser()
            if not workspace.is_absolute():
                workspace = path.parent / workspace
            workspace = workspace.resolve()
            if workspace in workspaces:
                raise ValueError("every task variant must use a distinct workspace")
            workspaces.add(workspace)
            resolved[variant] = workspace
        ids.add(task_id)
        normalized.append(
            {
                "id": task_id,
                "category": task.get("category", "uncategorized"),
                "prompt": prompt.strip(),
                "workspaces": resolved,
            }
        )
    return version.strip(), normalized


def blind_id(task_id: str, variant: str, seed: int) -> str:
    digest = hashlib.sha256(f"{seed}:{task_id}:{variant}".encode("utf-8")).hexdigest()
    return f"run-{digest[:12]}"


def build_schedule(tasks: list[dict], seed: int) -> list[dict]:
    schedule = [
        {
            "id": task["id"],
            "category": task["category"],
            "variant": variant,
            "prompt": task["prompt"],
            "workspace": task["workspaces"][variant],
            "blind_id": blind_id(task["id"], variant, seed),
        }
        for task in tasks
        for variant in VARIANTS
    ]
    random.Random(seed).shuffle(schedule)
    return schedule


def select_tasks(tasks: list[dict], ids: set[str] | None) -> list[dict]:
    if ids is None:
        return tasks
    selected = [task for task in tasks if task["id"] in ids]
    missing = ids - {task["id"] for task in selected}
    if missing:
        raise ValueError(f"unknown task IDs: {sorted(missing)}")
    if not selected:
        raise ValueError("no tasks selected")
    return selected


def parse_codex_output(stdout: str) -> dict:
    usage = {}
    responses = []
    valid_events = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        valid_events += 1
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            responses.append(text.strip())
    return {
        "usage": usage,
        "response": responses[-1] if responses else "",
        "valid_events": valid_events,
    }


def git_capture(workspace: Path) -> dict:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0 or status.returncode != 0:
        raise ValueError(f"workspace must be a Git repository: {workspace}")
    diff = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        raise ValueError(f"cannot inspect workspace diff: {workspace}")
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked.returncode != 0:
        raise ValueError(f"cannot inspect untracked files: {workspace}")
    diff_parts = [diff.stdout]
    for relative in sorted(path for path in untracked.stdout.split("\0") if path):
        untracked_diff = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--", "/dev/null", relative],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if untracked_diff.returncode not in (0, 1):
            raise ValueError(f"cannot inspect untracked file: {workspace / relative}")
        diff_parts.append(untracked_diff.stdout)
    complete_diff = "".join(diff_parts)
    return {
        "head": head.stdout.strip(),
        "status": status.stdout,
        "status_sha256": stable_sha256(status.stdout),
        "diff": complete_diff,
        "diff_sha256": hashlib.sha256(complete_diff.encode("utf-8")).hexdigest(),
    }


def checkpoint_matches(capture: dict, run: dict) -> bool:
    return (
        capture["head"] == run.get("workspace_head_after")
        and capture["status_sha256"] == run.get("workspace_status_sha256")
        and capture["diff_sha256"] == run.get("diff_sha256")
    )


def same_workspace_state(left: dict, right: dict) -> bool:
    return all(
        left[field] == right[field]
        for field in ("head", "status_sha256", "diff_sha256")
    )


def total_tokens(runs: list[dict]) -> int:
    return sum(
        value
        for run in runs
        for field in ("input_tokens", "output_tokens")
        if isinstance((value := run.get(field)), (int, float))
    )


def write_review_artifacts(
    result: dict,
    review_output: Path,
    mapping_output: Path,
) -> None:
    review_runs = []
    mapping = []
    for run in result["runs"]:
        review_runs.append(
            {
                "blind_id": run["blind_id"],
                "task_id": run["id"],
                "category": run.get("category", "uncategorized"),
                "agent_response": run["agent_response"],
                "diff_artifact": run["diff_artifact"],
                "diff_sha256": run["diff_sha256"],
                "diff_stored_sha256": run.get("diff_stored_sha256"),
                "workspace_status": run["workspace_status"],
                "tokens": run["input_tokens"] + run["output_tokens"]
                if isinstance(run.get("input_tokens"), (int, float))
                and isinstance(run.get("output_tokens"), (int, float))
                else None,
                "duration_seconds": run["duration_seconds"],
                "token_budget_exceeded": run.get("token_budget_exceeded"),
            }
        )
        mapping.append(
            {
                "blind_id": run["blind_id"],
                "task_id": run["id"],
                "variant": run["variant"],
            }
        )
    atomic_write_json(
        review_output,
        {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "task_set_version": result["experiment"]["task_set_version"],
            "runs": sorted(review_runs, key=lambda item: item["blind_id"]),
        },
    )
    atomic_write_json(
        mapping_output,
        {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "mapping": sorted(mapping, key=lambda item: item["blind_id"]),
        },
    )


def validate_skill_homes(
    baseline_home: Path,
    skill_home: Path,
    skill_name: str,
) -> str:
    for label, home in (("baseline", baseline_home), ("skill", skill_home)):
        if not home.is_dir():
            raise ValueError(f"{label} Codex home is not a directory: {home}")
    baseline_skill = baseline_home / "skills" / skill_name
    installed_skill = skill_home / "skills" / skill_name / "SKILL.md"
    if baseline_skill.exists():
        raise ValueError(f"baseline Codex home must not contain {skill_name}")
    if not installed_skill.is_file():
        raise ValueError(f"skill Codex home must contain skills/{skill_name}/SKILL.md")
    manifests = {}
    for label, home in (("baseline", baseline_home), ("skill", skill_home)):
        files = {}
        for candidate in sorted(home.rglob("*")):
            relative = candidate.relative_to(home)
            if not relative.parts or relative.parts[0] not in CODEX_HOME_CONTROL_PATHS:
                continue
            if relative.parts[:2] == ("skills", skill_name):
                continue
            key = relative.as_posix()
            if candidate.is_symlink():
                files[key] = {"type": "symlink", "target": os.readlink(candidate)}
            elif candidate.is_file():
                files[key] = {"type": "file", "sha256": file_sha256(candidate)}
        manifests[label] = files
    if manifests["baseline"] != manifests["skill"]:
        differing = sorted(
            key
            for key in set(manifests["baseline"]) | set(manifests["skill"])
            if manifests["baseline"].get(key) != manifests["skill"].get(key)
        )
        preview = ", ".join(differing[:5])
        if len(differing) > 5:
            preview += ", ..."
        raise ValueError(
            "baseline and Skill Codex homes differ outside the target Skill: "
            + preview
        )
    return stable_sha256(manifests["baseline"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_set", nargs="?", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--baseline-codex-home", type=Path)
    parser.add_argument("--skill-codex-home", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--review-output", type=Path)
    parser.add_argument("--mapping-output", type=Path)
    parser.add_argument("--skill-name", default="verify-engineering-work")
    parser.add_argument("--skill-version")
    parser.add_argument("--skill-commit", required=False, default="")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--ids", help="Comma-separated task IDs")
    parser.add_argument(
        "--held-out-isolation",
        choices=("pilot-local", "evaluator-external"),
        default="pilot-local",
        help="Isolation level of held-out verification assets",
    )
    parser.add_argument("--token-budget", type=int, default=20000)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-dirty-workspaces", action="store_true")
    parser.add_argument("--allow-workspace-write", action="store_true")
    parser.add_argument("--unsafe-raw-logs", action="store_true")
    args = parser.parse_args()

    required = (
        args.task_set,
        args.output,
        args.baseline_codex_home,
        args.skill_codex_home,
        args.artifacts_dir,
        args.review_output,
        args.mapping_output,
    )
    if any(value is None for value in required):
        parser.error("task set, output, both Codex homes, artifacts, review, and mapping paths are required")
    if not args.allow_workspace_write:
        raise ValueError("paired evaluation edits repositories; pass --allow-workspace-write")
    if args.timeout < 1 or args.token_budget < 1:
        raise ValueError("--timeout and --token-budget must be positive")
    if args.max_total_tokens is not None and args.max_total_tokens < 1:
        raise ValueError("--max-total-tokens must be positive")

    codex_home_manifest_sha256 = validate_skill_homes(
        args.baseline_codex_home,
        args.skill_codex_home,
        args.skill_name,
    )
    task_set_version, tasks = load_task_set(args.task_set)
    ids = {item.strip() for item in args.ids.split(",") if item.strip()} if args.ids else None
    tasks = select_tasks(tasks, ids)
    schedule = build_schedule(tasks, args.seed)

    environments = {}
    cli_versions = {}
    for variant, home in (
        ("baseline", args.baseline_codex_home),
        ("skill", args.skill_codex_home),
    ):
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(home.resolve())
        environments[variant] = environment
        cli_versions[variant] = codex_version(environment, args.codex_executable)
    if len(set(cli_versions.values())) != 1:
        raise ValueError("baseline and Skill variants must use the same Codex CLI version")

    experiment = {
        "skill_version": args.skill_version or project_version(),
        "rubric_version": RUBRIC_VERSION,
        "model": args.model,
        "harness": f"{cli_versions['baseline']}-workspace-write-paired-v1",
        "task_set_version": task_set_version,
        "task_set_sha256": file_sha256(args.task_set),
        "token_budget": args.token_budget,
        "skill_commit": args.skill_commit,
        "runner_schema_version": RUNNER_SCHEMA_VERSION,
        "order_seed": args.seed,
        "skill_body_sha256": file_sha256(
            args.skill_codex_home
            / "skills"
            / args.skill_name
            / "SKILL.md"
        ),
        "codex_home_manifest_sha256": codex_home_manifest_sha256,
        "held_out_isolation": args.held_out_isolation,
    }
    preparation_manifest = args.task_set.parent / "preparation-manifest.json"
    if preparation_manifest.is_file():
        experiment["preparation_manifest_sha256"] = file_sha256(
            preparation_manifest
        )
    selected = [
        {
            "id": run["id"],
            "variant": run["variant"],
            "blind_id": run["blind_id"],
        }
        for run in schedule
    ]
    if args.output.exists():
        if not args.resume:
            raise ValueError("output already exists; use --resume")
        result = json.loads(args.output.read_text(encoding="utf-8"))
        if result.get("experiment") != experiment or result.get("schedule") != selected:
            raise ValueError("resume metadata or schedule differs from current run")
        runs = result.get("runs")
        if not isinstance(runs, list):
            raise ValueError("resume output must contain runs")
    else:
        if args.resume:
            raise ValueError("--resume requires an existing output")
        runs = []
        result = {
            "experiment": experiment,
            "schedule": selected,
            "run_state": {},
            "runs": runs,
        }

    completed_runs = {
        (run.get("id"), run.get("variant")): run
        for run in runs
        if isinstance(run, dict)
    }
    if len(completed_runs) != len(runs):
        raise ValueError("resume runs must contain unique task variants")
    for item in schedule:
        capture = git_capture(item["workspace"])
        previous = completed_runs.get((item["id"], item["variant"]))
        if previous is not None:
            if not checkpoint_matches(capture, previous):
                raise ValueError(
                    f"completed workspace changed since checkpoint: {item['workspace']}"
                )
        elif capture["status"] and not args.allow_dirty_workspaces:
            raise ValueError(f"workspace is not clean: {item['workspace']}")

    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    completed = set(completed_runs)
    for index, item in enumerate(schedule, start=1):
        key = (item["id"], item["variant"])
        if key in completed:
            print(f"[{index}/{len(schedule)}] {item['blind_id']} (resumed)", file=sys.stderr)
            continue
        if args.max_total_tokens is not None and total_tokens(runs) >= args.max_total_tokens:
            result["run_state"] = {
                "status": "budget_exhausted",
                "completed_runs": len(runs),
                "remaining_runs": len(schedule) - len(runs),
                "total_tokens": total_tokens(runs),
                "max_total_tokens": args.max_total_tokens,
            }
            atomic_write_json(args.output, result)
            write_review_artifacts(result, args.review_output, args.mapping_output)
            return 3

        before = git_capture(item["workspace"])
        print(f"[{index}/{len(schedule)}] {item['blind_id']}", file=sys.stderr, flush=True)
        command = [
            args.codex_executable,
            "-a",
            "never",
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "-s",
            "workspace-write",
            "-m",
            args.model,
            "-C",
            str(item["workspace"]),
            item["prompt"],
        ]
        started = time.monotonic()
        completed_process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            env=environments[item["variant"]],
            check=False,
        )
        duration = time.monotonic() - started
        stdout_meta = write_log(
            args.artifacts_dir / f"{item['blind_id']}.stdout.jsonl",
            completed_process.stdout,
            args.unsafe_raw_logs,
        )
        write_log(
            args.artifacts_dir / f"{item['blind_id']}.stderr.log",
            completed_process.stderr,
            args.unsafe_raw_logs,
        )
        if completed_process.returncode != 0:
            result["run_state"] = {
                "status": "failed",
                "completed_runs": len(runs),
                "remaining_runs": len(schedule) - len(runs),
                "detail": f"{item['blind_id']}: Codex exited with {completed_process.returncode}",
            }
            atomic_write_json(args.output, result)
            raise RuntimeError(result["run_state"]["detail"])

        parsed = parse_codex_output(completed_process.stdout)
        after = git_capture(item["workspace"])
        diff_path = args.artifacts_dir / f"{item['blind_id']}.diff"
        diff_meta = write_log(diff_path, after["diff"], args.unsafe_raw_logs)
        if after["head"] != before["head"]:
            result["run_state"] = {
                "status": "failed",
                "completed_runs": len(runs),
                "remaining_runs": len(schedule) - len(runs),
                "detail": f"{item['blind_id']}: agent changed the workspace HEAD",
            }
            atomic_write_json(args.output, result)
            raise RuntimeError(result["run_state"]["detail"])
        usage = parsed["usage"]
        run_tokens = (
            usage["input_tokens"] + usage["output_tokens"]
            if isinstance(usage.get("input_tokens"), (int, float))
            and isinstance(usage.get("output_tokens"), (int, float))
            else None
        )
        runs.append(
            {
                "id": item["id"],
                "category": item["category"],
                "variant": item["variant"],
                "blind_id": item["blind_id"],
                "agent_response": parsed["response"],
                "valid_events": parsed["valid_events"],
                "turn_completed": bool(usage),
                "input_tokens": usage.get("input_tokens"),
                "cached_input_tokens": usage.get("cached_input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
                "token_budget_exceeded": (
                    run_tokens > args.token_budget
                    if run_tokens is not None
                    else None
                ),
                "duration_seconds": duration,
                "workspace_head_before": before["head"],
                "workspace_head_after": after["head"],
                "workspace_status_before_sha256": before["status_sha256"],
                "workspace_status": after["status"],
                "workspace_status_sha256": after["status_sha256"],
                "diff_artifact": diff_path.name,
                "diff_sha256": diff_meta["sha256"],
                "diff_stored_sha256": diff_meta["stored_sha256"],
                "stdout_sha256": stdout_meta["sha256"],
                "stdout_stored_sha256": stdout_meta["stored_sha256"],
                "log_redactions": stdout_meta["redactions"] + diff_meta["redactions"],
            }
        )
        result["run_state"] = {
            "status": "running",
            "completed_runs": len(runs),
            "remaining_runs": len(schedule) - len(runs),
            "total_tokens": total_tokens(runs),
            "max_total_tokens": args.max_total_tokens,
        }
        atomic_write_json(args.output, result)
        write_review_artifacts(result, args.review_output, args.mapping_output)

    result["run_state"] = {
        "status": "completed",
        "completed_runs": len(runs),
        "remaining_runs": 0,
        "total_tokens": total_tokens(runs),
        "max_total_tokens": args.max_total_tokens,
    }
    atomic_write_json(args.output, result)
    write_review_artifacts(result, args.review_output, args.mapping_output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
