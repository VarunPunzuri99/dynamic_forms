import os

import psycopg

from backend.draft_job import load_env

load_env()
with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT question_key, change_type, input_type, review_status, page_number, left(label, 120)
            FROM form_questions
            WHERE question_key ILIKE '%c1_3%'
               OR change_type = 'removed'
               OR question_key ILIKE '%f1_01%'
            ORDER BY change_type, page_number, question_key
            """
        )
        for row in cursor.fetchall():
            print("---")
            print("key:", row[0])
            print("change:", row[1], "| type:", row[2], "| review:", row[3], "| page:", row[4])
            print("label:", row[5])
