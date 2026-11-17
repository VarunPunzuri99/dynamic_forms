"""Phase 4B: atomic publishing and one-live-edition-per-form protection."""

from __future__ import annotations

import os

# These tests exercise local Postgres publish locking, not the C# read cutover.
os.environ["FORMS_ENGINE_SOURCE"] = "postgres"

import threading
import time
import unittest
import uuid
from unittest import mock

from psycopg.errors import UniqueViolation

from backend.draft_job import ensure_schema
from backend.store import (
    ReviewError,
    _LIVE_EDITION_UNIQUE_INDEX,
    _LIVE_PUBLISH_CONFLICT_MESSAGE,
    _lock_form_for_publish,
    _raise_if_live_unique_violation,
    approve_draft,
    connect,
    publish_edition,
)


def _unique_form(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _insert_edition(cursor, form_id: str, edition: str, status: str, is_live: bool) -> int:
    cursor.execute(
        """
        INSERT INTO form_editions (form_id, edition, storage_path, status, is_live)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (form_id, edition, f"docs/{form_id}_{edition}.pdf", status, is_live),
    )
    return cursor.fetchone()["id"]


def _insert_question(cursor, edition_id: int, question_key: str = "q1") -> None:
    cursor.execute(
        """
        INSERT INTO form_questions (
            edition_id, question_key, label, input_type, required, review_status,
            pdf_field, page_number, sort_order, change_type, label_source
        )
        VALUES (%s, %s, %s, 'text', false, 'needs_review', %s, 1, 1, 'reviewed', 'reviewed')
        """,
        (edition_id, question_key, f"Label {question_key}", question_key),
    )


def _selection(question_key: str = "q1") -> list[dict]:
    return [{"question_key": question_key, "label": f"Label {question_key}", "include": True}]


def _live_ids(cursor, form_id: str) -> list[int]:
    cursor.execute(
        """
        SELECT id FROM form_editions
        WHERE form_id = %s AND is_live = TRUE
        ORDER BY id
        """,
        (form_id,),
    )
    return [row["id"] for row in cursor.fetchall()]


def _edition_row(cursor, edition_id: int) -> dict:
    cursor.execute(
        "SELECT id, form_id, edition, status, is_live FROM form_editions WHERE id = %s",
        (edition_id,),
    )
    return cursor.fetchone()


def _delete_form(form_id: str) -> None:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM form_editions WHERE form_id = %s", (form_id,))
        connection.commit()


class PublishAtomicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with connect() as connection:
            ensure_schema(connection)
            connection.commit()
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT indexname FROM pg_indexes WHERE indexname = %s",
                    (_LIVE_EDITION_UNIQUE_INDEX,),
                )
                row = cursor.fetchone()
        if not row:
            raise unittest.SkipTest(
                f"Required index {_LIVE_EDITION_UNIQUE_INDEX} was not created"
            )

    def setUp(self) -> None:
        self.form_ids: list[str] = []

    def tearDown(self) -> None:
        for form_id in self.form_ids:
            _delete_form(form_id)

    def _track(self, form_id: str) -> str:
        self.form_ids.append(form_id)
        return form_id

    def test_publish_deactivates_previous_live_and_activates_target(self) -> None:
        form_id = self._track(_unique_form("p4b_seq"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_id = _insert_edition(cursor, form_id, "v1", "approved", True)
                draft_id = _insert_edition(cursor, form_id, "v2", "draft", False)
                _insert_question(cursor, draft_id)
            connection.commit()

        result = publish_edition(form_id, draft_id, _selection(), True)
        self.assertEqual(result["edition_id"], draft_id)
        self.assertTrue(result["is_live"])
        self.assertEqual(result["status"], "approved")

        with connect() as connection:
            with connection.cursor() as cursor:
                self.assertEqual(_live_ids(cursor, form_id), [draft_id])
                previous = _edition_row(cursor, live_id)
                target = _edition_row(cursor, draft_id)
        self.assertFalse(previous["is_live"])
        self.assertEqual(previous["status"], "approved")
        self.assertTrue(target["is_live"])
        self.assertEqual(target["status"], "approved")

    def test_failed_publish_rolls_back_and_preserves_live(self) -> None:
        form_id = self._track(_unique_form("p4b_fail"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_id = _insert_edition(cursor, form_id, "v1", "approved", True)
                draft_id = _insert_edition(cursor, form_id, "v2", "draft", False)
                _insert_question(cursor, draft_id)
            connection.commit()

        with self.assertRaises(ReviewError):
            publish_edition(form_id, draft_id, [], True)

        with connect() as connection:
            with connection.cursor() as cursor:
                self.assertEqual(_live_ids(cursor, form_id), [live_id])
                draft = _edition_row(cursor, draft_id)
        self.assertEqual(draft["status"], "draft")
        self.assertFalse(draft["is_live"])

    def test_unique_index_rejects_two_live_editions_same_form(self) -> None:
        form_id = self._track(_unique_form("p4b_uq"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "v1", "approved", True)
                second_id = _insert_edition(cursor, form_id, "v2", "approved", False)
            connection.commit()

        with connect() as connection:
            with connection.cursor() as cursor:
                with self.assertRaises(UniqueViolation) as raised:
                    cursor.execute(
                        "UPDATE form_editions SET is_live = TRUE WHERE id = %s",
                        (second_id,),
                    )
                self.assertEqual(
                    raised.exception.diag.constraint_name,
                    _LIVE_EDITION_UNIQUE_INDEX,
                )
            connection.rollback()

    def test_different_forms_can_each_have_a_live_edition(self) -> None:
        form_a = self._track(_unique_form("p4b_a"))
        form_b = self._track(_unique_form("p4b_b"))
        with connect() as connection:
            with connection.cursor() as cursor:
                id_a = _insert_edition(cursor, form_a, "v1", "approved", True)
                id_b = _insert_edition(cursor, form_b, "v1", "approved", True)
            connection.commit()

        with connect() as connection:
            with connection.cursor() as cursor:
                self.assertEqual(_live_ids(cursor, form_a), [id_a])
                self.assertEqual(_live_ids(cursor, form_b), [id_b])

    def test_repeated_publish_of_approved_edition_is_rejected(self) -> None:
        form_id = self._track(_unique_form("p4b_rep"))
        with connect() as connection:
            with connection.cursor() as cursor:
                draft_id = _insert_edition(cursor, form_id, "v1", "draft", False)
                _insert_question(cursor, draft_id)
            connection.commit()

        publish_edition(form_id, draft_id, _selection(), True)
        with self.assertRaises(ReviewError) as raised:
            publish_edition(form_id, draft_id, _selection(), True)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("already approved", raised.exception.message)

    def test_unique_violation_maps_to_controlled_conflict(self) -> None:
        form_id = self._track(_unique_form("p4b_map"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "v1", "approved", True)
                second_id = _insert_edition(cursor, form_id, "v2", "approved", False)
            connection.commit()

        with connect() as connection:
            with connection.cursor() as cursor:
                with self.assertRaises(UniqueViolation) as raised_uq:
                    cursor.execute(
                        "UPDATE form_editions SET is_live = TRUE WHERE id = %s",
                        (second_id,),
                    )
            connection.rollback()

        with self.assertRaises(ReviewError) as raised:
            _raise_if_live_unique_violation(raised_uq.exception)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.message, _LIVE_PUBLISH_CONFLICT_MESSAGE)

        other = UniqueViolation("other constraint")
        with mock.patch.object(
            UniqueViolation,
            "diag",
            new_callable=mock.PropertyMock,
            return_value=mock.Mock(constraint_name="some_other_constraint"),
        ):
            with self.assertRaises(UniqueViolation) as raised_other:
                _raise_if_live_unique_violation(other)
        self.assertIs(raised_other.exception, other)

    def test_legacy_approve_draft_uses_form_lock(self) -> None:
        with mock.patch("backend.store._lock_form_for_publish") as lock_mock:
            with mock.patch("backend.store.connect") as connect_mock:
                connection = mock.MagicMock()
                cursor = mock.MagicMock()
                connect_mock.return_value.__enter__.return_value = connection
                connection.cursor.return_value.__enter__.return_value = cursor
                cursor.fetchone.return_value = None
                with self.assertRaises(ReviewError):
                    approve_draft(_selection(), True)
                lock_mock.assert_called_once()
                self.assertEqual(lock_mock.call_args.args[1], "fw4")

    def test_concurrent_publish_same_form_leaves_one_live(self) -> None:
        form_id = self._track(_unique_form("p4b_conc"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "live", "approved", True)
                draft_a = _insert_edition(cursor, form_id, "a", "draft", False)
                draft_b = _insert_edition(cursor, form_id, "b", "draft", False)
                _insert_question(cursor, draft_a)
                _insert_question(cursor, draft_b)
            connection.commit()

        barrier = threading.Barrier(2, timeout=10)
        results: list[object] = [None, None]
        errors: list[BaseException | None] = [None, None]

        def worker(index: int, edition_id: int) -> None:
            try:
                barrier.wait()
                results[index] = publish_edition(form_id, edition_id, _selection(), True)
            except BaseException as exc:  # noqa: BLE001 - collect for assertion
                errors[index] = exc

        threads = [
            threading.Thread(target=worker, args=(0, draft_a)),
            threading.Thread(target=worker, args=(1, draft_b)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())

        successes = [result for result in results if isinstance(result, dict)]
        failures = [error for error in errors if error is not None]
        self.assertEqual(len(successes) + len(failures), 2)
        self.assertGreaterEqual(len(successes), 1)
        for error in failures:
            self.assertIsInstance(error, ReviewError)

        with connect() as connection:
            with connection.cursor() as cursor:
                live = _live_ids(cursor, form_id)
                rows = []
                for edition_id in (draft_a, draft_b):
                    rows.append(_edition_row(cursor, edition_id))
        self.assertEqual(len(live), 1)
        self.assertIn(live[0], (draft_a, draft_b))
        approved_live = [row for row in rows if row["is_live"]]
        self.assertEqual(len(approved_live), 1)
        self.assertEqual(approved_live[0]["status"], "approved")

    def test_concurrent_publish_different_forms_do_not_interfere(self) -> None:
        form_a = self._track(_unique_form("p4b_xa"))
        form_b = self._track(_unique_form("p4b_xb"))
        with connect() as connection:
            with connection.cursor() as cursor:
                live_a = _insert_edition(cursor, form_a, "live", "approved", True)
                live_b = _insert_edition(cursor, form_b, "live", "approved", True)
                draft_a = _insert_edition(cursor, form_a, "next", "draft", False)
                draft_b = _insert_edition(cursor, form_b, "next", "draft", False)
                _insert_question(cursor, draft_a)
                _insert_question(cursor, draft_b)
            connection.commit()

        barrier = threading.Barrier(2, timeout=10)
        errors: list[BaseException | None] = [None, None]

        def worker(index: int, form_id: str, edition_id: int) -> None:
            try:
                barrier.wait()
                publish_edition(form_id, edition_id, _selection(), True)
            except BaseException as exc:  # noqa: BLE001 - collect for assertion
                errors[index] = exc

        threads = [
            threading.Thread(target=worker, args=(0, form_a, draft_a)),
            threading.Thread(target=worker, args=(1, form_b, draft_b)),
        ]
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
        elapsed = time.perf_counter() - started

        self.assertEqual(errors, [None, None])
        with connect() as connection:
            with connection.cursor() as cursor:
                self.assertEqual(_live_ids(cursor, form_a), [draft_a])
                self.assertEqual(_live_ids(cursor, form_b), [draft_b])
                self.assertFalse(_edition_row(cursor, live_a)["is_live"])
                self.assertFalse(_edition_row(cursor, live_b)["is_live"])
        # Cross-form publishes should not need to wait on each other long.
        self.assertLess(elapsed, 20.0)

    def test_legacy_path_cannot_bypass_unique_index(self) -> None:
        """Any writer, including legacy SQL patterns, is blocked by the index."""
        form_id = self._track(_unique_form("p4b_leg"))
        with connect() as connection:
            with connection.cursor() as cursor:
                _insert_edition(cursor, form_id, "v1", "approved", True)
                second = _insert_edition(cursor, form_id, "v2", "draft", False)
            connection.commit()

        # Legacy clear-all-then-set without lock can still race; the index is the
        # final integrity gate for any code path that writes is_live.
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE form_editions SET is_live = false WHERE form_id = %s",
                    (form_id,),
                )
                cursor.execute(
                    """
                    UPDATE form_editions
                    SET status = 'approved', is_live = true
                    WHERE id = %s
                    """,
                    (second,),
                )
                # Simulate a second writer that skipped the clear step.
                with self.assertRaises(UniqueViolation) as raised:
                    cursor.execute(
                        """
                        INSERT INTO form_editions (form_id, edition, storage_path, status, is_live)
                        VALUES (%s, %s, %s, 'approved', TRUE)
                        """,
                        (form_id, "v3", f"docs/{form_id}_v3.pdf"),
                    )
                self.assertEqual(
                    raised.exception.diag.constraint_name,
                    _LIVE_EDITION_UNIQUE_INDEX,
                )
            connection.rollback()

        with connect() as connection:
            with connection.cursor() as cursor:
                # Rolled back: original live row is unchanged.
                self.assertEqual(len(_live_ids(cursor, form_id)), 1)


class LockHelperTests(unittest.TestCase):
    def test_lock_helper_uses_form_scoped_advisory_lock(self) -> None:
        cursor = mock.MagicMock()
        _lock_form_for_publish(cursor, "colorado")
        sql = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertEqual(params[1], "colorado")


if __name__ == "__main__":
    unittest.main()
