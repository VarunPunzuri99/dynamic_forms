"""Forms-engine source switching: C# SQL Server APIs vs local Postgres.

Phase 1: C# reads (editions, live definition, questions, blank PDFs).
Phase 2: C# save/publish/import/compare persistence via FormsEngine APIs.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from backend import csharp_client
from backend.csharp_client import CsharpApiError
from backend.draft_job import ROOT, build_rows, input_type, load_env, widgets
from backend.rules import RuleError, validate_rules

PDF_CACHE_DIR = ROOT / "output" / "csharp_pdf_cache"
_FORM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)


class FormsEngineError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def uses_csharp() -> bool:
    load_env()
    source = (os.environ.get("FORMS_ENGINE_SOURCE") or "").strip().lower()
    if source in {"postgres", "local", "pg"}:
        return False
    if source in {"csharp", "dotnet", "sqlserver", "remote"}:
        return csharp_client.configured()
    # Default: prefer C# whenever the remote FormsEngine is configured.
    return csharp_client.configured()


def configured_form_ids() -> list[str]:
    load_env()
    raw = os.environ.get("FORMS_ENGINE_FORM_IDS") or "fw4,colorado"
    ids = []
    for part in raw.split(","):
        form_id = part.strip()
        if form_id and _FORM_ID_RE.match(form_id):
            ids.append(form_id)
    return ids or ["fw4", "colorado"]


def _pick(data: dict, *names: str, default=None):
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def normalize_edition_summary(raw: dict) -> dict:
    question_count = int(_pick(raw, "questionCount", "question_count", default=0) or 0)
    approved_count = _pick(raw, "approvedCount", "approved_count", default=None)
    if approved_count is None:
        approved_count = question_count
    return {
        "id": int(_pick(raw, "id")),
        "form_id": _pick(raw, "formId", "form_id"),
        "edition": str(_pick(raw, "edition", default="")),
        "storage_path": _pick(raw, "storagePath", "storage_path"),
        "compared_to_path": _pick(raw, "comparedToPath", "compared_to_path"),
        "status": str(_pick(raw, "status", default="draft")).lower(),
        "is_live": bool(_pick(raw, "isLive", "is_live", default=False)),
        "question_count": question_count,
        "approved_count": int(approved_count or 0),
    }


def normalize_question_row(raw: dict) -> dict:
    options = _pick(raw, "optionsJson", "options_json", "options", default=None)
    return {
        "question_key": _pick(raw, "questionKey", "question_key", "id"),
        "label": _pick(raw, "label", default="") or "",
        "input_type": _pick(raw, "inputType", "input_type", "type", default="text") or "text",
        "required": bool(_pick(raw, "required", default=False)),
        "review_status": _pick(raw, "reviewStatus", "review_status", default="needs_review")
        or "needs_review",
        "pdf_field": _pick(raw, "pdfField", "pdf_field"),
        "checked_value": _pick(raw, "checkedValue", "checked_value"),
        "options_json": options,
        "page_number": _pick(raw, "pageNumber", "page_number"),
        "sort_order": int(_pick(raw, "sortOrder", "sort_order", default=0) or 0),
        "change_type": _pick(raw, "changeType", "change_type", default="reviewed") or "reviewed",
        "label_source": _pick(raw, "labelSource", "label_source", default="extracted") or "extracted",
        "prior_label": _pick(raw, "priorLabel", "prior_label"),
        "attention": _pick(raw, "attention"),
        "max_length": _pick(raw, "maxLength", "max_length"),
    }


def question_row_to_definition_item(row: dict) -> dict:
    item = {
        "id": row["question_key"],
        "label": row["label"],
        "type": row["input_type"],
        "required": bool(row.get("required")),
        "status": row.get("attention") or "verified",
    }
    if row.get("max_length"):
        item["max_length"] = row["max_length"]
    options = row.get("options_json")
    if options:
        item["options"] = options
    else:
        item["pdf_field"] = row.get("pdf_field")
        if row.get("checked_value"):
            item["checked_value"] = row["checked_value"]
    return item


def normalize_rules(raw_rules: Any) -> list:
    if raw_rules is None:
        return []
    if isinstance(raw_rules, str):
        try:
            parsed = json.loads(raw_rules)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(raw_rules, list):
        return raw_rules
    # C# JsonElement mis-serialization sometimes returns {"ValueKind": ...}.
    if isinstance(raw_rules, dict) and "ValueKind" in raw_rules:
        return []
    return []


def question_row_to_csharp(row: dict) -> dict:
    options = row.get("options_json")
    if options is not None and not isinstance(options, (dict, list, str, type(None))):
        options = None
    return {
        "questionKey": row.get("question_key"),
        "label": row.get("label") or "",
        "inputType": row.get("input_type") or "text",
        "required": bool(row.get("required")),
        "reviewStatus": row.get("review_status") or "needs_review",
        "pdfField": row.get("pdf_field"),
        "checkedValue": row.get("checked_value"),
        "optionsJson": options,
        "pageNumber": row.get("page_number"),
        "sortOrder": int(row.get("sort_order") or 0),
        "changeType": row.get("change_type") or "reviewed",
        "labelSource": row.get("label_source") or "extracted",
        "priorLabel": row.get("prior_label"),
        "attention": row.get("attention"),
        "maxLength": row.get("max_length"),
    }


def _map_csharp_error(exc: CsharpApiError, *, not_found_message: str | None = None) -> FormsEngineError:
    status = exc.status_code or 502
    if status == 404 and not_found_message:
        return FormsEngineError(not_found_message, 404)
    if status in {400, 404, 409}:
        return FormsEngineError(exc.message, status)
    if status in {401, 403}:
        return FormsEngineError(
            "The C# FormsEngine API rejected the request. Check the APIM subscription key.",
            status,
        )
    if status >= 500:
        return FormsEngineError(
            "The C# FormsEngine API failed while saving or publishing. "
            "Ask the backend team to check the FormsEngine publish/update logs.",
            502,
        )
    return FormsEngineError(exc.message or "The C# FormsEngine API request failed", 502)


def _normalize_listed_form(raw: dict) -> dict:
    """Normalize C# GET /api/forms-engine/forms item → Python picker shape."""
    form_id = str(_pick(raw, "formId", "form_id", default="") or "")
    display_name = _pick(raw, "displayName", "display_name", default=None)
    live_id = _pick(raw, "liveEditionId", "live_edition_id", default=None)
    live_edition = _pick(raw, "liveEdition", "live_edition", default=None)
    approved = _pick(raw, "liveApprovedCount", "live_approved_count", default=None)
    questions = _pick(raw, "liveQuestionCount", "live_question_count", default=0)
    live = None
    if live_id is not None:
        live = {
            "id": int(live_id),
            "edition": str(live_edition or ""),
            "storage_path": _pick(raw, "storagePath", "storage_path", default=None),
            "question_count": int(approved if approved is not None else (questions or 0)),
        }
    return {
        "form_id": form_id,
        "display_name": str(display_name or form_id),
        "live": live,
    }


