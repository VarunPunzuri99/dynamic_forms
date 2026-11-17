"""Fill distinct values into the 2025 amount boxes and render page 1."""

from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "docs" / "fw4--2025.pdf"
values = {
    "topmostSubform[0].Page1[0].Step3_ReadOrder[0].f1_06[0]": "111",
    "topmostSubform[0].Page1[0].Step3_ReadOrder[0].f1_07[0]": "222",
    "topmostSubform[0].Page1[0].f1_09[0]": "333",
    "topmostSubform[0].Page1[0].f1_10[0]": "444",
    "topmostSubform[0].Page1[0].f1_11[0]": "555",
    "topmostSubform[0].Page1[0].f1_12[0]": "666",
}
doc = pymupdf.open(source)
pending = dict(values)
for page in doc:
    for widget in page.widgets() or []:
        if widget.field_name in pending:
            widget.field_value = pending.pop(widget.field_name)
            widget.update()
out = ROOT / "output" / "fw4_2025_amount_probe.pdf"
doc.save(out)
doc[0].get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4), alpha=False).save(
    ROOT / "output" / "fw4_2025_amount_probe.png"
)
doc.close()
print("missing", pending)
print(out)
