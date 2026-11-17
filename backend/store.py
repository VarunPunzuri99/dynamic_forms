"""Read and update form editions stored in PostgreSQL.

The 2025 edition is the reviewed live form until the 2026 draft is approved.
The comparison draft already in the database is kept until the job runs again
or the reviewer replaces it with the checked 2026 wording.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Json

from backend.draft_job import ROOT, build_rows, ensure_schema, input_type, load_env, save_draft, widgets
from backend.field_locate import enrich_rows_with_locators, pdf_field_for_row, render_field_highlight
from backend.filler import load_definition
from backend.forms_engine import (
    FormsEngineError,
    approve_edition_fields as csharp_approve_edition_fields,
    compare_with_edition as csharp_compare_with_edition,
    ensure_edition_pdf_file,
    get_edition_detail,
    import_live_fields as csharp_import_live_fields,
    list_editions as csharp_list_editions,
    list_forms as csharp_list_forms,
    live_definition as csharp_live_definition,
    publish_edition as csharp_publish_edition,
    uses_csharp,
)
from backend.rules import RuleError, labels_look_conflicting, validate_rules

YEAR_FILES = {
    "2025": ROOT / "forms" / "fw4_2025_questions.json",
    "2026": ROOT / "forms" / "fw4_2026_questions.json",
}
SAMPLE_FILES = {
    "2025": ROOT / "experiments" / "sample_answers_2025.json",
    "2026": ROOT / "experiments" / "sample_answers.json",
}
EXCLUDED_LEAVES = {"f1_05[0]", "f1_12[0]", "f1_13[0]", "f1_14[0]", "f1_15[0]"}


class ReviewError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _from_forms_engine_error(exc: FormsEngineError) -> ReviewError:
    return ReviewError(exc.message, exc.status_code)


# Namespace for transaction-scoped form publication advisory locks.
_FORM_PUBLISH_LOCK_NS = 874201
_LIVE_EDITION_UNIQUE_INDEX = "uq_one_live_edition_per_form"
_LIVE_PUBLISH_CONFLICT_MESSAGE = (
    "Another edition may already have been published for this form. Refresh and try again."
)
_MULTIPLE_LIVE_EDITIONS_MESSAGE = (
    "This form has more than one live edition. An administrator or reviewer must resolve "
    "the conflict before the form can be used."
)


def connect() -> psycopg.Connection:
    load_env()
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _lock_form_for_publish(cursor, form_id: str) -> None:
    """Serialize publication for one form_id for the rest of this transaction."""
    cursor.execute(
        "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
        (_FORM_PUBLISH_LOCK_NS, form_id),
    )


def _raise_if_live_unique_violation(exc: UniqueViolation) -> None:
    constraint = getattr(exc.diag, "constraint_name", None) if exc.diag else None
    if constraint == _LIVE_EDITION_UNIQUE_INDEX:
        raise ReviewError(_LIVE_PUBLISH_CONFLICT_MESSAGE, 409) from exc
    raise exc


def _single_live_approved(rows: list, form_id: str) -> dict | None:
    """Return the sole live approved edition, None if none, or raise 409 if multiple."""
    if len(rows) > 1:
        raise ReviewError(_MULTIPLE_LIVE_EDITIONS_MESSAGE, 409)
    return rows[0] if rows else None


def _require_single_live_approved(rows: list, form_id: str) -> dict:
    edition = _single_live_approved(rows, form_id)
    if not edition:
        raise ReviewError(f"No approved {form_id} form is live", 404)
    return edition


def ensure_ready() -> None:
    with connect() as connection:
        ensure_schema(connection)
        _seed_edition(connection, "2025", review_status="approved", change_type="reviewed")
        _ensure_live_edition(connection)
        connection.commit()


def _seed_edition(connection, edition: str, review_status: str, change_type: str) -> None:
    definition = load_definition(YEAR_FILES[edition])
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM form_editions WHERE form_id = %s AND edition = %s",
            ("fw4", edition),
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                "SELECT count(*) AS count FROM form_questions WHERE edition_id = %s",
                (existing["id"],),
            )
            if cursor.fetchone()["count"]:
                return
            edition_id = existing["id"]
        else:
            cursor.execute(
                """
                INSERT INTO form_editions (form_id, edition, storage_path, status, is_live)
                VALUES ('fw4', %s, %s, %s, false)
                RETURNING id
                """,
                (edition, definition["source_pdf"], "approved" if review_status == "approved" else "draft"),
            )
            edition_id = cursor.fetchone()["id"]
        _insert_definition(cursor, edition_id, definition, review_status, change_type)


def _insert_definition(cursor, edition_id: int, definition: dict, review_status: str, change_type: str) -> None:
    for order, question in enumerate(definition["questions"], start=1):
        options = question.get("options")
        attention = question.get("status") if question.get("status") == "needs_review" else None
        cursor.execute(
            """
            INSERT INTO form_questions (
                edition_id, question_key, label, input_type, required, review_status,
                pdf_field, checked_value, page_number, sort_order, change_type,
                options_json, attention, max_length, label_source
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s)
            """,
            (
                edition_id,
                question["id"],
                question["label"],
                question["type"],
                bool(question.get("required")),
                review_status,
                question.get("pdf_field"),
                question.get("checked_value"),
                order,
                change_type,
                Json(options) if options else None,
                attention,
                question.get("max_length"),
                "reviewed" if change_type == "reviewed" else "extracted",
            ),
        )


def _ensure_live_edition(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM form_editions WHERE form_id = 'fw4' AND is_live LIMIT 1"
        )
        if cursor.fetchone():
            return
        cursor.execute(
            """
            UPDATE form_editions
            SET is_live = true
            WHERE form_id = 'fw4' AND edition = '2025' AND status = 'approved'
            """
        )


def list_forms() -> list[dict]:
    if uses_csharp():
        try:
            return csharp_list_forms()
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT form_id FROM form_editions ORDER BY form_id")
            form_ids = [row["form_id"] for row in cursor.fetchall()]
            forms = []
            for form_id in form_ids:
                cursor.execute(
                    """
                    SELECT id, edition, storage_path,
                           (SELECT count(*) FROM form_questions q
                            WHERE q.edition_id = e.id AND q.review_status = 'approved') AS question_count
                    FROM form_editions e
                    WHERE form_id = %s AND is_live AND status = 'approved'
                    ORDER BY id
                    """,
                    (form_id,),
                )
                live = _single_live_approved(cursor.fetchall(), form_id)
                forms.append({"form_id": form_id, "live": live})
    return forms


def list_editions(form_id: str) -> dict:
    if uses_csharp():
        try:
            return csharp_list_editions(form_id)
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, edition, status, is_live, storage_path, compared_to_path,
                       (SELECT count(*) FROM form_questions q WHERE q.edition_id = e.id) AS question_count,
                       (SELECT count(*) FROM form_questions q
                        WHERE q.edition_id = e.id AND q.review_status = 'approved') AS approved_count
                FROM form_editions e
                WHERE form_id = %s
                ORDER BY edition
                """,
                (form_id,),
            )
            editions = cursor.fetchall()
    if not editions:
        raise ReviewError(f"Form {form_id} has no editions", 404)
    live_rows = [
        edition
        for edition in editions
        if edition["is_live"] and edition["status"] == "approved"
    ]
    live = _single_live_approved(live_rows, form_id)
    return {"form_id": form_id, "live": live, "editions": editions}


