"""Unit tests for optional answer rules and label carry-over helpers."""

from __future__ import annotations

import unittest

from backend.filler import normalize_answers
from backend.rules import (
    RuleError,
    condition_met,
    disable_dependency_cycle_exists,
    evaluate_effects,
    labels_look_conflicting,
    validate_rules,
)

CHECKBOX = {"id": "exempt", "label": "Exempt", "type": "checkbox", "required": False}


QUESTIONS = [
    {"id": "q1", "label": "One", "type": "text", "required": True},
    {"id": "q2", "label": "Two", "type": "number", "required": False},
    {"id": "q3", "label": "Three", "type": "number", "required": True},
    {"id": "q4", "label": "Four", "type": "text", "required": False},
    {
        "id": "exempt",
        "label": "Exempt",
        "type": "checkbox",
        "required": False,
        "checked_value": "1",
        "pdf_field": "c1",
    },
]


class RuleValidationTests(unittest.TestCase):
    def test_rejects_unknown_question(self) -> None:
        with self.assertRaises(RuleError):
            validate_rules(
                [
                    {
                        "id": "r1",
                        "when_question": "q2",
                        "when_condition": "answered",
                        "then_question": "missing",
                        "then_effect": "disable",
                    }
                ],
                QUESTIONS,
            )

    def test_accepts_disable_rule(self) -> None:
        rules = validate_rules(
            [
                {
                    "id": "r1",
                    "when_question": "q2",
                    "when_condition": "answered",
                    "then_question": "q3",
                    "then_effect": "disable",
                }
            ],
            QUESTIONS,
        )
        self.assertEqual(rules[0]["then_effect"], "disable")


class RuleEvaluationTests(unittest.TestCase):
    def test_answering_q2_disables_q3_but_not_q4(self) -> None:
        rules = [
            {
                "id": "r1",
                "when_question": "q2",
                "when_condition": "answered",
                "then_question": "q3",
                "then_effect": "disable",
            }
        ]
        effects = evaluate_effects(
            QUESTIONS,
            rules,
            {"q1": "A", "q2": "10", "q3": "5", "q4": "B", "exempt": False},
        )
        self.assertFalse(effects["q2"]["disabled"])
        self.assertTrue(effects["q3"]["disabled"])
        self.assertFalse(effects["q4"]["disabled"])
        self.assertFalse(effects["q3"]["required"])

    def test_normalize_skips_required_on_disabled_question(self) -> None:
        definition = {
            "form_id": "demo",
            "edition": "2026",
            "source_pdf": "docs/fw4_2026.pdf",
            "questions": [
                {
                    "id": "q2",
                    "label": "Two",
                    "type": "number",
                    "required": False,
                    "pdf_field": "f2",
                },
                {
                    "id": "q3",
                    "label": "Three",
                    "type": "number",
                    "required": True,
                    "pdf_field": "f3",
                },
            ],
            "rules": [
                {
                    "id": "r1",
                    "when_question": "q2",
                    "when_condition": "answered",
                    "then_question": "q3",
                    "then_effect": "disable",
                }
            ],
        }
        normalized = normalize_answers(definition, {"q2": "25", "q3": "99"})
        self.assertEqual(normalized["q2"], "25")
        self.assertIsNone(normalized["q3"])

    def test_exempt_checked_can_disable_later_amounts(self) -> None:
        rules = validate_rules(
            [
                {
                    "id": "r1",
                    "when_question": "exempt",
                    "when_condition": "checked",
                    "then_question": "q3",
                    "then_effect": "disable",
                }
            ],
            QUESTIONS,
        )
        effects = evaluate_effects(
            QUESTIONS,
            rules,
            {"q1": "A", "q2": "", "q3": "5", "q4": "", "exempt": True},
        )
        self.assertTrue(effects["q3"]["disabled"])


