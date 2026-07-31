#!/usr/bin/env python3
"""Regression tests for paired fixture preparation and machine verification."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_paired_eval
import calibrate_engineering_pilot
import build_scored_eval
import codex_docker_wrapper
import create_controlled_codex_homes
import run_paired_eval
import score_eval
import verify_paired_eval


class PilotTaskSetTests(unittest.TestCase):
    def test_pilot_has_eight_distinct_categories_and_assets(self) -> None:
        version, tasks = prepare_paired_eval.load_spec(
            ROOT / "references" / "engineering-task-pilot-v1.json"
        )
        self.assertEqual(version, "engineering-pilot-v1")
        self.assertEqual(len(tasks), 8)
        self.assertEqual(len({task["category"] for task in tasks}), 8)
        self.assertTrue(all(task["template"].is_dir() for task in tasks))
        self.assertTrue(
            all(task["verification"]["hidden_test"].is_file() for task in tasks)
        )

    def test_pilot_unsolved_baseline_matches_calibration(self) -> None:
        result = calibrate_engineering_pilot.calibrate(
            ROOT / "references" / "engineering-task-pilot-v1.json"
        )
        self.assertEqual(
            result["summary"],
            {
                "tasks": 8,
                "public_passes": 4,
                "held_out_passes": 0,
                "calibration_mismatches": 0,
            },
        )


class WorkflowBehaviorCaseTests(unittest.TestCase):
    def test_behavior_cases_cover_decision_boundaries(self) -> None:
        payload = json.loads(
            (
                ROOT / "references" / "workflow-behavior-cases-v1.json"
            ).read_text(encoding="utf-8")
        )
        cases = payload["cases"]
        self.assertEqual(payload["version"], "1.0.0")
        self.assertEqual(len(cases), 12)
        self.assertEqual(len({case["id"] for case in cases}), 12)
        self.assertEqual(
            {case["expected_execution"] for case in cases},
            {"local/reversible", "external/state-changing"},
        )
        self.assertGreaterEqual(
            {case["category"] for case in cases},
            {"authorization", "validation", "failure-loop", "reporting"},
        )


class ControlledEnvironmentTests(unittest.TestCase):
    def test_controlled_homes_only_differ_by_target_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "controlled"
            manifest = create_controlled_codex_homes.create_environment(
                output,
                ROOT,
                "verify-engineering-work",
                create_controlled_codex_homes.DEFAULT_IMAGE,
            )
            baseline = output / manifest["baseline_home"]
            skill = output / manifest["skill_home"]
            self.assertFalse(
                (baseline / "skills" / "verify-engineering-work").exists()
            )
            self.assertTrue(
                (skill / "skills" / "verify-engineering-work" / "SKILL.md").is_file()
            )
            self.assertFalse(
                (
                    skill
                    / "skills"
                    / "verify-engineering-work"
                    / "references"
                    / "engineering-task-pilot-v1.json"
                ).exists()
            )
            self.assertFalse((baseline / "auth.json").exists())
            run_paired_eval.validate_skill_homes(
                baseline, skill, "verify-engineering-work"
            )

    def test_docker_wrapper_mounts_only_runtime_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            workspace = root / "workspace"
            auth = root / "auth.json"
            home.mkdir()
            workspace.mkdir()
            auth.write_text("{}\n", encoding="utf-8")
            command = codex_docker_wrapper.docker_command(
                ["exec", "-C", str(workspace), "test prompt"],
                {
                    "CODEX_HOME": str(home),
                    "CODEX_AUTH_FILE": str(auth),
                    "CODEX_DOCKER_IMAGE": "test-image",
                },
            )
            rendered = "\n".join(command)
            self.assertIn(f"source={home.resolve()},target={home.resolve()}", rendered)
            self.assertIn(
                f"source={workspace.resolve()},target={workspace.resolve()}", rendered
            )
            self.assertIn(f"source={auth.resolve()}", rendered)
            self.assertNotIn(str(ROOT / "evaluation"), rendered)
            self.assertEqual(command[-5], "test-image")

            local = codex_docker_wrapper.docker_command(
                ["exec", "-C", str(workspace), "test prompt"],
                {
                    "CODEX_HOME": str(home),
                    "CODEX_LOCAL_PROVIDER": "ollama",
                    "CODEX_DOCKER_IMAGE": "test-image",
                },
            )
            local_rendered = "\n".join(local)
            self.assertIn(
                "OLLAMA_PROXY_TARGET=host.docker.internal:11434", local_rendered
            )
            self.assertNotIn(str(auth.resolve()), local_rendered)
            self.assertEqual(local[local.index("exec") + 1], "--oss")
            self.assertEqual(
                local[local.index("--local-provider") + 1], "ollama"
            )

class PreparationAndVerificationTests(unittest.TestCase):
    def create_spec(self, root: Path) -> Path:
        template = root / "template"
        tests = template / "tests"
        tests.mkdir(parents=True)
        (template / "calculator.py").write_text(
            "def add(left, right):\n    return left + right\n",
            encoding="utf-8",
        )
        (tests / "test_calculator.py").write_text(
            "import unittest\n"
            "from calculator import add\n\n"
            "class Tests(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        hidden = root / "hidden.py"
        hidden.write_text(
            "import unittest\n"
            "from calculator import add\n\n"
            "class Hidden(unittest.TestCase):\n"
            "    def test_negative(self):\n"
            "        self.assertEqual(add(-2, 1), -1)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        spec = root / "spec.json"
        spec.write_text(
            json.dumps(
                {
                    "version": "fixture-test-v1",
                    "tasks": [
                        {
                            "id": "task-a",
                            "category": "test",
                            "prompt": "Verify the calculator.",
                            "template": "template",
                            "verification": {
                                "public_command": [
                                    sys.executable,
                                    "-m",
                                    "unittest",
                                    "discover",
                                    "-s",
                                    "tests",
                                    "-q",
                                ],
                                "hidden_test": "hidden.py",
                            },
                            "calibration": {
                                "public_passes": True,
                                "held_out_passes": True,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return spec

    def test_prepare_clones_identical_clean_commits_and_copies_hidden_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.create_spec(root)
            output = root / "prepared"
            manifest = prepare_paired_eval.prepare(spec, output)
            task_set = json.loads(
                (output / "paired-tasks.json").read_text(encoding="utf-8")
            )
            task = task_set["tasks"][0]
            baseline = output / task["workspaces"]["baseline"]
            skill = output / task["workspaces"]["skill"]
            self.assertEqual(
                run_paired_eval.git_capture(baseline)["head"],
                run_paired_eval.git_capture(skill)["head"],
            )
            self.assertEqual(run_paired_eval.git_capture(baseline)["status"], "")
            self.assertNotEqual(
                os.stat(baseline / "calculator.py").st_ino,
                os.stat(skill / "calculator.py").st_ino,
            )
            self.assertTrue(
                (output / task["verification"]["hidden_test"]).is_file()
            )
            self.assertEqual(len(manifest["tasks"]), 1)

    def test_machine_verifier_runs_public_and_hidden_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.create_spec(root)
            output = root / "prepared"
            prepare_paired_eval.prepare(spec, output)
            task_set_path = output / "paired-tasks.json"
            task_set = json.loads(task_set_path.read_text(encoding="utf-8"))
            task = task_set["tasks"][0]
            workspace = (output / task["workspaces"]["baseline"]).resolve()
            capture = run_paired_eval.git_capture(workspace)
            run_output = root / "runs.json"
            run_output.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "id": "task-a",
                                "variant": "baseline",
                                "blind_id": "blind-a",
                                "workspace_head_after": capture["head"],
                                "workspace_status": capture["status"],
                                "workspace_status_sha256": capture["status_sha256"],
                                "diff_sha256": capture["diff_sha256"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = verify_paired_eval.verify(
                task_set_path,
                run_output,
                root / "artifacts",
                timeout=10,
            )
            self.assertEqual(result["summary"], {"cases": 1, "passed": 1, "failed": 0})
            self.assertTrue(result["cases"][0]["machine_verification_passed"])


class MachineGateTests(unittest.TestCase):
    def test_publication_threshold_cannot_be_lowered_for_a_pilot(self) -> None:
        scores = dict(score_eval.CATEGORIES)
        pair = [
            {
                "id": "a",
                "variant": variant,
                "scores": scores,
                "critical_failures": [],
                "machine_verification_passed": True,
                "public_verification_passed": True,
                "held_out_verification_passed": True,
                "token_budget_exceeded": False,
            }
            for variant in ("baseline", "skill")
        ]
        experiment = {
            field: 100 if field == "token_budget" else "test-value"
            for field in score_eval.REQUIRED_EXPERIMENT_FIELDS
        }
        for field in score_eval.HASH_FIELDS:
            experiment[field] = "a" * 64
        experiment["skill_commit"] = "b" * 40
        experiment["held_out_isolation"] = "evaluator-external"
        result = score_eval.summarize(
            {"experiment": experiment, "cases": pair}, minimum_pairs=1
        )
        self.assertTrue(result["quality_gate"]["sample_size_sufficient"])
        self.assertEqual(result["quality_gate"]["publication_minimum_pairs"], 40)
        self.assertFalse(result["quality_gate"]["publication_ready"])
        invalid_experiment = dict(experiment)
        invalid_experiment["task_set_sha256"] = "not-a-hash"
        invalid_result = score_eval.summarize(
            {"experiment": invalid_experiment, "cases": pair}, minimum_pairs=1
        )
        self.assertIn(
            "task_set_sha256",
            invalid_result["quality_gate"]["invalid_experiment_metadata"],
        )

    def test_publication_gate_requires_machine_results_and_budget_compliance(self) -> None:
        scores = dict(score_eval.CATEGORIES)
        pair = [
            {
                "id": "a",
                "variant": "baseline",
                "scores": scores,
                "machine_verification_passed": None,
                "token_budget_exceeded": False,
            },
            {
                "id": "a",
                "variant": "skill",
                "scores": scores,
                "machine_verification_passed": True,
                "token_budget_exceeded": True,
            },
        ]
        result = score_eval.summarize({"cases": pair}, minimum_pairs=1)
        self.assertEqual(result["quality_gate"]["missing_machine_results"], 1)
        self.assertEqual(result["quality_gate"]["missing_machine_detail"], 2)
        self.assertEqual(result["quality_gate"]["token_budget_violations"], 1)
        self.assertEqual(result["quality_gate"]["missing_token_budget_status"], 0)
        self.assertFalse(result["quality_gate"]["publication_ready"])

    def test_failed_machine_result_becomes_a_critical_failure(self) -> None:
        scores = dict(score_eval.CATEGORIES)
        run_payload = {
            "experiment": {"task_set_sha256": "task-sha"},
            "runs": [
                {
                    "id": "task-a",
                    "variant": "baseline",
                    "blind_id": "blind-a",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "token_budget_exceeded": False,
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
        machine = {
            "experiment": {"task_set_sha256": "task-sha"},
            "cases": [
                {
                    "id": "task-a",
                    "variant": "baseline",
                    "blind_id": "blind-a",
                    "machine_verification_passed": False,
                    "public": {"passed": True},
                    "hidden": {"passed": False},
                }
            ],
        }
        merged = build_scored_eval.merge(
            run_payload,
            mapping,
            blind_scores,
            machine,
        )
        self.assertEqual(
            merged["cases"][0]["critical_failures"],
            ["machine_verification_failed"],
        )
        self.assertTrue(merged["cases"][0]["public_verification_passed"])
        self.assertFalse(merged["cases"][0]["held_out_verification_passed"])


if __name__ == "__main__":
    unittest.main()