def live_definition(form_id: str = "fw4") -> dict:
    if uses_csharp():
        try:
            return csharp_live_definition(form_id)
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, form_id, edition, storage_path, rules_json
                FROM form_editions
                WHERE form_id = %s AND is_live AND status = 'approved'
                ORDER BY id
                """,
                (form_id,),
            )
            edition = _require_single_live_approved(cursor.fetchall(), form_id)
            questions = _questions_for(cursor, edition["id"], published_only=True)
            cursor.execute(
                """
                SELECT id, edition, status,
                       (SELECT count(*) FROM form_questions q WHERE q.edition_id = e.id) AS question_count
                FROM form_editions e
                WHERE form_id = %s AND status = 'draft'
                ORDER BY edition DESC
                LIMIT 1
                """,
                (form_id,),
            )
            draft = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE review_status = 'needs_review') AS unreviewed_count,
                    count(*) AS stored_question_count
                FROM form_questions
                WHERE edition_id = %s
                """,
                (edition["id"],),
            )
            counts = cursor.fetchone()
    definition = _definition_from(edition, questions)
    definition["edition_id"] = edition["id"]
    definition["pending_draft"] = draft
    definition["unreviewed_count"] = counts["unreviewed_count"]
    definition["stored_question_count"] = counts["stored_question_count"]
    return definition


