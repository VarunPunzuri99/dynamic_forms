"""Inspect page-1 widgets and fill the 2026 exemption checkbox."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "output"
OLD = DOCS / "fw4--2025.pdf"
NEW = DOCS / "fw4_2026.pdf"


def page_widgets(path: Path, page_number: int = 1) -> list[dict]:
    doc = pymupdf.open(path)
    page = doc[page_number - 1]
    rows = []
    for widget in page.widgets() or []:
        states = None
        on_state = None
        try:
            states = widget.button_states()
        except Exception as exc:  # noqa: BLE001
            states = f"error: {exc}"
        try:
            on_state = widget.on_state()
        except Exception as exc:  # noqa: BLE001
            on_state = f"error: {exc}"
        rect = widget.rect
        rows.append(
            {
                "name": widget.field_name,
                "leaf": (widget.field_name or "").split(".")[-1],
                "type": widget.field_type_string,
                "value": widget.field_value,
                "rect": [round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1)],
                "states": states,
                "on_state": on_state,
            }
        )
    doc.close()
    rows.sort(key=lambda row: (row["rect"][1], row["rect"][0]))
    return rows


def words_left_of(path: Path, rect: list[float], page_number: int = 1) -> str:
    doc = pymupdf.open(path)
    page = doc[page_number - 1]
    x0, y0, x1, y1 = rect
    clip = pymupdf.Rect(40, y0 - 18, x0 - 2, y1 + 18)
    words = page.get_text("words", clip=clip) or []
    doc.close()
    words.sort(key=lambda word: (round(word[1], 1), word[0]))
    return " ".join(word[4] for word in words)


def fill_and_crop(field_name: str, on_value: str) -> dict:
    doc = pymupdf.open(NEW)
    page = doc[0]
    target = None
    for widget in page.widgets() or []:
        if widget.field_name == field_name:
            target = widget
            widget.field_value = on_value
            widget.update()
    if target is None:
        doc.close()
        raise RuntimeError(field_name)
    rect = pymupdf.Rect(target.rect)
    out_pdf = OUT / "fw4_2026_exempt_checked.pdf"
    doc.save(out_pdf, incremental=False, encryption=pymupdf.PDF_ENCRYPT_NONE)
    doc.close()

    filled = pymupdf.open(out_pdf)
    saved = None
    for widget in filled[0].widgets() or []:
        if widget.field_name == field_name:
            saved = widget.field_value
            rect = pymupdf.Rect(widget.rect)
    clip = pymupdf.Rect(36, rect.y0 - 28, 590, rect.y1 + 28)
    pixmap = filled[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
    image = OUT / "fw4_2026_exempt_checkbox_crop.png"
    pixmap.save(image)
    filled.close()
    return {
        "pdf": str(out_pdf),
        "image": str(image),
        "requested": on_value,
        "saved_value": saved,
    }


def main() -> None:
    old_rows = page_widgets(OLD)
    new_rows = page_widgets(NEW)
    old_leaves = {row["leaf"] for row in old_rows}
    new_leaves = {row["leaf"] for row in new_rows}
    added = [row for row in new_rows if row["leaf"] not in old_leaves]
    removed = [row for row in old_rows if row["leaf"] not in new_leaves]

    for row in added:
        row["text_to_the_left"] = words_left_of(NEW, row["rect"])

    checkbox = next(row for row in added if row["type"] == "CheckBox")
    on_value = checkbox["on_state"] or "Yes"
    if isinstance(on_value, str) and on_value.startswith("error"):
        on_value = "Yes"
    fill = fill_and_crop(checkbox["name"], on_value)

    report = {
        "page1_leaf_added": [row["leaf"] for row in added],
        "page1_leaf_removed": [row["leaf"] for row in removed],
        "added": added,
        "removed": removed,
        "fill": fill,
        "page1_2026": new_rows,
        "page1_2025": old_rows,
    }
    (OUT / "page1_widget_diff.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "page1_leaf_added": report["page1_leaf_added"],
        "page1_leaf_removed": report["page1_leaf_removed"],
        "added": [
            {
                "leaf": row["leaf"],
                "type": row["type"],
                "on_state": row["on_state"],
                "states": row["states"],
                "rect": row["rect"],
                "text_to_the_left": row["text_to_the_left"],
            }
            for row in added
        ],
        "fill": fill,
    }, indent=2))


if __name__ == "__main__":
    main()
