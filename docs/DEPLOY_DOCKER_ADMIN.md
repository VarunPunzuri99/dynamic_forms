# Deploy Forms POC with Docker Compose (admin PC / WSL)

This matches how **TrackPro** (`~/trackpro`) runs on `HYD-WS-023`, but uses **different ports** so both apps can run together.

| App | UI | API | Postgres |
|---|---|---|---|
| TrackPro (existing) | `:3000` | `:8000` | `:5432` / `:5433` |
| **Forms POC (this)** | **`:3001`** | **`:8001`** | **Not used** (C# SQL Server via APIM) |

Forms POC Python does **not** need Postgres in csharp mode. It talks to C# for all form data; this container only runs fill/rules/compare helpers + `/internal/fill` for C#.

Admin LAN IP example (update if it changes): `10.147.219.75`

---

## 0. One-time: Docker Desktop WSL integration

In Windows on the admin PC:

1. Open **Docker Desktop** → **Settings** → **Resources** → **WSL Integration**
2. Enable integration for your distro (the one where `fms@HYD-WS-023` runs)
3. Apply & restart
4. In WSL, confirm:

```bash
docker ps
docker compose version
```

If `docker` is missing in WSL, integration is off (you already hit that once).

---

## 1. Copy the project onto the admin machine

From your laptop (example with GitHub):

```bash
# In WSL on admin PC
cd ~
git clone <YOUR_GITHUB_REPO_URL> forms_poc
cd ~/forms_poc
```

Or copy the folder with SCP/USB, then:

```bash
cd ~/forms_poc
```

You should see:

```text
backend/  frontend/  docs/  docker-compose.yml  .env.docker.example  ...
```

Do **not** put this inside `~/trackpro`. Keep a sibling folder: `~/forms_poc`.

---

## 2. Create `.env` for Docker

```bash
cd ~/forms_poc
cp .env.docker.example .env
nano .env
```

Set at least:

```env
CORS_ORIGINS=http://10.147.219.75:3001,http://localhost:3001,http://127.0.0.1:3001
FORMS_ENGINE_SOURCE=csharp
CSHARP_API_BASE_URL=https://YOUR-APIM-HOST/empselser
OCP_APIM_SUBSCRIPTION_KEY=YOUR_REAL_KEY
# Optional: only if list-forms API is down
FORMS_ENGINE_FORM_IDS=fw4,colorado
```

No `DATABASE_URL` and no Postgres container — form data is 100% C# SQL.  
Form picker uses C# `GET /api/forms-engine/forms` (not the env seed) when that API is available.

Replace `10.147.219.75` if the admin PC LAN IP changed (`ip addr` / `ipconfig`).

`.env` stays on the server only (gitignored).

---

## 3. Build and start (same style as TrackPro)

```bash
cd ~/forms_poc
docker compose up -d --build
docker compose ps
```

Expected containers:

- `forms-frontend` → `0.0.0.0:3001->80`
- `forms-backend` → `0.0.0.0:8001->8000`

TrackPro containers (`rtcms-*` on 3000/8000/5432) should keep running unchanged.

---

## 4. Open the app

On the office network:

- **UI:** http://10.147.219.75:3001  
- **API health:** http://10.147.219.75:8001/api/health  
- **Internal fill (for C#):** http://10.147.219.75:8001/internal/fill  

The UI nginx proxies `/api` and `/internal` to the backend, so the browser can also use:

- http://10.147.219.75:3001/api/health

---

## 5. Day-2 commands

```bash
cd ~/forms_poc

# status
docker compose ps

# logs
docker compose logs -f backend
docker compose logs -f frontend

# rebuild after code pull
git pull
docker compose up -d --build

# stop only Forms POC (TrackPro stays up)
docker compose down

# stop + remove Forms DB volume (destructive)
docker compose down -v
```

If LAN IP changes (like TrackPro):

```bash
export NEW_IP="10.147.219.75"
sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=http://${NEW_IP}:3001,http://localhost:3001,http://127.0.0.1:3001|" .env
docker compose up -d --build backend
```

(Frontend uses relative `/api` via nginx, so usually **no frontend rebuild** for IP change—only CORS on backend.)

---

## 6. Give Yashwanth / C# the Python base URL

For **org LAN** callers that can reach the admin PC:

```json
"FormsEngine": {
  "PythonBaseUrl": "http://10.147.219.75:8001",
  "FillPath": "/internal/fill"
}
```

### Important about Azure cloud C#

`10.147.x.x` is a **private** address. Public Azure APIs **cannot** call it unless you have VPN / ExpressRoute / private link into the office network.

- Same office / VPN → `http://10.147.219.75:8001` can work  
- Public Azure only → need a public/hosted Python URL instead  

Optional: set `PYTHON_FILL_API_KEY` in `.env` and the same value in C# `PythonApiKey`.

---

## 7. Layout vs TrackPro

```text
~/trackpro/          # existing RTCMS
  docker-compose.yml
  backend/ frontend/

~/forms_poc/         # this project
  docker-compose.yml
  backend/ frontend/ docs/
  .env
```

Do not reuse TrackPro ports 3000 / 8000.

---

## 8. Quick troubleshooting

| Symptom | Fix |
|---|---|
| `docker: command not found` in WSL | Enable Docker Desktop WSL integration |
| Port already allocated | Confirm Forms uses 3001/8001; TrackPro kept 3000/8000 |
| UI loads, API fails | `docker compose logs backend`; check `.env` APIM key / `DATABASE_URL` |
| CORS errors | Update `CORS_ORIGINS` to exact UI URL (`http://IP:3001`) and recreate backend |
| C# from Azure times out | LAN IP not reachable from public Azure — use VPN or public host |