def import_live_fields(form_id: str) -> dict:
    if uses_csharp():
        try:
            return csharp_import_live_fields(form_id)
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, edition, storage_path
                FROM form_editions
                WHERE form_id = %s AND is_live AND status = 'approved'
                ORDER BY id
                """,
                (form_id,),
            )
            edition = _require_single_live_approved(cursor.fetchall(), form_id)
            cursor.execute(
                "SELECT count(*) AS count FROM form_questions WHERE edition_id = %s",
                (edition["id"],),
            )
            if cursor.fetchone()["count"]:
                raise ReviewError(
                    "This edition already has fields. Review those fields instead of reading the PDF again.",
                    409,
                )
        pdf_path = ROOT / edition["storage_path"]
        if not pdf_path.is_file():
            raise ReviewError("The PDF file is missing from its stored path", 404)
        extracted = widgets(pdf_path)
        if not extracted:
            raise ReviewError("No fillable fields were found in that PDF")
        seen: set[str] = set()
        with connection.cursor() as cursor:
            for order, row in enumerate(extracted, start=1):
                question_key = row["name"] or f"field-{order}"
                if question_key in seen:
                    question_key = f"{question_key}#{order}"
                seen.add(question_key)
                cursor.execute(
                    """
                    INSERT INTO form_questions (
                        edition_id, question_key, label, input_type, required,
                        review_status, pdf_field, checked_value, page_number,
                        sort_order, change_type
                    )
                    VALUES (%s, %s, %s, %s, false, 'needs_review', %s, %s, %s, %s, 'imported')
                    """,
                    (
                        edition["id"],
                        question_key,
                        row["label"],
                        input_type(row["type"]),
                        row["name"] or None,
                        row["checked_value"],
                        row["page"],
                        order,
                    ),
                )
        connection.commit()
    return {
        "form_id": form_id,
        "edition_id": edition["id"],
        "edition": edition["edition"],
        "storage_path": edition["storage_path"],
        "question_count": len(extracted),
    }


def _save_question_selection(
    cursor,
    edition_id: int,
    items: list[dict],
    confirm_generated_wording: bool,
    rules: object = None,
) -> int:
    if not isinstance(items, list) or not items:
        raise ReviewError("Choose at least one question to publish")
    included = [item for item in items if item.get("include")]
    if not included:
        raise ReviewError("Choose at least one question to publish")
    keys = [item.get("question_key") for item in items]
    cursor.execute(
        """
        SELECT question_key, change_type, input_type, required, pdf_field,
               checked_value, options_json, attention, max_length
        FROM form_questions
        WHERE edition_id = %s AND question_key = ANY(%s)
        """,
        (edition_id, keys),
    )
    known_rows = {row["question_key"]: row for row in cursor.fetchall()}
    if len(known_rows) != len(set(keys)):
        raise ReviewError("The fields changed. Reload the page and try again.")
    generated = [
        item["question_key"]
        for item in included
        if known_rows[item["question_key"]]["change_type"] != "reviewed"
    ]
    if generated and not confirm_generated_wording:
        raise ReviewError(
            "These rows still use wording taken from the PDF. Confirm that wording before publishing."
        )
    included_questions = []
    for item in items:
        label = " ".join(str(item.get("label") or "").split())
        if item.get("include") and not label:
            raise ReviewError("An included question needs wording")
        row = known_rows[item["question_key"]]
        label_source = "reviewed" if item.get("include") else row.get("change_type") or "extracted"
        if item.get("include") and known_rows[item["question_key"]]["change_type"] != "reviewed":
            label_source = "reviewed"
        cursor.execute(
            """
            UPDATE form_questions
            SET label = %s, review_status = %s, label_source = %s
            WHERE edition_id = %s AND question_key = %s
            """,
            (
                label,
                "approved" if item.get("include") else "removed",
                label_source if item.get("include") else "extracted",
                edition_id,
                item["question_key"],
            ),
        )
        if item.get("include"):
            included_questions.append(
                _to_question(
                    {
                        **row,
                        "question_key": item["question_key"],
                        "label": label,
                        "review_status": "approved",
                    }
                )
            )
    try:
        cleaned_rules = validate_rules(rules if rules is not None else [], included_questions)
    except RuleError as exc:
        raise ReviewError(str(exc)) from exc
    cursor.execute(
        "UPDATE form_editions SET rules_json = %s WHERE id = %s",
        (Json(cleaned_rules), edition_id),
    )
    return len(included)


def approve_edition_fields(
    form_id: str,
    edition_id: int,
    items: list[dict],
    confirm_generated_wording: bool,
    rules: object = None,
) -> dict:
    if uses_csharp():
        try:
            return csharp_approve_edition_fields(
                form_id, edition_id, items, confirm_generated_wording, rules
            )
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, edition
                FROM form_editions
                WHERE id = %s AND form_id = %s
                """,
                (edition_id, form_id),
            )
            edition = cursor.fetchone()
            if not edition:
                raise ReviewError("That edition is not on this form", 404)
            published = _save_question_selection(
                cursor, edition_id, items, confirm_generated_wording, rules
            )
        connection.commit()
    return {"form_id": form_id, "edition_id": edition_id, "edition": edition["edition"], "published_questions": published}


