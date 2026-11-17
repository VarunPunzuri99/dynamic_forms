"""Shared rule parity vectors — backend runner (Phase 3D)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.filler import normalize_answers, planned_values
from backend.rules import RuleError, condition_met, evaluate_effects, validate_rules

VECTORS_PATH = Path(__file__).with_name("rule_parity_vectors.json")


def load_vectors() -> dict:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


class SharedRuleParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = load_vectors()
        cls.questions = cls.payload["questions"]
        cls.by_id = {question["id"]: question for question in cls.questions}

    def test_shared_vectors(self) -> None:
        for case in self.payload["cases"]:
            with self.subTest(case_id=case["id"]):
                self._run_case(case)

    def _run_case(self, case: dict) -> None:
        kind = case["kind"]
        if kind == "condition":
            question = self.by_id[case["question_id"]]
            for sample in case["samples"]:
                actual = condition_met(
                    question,
                    sample.get("value"),
                    case["condition"],
                    sample.get("expected"),
                )
                self.assertEqual(actual, sample["expect"], msg=sample)
            return

        if kind == "effects":
            questions = case.get("questions_override") or self.questions
            effects = evaluate_effects(questions, case.get("rules") or [], case.get("answers") or {})
            for question_id, expected in case["expect_effects"].items():
                self.assertEqual(effects[question_id]["disabled"], expected["disabled"], msg=question_id)
                self.assertEqual(effects[question_id]["required"], expected["required"], msg=question_id)
            return

        if kind == "validate":
            if case["expect_valid"]:
                cleaned = validate_rules(case["rules"], self.questions)
                self.assertEqual(len(cleaned), len(case["rules"]))
            else:
                with self.assertRaises(RuleError):
                    validate_rules(case["rules"], self.questions)
            return

        if kind == "normalize":
            definition = case["definition"]
            normalized = normalize_answers(definition, case["answers"])
            self.assertEqual(normalized, case["expect_normalized"])
            planned = planned_values(definition, normalized)
            for key, value in (case.get("expect_planned") or {}).items():
                self.assertEqual(planned.get(key), value, msg=key)
            for key in case.get("expect_planned_absent") or []:
                self.assertNotIn(key, planned)
            return

        self.fail(f"Unknown case kind: {kind}")


if __name__ == "__main__":
    unittest.main()
