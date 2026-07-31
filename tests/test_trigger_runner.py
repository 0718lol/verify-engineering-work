#!/usr/bin/env python3
"""Regression tests for the resumable trigger evaluation runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_trigger_eval as runner
import reclassify_trigger_eval as reclassifier


class TriggerDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compatibility = runner.load_compatibility()
        cls.cli_version = next(iter(compatibility["codex_cli_versions"]))
        entry = compatibility["codex_cli_versions"][cls.cli_version]
        cls.triggered = (ROOT / entry["triggered_fixture"]).read_text(encoding="utf-8")
        cls.not_triggered = (ROOT / entry["not_triggered_fixture"]).read_text(
            encoding="utf-8"
        )

    def test_fixture_detects_skill_body_read(self) -> None:
        result = runner.parse_events(self.triggered, "verify-engineering-work", True)
        self.assertIs(result["triggered"], True)
        self.assertEqual(result["detection_method"], "skill_body_read")

    def test_installed_skill_directory_is_not_required_in_path(self) -> None:
        event = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        "sed -n '1,200p' "
                        "/tmp/checkout/verify-engineering-work/SKILL.md"
                    ),
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        )
        result = runner.parse_events(event, "verify-engineering-work", True)
        self.assertIs(result["triggered"], True)
        self.assertEqual(result["detection_method"], "skill_body_read")

    def test_resolved_snapshot_path_can_be_supplied(self) -> None:
        snapshot = Path("/tmp/frozen-project-v1/SKILL.md")
        event = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": f"sed -n '1,200p' {snapshot}",
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        )
        result = runner.parse_events(
            event,
            "verify-engineering-work",
            True,
            skill_body_paths=(snapshot,),
        )
        self.assertIs(result["triggered"], True)
        self.assertEqual(result["detection_method"], "skill_body_read")

    def test_path_mention_without_successful_read_is_not_a_trigger(self) -> None:
        for command, exit_code in (
            ("echo /tmp/verify-engineering-work/SKILL.md", 0),
            ("sed -n 1,20p /tmp/verify-engineering-work/SKILL.md", 2),
        ):
            event = json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": command,
                        "status": "completed" if exit_code == 0 else "failed",
                        "exit_code": exit_code,
                    },
                }
            )
            result = runner.parse_events(event, "verify-engineering-work", True)
            self.assertIsNone(result["triggered"])

    def test_verified_cli_detects_negative(self) -> None:
        result = runner.parse_events(self.not_triggered, "verify-engineering-work", True)
        self.assertIs(result["triggered"], False)
        self.assertEqual(result["detection_method"], "verified_absence")

    def test_unknown_cli_stays_indeterminate(self) -> None:
        result = runner.parse_events(self.not_triggered, "verify-engineering-work", False)
        self.assertIsNone(result["triggered"])
        self.assertEqual(result["detection_status"], "indeterminate")

    def test_structured_skill_event_has_priority(self) -> None:
        event = json.dumps(
            {"type": "skill.invoked", "skill_name": "verify-engineering-work"}
        )
        result = runner.parse_events(event, "verify-engineering-work", False)
        self.assertIs(result["triggered"], True)
        self.assertEqual(result["detection_method"], "structured_skill_event")

    def test_unverified_negative_requires_explicit_override(self) -> None:
        result = runner.parse_events(
            self.not_triggered,
            "verify-engineering-work",
            False,
            allow_unverified_negative=True,
        )
        self.assertIs(result["triggered"], False)
        self.assertEqual(
            result["detection_method"], "user_accepted_unverified_absence"
        )

    def test_log_redaction_hides_common_credentials(self) -> None:
        source = (
            "Authorization: Bearer secret-value-123456\n"
            'api_key="1234567890abcdef"\n'
            "github_pat_123456789012345678901234567890\n"
            "https://example.test/cb?token=query-secret-123456\n"
            "Cookie: session=cookie-secret-123456\n"
            "Authorization: Basic ZmFrZTpmYWtl"
        )
        redacted, count = runner.redact_log(source)
        self.assertEqual(count, 6)
        self.assertNotIn("secret-value", redacted)
        self.assertNotIn("1234567890abcdef", redacted)
        self.assertNotIn("github_pat_", redacted)
        self.assertNotIn("query-secret", redacted)
        self.assertNotIn("cookie-secret", redacted)
        self.assertNotIn("ZmFrZTpmYWtl", redacted)


class RunnerStateTests(unittest.TestCase):
    def namespace(self, **overrides: object) -> argparse.Namespace:
        values = {
            "input_usd_per_million": None,
            "output_usd_per_million": None,
            "max_input_tokens": None,
            "max_output_tokens": None,
            "max_total_tokens": None,
            "max_cost_usd": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_budget_stops_at_configured_total(self) -> None:
        observations = [{"input_tokens": 80, "output_tokens": 20}]
        totals = runner.usage_totals(observations, None, None)
        reason = runner.budget_exhaustion_reason(
            totals, self.namespace(max_total_tokens=100)
        )
        self.assertIn("total token budget reached", reason)

    def test_cost_is_only_estimated_with_explicit_rates(self) -> None:
        observations = [{"input_tokens": 1_000_000, "output_tokens": 500_000}]
        without_rates = runner.usage_totals(observations, None, None)
        with_rates = runner.usage_totals(observations, 2.0, 8.0)
        self.assertIsNone(without_rates["estimated_cost_usd"])
        self.assertEqual(with_rates["estimated_cost_usd"], 6.0)

    def test_resume_rejects_different_case_selection(self) -> None:
        payload = {
            "experiment": {"model": "test"},
            "run_state": {"selected_case_ids": ["case-a"]},
            "observations": [],
        }
        with self.assertRaisesRegex(ValueError, "case selection differs"):
            runner.verify_resume_payload(
                payload,
                {"model": "test"},
                ["case-a", "case-b"],
            )

    def test_atomic_write_replaces_complete_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            runner.atomic_write_json(output, {"value": 1})
            runner.atomic_write_json(output, {"value": 2})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"value": 2})
            self.assertFalse(output.with_name("result.json.tmp").exists())

    def test_run_state_records_limits_for_audit(self) -> None:
        state = runner.run_state(
            "running",
            ["case-a"],
            [],
            self.namespace(max_total_tokens=500),
        )
        self.assertEqual(state["limits"]["max_total_tokens"], 500)

    def test_main_writes_checkpoint_and_resume_skips_completed_case(self) -> None:
        compatibility = runner.load_compatibility()
        cli_version = next(iter(compatibility["codex_cli_versions"]))
        fixture_path = ROOT / compatibility["codex_cli_versions"][cli_version][
            "triggered_fixture"
        ]
        event_stream = fixture_path.read_text(encoding="utf-8")
        log_metadata = {
            "redactions": 0,
            "raw": False,
            "files": [],
            "usage": {"input_tokens": 12000, "output_tokens": 500},
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark.json"
            output = root / "observations.json"
            codex_home = root / "codex-home"
            workspace = root / "workspace"
            codex_home.mkdir()
            workspace.mkdir()
            benchmark.write_text(
                json.dumps({"cases": [{"id": "case-a", "prompt": "test prompt"}]}),
                encoding="utf-8",
            )
            arguments = [
                "run_trigger_eval.py",
                str(benchmark),
                str(output),
                "--codex-home",
                str(codex_home),
                "--workspace",
                str(workspace),
            ]

            with (
                mock.patch.object(runner, "codex_version", return_value=cli_version),
                mock.patch.object(
                    runner,
                    "run_codex_case",
                    return_value=(event_stream, 1, log_metadata),
                ) as execute,
                mock.patch.object(sys, "argv", arguments),
            ):
                self.assertEqual(runner.main(), 0)
                self.assertEqual(execute.call_count, 1)
                command = execute.call_args.args[0]
                self.assertEqual(command[0], "codex")
                self.assertEqual(command[command.index("-m") + 1], "gpt-5.6-sol")

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_state"]["status"], "completed")
            self.assertEqual(payload["run_state"]["completed_cases"], 1)

            with (
                mock.patch.object(runner, "codex_version", return_value=cli_version),
                mock.patch.object(runner, "run_codex_case") as execute,
                mock.patch.object(sys, "argv", arguments + ["--resume"]),
            ):
                self.assertEqual(runner.main(), 0)
                execute.assert_not_called()

    def test_retry_usage_includes_failed_attempts(self) -> None:
        first = subprocess.CompletedProcess(
            ["codex"],
            1,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }
            ),
            stderr="failed",
        )
        second = subprocess.CompletedProcess(
            ["codex"],
            0,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                }
            ),
            stderr="",
        )
        args = argparse.Namespace(
            retries=1,
            timeout=10,
            retry_delay_seconds=0,
            logs_dir=None,
            unsafe_raw_logs=False,
        )
        with mock.patch.object(runner.subprocess, "run", side_effect=[first, second]):
            _, attempts, metadata = runner.run_codex_case(
                ["codex"], {}, "case-a", args
            )
        self.assertEqual(attempts, 2)
        self.assertEqual(
            metadata["usage"], {"input_tokens": 15, "output_tokens": 3}
        )


class ReclassificationTests(unittest.TestCase):
    def test_redacted_retained_log_has_verifiable_stored_hash(self) -> None:
        fixture = (
            ROOT
            / "tests"
            / "fixtures"
            / "codex-cli-0.144.0-alpha.4"
            / "triggered.jsonl"
        ).read_text(encoding="utf-8")
        source = fixture + '\n{"token":"fake-secret-token-123456"}\n'
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            log_path = logs / "triggered.attempt-1.stdout.jsonl"
            metadata = runner.write_log(log_path, source, False)
            payload = {
                "experiment": {
                    "detection_schema_version": "2.3.0",
                    "negative_detection_verified": True,
                },
                "observations": [
                    {
                        "id": "triggered",
                        "triggered": True,
                        "detection_status": "triggered",
                        "detection_method": "skill_body_read",
                        "attempts": 1,
                        "log_redactions": metadata["redactions"],
                        "log_sha256": [
                            {
                                "attempt": 1,
                                "stdout_sha256": metadata["sha256"],
                                "stdout_stored_sha256": metadata["stored_sha256"],
                            }
                        ],
                    }
                ],
            }
            result, _ = reclassifier.reclassify(
                payload, logs, "verify-engineering-work"
            )
        self.assertGreater(metadata["redactions"], 0)
        self.assertIs(
            result["observations"][0]["log_integrity_verified"], True
        )

    def test_retained_log_can_correct_a_false_negative(self) -> None:
        fixture = (
            ROOT
            / "tests"
            / "fixtures"
            / "codex-cli-0.144.0-alpha.4"
            / "triggered.jsonl"
        ).read_text(encoding="utf-8")
        digest = hashlib.sha256(fixture.encode("utf-8")).hexdigest()
        payload = {
            "experiment": {
                "detection_schema_version": "2.1.0",
                "negative_detection_verified": True,
            },
            "observations": [
                {
                    "id": "triggered",
                    "triggered": False,
                    "detection_status": "not_triggered",
                    "detection_method": "verified_absence",
                    "attempts": 1,
                    "log_redactions": 0,
                    "log_sha256": [
                        {"attempt": 1, "stdout_sha256": digest}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            (logs / "triggered.attempt-1.stdout.jsonl").write_text(
                fixture,
                encoding="utf-8",
            )
            result, changed = reclassifier.reclassify(
                payload,
                logs,
                "verify-engineering-work",
            )
        observation = result["observations"][0]
        self.assertEqual(changed, 1)
        self.assertIs(observation["triggered"], True)
        self.assertEqual(observation["detection_method"], "skill_body_read")
        self.assertIs(observation["log_integrity_verified"], True)
        self.assertEqual(
            result["experiment"]["detection_schema_version"],
            runner.DETECTION_SCHEMA_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
