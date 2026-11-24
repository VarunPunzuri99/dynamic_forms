# FormsEngine → React Native Integration Guide

**Audience:** Cursor / engineers working in the **FMSOnboarding** React Native app (and optionally this Forms POC repo).  
**Purpose:** Single source of truth for how employee forms should flow after C# FormsEngine + Python fill are live.  
**Last updated:** 20 November 2026

---

## 1. Goal

Employee fills onboarding forms in the **React Native** app.  
RN talks **only to C#**. C# calls Python for PDF fill. C# stores the completed PDF in Azure Blob.

```text
Employee (RN)
    ↓
C#  GET  /api/forms-engine/forms/{formId}/live          ← questions + rules
    ↓  (employee answers in RN)
C#  POST /api/forms-engine/forms/{formId}/live/submissions
    ↓  validate against live SQL edition
C#  POST http://<python-host>:8001/internal/fill        ← internal only; RN never calls this
    ↓
Python  loads blank PDF from C#, binds answers, returns pdf_base64
    ↓
C#  uploads filled PDF to Azure Blob
    ↓
RN  receives success + blobPath
```

**Never call Python from React Native.**

---

## 2. Who owns what

| System | Owns |
|---|---|
| **C# + SQL Server + Blob** | Forms, editions, questions, rules, blank PDFs, completed PDFs, validation, Blob upload |
| **Python FastAPI** (Forms POC, admin `:8001`) | Fill / rules engine only — `POST /internal/fill` |
| **Forms Studio UI** (`:3001`) | Reviewers/testers only — **not** the employee path |
| **React Native (FMSOnboarding)** | Employee UX — load questions, collect answers, submit to C#, show preview when auth’d PDF is available |

---

## 3. Deployed Python (office / Dev-Testing reachability)

