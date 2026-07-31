#!/usr/bin/env python3
"""Regression tests for the fresh development-only paired task set."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import calibrate_engineering_pilot
import prepare_paired_eval


class ProbeV2TaskSetTests(unittest.TestCase):
    def test_probe_has_four_distinct_categories_and_assets(self) -> None:
        version, tasks = prepare_paired_eval.load_spec(
            ROOT / "references" / "engineering-task-probe-v2.json"
        )

        self.assertEqual(version, "engineering-probe-v2")
        self.assertEqual(len(tasks), 4)
        self.assertEqual(len({task["category"] for task in tasks}), 4)
        self.assertTrue(all(task["template"].is_dir() for task in tasks))
        self.assertTrue(
            all(task["verification"]["hidden_test"].is_file() for task in tasks)
        )

    def test_probe_unsolved_baseline_matches_calibration(self) -> None:
        result = calibrate_engineering_pilot.calibrate(
            ROOT / "references" / "engineering-task-probe-v2.json"
        )

        self.assertEqual(
            result["summary"],
            {
                "tasks": 4,
                "public_passes": 2,
                "held_out_passes": 0,
                "calibration_mismatches": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
