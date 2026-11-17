"""Validate W-4 answers and fill a blank PDF in memory.

The blank file is opened read-only for each request. Nothing is written back
to it, and filled documents are returned as bytes instead of a shared path.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pymupdf

_FILL_LOCK = threading.Lock()
_NUMBER = re.compile(r"^\d{1,9}(\.\d{1,2})?$")


class ValidationError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("invalid answers")
        self.errors = errors


class DefinitionError(Exception):
    pass


def load_definition(path: Path) -> dict:
    definition = json.loads(path.read_text(encoding="utf-8"))
    validate_definition(definition)
    return definition


def validate_definition(definition: dict) -> None:
    questions = definition.get("questions")
    if not isinstance(questions, list) or not questions:
        raise DefinitionError("Question file has no questions")
    seen_ids: set[str] = set()
    seen_fields: set[str] = set()
    for question in questions:
        question_id = question.get("id")
        if not question_id or question_id in seen_ids:
            raise DefinitionError(f"Duplicate or missing question id: {question_id}")
        seen_ids.add(question_id)
        question_type = question.get("type")
        if question_type == "choice":
            options = question.get("options") or []
            values = [option.get("value") for option in options]
            if len(options) < 2 or len(values) != len(set(values)):
                raise DefinitionError(f"{question_id} has invalid choices")
            for option in options:
                _claim_field(seen_fields, option.get("pdf_field"), question_id)
                if not option.get("checked_value"):
                    raise DefinitionError(f"{question_id} choice is missing checked_value")
        elif question_type in {"text", "number"}:
            _claim_field(seen_fields, question.get("pdf_field"), question_id)
        elif question_type == "checkbox":
            _claim_field(seen_fields, question.get("pdf_field"), question_id)
            if not question.get("checked_value"):
                raise DefinitionError(f"{question_id} is missing checked_value")
        else:
            raise DefinitionError(f"Unsupported question type for {question_id}")


def _claim_field(seen: set[str], field_name: str | None, question_id: str) -> None:
    if not field_name or field_name in seen:
        raise DefinitionError(f"{question_id} has a missing or duplicate PDF field")
    seen.add(field_name)


def public_questions(definition: dict) -> list[dict]:
    public = []
    for question in definition["questions"]:
        item = {
            "id": question["id"],
            "label": question["label"],
            "type": question["type"],
            "required": bool(question.get("required")),
            "status": question.get("status", "verified"),
        }
        if question["type"] == "text":
            item["max_length"] = int(question.get("max_length", 80))
        if question["type"] == "choice":
            item["options"] = [
                {"value": option["value"], "label": option["label"]}
                for option in question["options"]
            ]
        public.append(item)
    return public


def public_definition(definition: dict) -> dict:
    from backend.rules import public_rules

    return {
        "questions": public_questions(definition),
        "rules": public_rules(definition.get("rules")),
    }


def normalize_answers(definition: dict, answers: object) -> dict:
    if not isinstance(answers, dict):
        raise ValidationError([{"id": "", "message": "Answers must be an object"}])
    questions = {question["id"]: question for question in definition["questions"]}
    errors: list[dict] = []
    for key in sorted(set(answers) - set(questions)):
        errors.append({"id": key, "message": "Unknown question"})

    from backend.rules import apply_rules_to_answers

    working, effects = apply_rules_to_answers(definition, answers)
    normalized: dict = {}
    for question in definition["questions"]:
        question_id = question["id"]
        effect = effects[question_id]
        present = question_id in working and not effect["disabled"]
        raw = working.get(question_id)
        if effect["disabled"]:
            normalized[question_id] = False if question["type"] == "checkbox" else None
            continue
        try:
            required_question = dict(question)
            required_question["required"] = effect["required"]
            normalized[question_id] = _normalize_value(required_question, raw, present)
        except ValueError as exc:
            errors.append({"id": question_id, "message": str(exc)})
    if errors:
        raise ValidationError(errors)
    return normalized


def _normalize_value(question: dict, raw, present: bool):
    question_type = question["type"]
    required = bool(question.get("required"))
    if question_type == "text":
        return _normalize_text(raw, present, required, int(question.get("max_length", 80)))
    if question_type == "number":
        return _normalize_number(raw, present, required)
    if question_type == "checkbox":
        if not present:
            if required:
                raise ValueError("Choose yes or no")
            return False
        if not isinstance(raw, bool):
            raise ValueError("Choose yes or no")
        return raw
    if not present or raw in (None, ""):
        if required:
            raise ValueError("Select one option")
        return None
    if not isinstance(raw, str):
        raise ValueError("Select one option")
    allowed = {option["value"] for option in question["options"]}
    if raw not in allowed:
        raise ValueError("Select one of the listed options")
    return raw


def _normalize_text(raw, present: bool, required: bool, max_length: int):
    if not present or raw is None:
        if required:
            raise ValueError("This field is required")
        return None
    if not isinstance(raw, str):
        raise ValueError("Enter text")
    value = " ".join(raw.split())
    if not value:
        if required:
            raise ValueError("This field is required")
        return None
    if len(value) > max_length:
        raise ValueError(f"Use {max_length} characters or fewer")
    return value


def _normalize_number(raw, present: bool, required: bool):
    if not present or raw is None or raw == "":
        if required:
            raise ValueError("Enter an amount")
        return None
    if isinstance(raw, bool):
        raise ValueError("Enter digits only")
    if isinstance(raw, int):
        text = str(raw)
    elif isinstance(raw, float):
        text = str(int(raw)) if raw.is_integer() else f"{raw:.2f}".rstrip("0").rstrip(".")
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        raise ValueError("Enter digits only")
    if not _NUMBER.fullmatch(text):
        raise ValueError("Enter a positive amount with up to 2 decimal places and no commas")
    return text


def planned_values(definition: dict, normalized: dict) -> dict[str, str]:
    planned: dict[str, str] = {}
    for question in definition["questions"]:
        value = normalized[question["id"]]
        if question["type"] == "choice":
            for option in question["options"]:
                planned[option["pdf_field"]] = (
                    option["checked_value"] if value is not None and option["value"] == value else "Off"
                )
            continue
        if question["type"] == "checkbox":
            planned[question["pdf_field"]] = question["checked_value"] if value else "Off"
            continue
        if value is not None:
            planned[question["pdf_field"]] = value
    return planned


def preview_notices(definition: dict, normalized: dict) -> list[str]:
    notices = [
        "This is a preview copy. It is not a signed Form W-4. Filling the official PDF invalidates its original digital signature."
    ]
    exempt = normalized.get("exempt_from_withholding") is True
    other_filled = any(
        normalized.get(key) not in (None, False, "")
        for key in (
            "two_jobs_only",
            "qualifying_children_amount",
            "other_dependents_amount",
            "step3_total",
            "other_income",
            "deductions",
            "extra_withholding",
        )
    )
    if exempt and other_filled:
        notices.append(
            "Exemption is checked and other step answers are also filled. Confirm that combination. Steps 2 through 4 are completed only when they apply."
        )
    if exempt:
        notices.append(
            "The exemption question is marked needs review. This preview fills the new 2026 checkbox, and it is not approved for production onboarding."
        )
    return notices


def fill_pdf(definition: dict, normalized: dict, source_pdf: Path) -> bytes:
    if not source_pdf.is_file():
        raise FileNotFoundError(f"Blank PDF is missing: {source_pdf.name}")
    planned = planned_values(definition, normalized)
    with _FILL_LOCK:
        before = source_pdf.read_bytes()
        doc = pymupdf.open(source_pdf)
        try:
            _apply_values(doc, planned)
            filled = doc.tobytes(deflate=True)
        finally:
            doc.close()
        after = source_pdf.read_bytes()
    if before != after:
        raise RuntimeError("Blank PDF was modified")
    return filled


def _apply_values(doc: pymupdf.Document, values: dict[str, str]) -> None:
    pending = dict(values)
    for page in doc:
        for widget in page.widgets() or []:
            if widget.field_name not in pending:
                continue
            widget.field_value = pending.pop(widget.field_name)
            widget.update()
    if pending:
        missing = ", ".join(sorted(pending))
        raise RuntimeError(f"PDF fields not found: {missing}")


def read_field_values(pdf_bytes: bytes) -> dict[str, str]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    values = {}
    try:
        for page in doc:
            for widget in page.widgets() or []:
                value = widget.field_value
                if value not in (None, ""):
                    values[widget.field_name] = str(value)
    finally:
        doc.close()
    return values
