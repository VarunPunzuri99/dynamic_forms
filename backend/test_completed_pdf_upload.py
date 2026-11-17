"""Completed PDF upload to C# Blob after fill."""

from __future__ import annotations

import base64
import os
import unittest
from unittest import mock

os.environ.setdefault("FORMS_ENGINE_SOURCE", "csharp")

from backend import csharp_client
from backend.forms_engine import (
    FormsEngineError,
    live_definition,
    upload_completed_pdf_for_definition,
    uses_csharp,
)
from backend.main import _pdf_response


class UploadHelperTests(unittest.TestCase):
    def test_upload_helper_requires_csharp_ids(self) -> None:
        with mock.patch("backend.forms_engine.uses_csharp", return_value=True):
            with self.assertRaises(FormsEngineError):
                upload_completed_pdf_for_definition(
                    {"form_id": "fw4"},
                    b"%PDF-1.4",
                    filename="x.pdf",
                )

    def test_upload_helper_maps_blob_path(self) -> None:
        with mock.patch("backend.forms_engine.uses_csharp", return_value=True):
            with mock.patch(
                "backend.forms_engine.csharp_client.upload_completed_pdf",
                return_value={
                    "formId": "fw4",
                    "editionId": 2,
                    "blobPath": "https://example/blob/completed.pdf",
                },
            ) as upload:
                result = upload_completed_pdf_for_definition(
                    {"form_id": "fw4", "edition_id": 2},
                    b"%PDF-1.4 test",
                    filename="fw4-2026-completed.pdf",
                )
        upload.assert_called_once()
        self.assertEqual(result["blob_path"], "https://example/blob/completed.pdf")
        self.assertEqual(result["edition_id"], 2)


class PdfResponseUploadTests(unittest.TestCase):
    def test_pdf_response_uploads_and_keeps_preview(self) -> None:
        definition = {
            "form_id": "fw4",
            "edition": "2026",
            "edition_id": 2,
            "source_pdf": "docs/fw4_2026.pdf",
            "questions": [
                {
                    "id": "q1",
                    "label": "Q1",
                    "type": "text",
                    "required": False,
                    "pdf_field": "f1",
                }
            ],
            "rules": [],
        }
        with mock.patch("backend.main.normalize_answers", return_value={"q1": "Ada"}):
            with mock.patch("backend.main.resolve_blank_pdf", return_value=mock.Mock()):
                with mock.patch("backend.main.fill_pdf", return_value=b"%PDF-1.4 filled"):
                    with mock.patch("backend.main.preview_notices", return_value=[]):
                        with mock.patch("backend.main.uses_csharp", return_value=True):
                            with mock.patch(
                                "backend.main.upload_completed_pdf_for_definition",
                                return_value={"blob_path": "https://example/blob/done.pdf"},
                            ):
                                response = _pdf_response(
                                    definition,
                                    {"q1": "Ada"},
                                    "fw4-2026-completed.pdf",
                                )
        body = response.body
        import json

        payload = json.loads(body)
        self.assertTrue(payload["stored_to_blob"])
        self.assertEqual(payload["blob_path"], "https://example/blob/done.pdf")
        self.assertTrue(payload["pdf_base64"])
        self.assertEqual(base64.b64decode(payload["pdf_base64"]), b"%PDF-1.4 filled")
        self.assertTrue(any("Azure Blob" in notice for notice in payload["notices"]))

    def test_pdf_response_keeps_preview_when_upload_fails(self) -> None:
        definition = {
            "form_id": "fw4",
            "edition": "2026",
            "edition_id": 2,
            "source_pdf": "docs/fw4_2026.pdf",
            "questions": [
                {
                    "id": "q1",
                    "label": "Q1",
                    "type": "text",
                    "required": False,
                    "pdf_field": "f1",
                }
            ],
            "rules": [],
        }
        with mock.patch("backend.main.normalize_answers", return_value={"q1": "Ada"}):
            with mock.patch("backend.main.resolve_blank_pdf", return_value=mock.Mock()):
                with mock.patch("backend.main.fill_pdf", return_value=b"%PDF-1.4 filled"):
                    with mock.patch("backend.main.preview_notices", return_value=[]):
                        with mock.patch("backend.main.uses_csharp", return_value=True):
                            with mock.patch(
                                "backend.main.upload_completed_pdf_for_definition",
                                side_effect=FormsEngineError("upload failed", 502),
                            ):
                                response = _pdf_response(
                                    definition,
                                    {"q1": "Ada"},
                                    "fw4-2026-completed.pdf",
                                )
        import json

        payload = json.loads(response.body)
        self.assertFalse(payload["stored_to_blob"])
        self.assertIsNone(payload["blob_path"])
        self.assertTrue(payload["pdf_base64"])
        self.assertTrue(any("Blob upload failed" in notice for notice in payload["notices"]))


@unittest.skipUnless(
    csharp_client.configured() and uses_csharp(),
    "C# FormsEngine is not configured for live smoke tests",
)
class CompletedPdfUploadSmokeTests(unittest.TestCase):
    def test_upload_tiny_completed_pdf_for_live_fw4(self) -> None:
        definition = live_definition("fw4")
        self.assertIsNotNone(definition.get("edition_id"))
        # Minimal valid PDF header + body so C# accepts the multipart file.
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<<>>endobj\n"
            b"trailer<<>>\n"
            b"%%EOF\n"
        )
        result = upload_completed_pdf_for_definition(
            definition,
            pdf_bytes,
            filename=f"fw4-{definition['edition']}-smoke-completed.pdf",
        )
        self.assertTrue(result.get("blob_path"), result)


if __name__ == "__main__":
    unittest.main()
