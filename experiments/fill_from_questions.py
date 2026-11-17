"""Fill a blank 2026 W-4 from the reviewed question file and fake answers.

Uses the same validation and fill code as the preview API.
Writes output/fw4_2026_from_questions.pdf. Does not modify the blank PDF.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.filler import fill_pdf, load_definition, normalize_answers, read_field_values  # noqa: E402

QUESTIONS = ROOT / "forms" / "fw4_2026_questions.json"
ANSWERS = ROOT / "experiments" / "sample_answers.json"
OUTPUT = ROOT / "output" / "fw4_2026_from_questions.pdf"
PREVIEW = ROOT / "output" / "fw4_2026_from_questions_page1.png"


def main() -> None:
    definition = load_definition(QUESTIONS)
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    normalized = normalize_answers(definition, answers)
    pdf_bytes = fill_pdf(definition, normalized, ROOT / definition["source_pdf"])
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_bytes(pdf_bytes)
    stored = read_field_values(pdf_bytes)
    print(f"wrote {OUTPUT}")
    for field_name, value in stored.items():
        if value != "Off":
            print(f"OK       {value!r:18} {field_name}")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pixmap = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False)
        pixmap.save(PREVIEW)
    finally:
        doc.close()


if __name__ == "__main__":
    main()
