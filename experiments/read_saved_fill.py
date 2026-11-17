"""Read non-empty fields from the manually saved 2026 W-4 and render page 1."""

from pathlib import Path
import pymupdf

path = Path(r"D:\fms_solutions\forms_poc\output\fw4_2026_exempt_checked_filled.pdf")
out = path.parent / "fw4_2026_exempt_checked_filled_page1.png"
doc = pymupdf.open(path)
print("exists", path.exists(), "pages", doc.page_count)
for page_index, page in enumerate(doc, start=1):
    for widget in page.widgets() or []:
        value = widget.field_value
        if value not in (None, "", "Off"):
            rect = widget.rect
            print(
                f"p{page_index} {widget.field_type_string:8} {value!r:16} "
                f"y={rect.y0:.1f} {widget.field_name}"
            )
pixmap = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False)
pixmap.save(out)
print("image", out)
doc.close()
