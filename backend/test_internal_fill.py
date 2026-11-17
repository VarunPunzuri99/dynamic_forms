"""C# orchestration endpoint: POST /internal/fill."""

from __future__ import annotations

import base64
import os
import unittest
from unittest import mock

os.environ.setdefault("FORMS_ENGINE_SOURCE", "csharp")

from fastapi.testclient import TestClient

from backend.forms_engine import FormsEngineError, build_definition_from_fill_payload
from backend.main import app


class BuildDefinitionTests(unittest.TestCase):
    def test_requires_form_id(self) -> None:
        with self.assertRaises(FormsEngineError):
            build_definition_from_fill_payload({"answers": {}})

    def test_builds_from_inline_questions_and_field_map(self) -> None:
        definition = build_definition_from_fill_payload(
            {
                "formId": "fw4",
                "editionId": 2,
                "edition": "2026",
                "storagePath": "https://example/blank.pdf",
                "questions": [
                    {
                        "questionKey": "first_name",
                        "label": "First name",
                        "inputType": "text",
                        "required": True,
                    }
                ],
                "questionFieldMap": {"first_name": "topmost.f1_01[0]"},
            }
        )
        self.assertEqual(definition["form_id"], "fw4")
        self.assertEqual(definition["edition_id"], 2)
        self.assertEqual(definition["questions"][0]["id"], "first_name")
        self.assertEqual(definition["questions"][0]["pdf_field"], "topmost.f1_01[0]")


class InternalFillApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_unauthorized_when_api_key_configured(self) -> None:
        with mock.patch.dict(os.environ, {"PYTHON_FILL_API_KEY": "secret"}, clear=False):
            response = self.client.post("/internal/fill", json={"formId": "fw4", "answers": {}})
        self.assertEqual(response.status_code, 401)

    def test_fill_returns_pdf_base64_without_blob_upload(self) -> None:
        fake = {
            "form_id": "fw4",
            "edition": "2026",
            "edition_id": 2,
            "filename": "fw4-2026-completed.pdf",
            "notices": ["ok"],
            "pdf_bytes": b"%PDF-1.4",
            "pdf_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
        }
        with mock.patch("backend.main.fill_pdf_for_internal_request", return_value=fake):
            response = self.client.post(
                "/internal/fill",
                json={
                    "formId": "fw4",
                    "editionId": 2,
                    "answers": {"first_name": "Ada"},
                },
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["formId"], "fw4")
        self.assertEqual(body["editionId"], 2)
        self.assertEqual(body["pdf_base64"], fake["pdf_base64"])
        self.assertNotIn("blob_path", body)
        self.assertNotIn("blobPath", body)
        self.assertNotIn("stored_to_blob", body)


@unittest.skipUnless(
    os.environ.get("CSHARP_API_BASE_URL") and os.environ.get("OCP_APIM_SUBSCRIPTION_KEY"),
    "C# FormsEngine is not configured for live smoke tests",
)
class InternalFillSmokeTests(unittest.TestCase):
    def test_fill_live_fw4_with_acroform_answers(self) -> None:
        from backend.forms_engine import live_definition, fill_pdf_for_internal_request
        from backend.filler import read_field_values

        definition = live_definition("fw4")
        answers = {}
        for question in definition["questions"]:
            if question["type"] == "text":
                answers[question["id"]] = "SMOKE"
            elif question["type"] == "number":
                answers[question["id"]] = "3"
            elif question["type"] == "checkbox":
                answers[question["id"]] = False
            elif question["type"] == "choice":
                answers[question["id"]] = question["options"][0]["value"]

        result = fill_pdf_for_internal_request(
            {
                "formId": "fw4",
                "editionId": definition["edition_id"],
                "edition": definition["edition"],
                "storagePath": definition.get("source_pdf"),
                "questions": [
                    {
                        "questionKey": q["id"],
                        "label": q["label"],
                        "inputType": q["type"],
                        "required": q.get("required"),
                        "pdfField": q.get("pdf_field"),
                        "checkedValue": q.get("checked_value"),
                        "options": q.get("options"),
                    }
                    for q in definition["questions"]
                ],
                "answers": answers,
            }
        )
        self.assertTrue(result["pdf_base64"])
        values = read_field_values(result["pdf_bytes"])
        text_hits = [v for v in values.values() if v == "SMOKE"]
        self.assertGreaterEqual(len(text_hits), 1, values)


if __name__ == "__main__":
    unittest.main()
