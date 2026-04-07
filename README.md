# PipelinePro — WooCommerce Import Dashboard

A full-stack management dashboard for automating WooCommerce product imports from the Sunsky API.

- **Frontend**: React + Vite + TailwindCSS
- **Backend**: Python FastAPI
- **Database**: PostgreSQL
- **Package manager**: pnpm (Node.js tooling)

---

## Requirements

| Tool | Version |
|------|---------|
| Node.js | 18+ |
| pnpm | 9+ |
| Python | 3.11+ |
| PostgreSQL | 14+ |

Install pnpm if you don't have it:
```cmd
npm install -g pnpm
```

---

## Installation

### 1. Clone the repo

```cmd
git clone https://github.com/creative16developer-jpg/pipeline-pro.git
cd pipeline-pro
```

### 2. Install Node.js dependencies

```cmd
pnpm install
```

**Windows users** — if you get a `Cannot find module @rollup/rollup-win32-x64-msvc` error, run:
```cmd
cd node_modules\.pnpm\rollup@4.59.0\node_modules\rollup
npm install @rollup/rollup-win32-x64-msvc --no-save
cd ..\..\..\..
```

If you get a `lightningcss` or `@tailwindcss/oxide` error, same fix — go into that package folder and run `npm install --no-save`.

### 3. Install Python dependencies

```cmd
cd artifacts\pipeline
pip install fastapi uvicorn sqlalchemy[asyncio] asyncpg psycopg2-binary pydantic-settings httpx Pillow python-multipart woocommerce python-dotenv
```

### 4. Create the database

Open `psql` and run:
```sql
CREATE DATABASE pipeline_pro;
```

### 5. Configure environment

Create a file at `artifacts\pipeline\.env`:
```
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/pipeline_pro
```

Replace `YOUR_PASSWORD` with your PostgreSQL password.

### 6. Create database tables (first time only)

```cmd
cd artifacts\pipeline
python create_tables.py
```

You should see: `Done! All tables created successfully.`

---

## Running Locally

Open **two separate terminals**:

**Terminal 1 — Python backend** (inside `artifacts\pipeline\`):
```cmd
python main.py
```
Backend runs at `http://localhost:8000`

**Terminal 2 — Dashboard** (inside project root):
```cmd
pnpm --filter @workspace/dashboard run dev
```
Dashboard runs at `http://localhost:5173`

Open `http://localhost:5173` in your browser.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SUNSKY_API_KEY` | No | Sunsky API key (uses mock data if not set) |
| `SUNSKY_API_SECRET` | No | Sunsky API secret |
| `SUNSKY_API_URL` | No | Sunsky base URL (default: `https://www.sunsky-online.com/api`) |

---

## Project Structure

```
pipeline-pro/
├── artifacts/
│   ├── pipeline/           # Python FastAPI backend (PRIMARY)
│   │   ├── main.py         # App entry point
│   │   ├── database.py     # Async SQLAlchemy + asyncpg
│   │   ├── models/         # ORM models
│   │   ├── schemas/        # Pydantic schemas
│   │   ├── routers/        # API route handlers
│   │   └── pipeline/       # Sunsky client, WooCommerce client, image processor
│   └── dashboard/          # React + Vite frontend
│       └── vite.config.ts  # Proxies /api/* → http://localhost:8000
├── package.json
└── .npmrc
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/healthz` | Health check |
| GET | `/api/dashboard/stats` | Dashboard statistics |
| GET/POST | `/api/stores` | List / add WooCommerce stores |
| POST | `/api/stores/{id}/test` | Test WooCommerce connection |
| GET | `/api/products` | List products |
| GET/POST | `/api/jobs` | List / create jobs |
| POST | `/api/sunsky/fetch` | Fetch products from Sunsky |
| GET | `/api/sunsky/categories` | Get Sunsky categories |

---

## Milestone Plan

| Milestone | Scope |
|-----------|-------|
| M1 | Sunsky fetch + image processing pipeline |
| M2 | AI content generation (client's own API endpoint) |
| M3 | WooCommerce upload as drafts + category mapping |
| M4 | Dashboard polish, sync scheduler, delivery |

---

## Notes

- Products are uploaded to WooCommerce as **drafts** so the client can review before publishing
- Sunsky API requires IP whitelisting — mock data is used automatically as fallback
- All image processing uses Pillow (compress → watermark → WebP)
- No PHP anywhere — fully Python backend
