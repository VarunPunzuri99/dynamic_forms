"""Use cases for the 2026 W-4 preview API."""

from __future__ import annotations

import base64
import hashlib
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.filler import read_field_values
from backend.main import SOURCE_PDF, app

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

SINGLE = "topmostSubform[0].Page1[0].c1_1[0]"
MARRIED = "topmostSubform[0].Page1[0].c1_1[1]"
HEAD = "topmostSubform[0].Page1[0].c1_1[2]"
TWO_JOBS = "topmostSubform[0].Page1[0].c1_2[0]"
CITY = "topmostSubform[0].Page1[0].Step1a[0].f1_04[0]"
STEP3A = "topmostSubform[0].Page1[0].Step3_ReadOrder[0].f1_06[0]"
STEP3B = "topmostSubform[0].Page1[0].Step3_ReadOrder[0].f1_07[0]"
STEP3 = "topmostSubform[0].Page1[0].f1_08[0]"
STEP4C = "topmostSubform[0].Page1[0].f1_11[0]"
EXEMPT = "topmostSubform[0].Page1[0].c1_3[0]"
FIRST = "topmostSubform[0].Page1[0].Step1a[0].f1_01[0]"


def sample() -> dict:
    return {
        "first_name": "Test",
        "last_name": "Employee",
        "address": "100 Main Street",
        "city_state_zip": "Austin TX 78701",
        "filing_status": "single",
        "qualifying_children_amount": "2200",
        "other_dependents_amount": "500",
        "extra_withholding": "25",
        "two_jobs_only": False,
        "step3_total": "2200",
        "other_income": "500",
        "deductions": "1000",
        "exempt_from_withholding": True,
    }


def post(answers: dict):
    return client.post("/api/forms/fw4/2026/answers", json={"answers": answers})


def pdf_values(response) -> dict[str, str]:
    encoded = response.json()["pdf_base64"]
    return read_field_values(base64.b64decode(encoded))


class ApiTests(unittest.TestCase):
    def test_questions_hide_pdf_field_names(self) -> None:
        response = client.get("/api/forms/fw4/2026/questions")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["questions"]), 13)
        self.assertNotIn("pdf_field", response.text)
        exempt = next(item for item in body["questions"] if item["id"] == "exempt_from_withholding")
        self.assertEqual(exempt["status"], "needs_review")
        self.assertEqual(exempt["type"], "checkbox")

    def test_valid_answers_fill_the_matching_boxes(self) -> None:
        before = hashlib.sha256(SOURCE_PDF.read_bytes()).hexdigest()
        response = post(sample())
        after = hashlib.sha256(SOURCE_PDF.read_bytes()).hexdigest()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, after)
        values = pdf_values(response)
        self.assertTrue(response.json()["pdf_base64"].startswith("JVBERi"))
        self.assertEqual(values[FIRST], "Test")
        self.assertEqual(values[CITY], "Austin TX 78701")
        self.assertEqual(values[STEP3A], "2200")
        self.assertEqual(values[STEP3B], "500")
        self.assertEqual(values[STEP4C], "25")
        self.assertEqual(values[SINGLE], "1")
        self.assertEqual(values[MARRIED], "Off")
        self.assertEqual(values[HEAD], "Off")
        self.assertEqual(values[TWO_JOBS], "Off")
        self.assertEqual(values[STEP3], "2200")
        self.assertEqual(values[EXEMPT], "1")
        self.assertTrue(any("needs review" in notice for notice in response.json()["notices"]))

    def test_married_checks_only_the_married_box(self) -> None:
        answers = sample()
        answers["filing_status"] = "married"
        answers["exempt_from_withholding"] = False
        answers["two_jobs_only"] = True
        values = pdf_values(post(answers))
        self.assertEqual(values[MARRIED], "2")
        self.assertEqual(values[SINGLE], "Off")
        self.assertEqual(values[HEAD], "Off")
        self.assertEqual(values[TWO_JOBS], "1")
        self.assertEqual(values[EXEMPT], "Off")

    def test_blank_optional_amount_is_not_written(self) -> None:
        answers = sample()
        answers["step3_total"] = ""
        answers["other_income"] = None
        del answers["deductions"]
        values = pdf_values(post(answers))
        self.assertNotIn(STEP3, values)
        self.assertNotIn("topmostSubform[0].Page1[0].f1_09[0]", values)
        self.assertNotIn("topmostSubform[0].Page1[0].f1_10[0]", values)

    def test_required_and_unknown_values_are_rejected(self) -> None:
        answers = sample()
        answers["first_name"] = "   "
        answers["filing_status"] = "other"
        answers["step3_total"] = "2,200"
        answers["two_jobs_only"] = "yes"
        answers["nickname"] = "nope"
        response = post(answers)
        self.assertEqual(response.status_code, 400)
        ids = {error["id"] for error in response.json()["errors"]}
        self.assertEqual(
            ids,
            {"first_name", "filing_status", "step3_total", "two_jobs_only", "nickname"},
        )

    def test_sample_endpoint_matches_a_valid_submit(self) -> None:
        sample_response = client.get("/api/forms/fw4/2026/sample-answers")
        self.assertEqual(sample_response.status_code, 200)
        filled = post(sample_response.json())
        self.assertEqual(filled.status_code, 200)
        self.assertEqual(pdf_values(filled)[FIRST], "Test")


if __name__ == "__main__":
    unittest.main()