def publish_edition(
    form_id: str,
    edition_id: int,
    items: list[dict],
    confirm_generated_wording: bool,
    rules: object = None,
) -> dict:
    if uses_csharp():
        try:
            return csharp_publish_edition(
                form_id, edition_id, items, confirm_generated_wording, rules
            )
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                _lock_form_for_publish(cursor, form_id)
                cursor.execute(
                    """
                    SELECT id, edition, status
                    FROM form_editions
                    WHERE id = %s AND form_id = %s
                    """,
                    (edition_id, form_id),
                )
                edition = cursor.fetchone()
                if not edition:
                    raise ReviewError("That edition is not on this form", 404)
                if edition["status"] != "draft":
                    raise ReviewError(
                        "That edition is already approved, so it was not made live", 409
                    )
                published = _save_question_selection(
                    cursor, edition_id, items, confirm_generated_wording, rules
                )
                cursor.execute(
                    "UPDATE form_editions SET is_live = false WHERE form_id = %s",
                    (form_id,),
                )
                cursor.execute(
                    """
                    UPDATE form_editions
                    SET status = 'approved', is_live = true
                    WHERE id = %s AND form_id = %s
                    """,
                    (edition_id, form_id),
                )
            connection.commit()
    except UniqueViolation as exc:
        _raise_if_live_unique_violation(exc)
    return {
        "form_id": form_id,
        "edition_id": edition_id,
        "edition": edition["edition"],
        "status": "approved",
        "is_live": True,
        "published_questions": published,
    }


def sample_answers_for_live(form_id: str = "fw4") -> dict:
    definition = live_definition(form_id)
    sample_path = SAMPLE_FILES.get(definition["edition"]) if form_id == "fw4" else None
    if sample_path is None or not sample_path.is_file():
        return {}
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    allowed = {question["id"] for question in definition["questions"]}
    return {key: value for key, value in sample.items() if key in allowed}


