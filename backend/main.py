"""Local preview API for the 2026 Form W-4 question file."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.draft_job import load_env
from backend.filler import (
    DefinitionError,
    ValidationError,
    fill_pdf,
    load_definition,
    normalize_answers,
    preview_notices,
    public_definition,
    public_questions,
)
from backend.forms_engine import (
    FormsEngineError,
    extract_questions_for_internal_request,
    fill_pdf_for_internal_request,
    resolve_blank_pdf,
    upload_completed_pdf_for_definition,
    uses_csharp,
)
from backend import csharp_client
from backend.csharp_client import CsharpApiError
from backend.store import (
    ReviewError,
    approve_draft,
    approve_edition_fields,
    compare_with_edition,
    draft_review,
    edition_field_highlight,
    edition_questions,
    ensure_ready,
    import_live_fields,
    list_editions,
    list_forms,
    live_definition,
    preview_definition_from_items,
    publish_edition,
    run_comparison,
    sample_answers_for_live,
    use_reviewed_wording,
)

ROOT = Path(__file__).resolve().parents[1]
DEFINITION_PATH = ROOT / "forms" / "fw4_2026_questions.json"
SAMPLE_PATH = ROOT / "experiments" / "sample_answers.json"

try:
    DEFINITION = load_definition(DEFINITION_PATH)
    SOURCE_PDF = ROOT / DEFINITION["source_pdf"]
    if not SOURCE_PDF.is_file():
        raise DefinitionError(f"Blank PDF is missing: {SOURCE_PDF}")
except (OSError, json.JSONDecodeError, DefinitionError) as exc:
    raise RuntimeError(f"W-4 question file cannot be loaded: {exc}") from exc

def _cors_origins() -> list[str]:
    load_env()
    raw = (os.environ.get("CORS_ORIGINS") or "").strip()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:3001",
        "http://localhost:3001",
    ]


app = FastAPI(title="Forms Studio")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)

ensure_ready()


def _internal_fill_authorized(x_api_key: str | None, authorization: str | None) -> bool:
    """Optional shared secret for C# → Python fill calls.

    When PYTHON_FILL_API_KEY / FormsEngine-style key is unset, the endpoint is open
    (local POC). When set, require matching X-Api-Key or Bearer token.
    """
    load_env()
    expected = (
        (os.environ.get("PYTHON_FILL_API_KEY") or "").strip()
        or (os.environ.get("FORMS_ENGINE_PYTHON_API_KEY") or "").strip()
    )
    if not expected:
        return True
    if x_api_key and x_api_key.strip() == expected:
        return True
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if value == expected:
            return True
    return False


@app.post("/internal/fill")
async def internal_fill(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """C# orchestration fill endpoint. Returns pdf_base64 only; C# owns Blob upload."""
    if not _internal_fill_authorized(x_api_key, authorization):
        return JSONResponse(
            status_code=401,
            content={"errors": [{"id": "", "message": "Unauthorized fill request"}]},
        )
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"errors": [{"id": "", "message": "Request body must be JSON"}]},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"errors": [{"id": "", "message": "Request body must be an object"}]},
        )
    try:
        result = fill_pdf_for_internal_request(payload)
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"errors": exc.errors})
    except FormsEngineError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"errors": [{"id": "", "message": exc.message}]},
        )
    except ReviewError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"errors": [{"id": "", "message": exc.message}]},
        )
    except (FileNotFoundError, RuntimeError) as exc:
        return JSONResponse(
            status_code=500,
            content={"errors": [{"id": "", "message": f"The completed PDF could not be created: {exc}"}]},
        )
    return JSONResponse(
        content={
            "pdf_base64": result["pdf_base64"],
            "filename": result["filename"],
            "notices": result["notices"],
            "formId": result["form_id"],
            "editionId": result["edition_id"],
            "edition": result["edition"],
        }
    )


