"""Phase 1: C# FormsEngine read cutover helpers and smoke checks."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

# Force C# mode for these tests; remote smoke tests skip if unset.
os.environ.setdefault("FORMS_ENGINE_SOURCE", "csharp")

from backend.forms_engine import (
    ensure_edition_pdf_file,
    list_editions,
    list_forms,
    live_definition,
    normalize_edition_summary,
    normalize_question_row,
    question_row_to_definition_item,
    uses_csharp,
)
from backend import csharp_client


class NormalizationTests(unittest.TestCase):
    def test_edition_summary_maps_camel_case(self) -> None:
        row = normalize_edition_summary(
            {
                "id": 2,
                "formId": "fw4",
                "edition": "2026",
                "storagePath": "https://example/blob/fw4.pdf",
                "comparedToPath": None,
                "status": "approved",
                "isLive": True,
                "questionCount": 3,
            }
        )
        self.assertEqual(row["form_id"], "fw4")
        self.assertTrue(row["is_live"])
        self.assertEqual(row["storage_path"], "https://example/blob/fw4.pdf")
        self.assertEqual(row["question_count"], 3)
        self.assertEqual(row["approved_count"], 3)

    def test_question_row_and_definition_item(self) -> None:
        row = normalize_question_row(
            {
                "questionKey": "first_name",
                "label": "First name",
                "inputType": "text",
                "required": True,
                "reviewStatus": "approved",
                "pdfField": "f1_01[0]",
                "pageNumber": 1,
                "sortOrder": 2,
                "changeType": "reviewed",
                "labelSource": "reviewed",
            }
        )
        self.assertEqual(row["question_key"], "first_name")
        self.assertEqual(row["input_type"], "text")
        item = question_row_to_definition_item(row)
        self.assertEqual(item["id"], "first_name")
        self.assertEqual(item["type"], "text")
        self.assertEqual(item["pdf_field"], "f1_01[0]")


@unittest.skipUnless(
    csharp_client.configured() and uses_csharp(),
    "C# FormsEngine is not configured for live smoke tests",
)
class CsharpReadSmokeTests(unittest.TestCase):
    def test_list_forms_includes_configured_forms(self) -> None:
        forms = {item["form_id"]: item for item in list_forms()}
        self.assertIn("fw4", forms)
        self.assertIn("colorado", forms)
        self.assertIsNotNone(forms["fw4"]["live"])
        self.assertIsNotNone(forms["colorado"]["live"])
        self.assertIn(forms["fw4"]["live"]["edition"], {"2025", "2026"})
        self.assertIn(forms["colorado"]["live"]["edition"], {"2025", "2026"})

    def test_list_editions_and_live_definition(self) -> None:
        pack = list_editions("fw4")
        self.assertEqual(pack["form_id"], "fw4")
        self.assertEqual(len(pack["editions"]), 2)
        self.assertTrue(pack["live"]["is_live"])
        definition = live_definition("fw4")
        self.assertEqual(definition["form_id"], "fw4")
        self.assertEqual(definition["edition"], "2026")
        self.assertEqual(definition["edition_id"], pack["live"]["id"])
        self.assertIsInstance(definition["questions"], list)
        self.assertIsInstance(definition["rules"], list)

    def test_blank_pdf_download_cached(self) -> None:
        pack = list_editions("fw4")
        edition_id = int(pack["live"]["id"])
        path = ensure_edition_pdf_file("fw4", edition_id)
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 1000)
        self.assertTrue(path.read_bytes().startswith(b"%PDF"))
        again = ensure_edition_pdf_file("fw4", edition_id)
        self.assertEqual(path, again)


class SourceSwitchTests(unittest.TestCase):
    def test_postgres_source_disables_csharp(self) -> None:
        with mock.patch.dict(os.environ, {"FORMS_ENGINE_SOURCE": "postgres"}, clear=False):
            self.assertFalse(uses_csharp())

    def test_csharp_source_requires_config(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "FORMS_ENGINE_SOURCE": "csharp",
                "CSHARP_API_BASE_URL": "https://example.invalid",
                "OCP_APIM_SUBSCRIPTION_KEY": "test-key",
            },
            clear=False,
        ):
            self.assertTrue(uses_csharp())


if __name__ == "__main__":
    unittest.main()