def _list_forms_via_editions_fallback() -> list[dict]:
    """Legacy path: seed form ids from env, then load each editions pack."""
    forms: list[dict] = []
    for form_id in configured_form_ids():
        try:
            pack = csharp_client.get_editions(form_id)
        except CsharpApiError as exc:
            if exc.status_code == 404:
                forms.append({"form_id": form_id, "display_name": form_id, "live": None})
                continue
            raise _map_csharp_error(exc) from exc
        editions = [normalize_edition_summary(item) for item in (pack.get("editions") or [])]
        live_rows = [row for row in editions if row["is_live"] and row["status"] == "approved"]
        if len(live_rows) > 1:
            raise FormsEngineError(
                "This form has more than one live edition. An administrator or reviewer must resolve "
                "the conflict before the form can be used.",
                409,
            )
        live = None
        if live_rows:
            row = live_rows[0]
            live = {
                "id": row["id"],
                "edition": row["edition"],
                "storage_path": row["storage_path"],
                "question_count": row["approved_count"] or row["question_count"],
            }
        forms.append({"form_id": form_id, "display_name": form_id, "live": live})
    return forms


def list_forms() -> list[dict]:
    """Discover forms from C# list API; fall back to FORMS_ENGINE_FORM_IDS if needed."""
    try:
        pack = csharp_client.list_forms()
    except CsharpApiError as exc:
        if exc.status_code in {404, 501, 502, 503}:
            return _list_forms_via_editions_fallback()
        raise _map_csharp_error(exc) from exc

    raw_forms = pack.get("forms") if isinstance(pack, dict) else None
    if not isinstance(raw_forms, list):
        return _list_forms_via_editions_fallback()

    forms = [_normalize_listed_form(item) for item in raw_forms if isinstance(item, dict)]
    forms = [row for row in forms if row.get("form_id")]
    if not forms:
        return _list_forms_via_editions_fallback()
    forms.sort(key=lambda row: str(row.get("form_id") or ""))
    return forms