class DisableDependencyTests(unittest.TestCase):
    def test_linear_disable_chain_is_allowed(self) -> None:
        # q1 answered disables q2; disabled q2 is blank, which disables q3.
        rules = validate_rules(
            [
                {
                    "id": "r1",
                    "when_question": "q1",
                    "when_condition": "answered",
                    "then_question": "q2",
                    "then_effect": "disable",
                },
                {
                    "id": "r2",
                    "when_question": "q2",
                    "when_condition": "blank",
                    "then_question": "q3",
                    "then_effect": "disable",
                },
            ],
            QUESTIONS,
        )
        self.assertFalse(disable_dependency_cycle_exists(rules))
        effects = evaluate_effects(
            QUESTIONS,
            rules,
            {"q1": "A", "q2": "10", "q3": "5", "q4": "", "exempt": False},
        )
        self.assertTrue(effects["q2"]["disabled"])
        self.assertTrue(effects["q3"]["disabled"])

    def test_branching_disable_from_one_source_is_allowed(self) -> None:
        rules = validate_rules(
            [
                {
                    "id": "r1",
                    "when_question": "q1",
                    "when_condition": "answered",
                    "then_question": "q2",
                    "then_effect": "disable",
                },
                {
                    "id": "r2",
                    "when_question": "q1",
                    "when_condition": "answered",
                    "then_question": "q3",
                    "then_effect": "disable",
                },
            ],
            QUESTIONS,
        )
        self.assertEqual(len(rules), 2)

    def test_self_target_still_rejected(self) -> None:
        with self.assertRaises(RuleError):
            validate_rules(
                [
                    {
                        "id": "r1",
                        "when_question": "q1",
                        "when_condition": "answered",
                        "then_question": "q1",
                        "then_effect": "disable",
                    }
                ],
                QUESTIONS,
            )

    def test_disable_cycle_is_rejected(self) -> None:
        with self.assertRaises(RuleError):
            validate_rules(
                [
                    {
                        "id": "r1",
                        "when_question": "q1",
                        "when_condition": "answered",
                        "then_question": "q2",
                        "then_effect": "disable",
                    },
                    {
                        "id": "r2",
                        "when_question": "q2",
                        "when_condition": "answered",
                        "then_question": "q1",
                        "then_effect": "disable",
                    },
                ],
                QUESTIONS,
            )

    def test_required_optional_cycle_without_disable_is_allowed(self) -> None:
        rules = validate_rules(
            [
                {
                    "id": "r1",
                    "when_question": "q1",
                    "when_condition": "answered",
                    "then_question": "q2",
                    "then_effect": "required",
                },
                {
                    "id": "r2",
                    "when_question": "q2",
                    "when_condition": "answered",
                    "then_question": "q1",
                    "then_effect": "optional",
                },
            ],
            QUESTIONS,
        )
        self.assertFalse(disable_dependency_cycle_exists(rules))

    def test_multiple_rules_same_target_without_disable_cycle(self) -> None:
        rules = validate_rules(
            [
                {
                    "id": "r1",
                    "when_question": "q1",
                    "when_condition": "answered",
                    "then_question": "q3",
                    "then_effect": "disable",
                },
                {
                    "id": "r2",
                    "when_question": "q2",
                    "when_condition": "answered",
                    "then_question": "q3",
                    "then_effect": "required",
                },
            ],
            QUESTIONS,
        )
        effects = evaluate_effects(
            QUESTIONS,
            rules,
            {"q1": "A", "q2": "10", "q3": "5", "q4": "", "exempt": False},
        )
        self.assertTrue(effects["q3"]["disabled"])
        self.assertFalse(effects["q3"]["required"])


class CheckboxEqualsParityTests(unittest.TestCase):
    """Backend source of truth for checkbox equals — keep FE formRules in sync."""

    def test_checked_literals(self) -> None:
        for expected in (True, "true", "TRUE", "1", "yes", "YES"):
            with self.subTest(expected=expected):
                self.assertTrue(condition_met(CHECKBOX, True, "equals", expected))
                self.assertFalse(condition_met(CHECKBOX, False, "equals", expected))

    def test_unchecked_literals(self) -> None:
        for expected in ("false", "0", "no", "", None):
            with self.subTest(expected=expected):
                self.assertTrue(condition_met(CHECKBOX, False, "equals", expected))
                self.assertFalse(condition_met(CHECKBOX, True, "equals", expected))

    def test_equals_one_disables_like_checked(self) -> None:
        rules = [
            {
                "id": "r1",
                "when_question": "exempt",
                "when_condition": "equals",
                "when_value": "1",
                "then_question": "q3",
                "then_effect": "disable",
            }
        ]
        effects = evaluate_effects(
            QUESTIONS,
            rules,
            {"q1": "A", "q2": "", "q3": "5", "q4": "", "exempt": True},
        )
        self.assertTrue(effects["q3"]["disabled"])


class LabelCarryTests(unittest.TestCase):
    def test_amount_change_is_conflict(self) -> None:
        self.assertTrue(
            labels_look_conflicting(
                "Multiply qualifying children by $2,000.",
                "Multiply qualifying children by Dependent $2,200",
            )
        )

    def test_identical_wording_is_not_conflict(self) -> None:
        self.assertFalse(labels_look_conflicting("First name", "First name"))

    def test_missing_nearby_text_is_not_conflict(self) -> None:
        self.assertFalse(labels_look_conflicting("First name", "No nearby text found"))


if __name__ == "__main__":
    unittest.main()
