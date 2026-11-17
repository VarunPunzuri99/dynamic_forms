"""Unit tests for form-agnostic suggested inclusion defaults."""

from __future__ import annotations

import unittest

from backend.store import _primary_form_page, _suggested_include, _with_suggested_include


def row(**kwargs):
    base = {
        "question_key": "field",
        "pdf_field": "field",
        "page_number": 1,
        "change_type": "unchanged",
        "review_status": "needs_review",
    }
    base.update(kwargs)
    return base


class PrimaryFormPageTests(unittest.TestCase):
    def test_fw4_style_starts_on_page_one(self) -> None:
        rows = [
            row(question_key="a", page_number=1),
            row(question_key="b", page_number=3, change_type="added"),
            row(question_key="removed:x", page_number=1, change_type="removed"),
        ]
        self.assertEqual(_primary_form_page(rows), 1)

    def test_colorado_style_starts_on_page_two(self) -> None:
        rows = [
            row(question_key="Last Name", page_number=2, change_type="added"),
            row(question_key="City", page_number=2, change_type="unchanged"),
            row(question_key="Worksheet", page_number=4, change_type="added"),
        ]
        self.assertEqual(_primary_form_page(rows), 2)

    def test_missing_pages_are_ignored(self) -> None:
        rows = [
            row(question_key="a", page_number=None, change_type="imported"),
            row(question_key="b", page_number=2, change_type="imported"),
        ]
        self.assertEqual(_primary_form_page(rows), 2)


class SuggestedIncludeTests(unittest.TestCase):
    def test_colorado_new_and_unchanged_on_primary_page_are_suggested(self) -> None:
        rows = _with_suggested_include(
            [
                row(question_key="Last Name", pdf_field="Last Name", page_number=2, change_type="added"),
                row(question_key="First Name", pdf_field="First Name", page_number=2, change_type="unchanged"),
                row(question_key="WS", pdf_field="WS", page_number=5, change_type="added"),
            ]
        )
        by_key = {item["question_key"]: item["suggested_include"] for item in rows}
        self.assertTrue(by_key["Last Name"])
        self.assertTrue(by_key["First Name"])
        self.assertFalse(by_key["WS"])

    def test_removed_questions_are_not_suggested(self) -> None:
        rows = _with_suggested_include(
            [
                row(question_key="Keep", pdf_field="Keep", page_number=2, change_type="unchanged"),
                row(
                    question_key="removed:Old",
                    pdf_field=None,
                    page_number=2,
                    change_type="removed",
                ),
            ]
        )
        by_key = {item["question_key"]: item["suggested_include"] for item in rows}
        self.assertTrue(by_key["Keep"])
        self.assertFalse(by_key["removed:Old"])

    def test_missing_page_metadata_is_not_suggested(self) -> None:
        self.assertFalse(
            _suggested_include(
                row(question_key="NoPage", page_number=None, change_type="imported"),
                primary_page=2,
            )
        )

    def test_attention_does_not_block_suggestion(self) -> None:
        rows = _with_suggested_include(
            [
                row(
                    question_key="Flagged",
                    pdf_field="Flagged",
                    page_number=2,
                    change_type="unchanged",
                    attention="needs_review",
                )
            ]
        )
        self.assertTrue(rows[0]["suggested_include"])

    def test_fw4_page_one_exclusions_unchanged(self) -> None:
        rows = _with_suggested_include(
            [
                row(
                    question_key="topmostSubform[0].Page1[0].Step1a[0].f1_01[0]",
                    pdf_field="topmostSubform[0].Page1[0].Step1a[0].f1_01[0]",
                    page_number=1,
                    change_type="unchanged",
                ),
                row(
                    question_key="topmostSubform[0].Page1[0].f1_05[0]",
                    pdf_field="topmostSubform[0].Page1[0].f1_05[0]",
                    page_number=1,
                    change_type="unchanged",
                ),
                row(
                    question_key="topmostSubform[0].Page3[0].f3_07[0]",
                    pdf_field="topmostSubform[0].Page3[0].f3_07[0]",
                    page_number=3,
                    change_type="unchanged",
                ),
            ]
        )
        by_key = {item["question_key"]: item["suggested_include"] for item in rows}
        self.assertTrue(by_key["topmostSubform[0].Page1[0].Step1a[0].f1_01[0]"])
        self.assertFalse(by_key["topmostSubform[0].Page1[0].f1_05[0]"])
        self.assertFalse(by_key["topmostSubform[0].Page3[0].f3_07[0]"])

    def test_suggested_include_is_independent_of_review_status(self) -> None:
        """Reviewer decisions live in review_status; suggestion is only a default."""
        approved = row(
            question_key="Approved",
            pdf_field="Approved",
            page_number=2,
            change_type="unchanged",
            review_status="approved",
        )
        self.assertTrue(_suggested_include(approved, primary_page=2))


if __name__ == "__main__":
    unittest.main()
