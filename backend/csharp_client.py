"""HTTP client for the C# FormsEngine APIs (SQL Server + Blob via APIM)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from backend.draft_job import load_env

DEFAULT_TIMEOUT = 60.0
PDF_TIMEOUT = 120.0


class CsharpApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


def _settings() -> tuple[str, str]:
    load_env()
    base = (os.environ.get("CSHARP_API_BASE_URL") or "").rstrip("/")
    key = os.environ.get("OCP_APIM_SUBSCRIPTION_KEY") or ""
    if not base:
        raise CsharpApiError("CSHARP_API_BASE_URL is missing from .env")
    if not key:
        raise CsharpApiError("OCP_APIM_SUBSCRIPTION_KEY is missing from .env")
    return base, key


def configured() -> bool:
    load_env()
    return bool(
        (os.environ.get("CSHARP_API_BASE_URL") or "").strip()
        and (os.environ.get("OCP_APIM_SUBSCRIPTION_KEY") or "").strip()
    )


def _headers(key: str, *, accept: str = "application/json") -> dict[str, str]:
    return {
        "Ocp-Apim-Subscription-Key": key,
        "Accept": accept,
    }


def _client(timeout: float = DEFAULT_TIMEOUT, *, accept: str = "application/json") -> httpx.Client:
    base, key = _settings()
    return httpx.Client(
        base_url=base,
        headers=_headers(key, accept=accept),
        timeout=timeout,
        follow_redirects=True,
    )


def _raise_for_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = f"C# API {response.request.method} {response.request.url.path} failed ({response.status_code})"
    body: Any
    try:
        body = response.json()
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict) and first.get("message"):
                    message = str(first["message"])
                elif isinstance(first, str):
                    message = first
            elif body.get("message"):
                message = str(body["message"])
            elif body.get("title"):
                message = str(body["title"])
            elif isinstance(body.get("detail"), str):
                message = body["detail"]
    except Exception:
        body = (response.text or "")[:500]
    raise CsharpApiError(message, response.status_code, body)


def list_forms() -> dict:
    """GET /api/forms-engine/forms — discover all forms + live edition summary."""
    with _client() as client:
        response = client.get("/api/forms-engine/forms")
        _raise_for_response(response)
        return response.json()


def get_editions(form_id: str) -> dict:
    with _client() as client:
        response = client.get(f"/api/forms-engine/forms/{form_id}/editions")
        _raise_for_response(response)
        return response.json()


def get_edition(form_id: str, edition_id: int) -> dict:
    with _client() as client:
        response = client.get(f"/api/forms-engine/forms/{form_id}/editions/{edition_id}")
        _raise_for_response(response)
        return response.json()


def get_live_edition(form_id: str) -> dict:
    with _client() as client:
        response = client.get(f"/api/forms-engine/forms/{form_id}/live")
        _raise_for_response(response)
        return response.json()


def get_edition_pdf(form_id: str, edition_id: int) -> bytes:
    with _client(timeout=PDF_TIMEOUT, accept="application/pdf, application/octet-stream, */*") as client:
        response = client.get(f"/api/forms-engine/forms/{form_id}/editions/{edition_id}/pdf")
        _raise_for_response(response)
        content = response.content
        if not content:
            raise CsharpApiError("C# API returned an empty PDF", response.status_code, None)
        return content


def get_edition_pdf_by_code(form_id: str, edition: str) -> bytes:
    with _client(timeout=PDF_TIMEOUT, accept="application/pdf, application/octet-stream, */*") as client:
        response = client.get(f"/api/forms-engine/forms/{form_id}/editions/by-code/{edition}/pdf")
        _raise_for_response(response)
        content = response.content
        if not content:
            raise CsharpApiError("C# API returned an empty PDF", response.status_code, None)
        return content


def update_edition(form_id: str, edition_id: int, payload: dict) -> dict:
    with _client() as client:
        response = client.put(
            f"/api/forms-engine/forms/{form_id}/editions/{edition_id}",
            json=payload,
        )
        _raise_for_response(response)
        return response.json() if response.content else {}


def replace_questions(form_id: str, edition_id: int, questions: list[dict]) -> dict:
    with _client() as client:
        response = client.put(
            f"/api/forms-engine/forms/{form_id}/editions/{edition_id}/questions",
            json={"questions": questions},
        )
        _raise_for_response(response)
        return response.json() if response.content else {"editionId": edition_id, "savedCount": len(questions)}


def publish_edition(form_id: str, edition_id: int, confirm: bool = True) -> dict:
    with _client() as client:
        response = client.post(
            f"/api/forms-engine/forms/{form_id}/editions/{edition_id}/publish",
            json={"confirm": confirm},
        )
        _raise_for_response(response)
        return response.json() if response.content else {}


def import_edition_fields(form_id: str, edition_id: int, *, replace: bool = False) -> dict:
    """POST .../editions/{editionId}/import-fields — C# extract orchestration (calls Python /internal/extract)."""
    with _client(timeout=PDF_TIMEOUT) as client:
        response = client.post(
            f"/api/forms-engine/forms/{form_id}/editions/{edition_id}/import-fields",
            json={"replace": bool(replace)},
        )
        _raise_for_response(response)
        return response.json() if response.content else {}


def upload_completed_pdf(
    form_id: str,
    edition_id: int,
    pdf_bytes: bytes,
    filename: str = "completed.pdf",
    extra_fields: dict[str, str] | None = None,
) -> dict:
    files = {"file": (filename, pdf_bytes, "application/pdf")}
    data = dict(extra_fields or {})
    with _client(timeout=PDF_TIMEOUT) as client:
        response = client.post(
            f"/api/forms-engine/forms/{form_id}/editions/{edition_id}/completed-pdf",
            files=files,
            data=data,
        )
        _raise_for_response(response)
        return response.json() if response.content else {}


def health() -> dict:
    """Connectivity check using editions list for fw4."""
    data = get_editions("fw4")
    return {
        "ok": True,
        "formId": data.get("formId") or data.get("form_id") or "fw4",
        "editionCount": len(data.get("editions") or []),
        "liveEditionId": data.get("liveEditionId") or data.get("live_edition_id"),
    }
