#!/usr/bin/env python3
"""Reclassify trigger observations from retained Codex JSONL logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from run_trigger_eval import (
    DETECTION_SCHEMA_VERSION,
    atomic_write_json,
    load_json,
    parse_events,
)


def expected_stdout_hash(observation: dict, attempt: int) -> str | None:
    hashes = observation.get("log_sha256")
    if not isinstance(hashes, list):
        return None
    for entry in hashes:
        if isinstance(entry, dict) and entry.get("attempt") == attempt:
            value = entry.get("stdout_sha256")
            return value if isinstance(value, str) else None
    return None


def reclassify(payload: dict, logs_dir: Path, skill_name: str) -> tuple[dict, int]:
    experiment = payload.get("experiment")
    observations = payload.get("observations")
    if not isinstance(experiment, dict):
        raise ValueError("observation file must contain experiment metadata")
    if not isinstance(observations, list) or not observations:
        raise ValueError("observation file must contain at least one observation")

    negative_observable = experiment.get("negative_detection_verified") is True
    changed = 0
    updated_observations = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            raise ValueError(f"observations[{index}] must be an object")
        case_id = observation.get("id")
        attempt = observation.get("attempts")
        if not isinstance(case_id, str) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError(f"observations[{index}] has invalid id or attempts")
        log_path = logs_dir / f"{case_id}.attempt-{attempt}.stdout.jsonl"
        try:
            stdout = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read retained log {log_path}: {exc}") from exc

        detection = parse_events(
            stdout,
            skill_name,
            negative_observable=negative_observable,
        )
        previous = {
            "triggered": observation.get("triggered"),
            "detection_status": observation.get("detection_status"),
            "detection_method": observation.get("detection_method"),
        }
        current = {
            "triggered": detection["triggered"],
            "detection_status": detection["detection_status"],
            "detection_method": detection["detection_method"],
        }
        updated = dict(observation)
        updated.update(current)
        updated["turn_completed"] = detection["turn_completed"]
        updated["valid_events"] = detection["valid_events"]
        updated["input_tokens"] = detection["usage"].get("input_tokens")
        updated["output_tokens"] = detection["usage"].get("output_tokens")

        expected_hash = expected_stdout_hash(observation, attempt)
        if expected_hash is not None and observation.get("log_redactions") == 0:
            actual_hash = hashlib.sha256(stdout.encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"{case_id}: retained stdout hash does not match observation")
            updated["log_integrity_verified"] = True
        else:
            updated["log_integrity_verified"] = None

        if previous != current:
            changed += 1
            updated["reclassified_from"] = previous
        updated_observations.append(updated)

    updated_experiment = dict(experiment)
    previous_schema = updated_experiment.get("detection_schema_version")
    if previous_schema != DETECTION_SCHEMA_VERSION:
        updated_experiment["reclassified_from_detection_schema_version"] = previous_schema
    updated_experiment["detection_schema_version"] = DETECTION_SCHEMA_VERSION
    result = dict(payload)
    result["experiment"] = updated_experiment
    result["observations"] = updated_observations
    return result, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    parser.add_argument("logs_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--skill-name", default="verify-engineering-work")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.output.exists() and not args.overwrite:
        raise ValueError("output already exists; use --overwrite to replace it")
    if args.output.resolve() == args.observations.resolve():
        raise ValueError("write reclassified observations to a separate output file")
    payload, changed = reclassify(
        load_json(args.observations, "observations"),
        args.logs_dir,
        args.skill_name,
    )
    atomic_write_json(args.output, payload)
    print(
        f"wrote {len(payload['observations'])} observations to {args.output}; "
        f"changed classifications: {changed}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
