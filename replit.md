# WooCommerce Import Pipeline — Dashboard

## Overview

A full-stack management dashboard for automating WooCommerce product imports from the Sunsky API. Covers Phases 1, 3, and 4 of the client estimate.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (ESM bundle)
- **Frontend**: React + Vite, TailwindCSS, Recharts, Framer Motion

## Features

### Phase 1 — Sunsky API & Data Layer
- Sunsky API integration (mock mode when no key, falls back to real API when configured)
- PostgreSQL schema: stores, products, jobs, images, woo_categories, job_logs
- Product fetch, pagination, category filtering

### Phase 3 — WooCommerce Upload & Dashboard
- WooCommerce multi-store connector (add, test connection, delete)
- Category sync from WooCommerce stores
- Import job management (fetch, process, upload, sync types)
- Dashboard with live stats (total products, pending, processed, uploaded, failed, active jobs, stores)
- Job progress tracking with cancel support
- Product list with search and status filtering

## Structure

```text
artifacts/
├── api-server/         # Express 5 API server
│   └── src/
│       ├── lib/
│       │   ├── sunsky.ts       # Sunsky API integration (mock + real)
│       │   └── woocommerce.ts  # WooCommerce REST API client
│       └── routes/
│           ├── stores.ts       # Store CRUD + test + category sync
│           ├── products.ts     # Product list + detail
│           ├── jobs.ts         # Job management + progress simulation
│           ├── sunsky.ts       # Sunsky fetch endpoint
│           └── dashboard.ts    # Stats aggregation
├── dashboard/          # React dashboard frontend
│   └── src/
│       ├── pages/      # Dashboard, Products, Jobs, Stores, Sunsky
│       ├── hooks/      # React Query hooks for each domain
│       └── components/ # Layout, Modal, StatusBadge
lib/
├── api-spec/           # OpenAPI 3.1 spec (source of truth)
├── api-client-react/   # Generated React Query hooks
├── api-zod/            # Generated Zod schemas
└── db/
    └── schema/
        ├── stores.ts
        ├── products.ts
        ├── jobs.ts
        ├── images.ts
        ├── categories.ts
        └── jobLogs.ts
```

## Environment Variables Needed

| Variable | Description |
|---|---|
| `DATABASE_URL` | Auto-provided by Replit |
| `SUNSKY_API_KEY` | Sunsky API key (optional — uses mock data without it) |
| `SUNSKY_API_SECRET` | Sunsky API secret |
| `SUNSKY_API_URL` | Sunsky base URL (default: https://www.sunsky-online.com/api) |

## WooCommerce Store Setup

Add stores via the dashboard UI. Each store requires:
- Store name
- WordPress/WooCommerce URL (e.g. `https://yourstore.com`)
- Consumer Key (from WooCommerce → Settings → Advanced → REST API)
- Consumer Secret

## API Endpoints

- `GET /api/healthz` — health check
- `GET/POST /api/stores` — manage WooCommerce stores
- `POST /api/stores/:id/test` — test connection
- `GET/POST /api/stores/:id/categories` — sync categories
- `GET /api/products` — list products (with pagination, search, status filter)
- `GET/POST /api/jobs` — import jobs
- `POST /api/jobs/:id/cancel` — cancel a job
- `POST /api/sunsky/fetch` — fetch products from Sunsky
- `GET /api/sunsky/categories` — get Sunsky categories
- `GET /api/dashboard/stats` — dashboard statistics

## Development Commands

```bash
# Start API server
pnpm --filter @workspace/api-server run dev

# Start dashboard
pnpm --filter @workspace/dashboard run dev

# Run codegen after OpenAPI spec changes
pnpm --filter @workspace/api-spec run codegen

# Push DB schema changes
pnpm --filter @workspace/db run push
```

## Next Steps (for full implementation)

- Phase 2: Hook up Python image processing script (image download → compress → watermark)
- Add CSV/XLS import module
- Add Redis + Celery for proper job queuing (currently simulated)
- Add daily price/stock sync scheduler
- Add simulation mode and duplicate detection
- Add CSV/XLS export
- Performance testing with 5000+ products
