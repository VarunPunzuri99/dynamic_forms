"""Print a tight label for selected page-1 fields."""

from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def label(path: Path, leaf: str) -> None:
    doc = pymupdf.open(path)
    page = doc[0]
    for widget in page.widgets() or []:
        if not (widget.field_name or "").endswith(leaf):
            continue
        rect = widget.rect
        clip = pymupdf.Rect(300, rect.y0 - 6, rect.x0 - 1, rect.y1 + 6)
        words = page.get_text("words", clip=clip) or []
        words.sort(key=lambda word: (round(word[1], 1), word[0]))
        print(path.name, leaf, widget.field_type_string, [round(v, 1) for v in rect])
        print(" ", " ".join(word[4] for word in words))
    doc.close()


label(ROOT / "docs" / "fw4--2025.pdf", "f1_15[0]")
label(ROOT / "docs" / "fw4_2026.pdf", "f1_08[0]")
label(ROOT / "docs" / "fw4_2026.pdf", "c1_3[0]")
