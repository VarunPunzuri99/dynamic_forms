"""C# orchestration endpoint: POST /internal/extract."""

from __future__ import annotations

import base64
import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("FORMS_ENGINE_SOURCE", "csharp")

from fastapi.testclient import TestClient

from backend.forms_engine import FormsEngineError, extract_questions_for_internal_request
from backend.main import app

ROOT = Path(__file__).resolve().parents[1]


class ExtractPayloadTests(unittest.TestCase):
    def test_requires_pdf_or_edition(self) -> None:
        with self.assertRaises(FormsEngineError):
            extract_questions_for_internal_request({"formId": "fw4"})

    def test_extracts_from_pdf_base64(self) -> None:
        pdf_path = ROOT / "docs" / "fw4_2026.pdf"
        if not pdf_path.is_file():
            pdf_path = ROOT / "docs" / "fw4--2025.pdf"
        if not pdf_path.is_file():
            self.skipTest("No blank fw4 PDF under docs/")
        encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        result = extract_questions_for_internal_request(
            {
                "formId": "fw4",
                "editionId": 2,
                "edition": "2026",
                "pdf_base64": encoded,
            }
        )
        self.assertEqual(result["formId"], "fw4")
        self.assertGreater(result["importedCount"], 0)
        first = result["questions"][0]
        self.assertIn("questionKey", first)
        self.assertIn("label", first)
        self.assertIn("inputType", first)
        self.assertIn("pdfField", first)
        self.assertEqual(first["changeType"], "new")
        self.assertEqual(first["labelSource"], "pdf")


class InternalExtractApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_unauthorized_when_api_key_configured(self) -> None:
        with mock.patch.dict(os.environ, {"PYTHON_FILL_API_KEY": "secret"}, clear=False):
            response = self.client.post("/internal/extract", json={"formId": "fw4"})
        self.assertEqual(response.status_code, 401)

    def test_extract_returns_questions(self) -> None:
        fake = {
            "formId": "minnesota",
            "editionId": 12,
            "edition": "2026",
            "importedCount": 1,
            "questions": [
                {
                    "questionKey": "employee_name",
                    "label": "Employee name",
                    "inputType": "text",
                    "required": False,
                    "pdfField": "EmployeeName",
                    "checkedValue": None,
                    "optionsJson": None,
                    "pageNumber": 1,
                    "sortOrder": 1,
                    "changeType": "new",
                    "labelSource": "pdf",
                }
            ],
        }
        with mock.patch(
            "backend.main.extract_questions_for_internal_request",
            return_value=fake,
        ):
            response = self.client.post(
                "/internal/extract",
                json={
                    "formId": "minnesota",
                    "editionId": 12,
                    "edition": "2026",
                    "pdf_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
                },
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["questions"]), 1)
        self.assertEqual(body["questions"][0]["questionKey"], "employee_name")
        self.assertEqual(body["importedCount"], 1)


if __name__ == "__main__":
    unittest.main()
