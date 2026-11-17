# Setup guide — run Forms POC on a new machine

Follow these steps in order (Windows PowerShell). When done, open **http://localhost:5173/**.

---

## 1. Install prerequisites

1. Install **Git**.
2. Install **Python 3.12+** (check with `python --version`).
3. Install **Node.js 18+** (check with `node --version` and `npm --version`).
4. Install **PostgreSQL** and make sure the service is running.
5. Create a database named `forms_poc` (pgAdmin or `createdb`).
6. Get from the team:
   - C# FormsEngine **APIM base URL**
   - **Ocp-Apim-Subscription-Key**

---

## 2. Clone the repo

```powershell
git clone <YOUR_GITHUB_REPO_URL>
cd forms_poc
```

Replace `<YOUR_GITHUB_REPO_URL>` with the real GitHub URL.

---

## 3. Create your local `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

Edit `.env` and set real values:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/forms_poc
CSHARP_API_BASE_URL=https://YOUR-APIM-HOST/empselser
OCP_APIM_SUBSCRIPTION_KEY=YOUR_APIM_SUBSCRIPTION_KEY
FORMS_ENGINE_SOURCE=csharp
FORMS_ENGINE_FORM_IDS=fw4,colorado
```

Rules:

- Do **not** commit `.env`.
- Do **not** paste secrets into chat or PRs.
- Optional later: `PYTHON_FILL_API_KEY` if Python is exposed publicly for C# fill.

---

## 4. Backend (Python API)

Open a terminal in the repo root (`forms_poc`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Leave this terminal running.

Health check (new terminal or browser):

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health
```

Or open: http://127.0.0.1:8000/api/health

If you see `Address already in use` on port 8000, stop the other process first, then start uvicorn again.

---

## 5. Frontend (Vite)

Open a **second** terminal:

```powershell
cd forms_poc\frontend
npm install
npm run dev
```

Leave this terminal running.

Open the UI: **http://localhost:5173/**

Vite proxies `/api` calls to `http://127.0.0.1:8000`.

---

## 6. Smoke check in the browser

1. Open http://localhost:5173/
2. Confirm the form picker shows **fw4** and **colorado** (when C# is configured).
3. Open **Fill form** — questions should load.
4. Enter fake answers → **Create PDF** — preview tab should open.
5. Try **Review fields** and **Compare years** for the same form.

Use **fake answers only**. Do not enter real employee PII.

---

## 7. Useful commands (after first setup)

Activate venv and start API:

```powershell
cd forms_poc
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Start frontend:

```powershell
cd forms_poc\frontend
npm run dev
```

Optional tests:

```powershell
cd forms_poc
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m unittest backend.test_rules -v
.\.venv\Scripts\python.exe -m unittest backend.test_internal_fill -v
```

---

## 8. Common problems

| Problem | What to do |
|---|---|
| `.env` missing / wrong | Copy from `.env.example` and fill again |
| API will not start | Check Postgres is running and `DATABASE_URL` is correct |
| Forms list empty / C# errors | Check `CSHARP_API_BASE_URL` and `OCP_APIM_SUBSCRIPTION_KEY` |
| Frontend cannot reach API | Confirm API is on port 8000; restart Vite |
| Port 8000 busy | Stop the old `uvicorn` / Python process |
| `npm` errors | Use Node 18+; delete `node_modules` and run `npm install` again |

---

## 9. What not to commit

Never add these to git:

- `.env`
- `.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `output/`

They are listed in `.gitignore`.

---

## 10. Architecture reminder (for new members)

- **Forms Studio UI** (this repo) = reviewer + local employee demo.
- **Production employee path (planned):** React Native → C# FormsEngine → Python `POST /internal/fill` → C# Blob.
- Azure C# **cannot** call your laptop `127.0.0.1`. Public/tunnel Python URL is needed for that smoke test.

More detail: root `README.md`.
