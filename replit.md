# PipelinePro — WooCommerce Import Dashboard

## Overview

A full-stack management dashboard for automating WooCommerce product imports from the Sunsky API. Built across 4 milestones. Frontend is React/Vite; backend is Python FastAPI. Delivered via a shared private Git repo.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24 (frontend tooling only)
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Python FastAPI (port 8000) — **primary backend**
- **Legacy API**: Node.js Express 5 (port 8080) — kept for reference; dashboard no longer targets it
- **Database**: PostgreSQL (shared) — Python backend uses async SQLAlchemy + asyncpg
- **API codegen**: Orval (from OpenAPI spec) → React Query hooks
- **Build**: Vite (frontend), esbuild (Node legacy)
- **Frontend**: React + Vite, TailwindCSS, shadcn/ui, Recharts, Framer Motion

## Architecture

```
Browser
  │
  └──► Vite dev server (dashboard, dynamic port)
          │  /api/* proxy
          ▼
       Python FastAPI  (port 8000, artifacts/pipeline/)
          │
          └──► PostgreSQL (DATABASE_URL env)
```

The Vite dev server proxies all `/api/*` requests to the Python FastAPI backend at `http://localhost:8000`. No PHP anywhere — fully Python backend by client request.

## Project Structure

```text
artifacts/
├── pipeline/           # Python FastAPI backend (PRIMARY BACKEND)
│   ├── main.py                # App entry point, uvicorn, CORS
│   ├── database.py            # Async SQLAlchemy engine (asyncpg, sslmode fix)
│   ├── models/
│   │   └── models.py          # ORM models: Store, Product, Job, Image, etc.
│   ├── schemas/
│   │   └── schemas.py         # Pydantic I/O schemas
│   ├── routers/
│   │   ├── dashboard.py       # GET /api/dashboard/stats
│   │   ├── stores.py          # CRUD + test + category sync
│   │   ├── products.py        # Product list + detail
│   │   ├── jobs.py            # Job management + background runners
│   │   └── sunsky.py          # Sunsky fetch + categories
│   └── pipeline/
│       ├── sunsky_client.py   # Sunsky API client (MD5 auth, mock fallback)
│       ├── woo_client.py      # WooCommerce REST client (Basic Auth)
│       └── image_processor.py # Pillow: download → resize → watermark → WebP
├── api-server/         # Node.js Express backend (LEGACY — not actively used)
├── dashboard/          # React + Vite frontend
│   └── vite.config.ts  # Proxies /api/* → http://localhost:8000
lib/
├── api-spec/           # OpenAPI 3.1 spec (source of truth for codegen)
├── api-client-react/   # Generated React Query hooks (orval)
├── api-zod/            # Generated Zod schemas
└── db/                 # Drizzle ORM schema (Node.js, for reference)
client-preview/
└── pipeline-prototype.html   # Standalone HTML prototype with Roadmap/milestones
```

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Auto-provided by Replit (asyncpg, sslmode stripped automatically) |
| `SUNSKY_API_KEY` | Sunsky API key — IP-whitelisted; uses mock data without it |
| `SUNSKY_API_SECRET` | Sunsky API secret |
| `SUNSKY_API_URL` | Sunsky base URL (default: `https://www.sunsky-online.com/api`) |
| `SESSION_SECRET` | Replit secret for future auth sessions |

## Python Backend Endpoints

All mounted under `/api/`. Node.js api-server (port 8080) proxies `/api/pipelines/*` to Python (port 8000); all other `/api/*` routes are handled natively by the Node.js layer.

| Method | Path | Description |
|---|---|---|
| GET | `/api/healthz` | Health check |
| GET | `/api/dashboard/stats` | Aggregate counts + recent jobs |
| GET | `/api/stores` | List WooCommerce stores |
| POST | `/api/stores` | Add a new store |
| GET/PUT/DELETE | `/api/stores/{id}` | Get/update/delete a store |
| POST | `/api/stores/{id}/test` | Test WooCommerce connection |
| GET/POST | `/api/stores/{id}/categories` | List / sync WooCommerce categories |
| GET | `/api/products` | List products |
| GET | `/api/jobs` | List jobs |
| POST | `/api/jobs` | Create & start a job |
| POST | `/api/jobs/{id}/cancel` | Cancel a job |
| GET | `/api/pipelines` | List all pipeline runs |
| POST | `/api/pipelines` | Create a new pipeline run (queue if store busy) |
| GET | `/api/pipelines/{id}` | Pipeline detail + step jobs |
| POST | `/api/pipelines/{id}/resume` | Resume pipeline from review state |
| POST | `/api/pipelines/{id}/cancel` | Cancel pipeline |
| POST | `/api/pipelines/{id}/retry` | Retry failed/cancelled pipeline |
| GET | `/api/pipelines/{id}/logs` | Get pipeline logs |

## Pipeline System

### DB Tables (added via migration)
- `pipeline_jobs` — one row per pipeline run; status: queued/running/review/completed/failed/cancelled
- `pipeline_logs` — step-level log lines per pipeline
- `jobs.pipeline_job_id` — FK linking each step job back to its pipeline

### Execution Flow
Process → Generate (optional) → **Review pause** → Upload → Sync

### Queue Rule
Only ONE pipeline may be running/in-review per store at a time. Extras are auto-queued and auto-started when the current one finishes.

### Key Files
- `artifacts/pipeline/tasks/pipeline_tasks.py` — Celery orchestration engine
- `artifacts/pipeline/routers/pipeline.py` — REST API for pipelines
- `artifacts/pipeline/models/models.py` — PipelineJob + PipelineLog models
- `artifacts/pipeline/migrations/migrate_pipeline_jobs.sql` — DB migration
- `artifacts/api-server/src/routes/pipelines.ts` — Node.js proxy to Python
- `artifacts/dashboard/src/pages/Pipelines.tsx` — Monitoring page
- `artifacts/dashboard/src/pages/Pipeline.tsx` — New pipeline runner
- `artifacts/dashboard/src/lib/store-colors.ts` — Deterministic store color utility

## Sunsky Auth

MD5 signature: sort all params (including `key`) alphabetically → concat values → append `@` + secret → MD5 hex. TESTKEY/TESTSECRET are IP-whitelisted and return 403 on non-whitelisted IPs; mock data is used automatically as fallback.

## Milestone Plan

| Milestone | Scope | Est. |
|---|---|---|
| M1 | Sunsky fetch + image processing pipeline | ~30h |
| M2 | AI content generation (client's own API endpoint) | ~40h |
| M3 | WooCommerce upload as drafts + category mapping | ~40h |
| M4 | Dashboard polish, sync scheduler, Git delivery | ~22h |

## Running Locally

```bash
# Python backend (port 8000)
cd artifacts/pipeline && python3 main.py

# React dashboard
pnpm --filter @workspace/dashboard run dev

# Regenerate API client after OpenAPI spec changes
pnpm --filter @workspace/api-spec run codegen
```

## Key Design Decisions

- **No PHP** — client explicitly requested full Python backend
- **AI generation** — via client's own API endpoint only (URL + auth token TBD); no AI code shared by us
- **All image processing** — Pillow (lossless compress → watermark → WebP conversion)
- **Draft mode** — products uploaded to WooCommerce with `status: "draft"` so client can review before publishing
- **Mock fallback** — Sunsky client automatically returns realistic mock data when real credentials aren't available (IP-whitelist restriction)
