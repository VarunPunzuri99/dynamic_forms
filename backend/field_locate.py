"""Help reviewers match a draft question row to the real PDF box."""

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf

from backend.draft_job import widgets


def pdf_field_for_row(row: dict) -> str | None:
    if row.get("pdf_field"):
        return row["pdf_field"]
    key = row.get("question_key") or ""
    if key.startswith("removed:"):
        return key[len("removed:") :] or None
    return key or None


def field_leaf(field_name: str | None) -> str:
    if not field_name:
        return ""
    return field_name.split(".")[-1]


def widget_locator_map(pdf_path: Path) -> dict[str, dict]:
    if not pdf_path.is_file():
        return {}
    mapping: dict[str, dict] = {}
    page_counts: dict[int, int] = {}
    for row in widgets(pdf_path):
        name = row["name"]
        if not name:
            continue
        page = int(row["page"])
        page_counts[page] = page_counts.get(page, 0) + 1
        mapping[name] = {
            "page": page,
            "rect": list(row["rect"]),
            "index_on_page": page_counts[page],
            "type": row["type"],
        }
    return mapping


def enrich_rows_with_locators(rows: list[dict], storage_path: Path, compared_to_path: Path | None = None) -> list[dict]:
    current = widget_locator_map(storage_path)
    previous = widget_locator_map(compared_to_path) if compared_to_path else {}
    enriched = []
    for row in rows:
        item = dict(row)
        field_name = pdf_field_for_row(item)
        locator = None
        source_pdf = storage_path.name if storage_path.is_file() else ""
        if field_name and item.get("change_type") == "removed":
            locator = previous.get(field_name) or current.get(field_name)
            if compared_to_path and compared_to_path.is_file():
                source_pdf = compared_to_path.name
        elif field_name:
            locator = current.get(field_name) or previous.get(field_name)
        item["field_name"] = field_name
        item["field_leaf"] = field_leaf(field_name)
        if locator:
            item["rect"] = locator["rect"]
            item["index_on_page"] = locator["index_on_page"]
            item["locator_pdf"] = source_pdf
            item["position_label"] = (
                f"Page {locator['page']} · box {locator['index_on_page']} on that page"
            )
        else:
            item["rect"] = None
            item["index_on_page"] = None
            item["locator_pdf"] = source_pdf
            page = item.get("page_number")
            item["position_label"] = f"Page {page}" if page else "Page unknown"
        enriched.append(item)
    return enriched


def render_field_highlight(pdf_path: Path, field_name: str, page_number: int | None = None) -> dict:
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path.name)
    doc = pymupdf.open(pdf_path)
    try:
        target_page = None
        target_rect = None
        for index, page in enumerate(doc, start=1):
            if page_number is not None and index != page_number:
                continue
            for widget in page.widgets() or []:
                if (widget.field_name or "") != field_name:
                    continue
                target_page = index
                target_rect = widget.rect
                break
            if target_rect is not None:
                break
        if target_page is None or target_rect is None:
            raise KeyError(field_name)
        page = doc[target_page - 1]
        page.draw_rect(
            target_rect,
            color=(0.23, 0.25, 0.85),
            fill=(0.79, 0.82, 1.0),
            width=1.8,
        )
        # Wide pad so reviewers see labels above/beside the box (not a tight crop).
        pad_x = max(120, target_rect.width * 1.5)
        pad_y = max(140, target_rect.height * 2.5)
        clip = pymupdf.Rect(
            max(target_rect.x0 - pad_x, 0),
            max(target_rect.y0 - pad_y, 0),
            min(target_rect.x1 + pad_x, page.rect.x1),
            min(target_rect.y1 + pad_y, page.rect.y1),
        )
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        return {
            "field_name": field_name,
            "field_leaf": field_leaf(field_name),
            "page_number": target_page,
            "rect": [target_rect.x0, target_rect.y0, target_rect.x1, target_rect.y1],
            "png_base64": base64.b64encode(pix.tobytes("png")).decode("ascii"),
        }
    finally:
        doc.close()
