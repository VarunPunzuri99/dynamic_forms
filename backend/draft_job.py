"""Compare two blank PDFs and store a draft question list in PostgreSQL.

The PDF paths stand in for Azure Blob Storage. The database stores the draft
questions. Re-running replaces a draft edition and leaves an approved edition
unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pymupdf
from psycopg.rows import tuple_row

ROOT = Path(__file__).resolve().parents[1]
OLD_PDF = ROOT / "docs" / "fw4--2025.pdf"
NEW_PDF = ROOT / "docs" / "fw4_2026.pdf"

SCHEMA = """
CREATE TABLE IF NOT EXISTS form_editions (
    id bigserial PRIMARY KEY,
    form_id text NOT NULL,
    edition text NOT NULL,
    storage_path text NOT NULL,
    compared_to_path text,
    status text NOT NULL DEFAULT 'draft',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (form_id, edition)
);

CREATE TABLE IF NOT EXISTS form_questions (
    id bigserial PRIMARY KEY,
    edition_id bigint NOT NULL REFERENCES form_editions(id) ON DELETE CASCADE,
    question_key text NOT NULL,
    label text NOT NULL,
    input_type text NOT NULL,
    required boolean NOT NULL DEFAULT false,
    review_status text NOT NULL DEFAULT 'needs_review',
    pdf_field text,
    checked_value text,
    page_number integer,
    sort_order integer NOT NULL,
    change_type text NOT NULL,
    UNIQUE (edition_id, question_key)
);
"""


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        raise SystemExit("Missing .env file with DATABASE_URL")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is missing from .env")


def widgets(path: Path) -> list[dict]:
    doc = pymupdf.open(path)
    rows = []
    try:
        for page_number, page in enumerate(doc, start=1):
            for widget in page.widgets() or []:
                rect = widget.rect
                on_state = None
                try:
                    on_state = widget.on_state()
                except RuntimeError:
                    on_state = None
                rows.append(
                    {
                        "name": widget.field_name or "",
                        "type": widget.field_type_string or "text",
                        "page": page_number,
                        "rect": (rect.x0, rect.y0, rect.x1, rect.y1),
                        "checked_value": on_state,
                        "label": nearby_label(page, rect),
                    }
                )
    finally:
        doc.close()
    rows.sort(key=lambda row: (row["page"], row["rect"][1], row["rect"][0]))
    return rows


def nearby_label(page: pymupdf.Page, rect: pymupdf.Rect) -> str:
    clip = pymupdf.Rect(36, rect.y0 - 8, max(rect.x0 - 2, 36), rect.y1 + 8)
    words = page.get_text("words", clip=clip) or []
    words.sort(key=lambda word: (round(word[1], 1), word[0]))
    text = " ".join(word[4] for word in words)
    text = " ".join(text.split())
    if not text:
        return "No nearby text found"
    return text[:500]


def input_type(widget_type: str) -> str:
    if widget_type == "CheckBox":
        return "checkbox"
    if widget_type == "RadioButton":
        return "choice"
    return "text"


def build_rows(old_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    old_names = {row["name"] for row in old_rows}
    new_names = {row["name"] for row in new_rows}
    drafted = []
    for row in new_rows:
        drafted.append(
            {
                **row,
                "change_type": "added" if row["name"] not in old_names else "unchanged",
                "question_key": row["name"],
            }
        )
    for row in old_rows:
        if row["name"] in new_names:
            continue
        drafted.append(
            {
                **row,
                "change_type": "removed",
                "question_key": f"removed:{row['name']}",
                "pdf_field": None,
            }
        )
    return drafted


def ensure_schema(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA)
        cursor.execute(
            "ALTER TABLE form_editions ADD COLUMN IF NOT EXISTS is_live boolean NOT NULL DEFAULT false"
        )
        cursor.execute(
            "ALTER TABLE form_editions ADD COLUMN IF NOT EXISTS rules_json jsonb NOT NULL DEFAULT '[]'::jsonb"
        )
        cursor.execute(
            "ALTER TABLE form_questions ADD COLUMN IF NOT EXISTS options_json jsonb"
        )
        cursor.execute("ALTER TABLE form_questions ADD COLUMN IF NOT EXISTS attention text")
        cursor.execute("ALTER TABLE form_questions ADD COLUMN IF NOT EXISTS max_length integer")
        cursor.execute(
            "ALTER TABLE form_questions ADD COLUMN IF NOT EXISTS label_source text NOT NULL DEFAULT 'extracted'"
        )
        cursor.execute("ALTER TABLE form_questions ADD COLUMN IF NOT EXISTS prior_label text")
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_one_live_edition_per_form
            ON form_editions (form_id)
            WHERE is_live = TRUE
            """
        )


def save_draft(connection: psycopg.Connection, rows: list[dict]) -> tuple[int, dict[str, int]]:
    ensure_schema(connection)
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            "SELECT id, status FROM form_editions WHERE form_id = %s AND edition = %s",
            ("fw4", "2026"),
        )
        existing = cursor.fetchone()
        if existing and existing[1] != "draft":
            raise RuntimeError("The 2026 edition is already approved, so the draft was not replaced")
        if existing:
            edition_id = existing[0]
            cursor.execute(
                """
                UPDATE form_editions
                SET storage_path = %s, compared_to_path = %s
                WHERE id = %s
                """,
                ("docs/fw4_2026.pdf", "docs/fw4--2025.pdf", edition_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO form_editions (form_id, edition, storage_path, compared_to_path, status)
                VALUES (%s, %s, %s, %s, 'draft')
                RETURNING id
                """,
                ("fw4", "2026", "docs/fw4_2026.pdf", "docs/fw4--2025.pdf"),
            )
            edition_id = cursor.fetchone()[0]
        cursor.execute("DELETE FROM form_questions WHERE edition_id = %s", (edition_id,))
        counts = {"added": 0, "unchanged": 0, "removed": 0}
        for order, row in enumerate(rows, start=1):
            counts[row["change_type"]] += 1
            cursor.execute(
                """
                INSERT INTO form_questions (
                    edition_id, question_key, label, input_type, required,
                    review_status, pdf_field, checked_value, page_number,
                    sort_order, change_type
                )
                VALUES (%s, %s, %s, %s, false, 'needs_review', %s, %s, %s, %s, %s)
                """,
                (
                    edition_id,
                    row["question_key"],
                    row["label"],
                    input_type(row["type"]),
                    row["name"] if row["change_type"] != "removed" else None,
                    row["checked_value"] if row["change_type"] != "removed" else None,
                    row["page"],
                    order,
                    row["change_type"],
                ),
            )
    connection.commit()
    return edition_id, counts


def main() -> None:
    load_env()
    if not OLD_PDF.is_file() or not NEW_PDF.is_file():
        raise SystemExit("Both blank PDFs must be in docs")
    rows = build_rows(widgets(OLD_PDF), widgets(NEW_PDF))
    try:
        with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
            try:
                edition_id, counts = save_draft(connection, rows)
            except RuntimeError as exc:
                raise SystemExit(str(exc)) from None
    except psycopg.OperationalError:
        raise SystemExit("Could not connect to forms_poc. Check DATABASE_URL in .env") from None
    print(f"draft edition id: {edition_id}")
    print(
        f"questions: {len(rows)} "
        f"(added {counts['added']}, unchanged {counts['unchanged']}, removed {counts['removed']})"
    )


if __name__ == "__main__":
    sys.exit(main())