| What | URL |
|---|---|
| Forms Studio UI | `http://10.147.219.75:3001` |
| Python API | `http://10.147.219.75:8001` |
| Internal fill (C# only) | `http://10.147.219.75:8001/internal/fill` |

- Host: admin PC **HYD-WS-023** (Docker beside TrackPro; ports **3001 / 8001**).
- C# Dev/Testing is already configured:

```json
"FormsEngine": {
  "PythonBaseUrl": "http://10.147.219.75:8001",
  "FillPath": "/internal/fill"
}
```

- **LAN / VPN only.** Public Azure Production cannot reach `10.147.x.x` without VPN/ExpressRoute + Production `FormsEngine` config.
- `PYTHON_FILL_API_KEY` / C# `PythonApiKey`: **off** for now (both sides).

---

## 4. C# FormsEngine APIs (for React Native)

Same APIM product as existing onboarding APIs (**after** FormsEngine Swagger is deployed/imported):

```text
{APIM}/empselser/api/forms-engine/...
```

Same host + `Ocp-Apim-Subscription-Key` + Bearer token pattern as `/OnBoarding/...`.  
**Not** a separate FormsEngine gateway.

### 4.1 List forms (optional picker)

```http
GET /api/forms-engine/forms
```

Example response:

```json
{
  "forms": [
    {
      "formId": "fw4",
      "displayName": "Federal Form W-4",
      "liveEditionId": 2,
      "liveEdition": "2026",
      "liveQuestionCount": 15,
      "liveApprovedCount": 15
    },
    {
      "formId": "colorado",
      "displayName": "Colorado",
      "liveEditionId": 3,
      "liveEdition": "2025",
      "liveQuestionCount": 7,
      "liveApprovedCount": 7
    }
  ]
}
```

**Do not** replace `GET /OnBoarding/{id}/Forms` with this yet. Onboarding catalog + status still come from the legacy Forms list.

### 4.2 Load live questions

```http
GET /api/forms-engine/forms/{formId}/live
```

Returns live edition + approved questions + rules (**no PDF bytes**).  
Answer keys on submit **must** match `questionKey` from this response.

### 4.3 Submit / preview (fill + Blob)

```http
POST /api/forms-engine/forms/{formId}/live/submissions
```

Request:

```json
{
  "answers": {
    "employee_name": "Jane Doe",
    "exempt": true
  },
  "employeeId": "12345",
  "submissionId": "optional-client-id"
}
```

| Field | Meaning |
|---|---|
| `answers` | Required. Keys = `questionKey` from live. Required questions must be present. |
| `employeeId` | **Optional.** Blob filename tag only. Not validated against SQL. Prefer `String(essUserKey)`. Does **not** drive auth, ownership, or form status. |
| `submissionId` | Optional client id for tracing / filename. |

Success:

```json
{
  "formId": "fw4",
  "editionId": 2,
  "edition": "2026",
  "blobPath": "https://....blob.core.windows.net/temporaryfiles/Test_State_Forms/fw4/2026_completed_....pdf",
  "storedToBlob": true,
  "submissionId": "...",
  "notices": []
}
```

Errors:

```json
{ "errors": [{ "message": "..." }] }
```

### 4.4 What submit does **not** do (confirmed)

| Expectation | Reality |
|---|---|
| Mark onboarding form Complete | **No.** Does not update `EssUserOnBoardingForm` / `FormStatusValue`. |
| Include signature | **No.** Out of scope for FormsEngine v1. |
| Public/SAS `blobPath` | **No.** Private container (`PublicAccessType.None`), no SAS. |
| Map `essFormKey` → `formId` | **No.** No DB link yet. |

Legacy W-4 (`POST /OnBoarding/InsertW4Details`) still owns signature + `FormStatusValue = "P"` after signed submit.

---

## 5. Python internal fill (C# only — reference)

```http
POST /internal/fill
Content-Type: application/json
```

Outbound body from C# (camelCase):

```json
{
  "formId": "fw4",
  "editionId": 123,
  "edition": "2026",
  "storagePath": "https://....blob.../blank.pdf",
  "answers": { "questionKey": "value" },
  "rules": [],
  "questions": [
    {
      "questionKey": "...",
      "pdfField": "...",
      "inputType": "...",
      "checkedValue": "..."
    }
  ]
}
```

- Python loads blank PDF via C#  
  `GET /api/forms-engine/forms/{formId}/editions/{editionId}/pdf`  
  (**not** by downloading `storagePath` directly).
- Returns `{ pdf_base64, filename, notices, formId, editionId, edition }`.
- **C#** uploads Blob. Python `/internal/fill` does **not** write Blob.

Forms Studio POC UI preview is a **different** path (browser → Python `/api/...`); employees do not use it.

---

## 6. React Native today (audit summary — FMSOnboarding)

### 6.1 Current stack

| Layer | Path |
|---|---|
| Forms hub | `src/screens/home/OnBoardingFormsScreen.tsx` |
| Per-form card | `src/components/board/FormCard.tsx` |
| Routes | `src/routes/HomeStack.tsx` |
| W-4 entry | `src/components/board/w4online/W4InformationForm.tsx` |
| W-4 sign / final | `src/components/board/w4online/W4preview.tsx` → `SignatureFormView.tsx` |
| API client | `src/api/client.ts` (axios, Bearer + APIM key) |
| Config | `src/config.ts`, `app.config.js` → `API_BASE_URL`, `OCP_APIM_SUBSCRIPTION_KEY` |
| Service | `src/services/onboardingService.ts` |
| Store | `src/stores/onboarding.ts` |
| PDF viewer | `src/components/pdf/PdfViewer.tsx` — expects **base64**, not remote private URL |

### 6.2 Legacy APIs still in use

| API | Role | Keep? |
|---|---|---|
| `GET /OnBoarding/{id}/Forms` | Catalog + status + `formComponentName` | **Yes** (near-term) |
| `POST /OnBoarding/{id}/Form/{formId}` multipart | Scan/upload | **Yes** |
| `PATCH .../MarkAsRead` | Read-only docs | **Yes** |
| `GET DownloadForm` / `GetFormPreview` | Blank / readonly PDF | **Yes** |
| `GET GetSubmittedForm` | Completed PDF bytes (auth’d) | **Yes** until engine download exists |
| `GET GetSubmittedForm?preview=true` | Pre-sign PNG pages | Soft keep (old path) |
| `POST /OnBoarding/InsertW4Details` | W-4 answers + signature + status | Soft keep behind flag |
| `POST Post*WithHolding*` / I-9 / deposit / etc. | Other forms | Untouched until ported |

W-4 payload today is **flat** (`filingStatus`, `exemptFromFederalTax`, …) — **not** `{ answers: { questionKey: value } }`.

No draft-save API. No offline forms. No FormsEngine client in the app yet.

---

## 7. Gaps before full employee UX

| Gap | Impact | Owner |
|---|---|---|
| `essFormKey` → `formId` (`fw4`) | Can’t auto-route from Forms list | **C# preferred:** add `formId` on Forms list; or temporary RN fixed map |
| Submit does not set Complete | List status unchanged after engine submit | Legacy completion **or** C# status update |
| No signature on engine | Can’t finish legal W-4 via engine alone | Keep legacy signature path for v1 |
| Private `blobPath` (no SAS) | PdfViewer can’t open URL | **C#:** auth’d download/proxy (bytes/base64) or SAS |
| Production Python URL | Azure Prod can’t hit office LAN | Ops / Production `FormsEngine` config |

---

## 8. Integration plan (additive — do not break legacy)

### Phase 1 — Fill-only MVP (safe now)

**Goal:** Prove RN → C# live → C# submissions → Python fill → Blob, without replacing W-4 completion UX.

1. Add `src/services/formsEngineService.ts` (+ types) reusing existing axios client.
2. Feature flag (e.g. `USE_FORMS_ENGINE_FW4`).
3. Temporary map: one known W-4 `essFormKey` (or description) → `formId: "fw4"`.
4. New screen: load `GET .../forms/fw4/live`, render questions, submit `POST .../live/submissions` with:
   - `answers` keyed by `questionKey`
   - optional `employeeId: String(essUserKey)`
5. On success: toast / message that PDF was stored (`storedToBlob`).  
   **Do not** rely on `blobPath` in WebView.  
   **Do not** expect Forms list status to flip.
6. Leave `W4InformationForm`, `InsertW4Details`, upload, state forms, I-9, deposit **untouched**.

### Phase 2 — Real employee UX (needs C#)

Blocked on Yashwanth / backend:

1. **Auth’d PDF download/proxy** (or SAS) so RN can feed **base64** into `PdfViewer`.
2. **`formId` on** `GET /OnBoarding/{id}/Forms` (replace hard map).
3. **Decision:** should `live/submissions` also update onboarding status, **or** RN still call legacy `InsertW4Details` after fill for Complete + signature?

Until then: engine = **fill**; legacy = **signature + Complete + eye-icon preview**.

### Phase 3 — More forms

Expand flag to `colorado` and other `formId`s only after fw4 Phase 1–2 works end-to-end.

---

## 9. Files RN will likely touch later

| Priority | File | Change |
|---|---|---|
| New | `src/services/formsEngineService.ts` | Engine HTTP client |
| New | `src/types/formsEngineTypes.ts` | Live / submission types |
| New | FormsEngine form screen | Dynamic questions + submit |
| Edit | `src/components/board/FormCard.tsx` | Flagged navigate for fw4 |
| Edit | `src/routes/HomeStack.tsx` | Register screen |
| Edit | `app.config.js` / env | Feature flag |
| Maybe | `src/components/pdf/PdfViewer.tsx` | Helper once auth’d bytes/SAS exist |
| Leave | `onboardingService.ts` per-form POSTs, all `*online` screens | Until cutover |

---

## 10. Hard constraints for Cursor in the RN repo

- Prefer **additive** integration; do not refactor unrelated HR / auth / navigation.
- Do **not** delete legacy W-4 / state POSTs in Phase 1.
- Do **not** call Python URLs from the mobile app.
- Do **not** invent new C# endpoints; note gaps and wait for backend.
- PdfViewer today needs **base64/bytes** — private `blobPath` alone is insufficient.
- Keep `GET /OnBoarding/{id}/Forms` as the forms hub source of truth for status.

---

## 11. Quick checklist

**Backend (Dev/Testing) — ready**

- [x] Python `/internal/fill` on admin LAN  
- [x] C# → Python wired in Dev/Testing  
- [x] Blank PDF via C# edition PDF API  
- [x] C# Blob upload after fill  
- [x] List forms + live + submissions contracts  

**RN — not done yet**

- [ ] `formsEngineService` + flagged fw4 screen  
- [ ] End-to-end submit smoke test  

**C# / product — still needed for full UX**

- [ ] Auth’d completed-PDF download or SAS  
- [ ] `formId` on onboarding Forms list  
- [ ] Status / signature strategy after engine submit  
- [ ] Production Python reachability + `FormsEngine` in Production config  

---

## 12. One-line summary

**RN → C# FormsEngine (live + submissions) → Python fill → C# Blob.**  
Phase 1 proves fill behind a flag; legacy still owns signature, Complete status, and auth’d PDF preview until C# closes those gaps.