def draft_review() -> dict:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, edition, status, storage_path, compared_to_path, is_live
                FROM form_editions
                WHERE form_id = 'fw4' AND edition = '2026'
                """
            )
            edition = cursor.fetchone()
            if not edition:
                raise ReviewError("The 2026 draft does not exist yet. Run the comparison first.", 404)
            cursor.execute(
                """
                SELECT question_key, label, input_type, change_type, review_status,
                       pdf_field, page_number, sort_order, attention
                FROM form_questions
                WHERE edition_id = %s
                ORDER BY sort_order
                """,
                (edition["id"],),
            )
            rows = cursor.fetchall()
    questions = _with_suggested_include([dict(row) for row in rows])
    return {"edition": edition, "questions": questions}


def compare_with_edition(form_id: str, target_edition_id: int) -> dict:
    if uses_csharp():
        try:
            return csharp_compare_with_edition(form_id, target_edition_id)
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, edition, storage_path
                FROM form_editions
                WHERE form_id = %s AND is_live AND status = 'approved'
                ORDER BY id
                """,
                (form_id,),
            )
            try:
                current = _require_single_live_approved(cursor.fetchall(), form_id)
            except ReviewError as exc:
                if exc.status_code == 404:
                    raise ReviewError(
                        f"Form {form_id} has no live edition to compare from", 404
                    ) from exc
                raise
            cursor.execute(
                """
                SELECT id, edition, storage_path, status
                FROM form_editions
                WHERE id = %s AND form_id = %s
                """,
                (target_edition_id, form_id),
            )
            target = cursor.fetchone()
            if not target:
                raise ReviewError("That PDF is not an edition of this form", 404)
            if target["id"] == current["id"]:
                raise ReviewError("Choose a different PDF from the current one")
            if target["status"] != "draft":
                raise ReviewError(
                    "That edition is already approved, so its questions were not replaced",
                    409,
                )
            live_labels = _approved_labels_by_pdf_field(cursor, current["id"])
        old_pdf = ROOT / current["storage_path"]
        new_pdf = ROOT / target["storage_path"]
        if not old_pdf.is_file() or not new_pdf.is_file():
            raise ReviewError("One of the PDF files is missing from its stored path", 404)
        rows = build_rows(widgets(old_pdf), widgets(new_pdf))
        carried = _replace_questions(
            connection, target["id"], current["storage_path"], rows, live_labels
        )
        connection.commit()
    counts = {"added": 0, "unchanged": 0, "removed": 0, "carried_labels": carried}
    for row in rows:
        counts[row["change_type"]] += 1
    return {
        "form_id": form_id,
        "edition_id": target["id"],
        "edition": target["edition"],
        "compared_to_path": current["storage_path"],
        "storage_path": target["storage_path"],
        "counts": counts,
        "question_count": len(rows),
    }


def edition_questions(form_id: str, edition_id: int) -> dict:
    if uses_csharp():
        try:
            detail = get_edition_detail(form_id, edition_id)
            edition = detail["edition"]
            rows = detail["questions"]
            rules = detail["rules"]
            questions = _with_suggested_include([dict(row) for row in rows])
            for item in questions:
                item["label_conflict"] = bool(
                    item.get("label_source") == "carried"
                    and item.get("prior_label")
                    and labels_look_conflicting(
                        item.get("label") or "", item.get("prior_label") or ""
                    )
                )
            current_pdf = ensure_edition_pdf_file(form_id, edition_id)
            compared_pdf = None
            if edition.get("compared_to_path"):
                # Resolve compared PDF through the sibling edition when possible.
                pack = csharp_list_editions(form_id)
                compared = next(
                    (
                        row
                        for row in pack["editions"]
                        if row.get("storage_path") == edition.get("compared_to_path")
                    ),
                    None,
                )
                if compared:
                    compared_pdf = ensure_edition_pdf_file(form_id, int(compared["id"]))
            questions = enrich_rows_with_locators(questions, current_pdf, compared_pdf)
            return {"edition": edition, "questions": questions, "rules": rules}
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, form_id, edition, status, storage_path, compared_to_path, is_live, rules_json
                FROM form_editions
                WHERE id = %s AND form_id = %s
                """,
                (edition_id, form_id),
            )
            edition = cursor.fetchone()
            if not edition:
                raise ReviewError("That edition is not on this form", 404)
            cursor.execute(
                """
                SELECT question_key, label, input_type, change_type, review_status,
                       pdf_field, page_number, sort_order, attention, label_source, prior_label,
                       required, checked_value, options_json, max_length
                FROM form_questions
                WHERE edition_id = %s
                ORDER BY sort_order
                """,
                (edition_id,),
            )
            rows = cursor.fetchall()
    questions = _with_suggested_include([dict(row) for row in rows])
    for item in questions:
        item["label_conflict"] = bool(
            item.get("label_source") == "carried"
            and item.get("prior_label")
            and labels_look_conflicting(item.get("label") or "", item.get("prior_label") or "")
        )
    compared = ROOT / edition["compared_to_path"] if edition.get("compared_to_path") else None
    questions = enrich_rows_with_locators(
        questions,
        ROOT / edition["storage_path"],
        compared if compared and compared.is_file() else None,
    )
    rules = edition.pop("rules_json", None) or []
    return {"edition": edition, "questions": questions, "rules": rules}


def edition_field_highlight(form_id: str, edition_id: int, question_key: str) -> dict:
    if uses_csharp():
        try:
            detail = get_edition_detail(form_id, edition_id)
            edition = detail["edition"]
            row = next(
                (item for item in detail["questions"] if item.get("question_key") == question_key),
                None,
            )
            if not row:
                raise ReviewError("That field is not on this edition", 404)
            field_name = pdf_field_for_row(row)
            if not field_name:
                raise ReviewError("That row has no PDF field to highlight", 404)
            if row.get("change_type") == "removed" and edition.get("compared_to_path"):
                pack = csharp_list_editions(form_id)
                compared = next(
                    (
                        item
                        for item in pack["editions"]
                        if item.get("storage_path") == edition.get("compared_to_path")
                    ),
                    None,
                )
                if not compared:
                    raise ReviewError("The PDF file is missing from its stored path", 404)
                pdf_path = ensure_edition_pdf_file(form_id, int(compared["id"]))
                source = edition["compared_to_path"]
            else:
                pdf_path = ensure_edition_pdf_file(form_id, edition_id)
                source = edition["storage_path"]
            try:
                preview = render_field_highlight(pdf_path, field_name, row.get("page_number"))
            except KeyError as exc:
                raise ReviewError(
                    "That PDF field could not be found in the blank form", 404
                ) from exc
            preview["question_key"] = question_key
            preview["storage_path"] = source
            preview["change_type"] = row.get("change_type")
            return preview
        except FormsEngineError as exc:
            raise _from_forms_engine_error(exc) from exc
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, form_id, edition, storage_path, compared_to_path
                FROM form_editions
                WHERE id = %s AND form_id = %s
                """,
                (edition_id, form_id),
            )
            edition = cursor.fetchone()
            if not edition:
                raise ReviewError("That edition is not on this form", 404)
            cursor.execute(
                """
                SELECT question_key, pdf_field, page_number, change_type
                FROM form_questions
                WHERE edition_id = %s AND question_key = %s
                """,
                (edition_id, question_key),
            )
            row = cursor.fetchone()
            if not row:
                raise ReviewError("That field is not on this edition", 404)
    field_name = pdf_field_for_row(row)
    if not field_name:
        raise ReviewError("That row has no PDF field to highlight", 404)
    if row.get("change_type") == "removed" and edition.get("compared_to_path"):
        pdf_path = ROOT / edition["compared_to_path"]
        source = edition["compared_to_path"]
    else:
        pdf_path = ROOT / edition["storage_path"]
        source = edition["storage_path"]
    if not pdf_path.is_file():
        raise ReviewError("The PDF file is missing from its stored path", 404)
    try:
        preview = render_field_highlight(pdf_path, field_name, row.get("page_number"))
    except KeyError as exc:
        raise ReviewError("That PDF field could not be found in the blank form", 404) from exc
    preview["question_key"] = question_key
    preview["storage_path"] = source
    preview["change_type"] = row.get("change_type")
    return preview


