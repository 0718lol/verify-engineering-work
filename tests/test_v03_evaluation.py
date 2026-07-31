#!/usr/bin/env python3
"""Regression tests for v0.3 evaluation infrastructure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_scored_eval
import run_paired_eval
import run_trigger_eval
import score_trigger_eval


class TriggerBenchmarkV2Tests(unittest.TestCase):
    def test_boundary_benchmark_is_balanced_and_interleaved(self) -> None:
        cases = run_trigger_eval.load_cases(
            ROOT / "references" / "trigger-benchmark-v2.json"
        )
        self.assertEqual(len(cases), 40)
        self.assertEqual(sum(case["expected_trigger"] is True for case in cases), 20)
        self.assertEqual(sum(case["expected_trigger"] is False for case in cases), 20)
        self.assertTrue(
            all(
                cases[index]["expected_trigger"] != cases[index + 1]["expected_trigger"]
                for index in range(len(cases) - 1)
            )
        )
        self.assertTrue(all(case["difficulty"] == "boundary" for case in cases))

    def test_stratified_order_is_reproducible(self) -> None:
        cases = [
            {"id": f"yes-{index}", "expected_trigger": True}
            for index in range(4)
        ] + [
            {"id": f"no-{index}", "expected_trigger": False}
            for index in range(4)
        ]
        first = run_trigger_eval.ordered_cases(cases, "stratified", 42)
        second = run_trigger_eval.ordered_cases(cases, "stratified", 42)
        self.assertEqual(first, second)
        self.assertTrue(
            all(
                first[index]["expected_trigger"] != first[index + 1]["expected_trigger"]
                for index in range(len(first) - 1)
            )
        )

    def test_snapshot_reuse_requires_identical_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark.json"
            compatibility = root / "compatibility.json"
            snapshot = root / "snapshot"
            benchmark.write_text('{"cases":[]}\n', encoding="utf-8")
            compatibility.write_text('{"schema_version":"test"}\n', encoding="utf-8")
            first = run_trigger_eval.create_snapshot(
                snapshot, benchmark, compatibility, ["case-a"]
            )
            second = run_trigger_eval.create_snapshot(
                snapshot, benchmark, compatibility, ["case-a"]
            )
            self.assertEqual(first, second)
            (snapshot / "project" / "SKILL.md").write_text(
                "modified\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "missing or modified"):
                run_trigger_eval.create_snapshot(
                    snapshot, benchmark, compatibility, ["case-a"]
                )
            (snapshot / "project" / "SKILL.md").write_bytes(
                (ROOT / "SKILL.md").read_bytes()
            )
            benchmark.write_text('{"cases":[{"id":"changed"}]}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different experiment snapshot"):
                run_trigger_eval.create_snapshot(
                    snapshot, benchmark, compatibility, ["case-a"]
                )


class TriggerReportingTests(unittest.TestCase):
    def test_category_and_usage_reports(self) -> None:
        benchmark = {
            "version": "test",
            "cases": [
                {"id": "a", "category": "edge", "expected_trigger": True},
                {"id": "b", "category": "edge", "expected_trigger": False},
            ],
        }
        observations = {
            "observations": [
                {
                    "id": "a",
                    "triggered": True,
                    "input_tokens": 10,
                    "cached_input_tokens": 4,
                    "output_tokens": 2,
                    "reasoning_output_tokens": 1,
                },
                {
                    "id": "b",
                    "triggered": False,
                    "input_tokens": 6,
                    "cached_input_tokens": 2,
                    "output_tokens": 1,
                    "reasoning_output_tokens": 0,
                },
            ]
        }
        result = score_trigger_eval.score(
            benchmark, observations, minimum_cases=2
        )
        self.assertEqual(result["metrics_by_category"]["edge"]["scorable_cases"], 2)
        self.assertEqual(result["usage"]["overall"]["total_tokens"], 19)
        self.assertEqual(
            result["usage"]["expected_trigger"]["fields"]["cached_input_tokens"]["total"],
            4,
        )
        invalid_metadata = {
            field: False if field == "negative_detection_verified" else "value"
            for field in score_trigger_eval.REQUIRED_EXPERIMENT_FIELDS
        }
        invalid_metadata["skill_commit"] = "a" * 40
        invalid = score_trigger_eval.score(
            benchmark,
            {**observations, "experiment": invalid_metadata},
            minimum_cases=2,
        )
        self.assertIn(
            "benchmark_sha256",
            invalid["quality_gate"]["invalid_experiment_metadata"],
        )


class PairedRunnerTests(unittest.TestCase):
    def test_codex_homes_must_match_outside_target_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline-home"
            skill = root / "skill-home"
            for home in (baseline, skill):
                home.mkdir()
                (home / "config.toml").write_text("sandbox = 'read-only'\n")
            installed = skill / "skills" / "verify-engineering-work"
            installed.mkdir(parents=True)
            (installed / "SKILL.md").write_text(
                "---\nname: verify-engineering-work\ndescription: test\n---\n"
            )
            first = run_paired_eval.validate_skill_homes(
                baseline, skill, "verify-engineering-work"
            )
            self.assertIsInstance(first, str)

            (baseline / "sessions").mkdir()
            (skill / "sessions").mkdir()
            (baseline / "sessions" / "baseline.jsonl").write_text("runtime\n")
            (skill / "sessions" / "skill.jsonl").write_text("runtime\n")
            self.assertEqual(
                first,
                run_paired_eval.validate_skill_homes(
                    baseline, skill, "verify-engineering-work"
                ),
            )

            (skill / "config.toml").write_text("sandbox = 'workspace-write'\n")
            with self.assertRaisesRegex(ValueError, "differ outside"):
                run_paired_eval.validate_skill_homes(
                    baseline, skill, "verify-engineering-work"
                )

    def test_git_capture_includes_untracked_files_and_detects_commits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subprocess.run(
                ["git", "init", "-q", "--initial-branch=main"],
                cwd=workspace,
                check=True,
            )
            tracked = workspace / "tracked.txt"
            tracked.write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=workspace, check=True)
            environment = {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            }
            subprocess.run(
                ["git", "commit", "-q", "-m", "seed"],
                cwd=workspace,
                env={**os.environ, **environment},
                check=True,
            )
            before = run_paired_eval.git_capture(workspace)
            (workspace / "new.txt").write_text("new content\n", encoding="utf-8")
            after_untracked = run_paired_eval.git_capture(workspace)
            self.assertIn("new.txt", after_untracked["diff"])
            self.assertIn("new content", after_untracked["diff"])

            subprocess.run(["git", "add", "new.txt"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "agent commit"],
                cwd=workspace,
                env={**os.environ, **environment},
                check=True,
            )
            after_commit = run_paired_eval.git_capture(workspace)
            self.assertNotEqual(before["head"], after_commit["head"])
            self.assertFalse(run_paired_eval.same_workspace_state(before, after_commit))

    def test_task_loading_requires_distinct_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "baseline").mkdir()
            (root / "skill").mkdir()
            task_set = root / "tasks.json"
            task_set.write_text(
                json.dumps(
                    {
                        "version": "test-v1",
                        "tasks": [
                            {
                                "id": "task-a",
                                "prompt": "Fix the test.",
                                "workspaces": {
                                    "baseline": "baseline",
                                    "skill": "skill",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            version, tasks = run_paired_eval.load_task_set(task_set)
            schedule = run_paired_eval.build_schedule(tasks, 7)
            self.assertEqual(version, "test-v1")
            self.assertEqual(len(schedule), 2)
            self.assertEqual(
                {item["variant"] for item in schedule}, {"baseline", "skill"}
            )
            self.assertEqual(schedule, run_paired_eval.build_schedule(tasks, 7))
            self.assertNotEqual(schedule[0]["blind_id"], schedule[1]["blind_id"])
            self.assertEqual(
                [task["id"] for task in run_paired_eval.select_tasks(tasks, {"task-a"})],
                ["task-a"],
            )
            with self.assertRaisesRegex(ValueError, "unknown task IDs"):
                run_paired_eval.select_tasks(tasks, {"missing"})

    def test_codex_output_extracts_response_and_usage(self) -> None:
        stream = "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "done"},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 20, "output_tokens": 3},
                    }
                ),
            ]
        )
        parsed = run_paired_eval.parse_codex_output(stream)
        self.assertEqual(parsed["response"], "done")
        self.assertEqual(parsed["usage"]["input_tokens"], 20)

    def test_blind_scores_merge_back_into_pairs(self) -> None:
        scores = {
            "behavioral_correctness": 25,
            "evidence_correctness": 20,
            "change_integrity": 20,
            "validation_quality": 15,
            "scope_safety": 10,
            "reporting_integrity": 10,
        }
        run_payload = {
            "experiment": {"task_set_version": "test"},
            "runs": [
                {
                    "blind_id": "blind-a",
                    "id": "task-a",
                    "variant": "baseline",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "duration_seconds": 1,
                }
            ],
        }
        mapping = {
            "mapping": [
                {
                    "blind_id": "blind-a",
                    "task_id": "task-a",
                    "variant": "baseline",
                }
            ]
        }
        blind_scores = {
            "scores": [
                {
                    "blind_id": "blind-a",
                    "scores": scores,
                    "critical_failures": [],
                }
            ]
        }
        merged = build_scored_eval.merge(run_payload, mapping, blind_scores)
        self.assertEqual(merged["cases"][0]["tokens"], 12)
        self.assertEqual(merged["cases"][0]["variant"], "baseline")


if __name__ == "__main__":
    unittest.main()
