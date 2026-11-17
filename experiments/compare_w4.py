"""Compare the 2025 and 2026 Form W-4 PDFs and test filling the new checkbox.

Reads blank PDFs from docs/. Writes reports and a filled copy under output/.
Does not modify the source PDFs.
"""

from __future__ import annotations

import json
from difflib import unified_diff
from pathlib import Path

import fitz
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "output"
OLD = DOCS / "fw4--2025.pdf"
NEW = DOCS / "fw4_2026.pdf"


def widget_rows(path: Path) -> list[dict]:
    doc = fitz.open(path)
    rows = []
    for page_index, page in enumerate(doc):
        for widget in page.widgets() or []:
            rect = widget.rect
            rows.append(
                {
                    "page": page_index + 1,
                    "name": widget.field_name or "",
                    "type": widget.field_type_string or "",
                    "value": None if widget.field_value is None else str(widget.field_value),
                    "rect": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                }
            )
    doc.close()
    return rows


def nearby_words(path: Path, rect: list[float], page_number: int, margin: float = 80) -> str:
    doc = fitz.open(path)
    page = doc[page_number - 1]
    x0, y0, x1, y1 = rect
    clip = fitz.Rect(x0 - 20, y0 - margin, x1 + 420, y1 + 36)
    words = page.get_text("words", clip=clip) or []
    doc.close()
    words.sort(key=lambda word: (round(word[1], 1), word[0]))
    return " ".join(word[4] for word in words)


def on_state(field: dict) -> str | None:
    kids = field.get("/Kids") or []
    annotations = [field, *kids]
    for annotation in annotations:
        appearance = annotation.get("/AP") if hasattr(annotation, "get") else None
        if not appearance:
            continue
        normal = appearance.get("/N")
        if not normal:
            continue
        states = [str(key) for key in normal.keys()]
        for state in states:
            if state not in {"/Off", "/off"}:
                return state
    return None


def pypdf_fields(path: Path) -> dict[str, dict]:
    reader = PdfReader(str(path))
    raw = reader.get_fields() or {}
    summary = {}
    for name, field in raw.items():
        summary[name] = {
            "type": str(field.get("/FT")) if field.get("/FT") else None,
            "value": str(field.get("/V")) if field.get("/V") is not None else None,
            "on_state": on_state(field),
        }
    return summary


def page_text(path: Path) -> list[str]:
    doc = fitz.open(path)
    pages = [page.get_text() for page in doc]
    doc.close()
    return pages


def render_page(path: Path, page_number: int, destination: Path) -> None:
    doc = fitz.open(path)
    page = doc[page_number - 1]
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    pixmap.save(destination)
    doc.close()


def fill_checkbox(source: Path, destination: Path, field_name: str, on_value: str) -> dict:
    reader = PdfReader(str(source))
    # Clone through pymupdf so widget appearances are updated, then verify with pypdf.
    doc = fitz.open(source)
    updated = False
    for page in doc:
        for widget in page.widgets() or []:
            if widget.field_name == field_name:
                widget.field_value = on_value.lstrip("/")
                widget.update()
                updated = True
    if not updated:
        doc.close()
        raise RuntimeError(f"Widget not found: {field_name}")
    doc.save(destination)
    doc.close()

    filled = PdfReader(str(destination))
    fields = filled.get_fields() or {}
    saved = fields.get(field_name, {})
    return {
        "wrote": str(destination),
        "field": field_name,
        "requested_on_value": on_value,
        "saved_value": str(saved.get("/V")) if saved.get("/V") is not None else None,
        "reader_page_count": len(reader.pages),
        "filled_page_count": len(filled.pages),
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for path in (OLD, NEW):
        if not path.exists():
            raise SystemExit(f"Missing PDF: {path}")

    old_widgets = widget_rows(OLD)
    new_widgets = widget_rows(NEW)
    old_names = {row["name"] for row in old_widgets}
    new_names = {row["name"] for row in new_widgets}
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)

    old_fields = pypdf_fields(OLD)
    new_fields = pypdf_fields(NEW)
    old_text = page_text(OLD)
    new_text = page_text(NEW)

    added_details = []
    for name in added:
        matches = [row for row in new_widgets if row["name"] == name]
        for row in matches:
            detail = dict(row)
            detail["nearby_text"] = nearby_words(NEW, row["rect"], row["page"])
            detail["on_state"] = new_fields.get(name, {}).get("on_state")
            added_details.append(detail)

    phrases = [
        "Exempt from withholding",
        "I claim exemption from withholding",
        "$2,000",
        "$2,200",
        "write Exempt",
        "Form W-4",
    ]
    phrase_hits = {}
    for phrase in phrases:
        phrase_hits[phrase] = {
            "2025_pages": [index + 1 for index, text in enumerate(old_text) if phrase in text],
            "2026_pages": [index + 1 for index, text in enumerate(new_text) if phrase in text],
        }

    text_diff = "\n".join(
        unified_diff(
            old_text[0].splitlines(),
            new_text[0].splitlines(),
            fromfile="2025 page 1",
            tofile="2026 page 1",
            lineterm="",
        )
    )
    (OUT / "page1_text.diff").write_text(text_diff, encoding="utf-8")

    render_page(OLD, 1, OUT / "fw4_2025_page1.png")
    render_page(NEW, 1, OUT / "fw4_2026_page1.png")

    fill_result = None
    checkbox = next((row for row in added_details if row["type"] == "CheckBox"), None)
    if checkbox and checkbox.get("on_state"):
        filled_path = OUT / "fw4_2026_exempt_checked.pdf"
        fill_result = fill_checkbox(NEW, filled_path, checkbox["name"], checkbox["on_state"])
        render_page(filled_path, checkbox["page"], OUT / "fw4_2026_exempt_checked_page.png")

    # NeedAppearance flag check: some viewers ignore values unless this is set.
    # Record it as evidence; do not treat a saved value as visual proof by itself.
    report = {
        "files": {"2025": str(OLD), "2026": str(NEW)},
        "pages": {"2025": len(old_text), "2026": len(new_text)},
        "widget_counts": {"2025": len(old_widgets), "2026": len(new_widgets)},
        "pypdf_field_counts": {"2025": len(old_fields), "2026": len(new_fields)},
        "added_widget_names": added,
        "removed_widget_names": removed,
        "added_widgets": added_details,
        "phrase_hits": phrase_hits,
        "checkbox_fill": fill_result,
    }
    (OUT / "comparison_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({
        "pages": report["pages"],
        "widget_counts": report["widget_counts"],
        "added_widget_names": added,
        "removed_widget_names": removed,
        "added_widgets": [
            {
                "name": row["name"],
                "type": row["type"],
                "page": row["page"],
                "on_state": row.get("on_state"),
                "nearby_text": row["nearby_text"][:400],
            }
            for row in added_details
        ],
        "phrase_hits": phrase_hits,
        "checkbox_fill": fill_result,
    }, indent=2))


if __name__ == "__main__":
    main()
