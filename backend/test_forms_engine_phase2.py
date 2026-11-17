"""Phase 2: C# save/publish/import/compare persistence paths."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("FORMS_ENGINE_SOURCE", "csharp")

from backend.forms_engine import (
    FormsEngineError,
    apply_reviewer_selection,
    approve_edition_fields,
    compare_with_edition,
    import_live_fields,
    normalize_rules,
    publish_edition,
    question_row_to_csharp,
)


def _row(key: str, **kwargs):
    base = {
        "question_key": key,
        "label": f"Label {key}",
        "input_type": "text",
        "required": False,
        "review_status": "needs_review",
        "pdf_field": key,
        "checked_value": None,
        "options_json": None,
        "page_number": 1,
        "sort_order": 1,
        "change_type": "reviewed",
        "label_source": "extracted",
        "prior_label": None,
        "attention": None,
        "max_length": None,
    }
    base.update(kwargs)
    return base


class SelectionAndSerializationTests(unittest.TestCase):
    def test_question_row_to_csharp_camel_case(self) -> None:
        payload = question_row_to_csharp(_row("q1", review_status="approved"))
        self.assertEqual(payload["questionKey"], "q1")
        self.assertEqual(payload["reviewStatus"], "approved")
        self.assertEqual(payload["inputType"], "text")

    def test_normalize_rules_handles_csharp_valuekind_bug(self) -> None:
        self.assertEqual(normalize_rules({"ValueKind": []}), [])
        self.assertEqual(normalize_rules([{"id": "r1"}]), [{"id": "r1"}])

    def test_apply_reviewer_selection_updates_include_and_rules(self) -> None:
        existing = [
            _row("q1", sort_order=1),
            _row("q2", sort_order=2),
        ]
        items = [
            {"question_key": "q1", "label": "One", "include": True},
            {"question_key": "q2", "label": "Two", "include": True},
        ]
        rows, published, rules = apply_reviewer_selection(
            existing,
            items,
            True,
            [
                {
                    "id": "r1",
                    "when_question": "q1",
                    "when_condition": "answered",
                    "then_question": "q2",
                    "then_effect": "disable",
                }
            ],
        )
        self.assertEqual(published, 2)
        by_key = {row["question_key"]: row for row in rows}
        self.assertEqual(by_key["q1"]["review_status"], "approved")
        self.assertEqual(by_key["q2"]["review_status"], "approved")
        self.assertEqual(rules[0]["then_effect"], "disable")

    def test_apply_reviewer_selection_rejects_unknown_keys(self) -> None:
        with self.assertRaises(FormsEngineError):
            apply_reviewer_selection(
                [_row("q1")],
                [{"question_key": "missing", "label": "X", "include": True}],
                True,
                [],
            )


class CsharpWritePathTests(unittest.TestCase):
    def test_approve_edition_fields_calls_save_helpers(self) -> None:
        detail = {
            "edition": {
                "id": 4,
                "form_id": "colorado",
                "edition": "2026",
                "storage_path": "https://example/c.pdf",
                "compared_to_path": None,
                "status": "draft",
                "is_live": False,
            },
            "questions": [_row("q1"), _row("q2", sort_order=2)],
            "rules": [],
        }
        with mock.patch("backend.forms_engine.get_edition_detail", return_value=detail):
            with mock.patch("backend.forms_engine.save_questions_and_rules") as save:
                save.return_value = {"edition": detail["edition"], "saved_count": 2, "rules": []}
                result = approve_edition_fields(
                    "colorado",
                    4,
                    [
                        {"question_key": "q1", "label": "One", "include": True},
                        {"question_key": "q2", "label": "Two", "include": True},
                    ],
                    True,
                    [],
                )
        self.assertEqual(result["published_questions"], 2)
        save.assert_called_once()
        args = save.call_args
        self.assertEqual(args.args[0], "colorado")
        self.assertEqual(args.args[1], 4)
        self.assertEqual(len(args.args[2]), 2)

    def test_publish_edition_requires_draft_then_publishes(self) -> None:
        detail = {
            "edition": {
                "id": 4,
                "form_id": "colorado",
                "edition": "2026",
                "storage_path": "https://example/c.pdf",
                "compared_to_path": None,
                "status": "draft",
                "is_live": False,
            },
            "questions": [_row("q1")],
            "rules": [],
        }
        with mock.patch("backend.forms_engine.get_edition_detail", return_value=detail):
            with mock.patch("backend.forms_engine.save_questions_and_rules") as save:
                save.return_value = {"edition": detail["edition"], "saved_count": 1, "rules": []}
                with mock.patch("backend.forms_engine.csharp_client.publish_edition") as publish:
                    publish.return_value = {
                        "formId": "colorado",
                        "editionId": 4,
                        "edition": "2026",
                        "status": "approved",
                        "isLive": True,
                    }
                    result = publish_edition(
                        "colorado",
                        4,
                        [{"question_key": "q1", "label": "One", "include": True}],
                        True,
                        [],
                    )
        self.assertTrue(result["is_live"])
        self.assertEqual(result["status"], "approved")
        publish.assert_called_once_with("colorado", 4, True)

    def test_publish_rejects_already_approved(self) -> None:
        detail = {
            "edition": {
                "id": 2,
                "form_id": "fw4",
                "edition": "2026",
                "storage_path": "https://example/w.pdf",
                "compared_to_path": None,
                "status": "approved",
                "is_live": True,
            },
            "questions": [_row("q1")],
            "rules": [],
        }
        with mock.patch("backend.forms_engine.get_edition_detail", return_value=detail):
            with self.assertRaises(FormsEngineError) as raised:
                publish_edition(
                    "fw4",
                    2,
                    [{"question_key": "q1", "label": "One", "include": True}],
                    True,
                    [],
                )
        self.assertEqual(raised.exception.status_code, 409)

    def test_import_live_fields_writes_extracted_questions(self) -> None:
        live = {
            "id": 2,
            "formId": "fw4",
            "edition": "2026",
            "storagePath": "https://example/w.pdf",
            "comparedToPath": None,
            "status": "approved",
            "isLive": True,
            "rules": [],
            "questions": [],
        }
        extracted = [
            {
                "name": "field.a",
                "label": "A",
                "type": "Text",
                "page": 1,
                "checked_value": None,
            }
        ]
        with mock.patch("backend.forms_engine.csharp_client.get_live_edition", return_value=live):
            with mock.patch("backend.forms_engine.ensure_edition_pdf_file", return_value=mock.Mock()):
                with mock.patch("backend.forms_engine.widgets", return_value=extracted):
                    with mock.patch("backend.forms_engine.save_questions_and_rules") as save:
                        save.return_value = {"edition": {"edition": "2026"}, "saved_count": 1, "rules": []}
                        result = import_live_fields("fw4")
        self.assertEqual(result["question_count"], 1)
        self.assertEqual(result["edition_id"], 2)
        rows = save.call_args.args[2]
        self.assertEqual(rows[0]["question_key"], "field.a")
        self.assertEqual(rows[0]["change_type"], "imported")

    def test_compare_with_edition_requires_draft_target(self) -> None:
        live = {
            "id": 2,
            "formId": "fw4",
            "edition": "2026",
            "storagePath": "https://example/live.pdf",
            "status": "approved",
            "isLive": True,
            "questions": [],
            "rules": [],
        }
        detail = {
            "edition": {
                "id": 1,
                "form_id": "fw4",
                "edition": "2025",
                "storage_path": "https://example/old.pdf",
                "compared_to_path": None,
                "status": "approved",
                "is_live": False,
            },
            "questions": [],
            "rules": [],
        }
        with mock.patch("backend.forms_engine.csharp_client.get_live_edition", return_value=live):
            with mock.patch("backend.forms_engine.get_edition_detail", return_value=detail):
                with self.assertRaises(FormsEngineError) as raised:
                    compare_with_edition("fw4", 1)
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