def list_editions(form_id: str) -> dict:
    try:
        pack = csharp_client.get_editions(form_id)
    except CsharpApiError as exc:
        raise _map_csharp_error(exc, not_found_message=f"Form {form_id} has no editions") from exc
    editions = [normalize_edition_summary(item) for item in (pack.get("editions") or [])]
    editions.sort(key=lambda row: (str(row.get("edition") or ""), int(row["id"])))
    if not editions:
        raise FormsEngineError(f"Form {form_id} has no editions", 404)
    live_rows = [row for row in editions if row["is_live"] and row["status"] == "approved"]
    if len(live_rows) > 1:
        raise FormsEngineError(
            "This form has more than one live edition. An administrator or reviewer must resolve "
            "the conflict before the form can be used.",
            409,
        )
    live = live_rows[0] if live_rows else None
    return {"form_id": form_id, "live": live, "editions": editions}


def _edition_detail(form_id: str, edition_id: int) -> dict:
    try:
        raw = csharp_client.get_edition(form_id, edition_id)
    except CsharpApiError as exc:
        raise _map_csharp_error(exc, not_found_message="That edition is not on this form") from exc
    edition = normalize_edition_summary(raw)
    if edition.get("form_id") and edition["form_id"] != form_id:
        raise FormsEngineError("That edition is not on this form", 404)
    questions = [normalize_question_row(item) for item in (raw.get("questions") or [])]
    questions.sort(key=lambda row: (row.get("sort_order") or 0, row.get("question_key") or ""))
    rules = normalize_rules(_pick(raw, "rules", "rulesJson", "rules_json", default=[]))
    return {"edition": edition, "questions": questions, "rules": rules}


def get_edition_detail(form_id: str, edition_id: int) -> dict:
    return _edition_detail(form_id, edition_id)


def live_definition(form_id: str) -> dict:
    try:
        raw = csharp_client.get_live_edition(form_id)
    except CsharpApiError as exc:
        raise _map_csharp_error(
            exc, not_found_message=f"No approved {form_id} form is live"
        ) from exc

    edition = normalize_edition_summary(raw)
    if edition["status"] != "approved" or not edition["is_live"]:
        raise FormsEngineError(f"No approved {form_id} form is live", 404)

    raw_questions = [item for item in (raw.get("questions") or []) if isinstance(item, dict)]
    question_rows = [normalize_question_row(item) for item in raw_questions]
    has_review_status = any(
        ("reviewStatus" in item) or ("review_status" in item) for item in raw_questions
    )
    if has_review_status:
        published_rows = [
            row
            for row in question_rows
            if str(row.get("review_status") or "").lower() == "approved"
        ]
    else:
        # Employee-shaped payloads without reviewStatus are returned as-is.
        published_rows = question_rows

    questions = [question_row_to_definition_item(row) for row in published_rows]
    rules = normalize_rules(_pick(raw, "rules", "rulesJson", "rules_json", default=[]))

    pending_draft = None
    try:
        pack = list_editions(form_id)
        drafts = [row for row in pack["editions"] if row["status"] == "draft"]
        if drafts:
            drafts.sort(key=lambda row: str(row.get("edition") or ""), reverse=True)
            pending_draft = {
                "id": drafts[0]["id"],
                "edition": drafts[0]["edition"],
                "status": drafts[0]["status"],
                "question_count": drafts[0]["question_count"],
            }
    except FormsEngineError:
        pending_draft = None

    unreviewed = sum(
        1 for row in question_rows if str(row.get("review_status") or "").lower() == "needs_review"
    )
    return {
        "form_id": form_id,
        "edition": edition["edition"],
        "source_pdf": edition["storage_path"],
        "questions": questions,
        "rules": rules,
        "edition_id": edition["id"],
        "pending_draft": pending_draft,
        "unreviewed_count": unreviewed,
        "stored_question_count": len(question_rows),
    }