def _approved_labels_by_pdf_field(cursor, edition_id: int) -> dict[str, dict]:
    cursor.execute(
        """
        SELECT question_key, label, pdf_field, input_type, options_json
        FROM form_questions
        WHERE edition_id = %s AND review_status = 'approved' AND pdf_field IS NOT NULL
        """,
        (edition_id,),
    )
    mapping: dict[str, dict] = {}
    for row in cursor.fetchall():
        mapping[row["pdf_field"]] = {
            "question_key": row["question_key"],
            "label": row["label"],
            "input_type": row["input_type"],
        }
        for option in row["options_json"] or []:
            field = option.get("pdf_field")
            if field:
                mapping[field] = {
                    "question_key": row["question_key"],
                    "label": row["label"],
                    "input_type": row["input_type"],
                }
    return mapping


def _apply_carried_label(row: dict, live_labels: dict[str, dict]) -> tuple[str, str | None, str, str | None]:
    extracted = row["label"]
    field_name = row["name"] if row["change_type"] != "removed" else None
    if row["change_type"] == "removed":
        # Removed keys are "removed:{name}". Match the original field name.
        field_name = row["name"]
    prior = live_labels.get(field_name) if field_name else None
    if row["change_type"] in {"unchanged", "removed"} and prior and prior.get("label"):
        attention = "needs_review" if labels_look_conflicting(prior["label"], extracted) else None
        return prior["label"], extracted, "carried", attention
    return extracted, None, "extracted", None