@app.post("/internal/extract")
async def internal_extract(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """C# orchestration extract endpoint. Returns questions[]; C# owns FormQuestions write."""
    if not _internal_fill_authorized(x_api_key, authorization):
        return JSONResponse(
            status_code=401,
            content={"errors": [{"id": "", "message": "Unauthorized extract request"}]},
        )
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"errors": [{"id": "", "message": "Request body must be JSON"}]},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"errors": [{"id": "", "message": "Request body must be an object"}]},
        )
    try:
        result = extract_questions_for_internal_request(payload)
    except FormsEngineError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"errors": [{"id": "", "message": exc.message}]},
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content={"errors": [{"id": "", "message": f"The PDF fields could not be extracted: {exc}"}]},
        )
    return JSONResponse(
        content={
            "questions": result["questions"],
            "formId": result.get("formId"),
            "editionId": result.get("editionId"),
            "edition": result.get("edition"),
            "importedCount": result.get("importedCount"),
        }
    )


@app.get("/api/health")
def health() -> dict:
    payload = {
        "status": "ok",
        "form_id": DEFINITION["form_id"],
        "edition": DEFINITION["edition"],
        "forms_engine_source": "csharp" if uses_csharp() else "postgres",
    }
    if uses_csharp():
        try:
            payload["csharp"] = csharp_client.health()
        except Exception as exc:  # noqa: BLE001 - health must stay available
            payload["csharp"] = {"ok": False, "error": str(exc)}
    return payload


def current_definition() -> dict:
    return load_definition(DEFINITION_PATH)


@app.get("/api/forms/fw4/2026/questions")
def questions() -> dict:
    definition = current_definition()
    return {
        "form_id": definition["form_id"],
        "edition": definition["edition"],
        "questions": public_questions(definition),
    }


@app.get("/api/forms/fw4/2026/sample-answers")
def sample_answers() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


@app.post("/api/forms/fw4/2026/answers")
def answers(payload: dict) -> JSONResponse:
    raw_answers = payload.get("answers") if isinstance(payload, dict) else None
    try:
        definition = current_definition()
        normalized = normalize_answers(definition, raw_answers)
        pdf_bytes = fill_pdf(definition, normalized, ROOT / definition["source_pdf"])
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"errors": exc.errors})
    except FileNotFoundError:
        return JSONResponse(
            status_code=500,
            content={"errors": [{"id": "", "message": "The blank 2026 PDF is not available"}]},
        )
    except RuntimeError:
        return JSONResponse(
            status_code=500,
            content={"errors": [{"id": "", "message": "The preview PDF could not be created"}]},
        )
    return JSONResponse(
        content={
            "filename": "fw4-2026-preview.pdf",
            "notices": preview_notices(definition, normalized),
            "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        }
    )


def _pdf_response(definition: dict, raw_answers, filename: str) -> JSONResponse:
    try:
        normalized = normalize_answers(definition, raw_answers)
        source_pdf = resolve_blank_pdf(definition)
        pdf_bytes = fill_pdf(definition, normalized, source_pdf)
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"errors": exc.errors})
    except FormsEngineError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"errors": [{"id": "", "message": exc.message}]},
        )
    except (FileNotFoundError, RuntimeError):
        return JSONResponse(
            status_code=500,
            content={"errors": [{"id": "", "message": "The preview PDF could not be created"}]},
        )

    notices = list(preview_notices(definition, normalized))
    payload = {
        "filename": filename,
        "edition": definition["edition"],
        "form_id": definition.get("form_id"),
        "edition_id": definition.get("edition_id"),
        "notices": notices,
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "blob_path": None,
        "stored_to_blob": False,
    }

    # Keep the preview even if Blob upload fails; surface a notice instead.
    if uses_csharp() and definition.get("form_id") and definition.get("edition_id") is not None:
        try:
            stored = upload_completed_pdf_for_definition(definition, pdf_bytes, filename=filename)
            payload["blob_path"] = stored.get("blob_path")
            payload["stored_to_blob"] = bool(stored.get("blob_path"))
            if payload["stored_to_blob"]:
                notices.append("Completed PDF was saved to Azure Blob Storage.")
            else:
                notices.append("Completed PDF was generated, but the Blob path was missing in the C# response.")
        except FormsEngineError as exc:
            notices.append(f"Completed PDF preview was created, but Blob upload failed: {exc.message}")
        payload["notices"] = notices

    return JSONResponse(content=payload)