def ensure_edition_pdf_file(form_id: str, edition_id: int, *, by_code: str | None = None) -> Path:
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = PDF_CACHE_DIR / f"{form_id}_{edition_id}.pdf"
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path
    try:
        if by_code:
            pdf_bytes = csharp_client.get_edition_pdf_by_code(form_id, by_code)
        else:
            pdf_bytes = csharp_client.get_edition_pdf(form_id, edition_id)
    except CsharpApiError as exc:
        raise _map_csharp_error(exc, not_found_message="The PDF file is missing from its stored path") from exc
    if not pdf_bytes.startswith(b"%PDF"):
        raise FormsEngineError("The C# FormsEngine API did not return a PDF file", 502)
    tmp_path = cache_path.with_suffix(".pdf.part")
    tmp_path.write_bytes(pdf_bytes)
    tmp_path.replace(cache_path)
    return cache_path


def resolve_blank_pdf(definition: dict) -> Path:
    """Resolve a local PDF path for filling, using C# download when needed."""
    form_id = definition.get("form_id")
    edition_id = definition.get("edition_id")
    source = definition.get("source_pdf") or ""
    if uses_csharp() and form_id and edition_id is not None:
        return ensure_edition_pdf_file(str(form_id), int(edition_id))
    local = ROOT / str(source)
    if local.is_file():
        return local
    if uses_csharp() and form_id and definition.get("edition"):
        # Fall back to by-code download when edition_id is absent.
        pack = list_editions(str(form_id))
        match = next((row for row in pack["editions"] if row["edition"] == definition["edition"]), None)
        if match:
            return ensure_edition_pdf_file(str(form_id), int(match["id"]), by_code=str(match["edition"]))
    raise FormsEngineError("The blank PDF is not available", 404)


def upload_completed_pdf_for_definition(
    definition: dict,
    pdf_bytes: bytes,
    *,
    filename: str,
) -> dict:
    """Upload a filled PDF to C# Blob when FormsEngine source is C#.

    Returns a normalized dict with blob_path when upload succeeds.
    Raises FormsEngineError on failure.
    """
    if not uses_csharp():
        return {}
    form_id = definition.get("form_id")
    edition_id = definition.get("edition_id")
    if not form_id or edition_id is None:
        raise FormsEngineError(
            "Cannot upload the completed PDF because the live edition id is missing.",
            500,
        )
    if not pdf_bytes.startswith(b"%PDF"):
        raise FormsEngineError("Cannot upload the completed PDF because it is not a valid PDF.", 500)
    try:
        raw = csharp_client.upload_completed_pdf(
            str(form_id),
            int(edition_id),
            pdf_bytes,
            filename=filename,
        )
    except CsharpApiError as exc:
        raise _map_csharp_error(exc) from exc
    blob_path = _pick(raw or {}, "blobPath", "blob_path", "storagePath", "storage_path")
    return {
        "form_id": _pick(raw or {}, "formId", "form_id", default=form_id),
        "edition_id": _pick(raw or {}, "editionId", "edition_id", default=edition_id),
        "blob_path": blob_path,
        "raw": raw or {},
    }


def _coerce_options(raw: Any) -> list | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, list):
        return raw
    return None


def _definition_item_from_payload_question(raw: dict) -> dict:
    """Build a filler definition question from a C# / mobile question object."""
    row = normalize_question_row(raw)
    # Prefer explicit options array from C# mobile live payload.
    options = _coerce_options(raw.get("options")) or _coerce_options(row.get("options_json"))
    if options is not None:
        row["options_json"] = options
    item = question_row_to_definition_item(row)
    # Choice options from C# may use camelCase pdfField / checkedValue.
    if item.get("type") == "choice" and item.get("options"):
        normalized_options = []
        for option in item["options"]:
            if not isinstance(option, dict):
                continue
            normalized_options.append(
                {
                    "value": _pick(option, "value", default="") or "",
                    "label": _pick(option, "label", default="") or "",
                    "pdf_field": _pick(option, "pdfField", "pdf_field"),
                    "checked_value": _pick(option, "checkedValue", "checked_value", default="1") or "1",
                }
            )
        item["options"] = normalized_options
    if item.get("type") == "checkbox" and not item.get("checked_value"):
        item["checked_value"] = "1"
    if item.get("type") in {"text", "number", "checkbox"} and not item.get("pdf_field"):
        # Fall back to question key when C# sends short field names equal to the key.
        item["pdf_field"] = item["id"]
    return item


