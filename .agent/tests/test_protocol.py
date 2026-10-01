#!/usr/bin/env python3
"""Exhaustive tests for the frozen evidence/gate algebra."""

from __future__ import annotations
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from protocol_check import expected_gate
from gate_eval import evaluate

class ProtocolAlgebraTests(unittest.TestCase):
    def test_direct_truth_table(self):
        self.assertEqual(expected_gate("TRUE", "DIRECT"), "AUTHORIZED")
        self.assertEqual(expected_gate("FALSE", "DIRECT"), "REJECTED")
        self.assertEqual(expected_gate("UNDETERMINED", "DIRECT"), "BLOCKED")

    def test_approved_derived_truth_table(self):
        self.assertEqual(expected_gate("TRUE", "DERIVED", True), "AUTHORIZED")
        self.assertEqual(expected_gate("FALSE", "DERIVED", True), "REJECTED")
        self.assertEqual(expected_gate("UNDETERMINED", "DERIVED", True), "BLOCKED")

    def test_unapproved_derived_is_blocked(self):
        self.assertEqual(expected_gate("TRUE", "DERIVED", False), "BLOCKED")

    def test_inferred_always_blocks(self):
        for result in ("TRUE", "FALSE", "UNDETERMINED"):
            self.assertEqual(expected_gate(result, "INFERRED"), "BLOCKED")

    def test_unavailable_always_blocks(self):
        for result in ("TRUE", "FALSE", "UNDETERMINED"):
            self.assertEqual(expected_gate(result, "UNAVAILABLE"), "BLOCKED")

    def test_required_result_is_separate_from_truth(self):
        props = [{
            "proposition_id": "execution-result",
            "result": "FALSE",
            "derivation": "DIRECT",
            "mandatory": True,
            "required_result": "TRUE",
        }]
        out = evaluate(props)
        self.assertFalse(out["release_authorized"])
        self.assertEqual(out["gates"][0]["gate"], "REJECTED")

    def test_false_can_authorize_gate_requiring_false(self):
        props = [{
            "proposition_id": "must-not-exist",
            "result": "FALSE",
            "derivation": "DIRECT",
            "mandatory": True,
            "required_result": "FALSE",
        }]
        out = evaluate(props)
        self.assertTrue(out["release_authorized"])
        self.assertEqual(out["gates"][0]["gate"], "AUTHORIZED")

if __name__ == "__main__":
    unittest.main()