def _replace_questions(
    connection,
    edition_id: int,
    compared_to_path: str,
    rows: list[dict],
    live_labels: dict[str, dict] | None = None,
) -> int:
    live_labels = live_labels or {}
    carried = 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE form_editions
            SET compared_to_path = %s, rules_json = '[]'::jsonb
            WHERE id = %s
            """,
            (compared_to_path, edition_id),
        )
        cursor.execute("DELETE FROM form_questions WHERE edition_id = %s", (edition_id,))
        for order, row in enumerate(rows, start=1):
            label, prior_label, label_source, attention = _apply_carried_label(row, live_labels)
            if label_source == "carried":
                carried += 1
            cursor.execute(
                """
                INSERT INTO form_questions (
                    edition_id, question_key, label, input_type, required,
                    review_status, pdf_field, checked_value, page_number,
                    sort_order, change_type, label_source, prior_label, attention
                )
                VALUES (%s, %s, %s, %s, false, 'needs_review', %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    edition_id,
                    row["question_key"],
                    label,
                    input_type(row["type"]),
                    row["name"] if row["change_type"] != "removed" else None,
                    row["checked_value"] if row["change_type"] != "removed" else None,
                    row["page"],
                    order,
                    row["change_type"],
                    label_source,
                    prior_label,
                    attention,
                ),
            )
    return carried


def run_comparison() -> dict:
    if uses_csharp():
        raise ReviewError(
            "Legacy fw4 comparison job is unavailable while FormsEngine source is C#. "
            "Use Compare years for a form edition instead.",
            409,
        )
    old_pdf = ROOT / "docs" / "fw4--2025.pdf"
    new_pdf = ROOT / "docs" / "fw4_2026.pdf"
    rows = build_rows(widgets(old_pdf), widgets(new_pdf))
    with connect() as connection:
        try:
            edition_id, counts = save_draft(connection, rows)
        except RuntimeError as exc:
            raise ReviewError(str(exc), 409) from exc
        connection.commit()
    return {"edition_id": edition_id, "replaced_existing_draft": True, "counts": counts}


def use_reviewed_wording() -> dict:
    definition = load_definition(YEAR_FILES["2026"])
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, status FROM form_editions WHERE form_id = 'fw4' AND edition = '2026'"
            )
            edition = cursor.fetchone()
            if not edition:
                raise ReviewError("The 2026 draft does not exist yet.", 404)
            if edition["status"] != "draft":
                raise ReviewError("The 2026 edition is already approved.", 409)
            cursor.execute("DELETE FROM form_questions WHERE edition_id = %s", (edition["id"],))
            _insert_definition(cursor, edition["id"], definition, "needs_review", "reviewed")
        connection.commit()
    return {"question_count": len(definition["questions"])}


def approve_draft(items: list[dict], confirm_generated_wording: bool) -> dict:
    if uses_csharp():
        raise ReviewError(
            "Legacy fw4 draft approval is unavailable while FormsEngine source is C#. "
            "Use the modern publish API for a form edition instead.",
            409,
        )
    if not isinstance(items, list) or not items:
        raise ReviewError("Choose at least one question to publish")
    included = [item for item in items if item.get("include")]
    if not included:
        raise ReviewError("Choose at least one question to publish")
    form_id = "fw4"
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                _lock_form_for_publish(cursor, form_id)
                cursor.execute(
                    """
                    SELECT id, status
                    FROM form_editions
                    WHERE form_id = %s AND edition = '2026'
                    """,
                    (form_id,),
                )
                edition = cursor.fetchone()
                if not edition or edition["status"] != "draft":
                    raise ReviewError("There is no 2026 draft to approve", 409)
                keys = [item.get("question_key") for item in items]
                cursor.execute(
                    """
                    SELECT question_key, change_type
                    FROM form_questions
                    WHERE edition_id = %s AND question_key = ANY(%s)
                    """,
                    (edition["id"], keys),
                )
                known = {row["question_key"]: row["change_type"] for row in cursor.fetchall()}
                if len(known) != len(set(keys)):
                    raise ReviewError("The draft changed. Reload the review page and try again.")
                generated = [
                    item["question_key"]
                    for item in included
                    if known[item["question_key"]] != "reviewed"
                ]
                if generated and not confirm_generated_wording:
                    raise ReviewError(
                        "These rows still use wording taken from the PDF. Confirm that publish, or replace the draft with the reviewed 2026 wording first."
                    )
                for item in items:
                    label = " ".join(str(item.get("label") or "").split())
                    if item.get("include") and not label:
                        raise ReviewError("An included question needs wording")
                    cursor.execute(
                        """
                        UPDATE form_questions
                        SET label = %s,
                            review_status = %s
                        WHERE edition_id = %s AND question_key = %s
                        """,
                        (
                            label,
                            "approved" if item.get("include") else "removed",
                            edition["id"],
                            item["question_key"],
                        ),
                    )
                cursor.execute(
                    "UPDATE form_editions SET is_live = false WHERE form_id = %s",
                    (form_id,),
                )
                cursor.execute(
                    """
                    UPDATE form_editions
                    SET status = 'approved', is_live = true
                    WHERE id = %s AND form_id = %s
                    """,
                    (edition["id"], form_id),
                )
            connection.commit()
    except UniqueViolation as exc:
        _raise_if_live_unique_violation(exc)
    return {"edition": "2026", "published_questions": len(included)}