def _apply_question_field_map(questions: list[dict], field_map: dict) -> list[dict]:
    if not field_map:
        return questions
    updated = []
    for question in questions:
        item = dict(question)
        key = item.get("id")
        mapped = field_map.get(key) if key in field_map else None
        if mapped is None and key is not None:
            mapped = field_map.get(str(key))
        if isinstance(mapped, str) and mapped.strip():
            if item.get("type") == "choice":
                # Map applies to single-field questions; leave choice options alone.
                pass
            else:
                item["pdf_field"] = mapped.strip()
        elif isinstance(mapped, dict):
            pdf_field = _pick(mapped, "pdfField", "pdf_field")
            if pdf_field:
                item["pdf_field"] = pdf_field
            checked = _pick(mapped, "checkedValue", "checked_value")
            if checked:
                item["checked_value"] = checked
        updated.append(item)
    return updated


def build_definition_from_fill_payload(payload: dict) -> dict:
    """Build a fill definition from C# POST /internal/fill body.

    Accepts camelCase from FormsEngine:
    - formId, editionId, edition, storagePath
    - answers (handled by caller)
    - questions[] and/or questionFieldMap / questionFields
    Falls back to live_definition(formId) when questions are omitted.
    """
    if not isinstance(payload, dict):
        raise FormsEngineError("Fill payload must be an object", 400)

    form_id = _pick(payload, "formId", "form_id")
    if not form_id:
        raise FormsEngineError("formId is required", 400)
    form_id = str(form_id)

    edition_id = _pick(payload, "editionId", "edition_id")
    edition = _pick(payload, "edition")
    storage_path = _pick(payload, "storagePath", "storage_path", "blankPdfUrl", "blank_pdf_url")

    field_map_raw = (
        payload.get("questionFieldMap")
        or payload.get("question_field_map")
        or payload.get("questionFields")
        or payload.get("question_fields")
        or payload.get("fieldMap")
        or payload.get("field_map")
        or {}
    )
    if not isinstance(field_map_raw, dict):
        field_map_raw = {}

    raw_questions = payload.get("questions")
    rules = normalize_rules(_pick(payload, "rules", "rulesJson", "rules_json", default=[]))

    if isinstance(raw_questions, list) and raw_questions:
        questions = [
            _definition_item_from_payload_question(item)
            for item in raw_questions
            if isinstance(item, dict) and (_pick(item, "questionKey", "question_key", "id"))
        ]
        if not questions:
            raise FormsEngineError("questions were provided but none were usable", 400)
    else:
        # Prefer live edition from C# when the caller only sends answers + ids.
        live = live_definition(form_id)
        questions = list(live.get("questions") or [])
        if not rules:
            rules = list(live.get("rules") or [])
        if edition_id is None:
            edition_id = live.get("edition_id")
        if not edition:
            edition = live.get("edition")
        if not storage_path:
            storage_path = live.get("source_pdf")
        if edition_id is not None and live.get("edition_id") is not None:
            if int(edition_id) != int(live["edition_id"]):
                raise FormsEngineError(
                    f"editionId {edition_id} is not the live edition for {form_id}",
                    409,
                )

    questions = _apply_question_field_map(questions, field_map_raw)
    if not questions:
        raise FormsEngineError(f"No questions available to fill for {form_id}", 400)
    if edition_id is None:
        raise FormsEngineError("editionId is required (or provide a live edition via C#)", 400)

    return {
        "form_id": form_id,
        "edition": str(edition or ""),
        "edition_id": int(edition_id),
        "source_pdf": storage_path or "",
        "questions": questions,
        "rules": rules,
    }


def fill_pdf_for_internal_request(payload: dict) -> dict:
    """Fill a blank PDF for C# orchestration. Does not upload to Blob.

    Returns dict with pdf_bytes, pdf_base64, filename, notices, form_id, edition_id, edition.
    Raises FormsEngineError or ValidationError (from filler).
    """
    from backend.filler import fill_pdf, normalize_answers, preview_notices

    definition = build_definition_from_fill_payload(payload)
    answers = payload.get("answers")
    if answers is None:
        raise FormsEngineError("answers is required", 400)
    if not isinstance(answers, dict):
        raise FormsEngineError("answers must be an object", 400)

    normalized = normalize_answers(definition, answers)
    source_pdf = resolve_blank_pdf(definition)
    pdf_bytes = fill_pdf(definition, normalized, source_pdf)
    if not pdf_bytes.startswith(b"%PDF"):
        raise FormsEngineError("Filled output was not a valid PDF", 500)

    form_id = definition["form_id"]
    edition = definition.get("edition") or "edition"
    filename = f"{form_id}-{edition}-completed.pdf"
    notices = list(preview_notices(definition, normalized))
    return {
        "form_id": form_id,
        "edition": definition.get("edition"),
        "edition_id": definition.get("edition_id"),
        "filename": filename,
        "notices": notices,
        "pdf_bytes": pdf_bytes,
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
    }


