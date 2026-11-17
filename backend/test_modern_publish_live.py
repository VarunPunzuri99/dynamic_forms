"""Phase 4D: modern multi-form publish → live API integration verification."""

from __future__ import annotations

import os

# Local Postgres workflow tests; keep C# FormsEngine reads disabled here.
os.environ["FORMS_ENGINE_SOURCE"] = "postgres"

import threading
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from backend.main import app
from backend.store import (
    ReviewError,
    _MULTIPLE_LIVE_EDITIONS_MESSAGE,
    compare_with_edition,
    connect,
    import_live_fields,
    live_definition,
    publish_edition,
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


def _insert_question(
    cursor,
    edition_id: int,
    question_key: str,
    *,
    review_status: str = "needs_review",
    sort_order: int = 1,
) -> None:
    cursor.execute(
        """
        INSERT INTO form_questions (
            edition_id, question_key, label, input_type, required, review_status,
            pdf_field, page_number, sort_order, change_type, label_source
        )
        VALUES (%s, %s, %s, 'text', false, %s, %s, 1, %s, 'reviewed', 'reviewed')
        """,
        (
            edition_id,
            question_key,
            f"Label {question_key}",
            review_status,
            question_key,
            sort_order,
        ),
    )


def _selection(*question_keys: str) -> list[dict]:
    return [
        {"question_key": key, "label": f"Label {key}", "include": True}
        for key in question_keys
    ]


def _sample_rules() -> list[dict]:
    return [
        {
            "id": "r1",
            "when_question": "q1",
            "when_condition": "answered",
            "then_question": "q2",
            "then_effect": "disable",
        }
    ]


def _delete_form(form_id: str) -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM form_editions WHERE form_id = %s", (form_id,))
        connection.commit()


def _live_ids(form_id: str) -> list[int]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM form_editions
                WHERE form_id = %s AND is_live = TRUE
                ORDER BY id
                """,
                (form_id,),
            )
            return [row["id"] for row in cursor.fetchall()]


def _configured_live_snapshot() -> dict[str, list[tuple[int, str]]]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT form_id, id, edition
                FROM form_editions
                WHERE form_id IN ('fw4', 'colorado') AND is_live = TRUE
                ORDER BY form_id, id
                """
            )
            snapshot: dict[str, list[tuple[int, str]]] = {"fw4": [], "colorado": []}
            for row in cursor.fetchall():
                snapshot[row["form_id"]].append((row["id"], row["edition"]))
            return snapshot


def _index_present() -> bool:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_indexes WHERE indexname = %s",
                ("uq_one_live_edition_per_form",),
            )
            return cursor.fetchone() is not None


class ModernPublishLiveIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.form_ids: list[str] = []
        self.before_live = _configured_live_snapshot()

    def tearDown(self) -> None:
        for form_id in self.form_ids:
            _delete_form(form_id)
        self.assertEqual(_configured_live_snapshot(), self.before_live)

    def _track(self, form_id: str) -> str:
        self.form_ids.append(form_id)
        return form_id

    def test_live_api_returns_current_approved_live_edition(self) -> None:
        form_id = self._track(_unique_form("p4d_live"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_id = _insert_edition(cursor, form_id, "v1", "approved", True)
                _insert_question(cursor, live_id, "live_q", review_status="approved")
                _insert_edition(cursor, form_id, "v2", "draft", False)
            connection.commit()

        response = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["edition_id"], live_id)
        self.assertEqual(body["edition"], "v1")
        self.assertEqual([q["id"] for q in body["questions"]], ["live_q"])

    def test_publish_switches_live_and_employee_api_follows(self) -> None:
        form_id = self._track(_unique_form("p4d_switch"))
        with connect() as connection:
            with connection.cursor() as cursor:
                old_id = _insert_edition(cursor, form_id, "v1", "approved", True)
                _insert_question(cursor, old_id, "old_q", review_status="approved")
                draft_id = _insert_edition(cursor, form_id, "v2", "draft", False)
                _insert_question(cursor, draft_id, "q1", sort_order=1)
                _insert_question(cursor, draft_id, "q2", sort_order=2)
            connection.commit()

        rules = _sample_rules()
        publish = client.post(
            f"/api/forms/{form_id}/editions/{draft_id}/publish",
            json={
                "confirm_generated_wording": True,
                "questions": _selection("q1", "q2"),
                "rules": rules,
            },
        )
        self.assertEqual(publish.status_code, 200, publish.text)
        self.assertEqual(publish.json()["edition_id"], draft_id)
        self.assertTrue(publish.json()["is_live"])

        self.assertEqual(_live_ids(form_id), [draft_id])

        live = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(live.status_code, 200)
        body = live.json()
        self.assertEqual(body["edition_id"], draft_id)
        self.assertEqual(body["edition"], "v2")
        self.assertEqual([q["id"] for q in body["questions"]], ["q1", "q2"])
        self.assertEqual(body["rules"][0]["then_effect"], "disable")
        self.assertEqual(body["rules"][0]["then_question"], "q2")
        self.assertEqual(body["rules"][0]["when_question"], "q1")

        editions = client.get(f"/api/forms/{form_id}/editions")
        self.assertEqual(editions.status_code, 200)
        pack = editions.json()
        self.assertEqual(pack["live"]["id"], draft_id)
        by_id = {row["id"]: row for row in pack["editions"]}
        self.assertFalse(by_id[old_id]["is_live"])
        self.assertTrue(by_id[draft_id]["is_live"])
        self.assertEqual(by_id[draft_id]["status"], "approved")

    def test_failed_publish_preserves_previous_live(self) -> None:
        form_id = self._track(_unique_form("p4d_fail"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_id = _insert_edition(cursor, form_id, "v1", "approved", True)
                draft_id = _insert_edition(cursor, form_id, "v2", "draft", False)
                _insert_question(cursor, draft_id, "q1")
            connection.commit()

        response = client.post(
            f"/api/forms/{form_id}/editions/{draft_id}/publish",
            json={"confirm_generated_wording": True, "questions": [], "rules": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_live_ids(form_id), [live_id])
        live = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["edition_id"], live_id)

    def test_concurrent_competing_drafts_leave_one_live(self) -> None:
        form_id = self._track(_unique_form("p4d_race"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "live", "approved", True)
                draft_a = _insert_edition(cursor, form_id, "a", "draft", False)
                draft_b = _insert_edition(cursor, form_id, "b", "draft", False)
                _insert_question(cursor, draft_a, "q1")
                _insert_question(cursor, draft_b, "q1")
            connection.commit()

        barrier = threading.Barrier(2, timeout=10)
        outcomes: list[tuple[str, object]] = []

        def worker(edition_id: int) -> None:
            try:
                barrier.wait()
                result = publish_edition(form_id, edition_id, _selection("q1"), True)
                outcomes.append(("ok", result["edition_id"]))
            except BaseException as exc:  # noqa: BLE001 - collect for assertion
                outcomes.append(("err", exc))

        threads = [
            threading.Thread(target=worker, args=(draft_a,)),
            threading.Thread(target=worker, args=(draft_b,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())

        self.assertEqual(len(outcomes), 2)
        self.assertEqual(len(_live_ids(form_id)), 1)
        live = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(live.status_code, 200)
        self.assertIn(live.json()["edition_id"], (draft_a, draft_b))

    def test_cross_form_publish_isolation(self) -> None:
        form_a = self._track(_unique_form("p4d_xa"))
        form_b = self._track(_unique_form("p4d_xb"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_a = _insert_edition(cursor, form_a, "live", "approved", True)
                live_b = _insert_edition(cursor, form_b, "live", "approved", True)
                draft_a = _insert_edition(cursor, form_a, "next", "draft", False)
                draft_b = _insert_edition(cursor, form_b, "next", "draft", False)
                _insert_question(cursor, draft_a, "qa")
                _insert_question(cursor, draft_b, "qb")
            connection.commit()

        barrier = threading.Barrier(2, timeout=10)
        errors: list[BaseException | None] = [None, None]

        def worker(index: int, form_id: str, edition_id: int, key: str) -> None:
            try:
                barrier.wait()
                publish_edition(form_id, edition_id, _selection(key), True)
            except BaseException as exc:  # noqa: BLE001 - collect for assertion
                errors[index] = exc

        threads = [
            threading.Thread(target=worker, args=(0, form_a, draft_a, "qa")),
            threading.Thread(target=worker, args=(1, form_b, draft_b, "qb")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())

        self.assertEqual(errors, [None, None])
        self.assertEqual(_live_ids(form_a), [draft_a])
        self.assertEqual(_live_ids(form_b), [draft_b])
        self.assertNotEqual(live_a, draft_a)
        self.assertNotEqual(live_b, draft_b)

        live_a_resp = client.get(f"/api/forms/{form_a}/live")
        live_b_resp = client.get(f"/api/forms/{form_b}/live")
        self.assertEqual(live_a_resp.json()["edition_id"], draft_a)
        self.assertEqual(live_b_resp.json()["edition_id"], draft_b)
        self.assertEqual(live_a_resp.json()["questions"][0]["id"], "qa")
        self.assertEqual(live_b_resp.json()["questions"][0]["id"], "qb")

    def test_duplicate_live_read_apis_return_409(self) -> None:
        form_id = self._track(_unique_form("p4d_dup"))
        fake_rows = [
            {
                "id": 11,
                "form_id": form_id,
                "edition": "a",
                "storage_path": "docs/a.pdf",
                "rules_json": [],
            },
            {
                "id": 12,
                "form_id": form_id,
                "edition": "b",
                "storage_path": "docs/b.pdf",
                "rules_json": [],
            },
        ]
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
                from backend.store import list_forms

                list_forms()
        self.assertEqual(raised.exception.status_code, 409)

        # Store-level employee read also refuses arbitrary choice.
        connection2 = mock.MagicMock()
        cursor2 = mock.MagicMock()
        connection2.cursor.return_value.__enter__.return_value = cursor2
        cursor2.fetchall.return_value = fake_rows
        with mock.patch("backend.store.connect") as connect_mock:
            connect_mock.return_value.__enter__.return_value = connection2
            with self.assertRaises(ReviewError) as raised_live:
                live_definition(form_id)
        self.assertEqual(raised_live.exception.status_code, 409)

    def test_no_live_edition_preserves_not_found(self) -> None:
        form_id = self._track(_unique_form("p4d_none"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "v1", "draft", False)
            connection.commit()

        response = client.get(f"/api/forms/{form_id}/live")
        self.assertEqual(response.status_code, 404)
        message = response.json()["errors"][0]["message"].lower()
        self.assertIn("is live", message)
        self.assertIn(form_id.lower(), message)


class ReviewerLiveReadHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.form_ids: list[str] = []
        self.before_live = _configured_live_snapshot()

    def tearDown(self) -> None:
        for form_id in self.form_ids:
            _delete_form(form_id)
        self.assertEqual(_configured_live_snapshot(), self.before_live)

    def _track(self, form_id: str) -> str:
        self.form_ids.append(form_id)
        return form_id

    def test_import_live_fields_rejects_multiple_live(self) -> None:
        form_id = self._track(_unique_form("p4d_imp"))
        connection = mock.MagicMock()
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [
            {"id": 1, "edition": "a", "storage_path": "a.pdf"},
            {"id": 2, "edition": "b", "storage_path": "b.pdf"},
        ]
        with mock.patch("backend.store.connect") as connect_mock:
            connect_mock.return_value.__enter__.return_value = connection
            with self.assertRaises(ReviewError) as raised:
                import_live_fields(form_id)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.message, _MULTIPLE_LIVE_EDITIONS_MESSAGE)

    def test_compare_with_edition_rejects_multiple_live(self) -> None:
        form_id = self._track(_unique_form("p4d_cmp"))
        connection = mock.MagicMock()
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [
            {"id": 1, "edition": "a", "storage_path": "a.pdf"},
            {"id": 2, "edition": "b", "storage_path": "b.pdf"},
        ]
        with mock.patch("backend.store.connect") as connect_mock:
            connect_mock.return_value.__enter__.return_value = connection
            with self.assertRaises(ReviewError) as raised:
                compare_with_edition(form_id, 99)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.message, _MULTIPLE_LIVE_EDITIONS_MESSAGE)

    def test_compare_with_edition_preserves_no_live_message(self) -> None:
        form_id = self._track(_unique_form("p4d_cmp_none"))
        with connect() as connection:
            with connection.cursor() as cursor:
                draft_id = _insert_edition(cursor, form_id, "v1", "draft", False)
            connection.commit()

        with self.assertRaises(ReviewError) as raised:
            compare_with_edition(form_id, draft_id)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(
            raised.exception.message,
            f"Form {form_id} has no live edition to compare from",
        )

    def test_import_live_fields_preserves_no_live_404(self) -> None:
        form_id = self._track(_unique_form("p4d_imp_none"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "v1", "draft", False)
            connection.commit()

        with self.assertRaises(ReviewError) as raised:
            import_live_fields(form_id)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(
            raised.exception.message,
            f"No approved {form_id} form is live",
        )


class Phase4GuardsStillPresentTests(unittest.TestCase):
    def test_unique_index_still_present(self) -> None:
        self.assertTrue(_index_present())

    def test_configured_forms_still_have_one_live_each(self) -> None:
        snapshot = _configured_live_snapshot()
        self.assertEqual(len(snapshot["fw4"]), 1)
        self.assertEqual(len(snapshot["colorado"]), 1)


if __name__ == "__main__":
    unittest.main()
