"""Optional answer rules for a form edition.

Rules are stored on the edition. They never come from the PDF. The same
checks run when an employee types and when the server validates a submit.
"""

from __future__ import annotations

import re
from typing import Any

WHEN_CONDITIONS = frozenset({"answered", "blank", "checked", "equals"})
THEN_EFFECTS = frozenset({"disable", "optional", "required"})


class RuleError(ValueError):
    pass


def empty_answer(question_type: str):
    return False if question_type == "checkbox" else None


def has_answer(question: dict, value: Any) -> bool:
    question_type = question["type"]
    if question_type == "checkbox":
        return value is True
    if question_type == "choice":
        return isinstance(value, str) and bool(value)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def condition_met(question: dict, value: Any, condition: str, expected: Any = None) -> bool:
    if condition == "answered":
        return has_answer(question, value)
    if condition == "blank":
        return not has_answer(question, value)
    if condition == "checked":
        if question["type"] != "checkbox":
            return False
        return value is True
    if condition == "equals":
        if question["type"] == "checkbox":
            want = expected is True or str(expected).lower() in {"true", "1", "yes"}
            return value is True if want else value is False
        if value is None:
            return False
        return str(value).strip() == str(expected if expected is not None else "").strip()
    return False


def validate_rules(rules: object, questions: list[dict]) -> list[dict]:
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise RuleError("Rules must be a list")
    by_id = {question["id"]: question for question in questions}
    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(rules, start=1):
        if not isinstance(raw, dict):
            raise RuleError(f"Rule {index} is invalid")
        rule_id = str(raw.get("id") or f"rule-{index}").strip()
        if not rule_id or rule_id in seen_ids:
            raise RuleError(f"Rule {index} needs a unique id")
        seen_ids.add(rule_id)
        when_question = str(raw.get("when_question") or "").strip()
        then_question = str(raw.get("then_question") or "").strip()
        when_condition = str(raw.get("when_condition") or "").strip()
        then_effect = str(raw.get("then_effect") or "").strip()
        when_value = raw.get("when_value")
        if when_question not in by_id:
            raise RuleError(f"Rule {index} refers to a question that is not on the form")
        if then_question not in by_id:
            raise RuleError(f"Rule {index} refers to a question that is not on the form")
        if when_question == then_question:
            raise RuleError(f"Rule {index} cannot apply a question to itself")
        if when_condition not in WHEN_CONDITIONS:
            raise RuleError(f"Rule {index} has an unknown condition")
        if then_effect not in THEN_EFFECTS:
            raise RuleError(f"Rule {index} has an unknown result")
        when = by_id[when_question]
        if when_condition == "checked" and when["type"] != "checkbox":
            raise RuleError(f"Rule {index} can only use 'is checked' on a yes/no question")
        if when_condition == "equals":
            if when_value is None or (isinstance(when_value, str) and not when_value.strip()):
                raise RuleError(f"Rule {index} needs a value for 'equals'")
            if when["type"] == "choice":
                allowed = {option["value"] for option in when.get("options") or []}
                if str(when_value) not in allowed:
                    raise RuleError(f"Rule {index} equals a value that is not one of the choices")
        cleaned.append(
            {
                "id": rule_id,
                "when_question": when_question,
                "when_condition": when_condition,
                "when_value": None if when_condition != "equals" else when_value,
                "then_question": then_question,
                "then_effect": then_effect,
            }
        )
    if disable_dependency_cycle_exists(cleaned):
        raise RuleError(
            "Rules that turn questions off cannot form a loop "
            "(for example A turns off B and B turns off A)"
        )
    return cleaned


def disable_dependency_cycle_exists(rules: list[dict]) -> bool:
    """True when disable edges alone contain a directed cycle.

    required/optional edges are ignored: they do not clear answers during
    evaluation, so loops among them are stable. Disable edges feed blanked
    answers back into later conditions and can wipe employee input.
    """
    graph: dict[str, set[str]] = {}
    for rule in rules:
        if rule.get("then_effect") != "disable":
            continue
        source = rule["when_question"]
        target = rule["then_question"]
        graph.setdefault(source, set()).add(target)
        graph.setdefault(target, set())

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> bool:
        visiting.add(node)
        for nxt in graph.get(node, ()):
            if nxt in visiting:
                return True
            if nxt not in visited and dfs(nxt):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(dfs(node) for node in list(graph) if node not in visited)


