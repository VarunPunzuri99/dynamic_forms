"""Phase 4C: employee live-edition read-path cardinality safety."""

from __future__ import annotations

import os

# Local Postgres cardinality tests; keep C# FormsEngine reads disabled here.
os.environ["FORMS_ENGINE_SOURCE"] = "postgres"

import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from backend.main import app
from backend.store import (
    ReviewError,
    _MULTIPLE_LIVE_EDITIONS_MESSAGE,
    _require_single_live_approved,
    _single_live_approved,
    connect,
    list_editions,
    list_forms,
    live_definition,
)


client = TestClient(app)


def _unique_form(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _insert_edition(cursor, form_id: str, edition: str, status: str, is_live: bool) -> int:
    cursor.execute(
        """
        INSERT INTO form_editions (form_id, edition, storage_path, status, is_live, rules_json)
        VALUES (%s, %s, %s, %s, %s, '[]'::jsonb)
        RETURNING id
        """,
        (form_id, edition, f"docs/{form_id}_{edition}.pdf", status, is_live),
    )
    return cursor.fetchone()["id"]


def _insert_approved_question(cursor, edition_id: int, question_key: str = "q1") -> None:
    cursor.execute(
        """
        INSERT INTO form_questions (
            edition_id, question_key, label, input_type, required, review_status,
            pdf_field, page_number, sort_order, change_type, label_source
        )
        VALUES (%s, %s, %s, 'text', false, 'approved', %s, 1, 1, 'reviewed', 'reviewed')
        """,
        (edition_id, question_key, f"Label {question_key}", question_key),
    )


def _delete_form(form_id: str) -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM form_editions WHERE form_id = %s", (form_id,))
        connection.commit()


def _live_snapshot() -> dict[str, list[int]]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT form_id, id
                FROM form_editions
                WHERE form_id IN ('fw4', 'colorado') AND is_live = TRUE
                ORDER BY form_id, id
                """
            )
            snapshot: dict[str, list[int]] = {"fw4": [], "colorado": []}
            for row in cursor.fetchall():
                snapshot[row["form_id"]].append(row["id"])
            return snapshot


class LiveCardinalityHelperTests(unittest.TestCase):
    def test_zero_rows_returns_none_or_404(self) -> None:
        self.assertIsNone(_single_live_approved([], "demo"))
        with self.assertRaises(ReviewError) as raised:
            _require_single_live_approved([], "demo")
        self.assertEqual(raised.exception.status_code, 404)
        self.assertIn("No approved demo form is live", raised.exception.message)

    def test_one_row_returns_that_edition(self) -> None:
        row = {"id": 7, "edition": "v1"}
        self.assertIs(_single_live_approved([row], "demo"), row)
        self.assertIs(_require_single_live_approved([row], "demo"), row)

    def test_multiple_rows_raise_controlled_409(self) -> None:
        rows = [{"id": 1}, {"id": 2}]
        with self.assertRaises(ReviewError) as raised:
            _single_live_approved(rows, "demo")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.message, _MULTIPLE_LIVE_EDITIONS_MESSAGE)
        with self.assertRaises(ReviewError) as raised_req:
            _require_single_live_approved(rows, "demo")
        self.assertEqual(raised_req.exception.status_code, 409)


class LiveDefinitionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.form_ids: list[str] = []
        self.before_live = _live_snapshot()

    def tearDown(self) -> None:
        for form_id in self.form_ids:
            _delete_form(form_id)
        self.assertEqual(_live_snapshot(), self.before_live)

    def _track(self, form_id: str) -> str:
        self.form_ids.append(form_id)
        return form_id

    def test_no_live_approved_edition_returns_404(self) -> None:
        form_id = self._track(_unique_form("p4c_none"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "v1", "draft", False)
            connection.commit()

        with self.assertRaises(ReviewError) as raised:
            live_definition(form_id)
        self.assertEqual(raised.exception.status_code, 404)

        response = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["errors"][0]["message"],
            f"No approved {form_id} form is live",
        )

    def test_exactly_one_live_approved_edition_succeeds(self) -> None:
        form_id = self._track(_unique_form("p4c_one"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_id = _insert_edition(cursor, form_id, "v1", "approved", True)
                _insert_approved_question(cursor, live_id)
                _insert_edition(cursor, form_id, "v2", "draft", False)
            connection.commit()

        definition = live_definition(form_id)
        self.assertEqual(definition["edition_id"], live_id)
        self.assertEqual(definition["edition"], "v1")
        self.assertEqual(definition["form_id"], form_id)
        self.assertEqual(len(definition["questions"]), 1)

        response = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["edition_id"], live_id)
        self.assertEqual(body["edition"], "v1")
        self.assertEqual(len(body["questions"]), 1)

    def test_multiple_live_approved_editions_return_409_without_arbitrary_choice(self) -> None:
        form_id = self._track(_unique_form("p4c_multi"))
        fake_rows = [
            {
                "id": 101,
                "form_id": form_id,
                "edition": "a",
                "storage_path": "docs/a.pdf",
                "rules_json": [],
            },
            {
                "id": 102,
                "form_id": form_id,
                "edition": "b",
                "storage_path": "docs/b.pdf",
                "rules_json": [],
            },
        ]
        connection = mock.MagicMock()
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = fake_rows

        with mock.patch("backend.store.connect") as connect_mock:
            connect_mock.return_value.__enter__.return_value = connection
            with self.assertRaises(ReviewError) as raised:
                live_definition(form_id)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.message, _MULTIPLE_LIVE_EDITIONS_MESSAGE)
        # No question loading when cardinality is invalid.
        cursor.execute.assert_called()
        executed_sql = " ".join(
            call.args[0] for call in cursor.execute.call_args_list if call.args
        )
        self.assertIn("is_live", executed_sql)
        self.assertNotIn("form_questions", executed_sql.lower())

        with mock.patch(
            "backend.main.live_definition",
            side_effect=ReviewError(_MULTIPLE_LIVE_EDITIONS_MESSAGE, 409),
        ):
            response = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["errors"][0]["message"],
            _MULTIPLE_LIVE_EDITIONS_MESSAGE,
        )
        self.assertNotIn("edition_id", response.json())
        self.assertNotIn("questions", response.json())

    def test_different_forms_remain_isolated(self) -> None:
        form_a = self._track(_unique_form("p4c_iso_a"))
        form_b = self._track(_unique_form("p4c_iso_b"))
        with connect() as connection:
            with connection.cursor() as cursor:
                id_a = _insert_edition(cursor, form_a, "v1", "approved", True)
                id_b = _insert_edition(cursor, form_b, "v1", "approved", True)
                _insert_approved_question(cursor, id_a, "qa")
                _insert_approved_question(cursor, id_b, "qb")
            connection.commit()

        def_a = live_definition(form_a)
        def_b = live_definition(form_b)
        self.assertEqual(def_a["edition_id"], id_a)
        self.assertEqual(def_b["edition_id"], id_b)
        self.assertEqual(def_a["questions"][0]["id"], "qa")
        self.assertEqual(def_b["questions"][0]["id"], "qb")


class ListFormsAndEditionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.form_ids: list[str] = []
        self.before_live = _live_snapshot()

    def tearDown(self) -> None:
        for form_id in self.form_ids:
            _delete_form(form_id)
        self.assertEqual(_live_snapshot(), self.before_live)

    def _track(self, form_id: str) -> str:
        self.form_ids.append(form_id)
        return form_id

    def test_list_forms_returns_single_live_or_null(self) -> None:
        form_live = self._track(_unique_form("p4c_lf_live"))
        form_none = self._track(_unique_form("p4c_lf_none"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_id = _insert_edition(cursor, form_live, "v1", "approved", True)
                _insert_approved_question(cursor, live_id)
                _insert_edition(cursor, form_none, "v1", "draft", False)
            connection.commit()

        forms = {item["form_id"]: item for item in list_forms()}
        self.assertEqual(forms[form_live]["live"]["id"], live_id)
        self.assertIsNone(forms[form_none]["live"])

    def test_list_forms_raises_409_on_multiple_live(self) -> None:
        form_id = "p4c_list_forms_multi"
        connection = mock.MagicMock()
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.side_effect = [
            [{"form_id": form_id}],
            [{"id": 1, "edition": "a"}, {"id": 2, "edition": "b"}],
        ]
        with mock.patch("backend.store.connect") as connect_mock:
            connect_mock.return_value.__enter__.return_value = connection
            with self.assertRaises(ReviewError) as raised:
                list_forms()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.message, _MULTIPLE_LIVE_EDITIONS_MESSAGE)

        with mock.patch("backend.main.list_forms", side_effect=raised.exception):
            response = client.get("/api/forms")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["errors"][0]["message"],
            _MULTIPLE_LIVE_EDITIONS_MESSAGE,
        )

    def test_list_editions_returns_single_live_pointer(self) -> None:
        form_id = self._track(_unique_form("p4c_le_one"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_id = _insert_edition(cursor, form_id, "v1", "approved", True)
                draft_id = _insert_edition(cursor, form_id, "v2", "draft", False)
            connection.commit()

        pack = list_editions(form_id)
        self.assertEqual(pack["live"]["id"], live_id)
        self.assertEqual({row["id"] for row in pack["editions"]}, {live_id, draft_id})

    def test_list_editions_raises_409_on_multiple_live(self) -> None:
        form_id = self._track(_unique_form("p4c_le_multi"))
        editions = [
            {
                "id": 1,
                "edition": "a",
                "status": "approved",
                "is_live": True,
                "storage_path": "a.pdf",
                "compared_to_path": None,
                "question_count": 0,
                "approved_count": 0,
            },
            {
                "id": 2,
                "edition": "b",
                "status": "approved",
                "is_live": True,
                "storage_path": "b.pdf",
                "compared_to_path": None,
                "question_count": 0,
                "approved_count": 0,
            },
        ]
        connection = mock.MagicMock()
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = editions
        with mock.patch("backend.store.connect") as connect_mock:
            connect_mock.return_value.__enter__.return_value = connection
            with self.assertRaises(ReviewError) as raised:
                list_editions(form_id)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.message, _MULTIPLE_LIVE_EDITIONS_MESSAGE)

        with mock.patch("backend.main.list_editions", side_effect=raised.exception):
            response = client.get(f"/api/forms/{form_id}/editions")
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["errors"][0]["message"], _MULTIPLE_LIVE_EDITIONS_MESSAGE)
        self.assertNotIn("editions", body)
        self.assertNotIn("live", body)


if __name__ == "__main__":
    unittest.main()
