"""Show non-empty widget values in the filled 2026 PDF."""

from pathlib import Path
import pymupdf

path = Path(__file__).resolve().parents[1] / "output" / "fw4_2026_exempt_checked.pdf"
doc = pymupdf.open(path)
print("pages", doc.page_count)
for page_index, page in enumerate(doc, start=1):
    for widget in page.widgets() or []:
        value = widget.field_value
        if value not in (None, "", "Off"):
            print(f"p{page_index} {widget.field_type_string:8} {value!r:12} {widget.field_name}")
doc.close()