def _review_error(exc: ReviewError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"errors": [{"id": "", "message": exc.message}]})


@app.get("/api/forms/live")
def live_form() -> JSONResponse:
    try:
        definition = live_definition()
    except ReviewError as exc:
        return _review_error(exc)
    pending = definition.pop("pending_draft")
    edition_id = definition.pop("edition_id")
    return {
        "form_id": definition["form_id"],
        "edition_id": edition_id,
        "edition": definition["edition"],
        "questions": public_questions(definition),
        "pending_draft": pending,
    }


@app.get("/api/forms/live/sample-answers")
def live_sample_answers():
    try:
        return sample_answers_for_live()
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/forms/live/answers")
def live_answers(payload: dict) -> JSONResponse:
    try:
        definition = live_definition()
    except ReviewError as exc:
        return _review_error(exc)
    definition.pop("pending_draft", None)
    raw_answers = payload.get("answers") if isinstance(payload, dict) else None
    return _pdf_response(definition, raw_answers, f"fw4-{definition['edition']}-completed.pdf")


def _live_payload(form_id: str) -> dict:
    definition = live_definition(form_id)
    pending = definition.pop("pending_draft")
    edition_id = definition.pop("edition_id")
    unreviewed_count = definition.pop("unreviewed_count")
    stored_question_count = definition.pop("stored_question_count")
    public = public_definition(definition)
    return {
        "form_id": definition["form_id"],
        "edition_id": edition_id,
        "edition": definition["edition"],
        "storage_path": definition["source_pdf"],
        "questions": public["questions"],
        "rules": public["rules"],
        "pending_draft": pending,
        "unreviewed_count": unreviewed_count,
        "stored_question_count": stored_question_count,
    }


@app.get("/api/forms")
def forms():
    try:
        return list_forms()
    except ReviewError as exc:
        return _review_error(exc)


@app.get("/api/forms/{form_id}/live")
def live_form_by_id(form_id: str):
    try:
        return _live_payload(form_id)
    except ReviewError as exc:
        return _review_error(exc)


@app.get("/api/forms/{form_id}/live/sample-answers")
def live_sample_by_id(form_id: str):
    try:
        return sample_answers_for_live(form_id)
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/forms/{form_id}/live/answers")
def live_answers_by_id(form_id: str, payload: dict) -> JSONResponse:
    try:
        definition = live_definition(form_id)
    except ReviewError as exc:
        return _review_error(exc)
    definition.pop("pending_draft", None)
    # Keep edition_id on the definition so completed-pdf can be uploaded to C#.
    raw_answers = payload.get("answers") if isinstance(payload, dict) else None
    return _pdf_response(definition, raw_answers, f"{form_id}-{definition['edition']}-completed.pdf")


@app.post("/api/forms/{form_id}/live/fields")
def import_fields(form_id: str):
    try:
        return import_live_fields(form_id)
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/forms/{form_id}/editions/{edition_id}/import-fields")
async def import_edition_fields(form_id: str, edition_id: int, request: Request):
    """Studio → C# import-fields (C# calls Python /internal/extract). Works on draft editions.

    Outbound C# call is sync httpx; run in a thread so this process can still serve
    C#'s callback to POST /internal/extract (avoids single-worker deadlock → 504).
    """
    replace = False
    try:
        body = await request.json()
        if isinstance(body, dict):
            replace = bool(body.get("replace"))
    except Exception:
        replace = False
    try:
        return await asyncio.to_thread(
            csharp_client.import_edition_fields,
            form_id,
            edition_id,
            replace=replace,
        )
    except CsharpApiError as exc:
        return JSONResponse(
            status_code=exc.status_code or 502,
            content={"errors": [{"id": "", "message": exc.message}]},
        )
    except FormsEngineError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"errors": [{"id": "", "message": exc.message}]},
        )


