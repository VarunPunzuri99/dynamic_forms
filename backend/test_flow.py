"""Live edition stays on 2025 until the 2026 draft is approved.

The test approves 2026 only long enough to prove the switch, then puts the
original comparison draft back and makes 2025 live again.
"""

from __future__ import annotations

import base64
import unittest

from fastapi.testclient import TestClient

from backend.filler import read_field_values
from backend.main import app
from backend.store import connect, run_comparison

client = TestClient(app)

FIRST = "topmostSubform[0].Page1[0].Step1a[0].f1_01[0]"
CHILDREN_2025 = "topmostSubform[0].Page1[0].Step3_ReadOrder[0].f1_06[0]"
EXEMPT = "topmostSubform[0].Page1[0].c1_3[0]"


def restore_database() -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE form_editions
                SET status = 'draft', is_live = false
                WHERE form_id = 'fw4' AND edition = '2026'
                """
            )
            cursor.execute(
                """
                UPDATE form_editions
                SET status = 'approved', is_live = true
                WHERE form_id = 'fw4' AND edition = '2025'
                """
            )
        connection.commit()
    run_comparison()


class LiveEditionFlow(unittest.TestCase):
    def tearDown(self) -> None:
        restore_database()

    def test_employee_keeps_2025_until_2026_is_approved(self) -> None:
        live = client.get("/api/forms/live")
        self.assertEqual(live.status_code, 200, live.text)
        self.assertEqual(live.json()["edition"], "2025")
        self.assertEqual(live.json()["pending_draft"]["edition"], "2026")
        self.assertTrue(live.json()["pending_draft"]["question_count"] >= 50)
        self.assertFalse(any(question["id"] == "exempt_from_withholding" for question in live.json()["questions"]))

        sample = client.get("/api/forms/live/sample-answers")
        self.assertEqual(sample.status_code, 200, sample.text)
        self.assertEqual(sample.json()["qualifying_children_amount"], "2000")
        filled = client.post("/api/forms/live/answers", json={"answers": sample.json()})
        self.assertEqual(filled.status_code, 200, filled.text)
        values = read_field_values(base64.b64decode(filled.json()["pdf_base64"]))
        self.assertEqual(values[FIRST], "Test")
        self.assertEqual(values[CHILDREN_2025], "2000")

        compared = client.post("/api/jobs/compare")
        self.assertEqual(compared.status_code, 200, compared.text)
        self.assertGreater(compared.json()["counts"]["added"], 0)
        self.assertEqual(client.get("/api/forms/live").json()["edition"], "2025")

        review = client.get("/api/reviews/fw4/2026")
        self.assertEqual(review.status_code, 200, review.text)
        questions = review.json()["questions"]
        self.assertGreaterEqual(len(questions), 50)
        ssn = next(row for row in questions if (row["pdf_field"] or "").endswith("f1_05[0]"))
        self.assertFalse(ssn["suggested_include"])
        included = next(row for row in questions if row["suggested_include"])

        rejected = client.post(
            "/api/reviews/fw4/2026/approve",
            json={"questions": [{"question_key": included["question_key"], "label": "x", "include": False}]},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        unconfirmed = client.post(
            "/api/reviews/fw4/2026/approve",
            json={
                "questions": [
                    {"question_key": included["question_key"], "label": included["label"], "include": True}
                ],
                "confirm_generated_wording": False,
            },
        )
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.text)
        self.assertEqual(client.get("/api/forms/live").json()["edition"], "2025")

        wording = client.post("/api/reviews/fw4/2026/use-reviewed-wording")
        self.assertEqual(wording.status_code, 200, wording.text)
        self.assertEqual(client.get("/api/forms/live").json()["edition"], "2025")
        reviewed = client.get("/api/reviews/fw4/2026").json()["questions"]
        self.assertTrue(reviewed)
        self.assertTrue(all(row["change_type"] == "reviewed" for row in reviewed))

        approved = client.post(
            "/api/reviews/fw4/2026/approve",
            json={
                "questions": [
                    {"question_key": row["question_key"], "label": row["label"], "include": True}
                    for row in reviewed
                ]
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        blocked = client.post("/api/jobs/compare")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(client.get("/api/forms/live").json()["edition"], "2026")

        sample_2026 = client.get("/api/forms/live/sample-answers").json()
        filled_2026 = client.post("/api/forms/live/answers", json={"answers": sample_2026})
        self.assertEqual(filled_2026.status_code, 200, filled_2026.text)
        values_2026 = read_field_values(base64.b64decode(filled_2026.json()["pdf_base64"]))
        self.assertEqual(values_2026[FIRST], "Test")
        self.assertEqual(values_2026[EXEMPT], "1")


if __name__ == "__main__":
    unittest.main()