def _pdf_bytes_from_extract_payload(payload: dict) -> bytes:
    """Prefer inline pdf_base64 from C#; else download blank PDF via formId + editionId."""
    raw_b64 = _pick(payload, "pdf_base64", "pdfBase64", "pdf")
    if raw_b64:
        text = str(raw_b64).strip()
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]
        try:
            pdf_bytes = base64.b64decode(text, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise FormsEngineError(f"pdf_base64 is not valid base64: {exc}", 400) from exc
        if not pdf_bytes.startswith(b"%PDF"):
            raise FormsEngineError("pdf_base64 did not decode to a PDF", 400)
        return pdf_bytes

    form_id = _pick(payload, "formId", "form_id")
    edition_id = _pick(payload, "editionId", "edition_id")
    if form_id and edition_id is not None:
        path = ensure_edition_pdf_file(str(form_id), int(edition_id))
        return path.read_bytes()

    raise FormsEngineError(
        "Provide pdf_base64, or formId + editionId so the blank PDF can be loaded",
        400,
    )


def extract_questions_for_internal_request(payload: dict) -> dict:
    """Extract AcroForm fields for C# POST /internal/extract.

    C# owns persistence (FormQuestions). Python only returns question drafts.
    """
    if not isinstance(payload, dict):
        raise FormsEngineError("Extract payload must be an object", 400)

    form_id = _pick(payload, "formId", "form_id")
    edition_id = _pick(payload, "editionId", "edition_id")
    edition = _pick(payload, "edition")
    if not form_id:
        raise FormsEngineError("formId is required", 400)

    pdf_bytes = _pdf_bytes_from_extract_payload(payload)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(pdf_bytes)
            tmp_path = Path(handle.name)
        extracted = widgets(tmp_path)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not extracted:
        raise FormsEngineError("No fillable fields were found in that PDF", 400)

    seen: set[str] = set()
    questions: list[dict] = []
    for order, row in enumerate(extracted, start=1):
        question_key = row["name"] or f"field-{order}"
        if question_key in seen:
            question_key = f"{question_key}#{order}"
        seen.add(question_key)
        label = (row.get("label") or "").strip() or question_key
        questions.append(
            {
                "questionKey": question_key,
                "label": label,
                "inputType": input_type(row["type"]),
                "required": False,
                "pdfField": row["name"] or None,
                "checkedValue": row.get("checked_value"),
                "optionsJson": None,
                "pageNumber": row.get("page"),
                "sortOrder": order,
                "changeType": "new",
                "labelSource": "pdf",
            }
        )

    return {
        "formId": str(form_id),
        "editionId": int(edition_id) if edition_id is not None else None,
        "edition": str(edition) if edition is not None else None,
        "questions": questions,
        "importedCount": len(questions),
    }


def _edition_update_payload(edition: dict, *, status: str | None = None, rules: list | None = None) -> dict:
    return {
        "edition": edition.get("edition"),
        "storagePath": edition.get("storage_path") or edition.get("storagePath"),
        "comparedToPath": edition.get("compared_to_path")
        if "compared_to_path" in edition
        else edition.get("comparedToPath"),
        "status": status or edition.get("status") or "draft",
        "rules": rules if rules is not None else normalize_rules(edition.get("rules")),
    }


def save_questions_and_rules(
    form_id: str,
    edition_id: int,
    rows: list[dict],
    rules: list,
    *,
    status: str | None = None,
    compared_to_path: str | None = None,
) -> dict:
    detail = get_edition_detail(form_id, edition_id)
    edition = dict(detail["edition"])
    if compared_to_path is not None:
        edition["compared_to_path"] = compared_to_path
    payload_status = status or edition.get("status") or "draft"
    try:
        saved = csharp_client.replace_questions(
            form_id,
            edition_id,
            [question_row_to_csharp(row) for row in rows],
        )
        updated = csharp_client.update_edition(
            form_id,
            edition_id,
            _edition_update_payload(edition, status=payload_status, rules=rules),
        )
    except CsharpApiError as exc:
        raise _map_csharp_error(exc) from exc
    return {
        "edition": normalize_edition_summary({**edition, **(updated or {})}),
        "saved_count": int(_pick(saved or {}, "savedCount", "saved_count", default=len(rows)) or len(rows)),
        "rules": rules,
    }


def apply_reviewer_selection(
    existing_rows: list[dict],
    items: list[dict],
    confirm_generated_wording: bool,
    rules: object = None,
) -> tuple[list[dict], int, list]:
    if not isinstance(items, list) or not items:
        raise FormsEngineError("Choose at least one question to publish", 400)
    included = [item for item in items if item.get("include")]
    if not included:
        raise FormsEngineError("Choose at least one question to publish", 400)

    by_key = {row["question_key"]: dict(row) for row in existing_rows}
    keys = [item.get("question_key") for item in items]
    if any(key not in by_key for key in keys):
        raise FormsEngineError("The fields changed. Reload the page and try again.", 400)

    generated = [
        item["question_key"]
        for item in included
        if by_key[item["question_key"]].get("change_type") != "reviewed"
    ]
    if generated and not confirm_generated_wording:
        raise FormsEngineError(
            "These rows still use wording taken from the PDF. Confirm that wording before publishing.",
            400,
        )

    included_questions = []
    for item in items:
        key = item.get("question_key")
        label = " ".join(str(item.get("label") or "").split())
        if item.get("include") and not label:
            raise FormsEngineError("An included question needs wording", 400)
        row = by_key[key]
        if item.get("include"):
            row["label"] = label
            row["review_status"] = "approved"
            row["label_source"] = "reviewed"
            included_questions.append(question_row_to_definition_item(row))
        else:
            row["label"] = label or row.get("label") or ""
            row["review_status"] = "removed"
            row["label_source"] = "extracted"

    try:
        cleaned_rules = validate_rules(rules if rules is not None else [], included_questions)
    except RuleError as exc:
        raise FormsEngineError(str(exc), 400) from exc

    # Keep questions not present in the reviewer payload unchanged.
    touched = set(keys)
    merged = []
    for row in existing_rows:
        merged.append(by_key[row["question_key"]] if row["question_key"] in touched else row)
    # Preserve deterministic order.
    merged.sort(key=lambda row: (int(row.get("sort_order") or 0), str(row.get("question_key") or "")))
    return merged, len(included), cleaned_rules


def approve_edition_fields(
    form_id: str,
    edition_id: int,
    items: list[dict],
    confirm_generated_wording: bool,
    rules: object = None,
) -> dict:
    detail = get_edition_detail(form_id, edition_id)
    rows, published, cleaned_rules = apply_reviewer_selection(
        detail["questions"], items, confirm_generated_wording, rules
    )
    saved = save_questions_and_rules(
        form_id,
        edition_id,
        rows,
        cleaned_rules,
        status=detail["edition"].get("status"),
    )
    return {
        "form_id": form_id,
        "edition_id": edition_id,
        "edition": saved["edition"].get("edition") or detail["edition"]["edition"],
        "published_questions": published,
    }


def publish_edition(
    form_id: str,
    edition_id: int,
    items: list[dict],
    confirm_generated_wording: bool,
    rules: object = None,
) -> dict:
    detail = get_edition_detail(form_id, edition_id)
    edition = detail["edition"]
    if edition.get("status") != "draft":
        raise FormsEngineError(
            "That edition is already approved, so it was not made live",
            409,
        )
    rows, published, cleaned_rules = apply_reviewer_selection(
        detail["questions"], items, confirm_generated_wording, rules
    )
    save_questions_and_rules(
        form_id,
        edition_id,
        rows,
        cleaned_rules,
        status="draft",
    )
    try:
        result = csharp_client.publish_edition(form_id, edition_id, True)
    except CsharpApiError as exc:
        raise _map_csharp_error(exc) from exc
    return {
        "form_id": form_id,
        "edition_id": edition_id,
        "edition": _pick(result or {}, "edition", default=edition["edition"]),
        "status": "approved",
        "is_live": True,
        "published_questions": published,
    }


def import_live_fields(form_id: str) -> dict:
    try:
        raw_live = csharp_client.get_live_edition(form_id)
    except CsharpApiError as exc:
        raise _map_csharp_error(exc, not_found_message=f"No approved {form_id} form is live") from exc
    edition = normalize_edition_summary(raw_live)
    existing = [normalize_question_row(item) for item in (raw_live.get("questions") or [])]
    if existing:
        raise FormsEngineError(
            "This edition already has fields. Review those fields instead of reading the PDF again.",
            409,
        )
    pdf_path = ensure_edition_pdf_file(form_id, int(edition["id"]))
    extracted = widgets(pdf_path)
    if not extracted:
        raise FormsEngineError("No fillable fields were found in that PDF", 400)
    seen: set[str] = set()
    rows: list[dict] = []
    for order, row in enumerate(extracted, start=1):
        question_key = row["name"] or f"field-{order}"
        if question_key in seen:
            question_key = f"{question_key}#{order}"
        seen.add(question_key)
        rows.append(
            {
                "question_key": question_key,
                "label": row["label"],
                "input_type": input_type(row["type"]),
                "required": False,
                "review_status": "needs_review",
                "pdf_field": row["name"] or None,
                "checked_value": row["checked_value"],
                "options_json": None,
                "page_number": row["page"],
                "sort_order": order,
                "change_type": "imported",
                "label_source": "extracted",
                "prior_label": None,
                "attention": None,
                "max_length": None,
            }
        )
    save_questions_and_rules(
        form_id,
        int(edition["id"]),
        rows,
        normalize_rules(raw_live.get("rules")),
        status=edition.get("status") or "approved",
    )
    return {
        "form_id": form_id,
        "edition_id": edition["id"],
        "edition": edition["edition"],
        "storage_path": edition["storage_path"],
        "question_count": len(rows),
    }


def _labels_from_rows(rows: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for row in rows:
        if str(row.get("review_status") or "").lower() != "approved":
            continue
        if row.get("pdf_field"):
            mapping[row["pdf_field"]] = {
                "question_key": row["question_key"],
                "label": row["label"],
                "input_type": row["input_type"],
            }
        for option in row.get("options_json") or []:
            field = option.get("pdf_field") if isinstance(option, dict) else None
            if field:
                mapping[field] = {
                    "question_key": row["question_key"],
                    "label": row["label"],
                    "input_type": row["input_type"],
                }
    return mapping


def compare_with_edition(form_id: str, target_edition_id: int) -> dict:
    from backend.store import _apply_carried_label  # local import avoids cycle at module load

    try:
        raw_live = csharp_client.get_live_edition(form_id)
    except CsharpApiError as exc:
        raise _map_csharp_error(
            exc, not_found_message=f"Form {form_id} has no live edition to compare from"
        ) from exc
    current = normalize_edition_summary(raw_live)
    current_rows = [normalize_question_row(item) for item in (raw_live.get("questions") or [])]
    detail = get_edition_detail(form_id, target_edition_id)
    target = detail["edition"]
    if target["id"] == current["id"]:
        raise FormsEngineError("Choose a different PDF from the current one", 400)
    if target.get("status") != "draft":
        raise FormsEngineError(
            "That edition is already approved, so its questions were not replaced",
            409,
        )

    old_pdf = ensure_edition_pdf_file(form_id, int(current["id"]))
    new_pdf = ensure_edition_pdf_file(form_id, int(target["id"]))
    rows = build_rows(widgets(old_pdf), widgets(new_pdf))
    live_labels = _labels_from_rows(current_rows)

    question_rows: list[dict] = []
    carried = 0
    for order, row in enumerate(rows, start=1):
        label, prior_label, label_source, attention = _apply_carried_label(row, live_labels)
        if label_source == "carried":
            carried += 1
        question_rows.append(
            {
                "question_key": row["question_key"],
                "label": label,
                "input_type": input_type(row["type"]),
                "required": False,
                "review_status": "needs_review",
                "pdf_field": row["name"] if row["change_type"] != "removed" else None,
                "checked_value": row["checked_value"] if row["change_type"] != "removed" else None,
                "options_json": None,
                "page_number": row["page"],
                "sort_order": order,
                "change_type": row["change_type"],
                "label_source": label_source,
                "prior_label": prior_label,
                "attention": attention,
                "max_length": None,
            }
        )

    save_questions_and_rules(
        form_id,
        int(target["id"]),
        question_rows,
        [],
        status="draft",
        compared_to_path=current["storage_path"],
    )
    counts = {"added": 0, "unchanged": 0, "removed": 0, "carried_labels": carried}
    for row in rows:
        counts[row["change_type"]] += 1
    return {
        "form_id": form_id,
        "edition_id": target["id"],
        "edition": target["edition"],
        "compared_to_path": current["storage_path"],
        "storage_path": target["storage_path"],
        "counts": counts,
        "question_count": len(rows),
    }
