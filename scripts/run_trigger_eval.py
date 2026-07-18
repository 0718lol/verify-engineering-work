#!/usr/bin/env python3
"""Run a resumable, read-only Codex implicit-trigger benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERSION_PATH = ROOT / "VERSION"
DEFAULT_COMPATIBILITY_PATH = ROOT / "references" / "compatibility.json"
DETECTION_SCHEMA_VERSION = "2.3.0"
SNAPSHOT_SCHEMA_VERSION = "1.0.0"
REDACTION_PATTERNS = (
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"([\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]{8,})"
    ),
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)[A-Za-z0-9._~+/-]+"),
)


def load_json(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def project_version(path: Path = VERSION_PATH) -> str:
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot read version file {path}: {exc}") from exc
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION must contain a semantic version such as 0.2.2")
    return version


def load_compatibility(path: Path = DEFAULT_COMPATIBILITY_PATH) -> dict:
    payload = load_json(path, "compatibility configuration")
    if not isinstance(payload.get("codex_cli_versions"), dict):
        raise ValueError("compatibility configuration must contain codex_cli_versions")
    return payload


def negative_detection_observable(cli_version: str, compatibility: dict) -> bool:
    entry = compatibility["codex_cli_versions"].get(cli_version)
    return isinstance(entry, dict) and entry.get("negative_detection_observable") is True


def load_cases(path: Path) -> list[dict]:
    payload = load_json(path, "benchmark")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark must contain a non-empty cases array")
    ids = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"cases[{index}] must be an object")
        case_id = case.get("id")
        prompt = case.get("prompt")
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", case_id):
            raise ValueError(f"cases[{index}].id must be filesystem-safe")
        if case_id in ids:
            raise ValueError(f"duplicate case ID: {case_id}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{case_id}: prompt must be a non-empty string")
        ids.add(case_id)
    return cases


def selected_cases(cases: list[dict], ids: set[str] | None, limit: int | None) -> list[dict]:
    selected = [case for case in cases if ids is None or case["id"] in ids]
    if ids is not None:
        missing = ids - {case["id"] for case in selected}
        if missing:
            raise ValueError(f"unknown case IDs: {sorted(missing)}")
    return selected[:limit] if limit is not None else selected


def ordered_cases(
    cases: list[dict],
    strategy: str,
    seed: int,
) -> list[dict]:
    ordered = list(cases)
    if strategy == "benchmark":
        return ordered
    rng = random.Random(seed)
    if strategy == "shuffle":
        rng.shuffle(ordered)
        return ordered
    positives = [case for case in ordered if case.get("expected_trigger") is True]
    negatives = [case for case in ordered if case.get("expected_trigger") is False]
    if len(positives) + len(negatives) != len(ordered):
        raise ValueError("stratified order requires boolean expected_trigger labels")
    rng.shuffle(positives)
    rng.shuffle(negatives)
    interleaved = []
    for index in range(max(len(positives), len(negatives))):
        if index < len(positives):
            interleaved.append(positives[index])
        if index < len(negatives):
            interleaved.append(negatives[index])
    return interleaved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_snapshot(
    snapshot_dir: Path,
    benchmark_path: Path,
    compatibility_path: Path,
    selected_ids: list[str],
) -> str:
    project_files = (
        "VERSION",
        "SKILL.md",
        "agents/openai.yaml",
        "references/risk-matrix.md",
        "scripts/run_trigger_eval.py",
        "scripts/score_trigger_eval.py",
        "scripts/reclassify_trigger_eval.py",
    )
    manifest = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "project_version": project_version(),
        "benchmark": {
            "stored_as": "inputs/benchmark.json",
            "sha256": file_sha256(benchmark_path),
        },
        "compatibility": {
            "stored_as": "inputs/compatibility.json",
            "sha256": file_sha256(compatibility_path),
        },
        "selected_case_ids": selected_ids,
        "selected_case_ids_sha256": stable_sha256(selected_ids),
        "project_files": {
            relative: file_sha256(ROOT / relative)
            for relative in project_files
            if (ROOT / relative).is_file()
        },
    }
    manifest_hash = stable_sha256(manifest)
    manifest["manifest_sha256"] = manifest_hash
    manifest_path = snapshot_dir / "manifest.json"
    if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
        existing = load_json(manifest_path, "snapshot manifest")
        if existing != manifest:
            raise ValueError("snapshot directory contains a different experiment snapshot")
        stored_files = {
            snapshot_dir / "inputs" / "benchmark.json": manifest["benchmark"]["sha256"],
            snapshot_dir / "inputs" / "compatibility.json": manifest["compatibility"]["sha256"],
            **{
                snapshot_dir / "project" / relative: digest
                for relative, digest in manifest["project_files"].items()
            },
        }
        for stored_path, expected_digest in stored_files.items():
            if not stored_path.is_file() or file_sha256(stored_path) != expected_digest:
                raise ValueError(f"snapshot file is missing or modified: {stored_path}")
        return manifest_hash

    (snapshot_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "project").mkdir(parents=True, exist_ok=True)
    shutil.copy2(benchmark_path, snapshot_dir / "inputs" / "benchmark.json")
    shutil.copy2(compatibility_path, snapshot_dir / "inputs" / "compatibility.json")
    for relative in manifest["project_files"]:
        destination = snapshot_dir / "project" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    atomic_write_json(manifest_path, manifest)
    return manifest_hash


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


def event_reads_skill_body(
    event: dict,
    skill_name: str,
    skill_body_paths: tuple[Path, ...] = (),
) -> bool:
    marker = f"{skill_name}/SKILL.md"
    resolved_markers = tuple(
        marker
        for path in skill_body_paths
        for marker in {
            str(path).replace("\\", "/"),
            str(path.resolve()).replace("\\", "/"),
        }
    )
    containers = [event]
    item = event.get("item")
    if isinstance(item, dict):
        containers.append(item)
    for container in containers:
        for field in ("command", "arguments", "input", "path", "uri", "file", "filename"):
            value = container.get(field)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str):
                normalized = value.replace("\\", "/")
                if marker in normalized or any(
                    resolved in normalized for resolved in resolved_markers
                ):
                    return True
    return False


def parse_events(
    stdout: str,
    skill_name: str,
    negative_observable: bool,
    allow_unverified_negative: bool = False,
    skill_body_paths: tuple[Path, ...] = (),
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
        body_read = body_read or event_reads_skill_body(
            event,
            skill_name,
            skill_body_paths,
        )
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
    elif turn_completed and (negative_observable or allow_unverified_negative):
        triggered = False
        status = "not_triggered"
        method = (
            "verified_absence"
            if negative_observable
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


def redact_log(text: str) -> tuple[str, int]:
    redactions = 0

    def replace_secret(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        if match.lastindex and match.lastindex >= 2:
            return "".join(group or "" for group in match.groups()[:-1]) + "[REDACTED]"
        return "[REDACTED]"

    for pattern in REDACTION_PATTERNS:
        text = pattern.sub(replace_secret, text)
    return text, redactions


def write_log(
    path: Path,
    content: str,
    unsafe_raw_logs: bool,
) -> dict:
    original_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if unsafe_raw_logs:
        stored = content
        redactions = 0
    else:
        stored, redactions = redact_log(content)
    path.write_text(stored, encoding="utf-8")
    return {
        "sha256": original_hash,
        "redactions": redactions,
        "raw": unsafe_raw_logs,
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


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def usage_totals(
    observations: list[dict],
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
) -> dict:
    input_tokens = sum(
        value
        for observation in observations
        if isinstance((value := observation.get("input_tokens")), (int, float))
    )
    output_tokens = sum(
        value
        for observation in observations
        if isinstance((value := observation.get("output_tokens")), (int, float))
    )
    cached_input_tokens = sum(
        value
        for observation in observations
        if isinstance((value := observation.get("cached_input_tokens")), (int, float))
    )
    reasoning_output_tokens = sum(
        value
        for observation in observations
        if isinstance((value := observation.get("reasoning_output_tokens")), (int, float))
    )
    estimated_cost = None
    if input_usd_per_million is not None and output_usd_per_million is not None:
        estimated_cost = (
            input_tokens * input_usd_per_million
            + output_tokens * output_usd_per_million
        ) / 1_000_000
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": estimated_cost,
    }


def budget_exhaustion_reason(totals: dict, args: argparse.Namespace) -> str | None:
    checks = (
        ("input token budget", totals["input_tokens"], args.max_input_tokens),
        ("output token budget", totals["output_tokens"], args.max_output_tokens),
        ("total token budget", totals["total_tokens"], args.max_total_tokens),
        ("estimated cost budget", totals["estimated_cost_usd"], args.max_cost_usd),
    )
    for name, value, maximum in checks:
        if maximum is not None and value is not None and value >= maximum:
            return f"{name} reached: {value} >= {maximum}"
    return None


def run_state(
    status: str,
    selected_ids: list[str],
    observations: list[dict],
    args: argparse.Namespace,
    detail: str | None = None,
) -> dict:
    state = {
        "status": status,
        "selected_case_ids": selected_ids,
        "completed_cases": len(observations),
        "remaining_cases": len(selected_ids) - len(observations),
        "usage": usage_totals(
            observations,
            args.input_usd_per_million,
            args.output_usd_per_million,
        ),
        "limits": {
            "max_input_tokens": args.max_input_tokens,
            "max_output_tokens": args.max_output_tokens,
            "max_total_tokens": args.max_total_tokens,
            "max_cost_usd": args.max_cost_usd,
            "input_usd_per_million": args.input_usd_per_million,
            "output_usd_per_million": args.output_usd_per_million,
        },
    }
    if detail:
        state["detail"] = detail
    return state


def verify_resume_payload(
    payload: dict,
    experiment: dict,
    selected_ids: list[str],
) -> list[dict]:
    if payload.get("experiment") != experiment:
        raise ValueError("resume experiment metadata differs from the current run")
    state = payload.get("run_state")
    if not isinstance(state, dict) or state.get("selected_case_ids") != selected_ids:
        raise ValueError("resume case selection differs from the current run")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError("resume file must contain observations")
    ids = [observation.get("id") for observation in observations if isinstance(observation, dict)]
    if len(ids) != len(observations) or len(set(ids)) != len(ids):
        raise ValueError("resume observations must have unique IDs")
    if any(case_id not in selected_ids for case_id in ids):
        raise ValueError("resume observations contain an unselected case")
    return observations


def run_codex_case(
    command: list[str],
    environment: dict[str, str],
    case_id: str,
    args: argparse.Namespace,
) -> tuple[str, int, dict]:
    last_error = ""
    combined_log_metadata = {
        "redactions": 0,
        "raw": args.unsafe_raw_logs,
        "attempts": 0,
        "files": [],
    }
    for attempt in range(1, args.retries + 2):
        stdout = ""
        stderr = ""
        returncode = None
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                env=environment,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            last_error = f"timed out after {args.timeout}s"

        combined_log_metadata["attempts"] = attempt
        if args.logs_dir:
            stdout_metadata = write_log(
                args.logs_dir / f"{case_id}.attempt-{attempt}.stdout.jsonl",
                stdout,
                args.unsafe_raw_logs,
            )
            stderr_metadata = write_log(
                args.logs_dir / f"{case_id}.attempt-{attempt}.stderr.log",
                stderr,
                args.unsafe_raw_logs,
            )
            combined_log_metadata["redactions"] += (
                stdout_metadata["redactions"] + stderr_metadata["redactions"]
            )
            combined_log_metadata["files"].append(
                {
                    "attempt": attempt,
                    "stdout_sha256": stdout_metadata["sha256"],
                    "stderr_sha256": stderr_metadata["sha256"],
                }
            )
        if returncode == 0:
            return stdout, attempt, combined_log_metadata
        if returncode is not None:
            last_error = f"Codex exited with {returncode}"
        if attempt <= args.retries:
            time.sleep(args.retry_delay_seconds)
    raise RuntimeError(f"{case_id}: {last_error}")


def validate_arguments(args: argparse.Namespace) -> None:
    for field in (
        "limit",
        "timeout",
        "max_input_tokens",
        "max_output_tokens",
        "max_total_tokens",
    ):
        value = getattr(args, field)
        if value is not None and value < 1:
            raise ValueError(f"--{field.replace('_', '-')} must be at least 1")
    if args.retries < 0:
        raise ValueError("--retries must be non-negative")
    if args.retry_delay_seconds < 0:
        raise ValueError("--retry-delay-seconds must be non-negative")
    if args.resume and args.overwrite:
        raise ValueError("--resume and --overwrite are mutually exclusive")
    rates = (args.input_usd_per_million, args.output_usd_per_million)
    if any(rate is not None and rate <= 0 for rate in rates):
        raise ValueError("token price rates must be positive")
    if args.max_cost_usd is not None:
        if args.max_cost_usd <= 0:
            raise ValueError("--max-cost-usd must be positive")
        if any(rate is None for rate in rates):
            raise ValueError(
                "--max-cost-usd requires --input-usd-per-million and "
                "--output-usd-per-million"
            )


def self_test() -> None:
    project_version()
    compatibility = load_compatibility()
    cli_version = next(iter(compatibility["codex_cli_versions"]))
    entry = compatibility["codex_cli_versions"][cli_version]
    triggered_fixture = (ROOT / entry["triggered_fixture"]).read_text(encoding="utf-8")
    negative_fixture = (ROOT / entry["not_triggered_fixture"]).read_text(encoding="utf-8")
    assert parse_events(triggered_fixture, "verify-engineering-work", True)["triggered"] is True
    assert parse_events(negative_fixture, "verify-engineering-work", True)["triggered"] is False
    assert parse_events(negative_fixture, "verify-engineering-work", False)["triggered"] is None

    redacted, count = redact_log(
        'Authorization: Bearer secret-value-123456 token="ghp_12345678901234567890"'
    )
    assert "secret-value" not in redacted and "ghp_" not in redacted and count == 2

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "result.json"
        atomic_write_json(output, {"ok": True})
        assert load_json(output, "self-test result") == {"ok": True}
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", nargs="?", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--compatibility", type=Path, default=DEFAULT_COMPATIBILITY_PATH)
    parser.add_argument("--skill-name", default="verify-engineering-work")
    parser.add_argument("--skill-version")
    parser.add_argument("--skill-commit", default="")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--ids", help="Comma-separated benchmark IDs")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--order",
        choices=("benchmark", "shuffle", "stratified"),
        default="benchmark",
        help="Reproducible case ordering strategy",
    )
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--retry-delay-seconds", type=float, default=2.0)
    parser.add_argument("--logs-dir", type=Path)
    parser.add_argument("--unsafe-raw-logs", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-input-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
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
    validate_arguments(args)
    if not args.codex_home.is_dir():
        raise ValueError(f"--codex-home is not a directory: {args.codex_home}")
    if not args.workspace.is_dir():
        raise ValueError(f"--workspace is not a directory: {args.workspace}")

    ids = {item.strip() for item in args.ids.split(",") if item.strip()} if args.ids else None
    cases = selected_cases(load_cases(args.benchmark), ids, args.limit)
    cases = ordered_cases(cases, args.order, args.seed)
    if not cases:
        raise ValueError("no benchmark cases selected")
    selected_ids = [case["id"] for case in cases]
    if args.logs_dir:
        args.logs_dir.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(args.codex_home.resolve())
    cli_version = codex_version(environment)
    compatibility = load_compatibility(args.compatibility)
    negative_observable = negative_detection_observable(cli_version, compatibility)
    skill_version = args.skill_version or project_version()
    experiment = {
        "skill_version": skill_version,
        "model": args.model,
        "harness": f"{cli_version}-read-only",
        "skill_commit": args.skill_commit,
        "detection_schema_version": DETECTION_SCHEMA_VERSION,
        "negative_detection_verified": negative_observable,
        "compatibility_schema_version": compatibility.get("schema_version"),
        "benchmark_sha256": file_sha256(args.benchmark),
        "compatibility_sha256": file_sha256(args.compatibility),
        "case_order": args.order,
        "order_seed": args.seed,
    }
    installed_skill_body = (
        args.codex_home / "skills" / args.skill_name / "SKILL.md"
    )
    skill_body_paths = (
        (installed_skill_body.resolve(),) if installed_skill_body.is_file() else ()
    )
    if skill_body_paths:
        experiment["skill_body_sha256"] = file_sha256(skill_body_paths[0])
    if args.snapshot_dir:
        experiment["snapshot_manifest_sha256"] = create_snapshot(
            args.snapshot_dir,
            args.benchmark,
            args.compatibility,
            selected_ids,
        )

    if args.output.exists():
        if args.resume:
            payload = load_json(args.output, "resume file")
            observations = verify_resume_payload(payload, experiment, selected_ids)
        elif args.overwrite:
            observations = []
        else:
            raise ValueError("output already exists; use --resume or --overwrite")
    else:
        if args.resume:
            raise ValueError("--resume requires an existing output file")
        observations = []

    payload = {
        "experiment": experiment,
        "run_state": run_state("running", selected_ids, observations, args),
        "observations": observations,
    }
    atomic_write_json(args.output, payload)
    completed_ids = {observation["id"] for observation in observations}
    suffix = "\n\nOnly assess this request in read-only mode. Do not perform external operations."

    for index, case in enumerate(cases, start=1):
        case_id = case["id"]
        if case_id in completed_ids:
            print(f"[{index}/{len(cases)}] {case_id} (resumed)", file=sys.stderr, flush=True)
            continue

        totals = usage_totals(
            observations,
            args.input_usd_per_million,
            args.output_usd_per_million,
        )
        exhausted = budget_exhaustion_reason(totals, args)
        if exhausted:
            payload["run_state"] = run_state(
                "budget_exhausted", selected_ids, observations, args, exhausted
            )
            atomic_write_json(args.output, payload)
            print(f"stopped: {exhausted}", file=sys.stderr)
            return 3

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
            stdout, attempts, log_metadata = run_codex_case(
                command, environment, case_id, args
            )
        except RuntimeError as exc:
            payload["run_state"] = run_state(
                "failed", selected_ids, observations, args, str(exc)
            )
            atomic_write_json(args.output, payload)
            raise

        detection = parse_events(
            stdout,
            args.skill_name,
            negative_observable,
            allow_unverified_negative=args.allow_unverified_negative,
            skill_body_paths=skill_body_paths,
        )
        observations.append(
            {
                "id": case_id,
                "triggered": detection["triggered"],
                "detection_status": detection["detection_status"],
                "detection_method": detection["detection_method"],
                "turn_completed": detection["turn_completed"],
                "valid_events": detection["valid_events"],
                "attempts": attempts,
                "log_redactions": log_metadata["redactions"],
                "raw_logs": log_metadata["raw"],
                "log_sha256": log_metadata["files"],
                "input_tokens": detection["usage"].get("input_tokens"),
                "cached_input_tokens": detection["usage"].get("cached_input_tokens"),
                "output_tokens": detection["usage"].get("output_tokens"),
                "reasoning_output_tokens": detection["usage"].get(
                    "reasoning_output_tokens"
                ),
            }
        )
        payload["run_state"] = run_state(
            "running", selected_ids, observations, args
        )
        atomic_write_json(args.output, payload)

    payload["run_state"] = run_state("completed", selected_ids, observations, args)
    atomic_write_json(args.output, payload)
    print(f"wrote {len(observations)} observations to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