def evaluate_effects(questions: list[dict], rules: list[dict], answers: dict) -> dict[str, dict]:
    """Return per-question effect flags after rules settle.

    Disabled fields are treated as blank when later rules are checked, so a
    disabled answer cannot keep another field off. Disable wins over required
    or optional when more than one rule targets the same question.
    """
    by_id = {question["id"]: question for question in questions}
    working = dict(answers)
    disabled: set[str] = set()
    optional: set[str] = set()
    required: set[str] = set()

    for _ in range(len(questions) + 2):
        next_disabled: set[str] = set()
        next_optional: set[str] = set()
        next_required: set[str] = set()
        for rule in rules:
            source_id = rule["when_question"]
            target_id = rule["then_question"]
            if source_id not in by_id or target_id not in by_id:
                continue
            source_value = empty_answer(by_id[source_id]["type"]) if source_id in disabled else working.get(source_id)
            if not condition_met(by_id[source_id], source_value, rule["when_condition"], rule.get("when_value")):
                continue
            effect = rule["then_effect"]
            if effect == "disable":
                next_disabled.add(target_id)
            elif effect == "optional":
                next_optional.add(target_id)
            elif effect == "required":
                next_required.add(target_id)
        next_optional -= next_disabled
        next_required -= next_disabled
        for question_id in next_disabled:
            working[question_id] = empty_answer(by_id[question_id]["type"])
        if next_disabled == disabled and next_optional == optional and next_required == required:
            break
        disabled, optional, required = next_disabled, next_optional, next_required

    effects = {}
    for question in questions:
        question_id = question["id"]
        effects[question_id] = {
            "disabled": question_id in disabled,
            "required": (
                False
                if question_id in disabled
                else True
                if question_id in required
                else False
                if question_id in optional
                else bool(question.get("required"))
            ),
        }
    return effects


def apply_rules_to_answers(definition: dict, answers: dict) -> tuple[dict, dict[str, dict]]:
    rules = definition.get("rules") or []
    effects = evaluate_effects(definition["questions"], rules, answers)
    cleaned = dict(answers)
    for question in definition["questions"]:
        question_id = question["id"]
        if effects[question_id]["disabled"]:
            cleaned[question_id] = empty_answer(question["type"])
    return cleaned, effects


def public_rules(rules: list[dict] | None) -> list[dict]:
    if not rules:
        return []
    return [
        {
            "id": rule["id"],
            "when_question": rule["when_question"],
            "when_condition": rule["when_condition"],
            "when_value": rule.get("when_value"),
            "then_question": rule["then_question"],
            "then_effect": rule["then_effect"],
        }
        for rule in rules
    ]


def labels_look_conflicting(approved: str, extracted: str) -> bool:
    """True when the new PDF fragment suggests the carried sentence may be wrong."""
    left = " ".join((approved or "").lower().split())
    right = " ".join((extracted or "").lower().split())
    if not left or not right or right == "no nearby text found":
        return False
    if left == right:
        return False

    left_amounts = set(re.findall(r"\$[\d,]+", left))
    right_amounts = set(re.findall(r"\$[\d,]+", right))
    if left_amounts and right_amounts and left_amounts != right_amounts:
        return True
    left_steps = set(re.findall(r"\b(?:step\s*)?(\d+[a-c]?)\b", left))
    right_steps = set(re.findall(r"\b(?:step\s*)?(\d+[a-c]?)\b", right))
    # Only treat as conflict when both sides name a step and they disagree.
    if left_steps and right_steps and left_steps.isdisjoint(right_steps):
        # Avoid false positives on short fragments like "(c)".
        if len(right) >= 20 or any(token.startswith(("3", "4")) for token in right_steps):
            return True
    return False
