import os

import psycopg

from backend.draft_job import load_env

load_env()
with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, storage_path, compared_to_path
            FROM form_editions
            WHERE form_id = 'fw4' AND edition = '2026'
            """
        )
        print("edition", cursor.fetchone())
        cursor.execute(
            """
            SELECT change_type, count(*)
            FROM form_questions
            GROUP BY change_type
            ORDER BY change_type
            """
        )
        print("counts", cursor.fetchall())
        cursor.execute(
            """
            SELECT change_type, input_type, left(label, 100)
            FROM form_questions
            WHERE page_number = 1 AND change_type = 'added'
            ORDER BY sort_order
            """
        )
        print("page1_added")
        for row in cursor.fetchall():
            print(row)