@app.post("/api/forms/{form_id}/editions/{edition_id}/publish")
def publish_fields(form_id: str, edition_id: int, payload: dict):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"errors": [{"id": "", "message": "Invalid request"}]})
    try:
        return publish_edition(
            form_id,
            edition_id,
            payload.get("questions") or [],
            bool(payload.get("confirm_generated_wording")),
            payload.get("rules"),
        )
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/forms/{form_id}/editions/{edition_id}/approve")
def approve_fields(form_id: str, edition_id: int, payload: dict):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"errors": [{"id": "", "message": "Invalid request"}]})
    try:
        return approve_edition_fields(
            form_id,
            edition_id,
            payload.get("questions") or [],
            bool(payload.get("confirm_generated_wording")),
            payload.get("rules"),
        )
    except ReviewError as exc:
        return _review_error(exc)


@app.get("/api/forms/{form_id}/editions")
def form_editions(form_id: str):
    try:
        return list_editions(form_id)
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/forms/{form_id}/compare")
def compare_selected(form_id: str, payload: dict):
    edition_id = payload.get("edition_id") if isinstance(payload, dict) else None
    try:
        return compare_with_edition(form_id, int(edition_id))
    except (ReviewError, TypeError, ValueError) as exc:
        if isinstance(exc, ReviewError):
            return _review_error(exc)
        return JSONResponse(
            status_code=400,
            content={"errors": [{"id": "", "message": "Choose the PDF to compare"}]},
        )


@app.get("/api/forms/{form_id}/editions/{edition_id}")
def edition_detail(form_id: str, edition_id: int):
    try:
        return edition_questions(form_id, edition_id)
    except ReviewError as exc:
        return _review_error(exc)


@app.get("/api/forms/{form_id}/editions/{edition_id}/field-highlight")
def edition_highlight(form_id: str, edition_id: int, question_key: str):
    try:
        return edition_field_highlight(form_id, edition_id, question_key)
    except ReviewError as exc:
        return _review_error(exc)


@app.get("/api/reviews/fw4/2026")
def review_draft():
    try:
        return draft_review()
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/jobs/compare")
def compare_forms():
    try:
        return run_comparison()
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/reviews/fw4/2026/use-reviewed-wording")
def reviewed_wording():
    try:
        return use_reviewed_wording()
    except ReviewError as exc:
        return _review_error(exc)


@app.post("/api/reviews/fw4/2026/preview")
def review_preview(payload: dict) -> JSONResponse:
    items = payload.get("questions") if isinstance(payload, dict) else None
    try:
        definition = preview_definition_from_items(items or [])
    except ReviewError as exc:
        return _review_error(exc)
    raw_answers = {}
    for question in definition["questions"]:
        if question["type"] == "checkbox":
            raw_answers[question["id"]] = True
        elif question["type"] == "choice":
            raw_answers[question["id"]] = question["options"][0]["value"]
        elif question["type"] == "number":
            raw_answers[question["id"]] = "1"
        else:
            raw_answers[question["id"]] = "Test"
    return _pdf_response(definition, raw_answers, "fw4-2026-draft-preview.pdf")


@app.post("/api/reviews/fw4/2026/approve")
def review_approve(payload: dict):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"errors": [{"id": "", "message": "Invalid request"}]})
    try:
        return approve_draft(payload.get("questions") or [], bool(payload.get("confirm_generated_wording")))
    except ReviewError as exc:
        return _review_error(exc)
