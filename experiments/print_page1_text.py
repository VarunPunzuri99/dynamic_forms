from pathlib import Path
import pymupdf

doc = pymupdf.open(Path(__file__).resolve().parents[1] / "docs" / "fw4_2026.pdf")
print(doc[0].get_text())
doc.close()
