# Dynamic US Onboarding Forms POC

Local R&D for turning blank IRS/state fillable PDFs into reviewer-approved employee questions, then filling the blank PDF with answers. Forms are multi-tenant by `form_id` (today: **fw4**, **colorado**; architecture is form-agnostic for future federal and 50-state forms). Blank PDFs are never modified on disk; filled copies are generated in memory / under `output/`.

**Last updated:** 16 November 2026 — Phase 3 rules parity, Phase 4B/4C/4D live-edition safety, Phase 1–2 C# FormsEngine cutover, **completed-PDF Blob upload**, **C# orchestration `/internal/fill`**, and **Forms Studio** UI (tabs, zoom, loaders).

---

## Status summary (done vs next)

| Area | Status |
|---|---|
| Reviewer: import → review → rules → publish | Done (via C# when configured) |
| Employee POC fill → bind PDF → Blob | Done (`UI → Python → C# completed-pdf`) |
| `POST /internal/fill` for C# / RN | Done locally (fill only; no Blob) |
| C# `GET live` + `POST .../live/submissions` | Done on Azure (Yashwanth) |
| Azure C# → public Python fill → Blob | **Pending** (needs reachable Python URL) |
| React Native client | Not started (call C# only) |
| Sample answers under C# AcroForm ids | Known gap (Fill sample often empty) |
| Deploy reviewer UI / auth | Not started |

**Target production fill path:** RN → C# `live/submissions` → Python `/internal/fill` → C# Blob.  
**Current POC demo path:** Vite UI → Python `/api/forms/{id}/live/answers` → Python uploads Blob via C#.

---

## What this POC proves

1. Read widgets from a blank PDF and draft employee-facing questions.
2. Reviewers edit labels, choose which fields appear, attach optional rules, and **publish** an edition as the sole **live** edition for that form.
3. Compare a new year PDF to the live edition; unchanged PDF field names can **reuse approved wording**.
4. Optional **answer rules** (not from the PDF) can disable / require / make optional later questions (frontend + backend parity).
5. Reviewers can **confirm a label against the real PDF box** (highlighted crop + zoom).
6. Employees fill the live form; the API validates rules, returns a filled PDF, and (when using C#) uploads the completed PDF to Azure Blob via FormsEngine.
7. Publishing is **atomic and form-scoped**: at most one live edition per `form_id`.
8. C# can orchestrate fill by calling Python `/internal/fill` (answers + form/edition ids → `pdf_base64`; C# owns Blob).

Out of scope for this POC: Expo / mobile apps in-repo, real PII, inventing new states beyond what is already configured. C# FormsEngine + Azure Blob are in scope when `FORMS_ENGINE_SOURCE=csharp`.

---

## Features (current)

| Feature | Where it lives | Notes |
|---|---|---|
| Forms Studio shell | `App.jsx`, `styles.css` | Full-width sticky header, form picker, loaders |
| Fill live form | `EmployeeForm.jsx` | Tabs: **Fill form** → **PDF preview** after Create PDF |
| Compare years | `Compare.jsx` | Diff widgets → draft → save / publish; busy overlays |
| Review fields | `Fields.jsx` | Review/approve live edition; loaders on save |
| PDF box highlight | `FieldPreview.jsx` + `field_locate.py` | Wider crop + **Zoom in / out / Reset** + pan |
| Activity indicators | `LoadingUI.jsx` | Page loaders, status banners, overlays, button spinners |
| Optional rules | `RulesEditor.jsx`, `rules.py`, `formRules.js` | FE + BE parity |
| Suggested include | `store.py` | Primary form page (form-agnostic) |
| Label carry | compare path | Same PDF field → reuse live label |
| Atomic publish / one live | `store` + C# publish | Form-scoped |
| PDF fill | `filler.py` | PyMuPDF bind |
| Completed PDF → Blob | `forms_engine.upload_completed_pdf_*` | After POC fill when C# |
| C# fill orchestration | `POST /internal/fill` | Returns `pdf_base64` only |

**Removed from UI:** old W-4-only `#review` (`Review.jsx`). `#review` redirects to `#fields/fw4`.

---

## Architecture

```
Reviewer (Forms Studio Vite)
  → Python /api/...  → C# FormsEngine (APIM) → SQL Server + Blob (blank PDFs, questions)

Employee demo (same Vite Fill tab)
  → Python POST .../live/answers → fill → C# POST .../completed-pdf → Blob

Production (planned)
  RN → C# GET live + POST live/submissions → Python POST /internal/fill → C# Blob
```

- **Python owns:** extract/compare helpers, rules validate, AcroForm bind/fill.
- **C# owns:** SQL editions/questions, blank + completed Blob, mobile front door.
- Azure C# **cannot** call `http://127.0.0.1:8000` — Python must be tunneled or deployed for submissions smoke.

---

## FormsEngine source

`.env`:

| Variable | Purpose |
|---|---|
| `FORMS_ENGINE_SOURCE` | `csharp` (when configured) or `postgres` |
| `CSHARP_API_BASE_URL` | Azure APIM base |
| `OCP_APIM_SUBSCRIPTION_KEY` | APIM subscription key |
| `FORMS_ENGINE_FORM_IDS` | Comma list, default `fw4,colorado` |
| `PYTHON_FILL_API_KEY` | Optional; required for non-local `/internal/fill` |
| `DATABASE_URL` | Postgres (startup bootstrap / Phase 4 tests; still required to boot today) |

Phase 1 reads use C#: forms list, live, editions, blank PDF, highlight.  
Phase 2 writes use C#: save questions/rules, publish, import fields, compare, completed-pdf upload.

Blank PDFs from C# are cached under `output/csharp_pdf_cache/` (gitignored).

### POC fill → Blob

`POST /api/forms/{form_id}/live/answers` returns `pdf_base64` always. When C# + `edition_id` are present, Python also uploads the same bytes to FormsEngine and returns `blob_path` / `stored_to_blob`. Upload failure does not block preview (notice only).

### C# orchestration fill (RN path)

C# config (Azure):

```json
"FormsEngine": {
  "PythonBaseUrl": "https://<reachable-python-host>",
  "PythonApiKey": "",
  "FillPath": "/internal/fill"
}
```

`POST /internal/fill` body (camelCase):

```json
{
  "formId": "fw4",
  "editionId": 2,
  "edition": "2026",
  "storagePath": "https://.../blank.pdf",
  "questions": [ { "questionKey": "...", "label": "...", "inputType": "text", "pdfField": "..." } ],
  "questionFieldMap": { "optionalKey": "pdf.field.name" },
  "answers": { "questionKey": "value" }
}
```

If `questions` is omitted, Python loads the live edition from C#. Response: `pdf_base64`, `filename`, `notices`, `formId`, `editionId`, `edition`. **No Blob upload here** (C# saves).

Optional auth: `X-Api-Key` or Bearer matching `PYTHON_FILL_API_KEY`.

Known C# notes:
- Do not send `isLive` on update (publish owns live switch).
- Dropdown `approvedCount` can lag until questions are re-saved.
- Live question ids may be full AcroForm paths (fw4) or short names (colorado) — both are valid.

---

## How to run

### Prerequisites

- Python 3.12+ with venv  
- Node.js for Vite  
- PostgreSQL via `DATABASE_URL`  
- C# FormsEngine APIM URL + key in `.env` for csharp mode  

### Backend

```powershell
cd d:\fms_solutions\forms_poc
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

If port 8000 is already bound, stop the old process before starting another.

### Frontend

```powershell
cd d:\fms_solutions\forms_poc\frontend
npm run dev
```

Open **http://localhost:5173/** — Vite proxies `/api` → `http://127.0.0.1:8000` (not `/internal`; call fill host directly for C# tests).

### Docker Compose (org admin PC / WSL)

Same pattern as TrackPro, **different ports** so both can run:

| Service | Port |
|---|---|
| Forms UI | **3001** |
| Forms API | **8001** |

No Postgres container — csharp mode uses C# SQL Server only.

See **`docs/DEPLOY_DOCKER_ADMIN.md`** for full WSL steps (`~/forms_poc`, `.env`, `docker compose up -d --build`). Quick start:

```bash
cp .env.docker.example .env   # edit APIM key + CORS_ORIGINS
docker compose up -d --build
```

UI: `http://<ADMIN_LAN_IP>:3001` · API: `http://<ADMIN_LAN_IP>:8001/api/health`

### Quick checks

```powershell
.\.venv\Scripts\python.exe -m backend.test_rules
.\.venv\Scripts\python.exe -m backend.test_rule_parity
.\.venv\Scripts\python.exe -m unittest backend.test_internal_fill backend.test_completed_pdf_upload -v
.\.venv\Scripts\python.exe -m unittest backend.test_publish_atomic backend.test_live_edition_safety backend.test_modern_publish_live -v
Invoke-WebRequest http://127.0.0.1:8000/api/health
```

Do not run `backend.test_flow` unless asked.

---

## UI map (Forms Studio)

| Nav | Hash | Component | Purpose |
|---|---|---|---|
| Fill form | `#form/{formId}` | `EmployeeForm.jsx` | Tab 1 fill → Create PDF → Tab 2 preview + Blob link |
| Compare years | `#compare/{formId}` | `Compare.jsx` | Compare → review → save / publish |
| Review fields | `#fields/{formId}` | `Fields.jsx` | Approve live questions + rules |

### Fill form UX

1. Load live questions (page loader).  
2. Enter answers on **Fill form** tab (2-column dense grid).  
3. **Create PDF** → busy overlay → switches to **PDF preview** tab.  
4. When C# upload succeeds, UI shows Blob link.

### Reviewer highlight UX

1. Open Review or Compare; click a row.  
2. Right panel loads highlight PNG (wider context crop).  
3. Use **Zoom out / in / Reset** and scroll to pan.

### Publish flow (Compare)

1. Compare draft vs live → edit labels / rules.  
2. **Save** → draft. **Approve and make live** → publish.  
3. Success → Fill for that form. **409** → message + refresh, not success.

---

## Codebase layout

```
forms_poc/
  .env
  requirements.txt
  docs/                  # Blank PDFs
  forms/                 # Legacy static fw4 JSON
  experiments/           # Sample answers (friendly keys; may not match C# ids)
  output/                # Filled PDFs + csharp_pdf_cache (gitignored)
  backend/
    main.py              # FastAPI: /api/* + /internal/fill
    forms_engine.py      # C# normalize, live, fill helpers, Blob upload helper
    csharp_client.py     # APIM HTTP client
    store.py             # Editions / compare / publish (postgres + csharp switch)
    draft_job.py         # Extract + schema bootstrap
    field_locate.py      # Locators + highlight PNG (wide pad)
    rules.py / filler.py
    test_*.py            # rules, parity, publish, phase1/2, internal fill, completed pdf
  frontend/
    vite.config.js
    src/
      App.jsx
      EmployeeForm.jsx
      Compare.jsx
      Fields.jsx
      FieldPreview.jsx
      LoadingUI.jsx
      RulesEditor.jsx
      formRules.js
      styles.css
```

---

## Data model

### PostgreSQL (local / Phase 4)

Still bootstrapped on API start. When `FORMS_ENGINE_SOURCE=csharp`, live CRUD goes to SQL Server via C#; Postgres remains for bootstrap and postgres-mode tests.

- `form_editions` — `form_id`, `edition`, paths, `status`, `is_live`, `rules_json`  
- `form_questions` — keys, labels, `pdf_field`, review/change metadata  
- Unique live: `uq_one_live_edition_per_form`

### C# / SQL Server (production store)

Editions + questions + Blob paths owned by FormsEngine. Completed PDFs under patterns like  
`Test_State_Forms/{formId}/{edition}/completed/{employee}/{guid}.pdf`.

---

## Optional rules

Reviewer-authored JSON on the edition (not from the PDF):

```json
{
  "id": "rule-1",
  "when_question": "<question id>",
  "when_condition": "answered|blank|checked|equals",
  "when_value": "optional for equals",
  "then_question": "<other question id>",
  "then_effect": "disable|optional|required"
}
```

Applied in browser (`formRules.js`) and on submit (`rules.py` + `filler.normalize_answers`). Shared vectors: `backend/rule_parity_vectors.json`.

---

## Main API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness (+ csharp probe when configured) |
| GET | `/api/forms` | List forms + live summary |
| GET | `/api/forms/{form_id}/live` | Live questions + rules |
| GET | `/api/forms/{form_id}/live/sample-answers` | Demo answers (may be empty under C# ids) |
| POST | `/api/forms/{form_id}/live/answers` | Validate + fill (+ Blob when C#) |
| POST | `/internal/fill` | C# orchestration fill → `pdf_base64` only |
| POST | `/api/forms/{form_id}/live/fields` | Import live PDF widgets |
| GET | `/api/forms/{form_id}/editions` | Edition list |
| POST | `/api/forms/{form_id}/compare` | Compare into draft |
| GET | `/api/forms/{form_id}/editions/{id}` | Questions + rules + locators |
| GET | `/api/forms/{form_id}/editions/{id}/field-highlight` | Highlight PNG |
| POST | `/api/forms/{form_id}/editions/{id}/approve` | Save draft selection |
| POST | `/api/forms/{form_id}/editions/{id}/publish` | Make edition live |

### C# FormsEngine (APIM) — used by this POC / RN

| Method | Path | Notes |
|---|---|---|
| GET | `.../forms/{formId}/live` | Mobile-ready questions + rules |
| POST | `.../forms/{formId}/live/submissions` | RN answers → Python fill → Blob |
| POST | `.../editions/{id}/completed-pdf` | Multipart `file` → `blobPath` |
| GET/PUT | editions, questions, pdf, publish | Reviewer persistence |

Legacy Python routes (`/api/forms/fw4/2026/...`, `/api/reviews/...`) remain mounted but are not the primary UI path.

---

## Blank PDFs

Under `docs/` (do not edit in place):

- `fw4--2025.pdf`, `fw4_2026.pdf`  
- `colorado_2025.pdf`, `colorado_2026.pdf`  

---

## Suggested demo path

1. Start Postgres, API (`--reload` preferred), and Vite.  
2. **Fill form** `fw4` — live questions; Create PDF; confirm preview tab + Blob notice when C#.  
3. **Review fields** — highlight + zoom; optional rule; approve.  
4. Switch to **colorado** — same flows (short PDF field names are OK).  
5. **Compare years** — draft compare → save → publish; confirm other form’s live edition unchanged.  
6. For Azure submissions: expose Python publicly, set C# `PythonBaseUrl`, POST `live/submissions`.

---

## Known limitations

- Nearby-label extraction often misses labels **above** the box — use highlight + zoom.  
- Sample answers JSON uses friendly keys (`first_name`, …); live C# fw4 often uses AcroForm paths → **Fill sample** may load nothing. Colorado has no sample file.  
- `approvedCount` in C# dropdown can lag.  
- No auth on `/api/*`; `/internal/fill` open unless `PYTHON_FILL_API_KEY` is set.  
- Postgres still required at API startup even in csharp mode.  
- Dual fill orchestration (POC vs RN) until prod settles on C# front door only.  
- No Dockerfile / CI / RN app in this repo yet.

---

## Next steps (recommended)

1. Tunnel or deploy Python; point Azure `PythonBaseUrl` at it; smoke `live/submissions`.  
2. Set `PYTHON_FILL_API_KEY` before any public host.  
3. Fix sample answers for live question keys.  
4. Wire React Native to C# live + submissions only.  
5. Deploy Forms Studio for reviewers; add auth; optional Postgres-free csharp boot.  

---

## Constraints / team agreements

- Never print or log DB passwords, tokens, or real employee PII.  
- Do not leave a second uvicorn on port 8000.  
- Do not invent extra state forms beyond the repo.  
- Do not weaken one-live-per-form guarantees.  
- Prefer multi-form Compare + Review + Fill over reviving the old W-4-only review screen.  
- RN must not call Python for questions/submit long-term — C# is the front door.  