def preview_definition_from_items(items: list[dict]) -> dict:
    included = {item["question_key"] for item in items if item.get("include")}
    labels = {item["question_key"]: item.get("label") for item in items}
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, storage_path FROM form_editions
                WHERE form_id = 'fw4' AND edition = '2026' AND status = 'draft'
                """
            )
            edition = cursor.fetchone()
            if not edition:
                raise ReviewError("There is no 2026 draft to preview", 404)
            questions = _questions_for(cursor, edition["id"], published_only=False)
    selected = []
    for question in questions:
        if question["id"] not in included:
            continue
        question["label"] = labels.get(question["id"]) or question["label"]
        selected.append(question)
    if not selected:
        raise ReviewError("Choose at least one question to preview")
    return {
        "form_id": "fw4",
        "edition": "2026",
        "source_pdf": edition["storage_path"],
        "questions": selected,
    }


def _questions_for(cursor, edition_id: int, published_only: bool) -> list[dict]:
    status_filter = "AND review_status = 'approved'" if published_only else ""
    cursor.execute(
        f"""
        SELECT question_key, label, input_type, required, review_status, pdf_field,
               checked_value, options_json, attention, max_length
        FROM form_questions
        WHERE edition_id = %s {status_filter}
        ORDER BY sort_order
        """,
        (edition_id,),
    )
    return [_to_question(row) for row in cursor.fetchall()]


def _definition_from(edition: dict, questions: list[dict]) -> dict:
    return {
        "form_id": edition["form_id"],
        "edition": edition["edition"],
        "source_pdf": edition["storage_path"],
        "questions": questions,
        "rules": edition.get("rules_json") or [],
    }


def _to_question(row: dict) -> dict:
    question = {
        "id": row["question_key"],
        "label": row["label"],
        "type": row["input_type"],
        "required": row["required"],
        "status": row["attention"] or "verified",
    }
    if row.get("max_length"):
        question["max_length"] = row["max_length"]
    if row.get("options_json"):
        question["options"] = row["options_json"]
    else:
        question["pdf_field"] = row.get("pdf_field")
        if row.get("checked_value"):
            question["checked_value"] = row["checked_value"]
    return question


def _primary_form_page(rows: list[dict]) -> int | None:
    """Earliest page that still has a non-removed widget.

    Forms do not all put the employee section on PDF page 1 (Colorado starts on
    page 2). Using the first active page keeps W-4 on page 1 and stays
    form-agnostic for future editions.
    """
    pages = [
        int(row["page_number"])
        for row in rows
        if row.get("change_type") != "removed" and row.get("page_number") is not None
    ]
    return min(pages) if pages else None


def _suggested_include(row: dict, primary_page: int | None = None) -> bool:
    """Default checkbox for reviewers — not a stored approval decision."""
    if row.get("change_type") == "removed":
        return False
    page = row.get("page_number")
    if page is None:
        return False
    if primary_page is None:
        primary_page = 1
    if int(page) != int(primary_page):
        return False
    leaf = (row.get("pdf_field") or row.get("question_key") or "").split(".")[-1]
    return leaf not in EXCLUDED_LEAVES


def _with_suggested_include(rows: list[dict]) -> list[dict]:
    primary_page = _primary_form_page(rows)
    annotated = []
    for row in rows:
        item = dict(row)
        item["suggested_include"] = _suggested_include(item, primary_page)
        annotated.append(item)
    return annotated
