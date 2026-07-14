"""
PipelinePro — Python FastAPI backend.

Replaces the Node.js/Express API server.
All endpoints match the existing OpenAPI contract so the React
dashboard works without changes.

Run:
    uvicorn main:app --host 0.0.0.0 --port $PORT --reload
"""

import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from contextlib import asynccontextmanager
from pathlib import Path
from config import get_settings
from database import engine, Base
from routers import dashboard, stores, products, jobs, sunsky, content, pipeline, csv_import
from routers import settings as settings_router
from routers import map_step, enrich as enrich_router
from routers import attr_rules, attr_profiles, inventory_mapping
import models.models  # noqa: F401 — registers all ORM models with Base

STATIC_DIR = Path(__file__).parent.parent / "dashboard" / "dist" / "public"
IMAGES_DIR = Path(__file__).parent / "images" / "processed"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

settings = get_settings()


async def _run_enum_migrations():
    """
    ALTER TYPE ADD VALUE cannot run inside an explicit transaction (PG < 12).
    Run these separately with AUTOCOMMIT isolation before the main migrations.
    Safe to run repeatedly — IF NOT EXISTS makes each statement idempotent.
    """
    import sqlalchemy as sa
    try:
        async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            await conn.execute(sa.text(
                "ALTER TYPE job_type ADD VALUE IF NOT EXISTS 'csv_import'"
            ))
    except Exception:
        pass  # enum type not yet created (fresh DB — create_all handles it)


async def _migrate_pipeline_job_status():
    """
    T02 — Convert pipeline_jobs.status from VARCHAR(20) to a proper PostgreSQL
    enum type with all valid values (including the new category_review state).
    Fully idempotent — safe to run on every startup.
    """
    import sqlalchemy as sa
    _values = [
        "queued", "running", "review", "enrich_review", "category_review",
        "completed", "failed", "cancelled",
    ]

    # Step 1: Create the enum type and add any missing values (needs AUTOCOMMIT)
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        try:
            vals_sql = ", ".join(f"'{v}'" for v in _values)
            await conn.execute(sa.text(
                f"CREATE TYPE pipeline_job_status AS ENUM ({vals_sql})"
            ))
        except Exception:
            pass  # already exists

        for val in _values:
            try:
                await conn.execute(sa.text(
                    f"ALTER TYPE pipeline_job_status ADD VALUE IF NOT EXISTS '{val}'"
                ))
            except Exception:
                pass

    # Step 2: Alter the column type if it is still varchar (transactional)
    async with engine.begin() as conn:
        row = (await conn.execute(sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'pipeline_jobs' AND column_name = 'status'"
        ))).fetchone()
        if row and row[0] == "character varying":
            await conn.execute(sa.text(
                "ALTER TABLE pipeline_jobs "
                "ALTER COLUMN status TYPE pipeline_job_status "
                "USING status::pipeline_job_status"
            ))


async def _recover_stuck_pipelines():
    """
    T03 — On startup, any pipeline still in 'running' status is stuck
    (the server crashed mid-run).  Mark them as 'failed' so the operator
    can retry.  Pipelines in a pause state (review / category_review /
    enrich_review) are intentionally waiting — do NOT touch them.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            sa.text(
                "UPDATE pipeline_jobs SET status = 'failed', "
                "error_message = 'Server restarted while pipeline was running — please retry.', "
                "updated_at = NOW() "
                "WHERE status = 'running' "
                "RETURNING id"
            )
        )
        recovered = result.fetchall()
        if recovered:
            ids = [str(r[0]) for r in recovered]
            print(f"[startup] T03: marked {len(ids)} stuck pipeline(s) as failed: {', '.join(ids)}")
        await db.commit()


async def _run_migrations(conn):
    """Run all pending SQL migrations idempotently on startup.
    Skips ALTER TYPE ADD VALUE statements — those run via _run_enum_migrations."""
    import sqlalchemy as sa

    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        return

    # Run all .sql files in alphabetical order — each is idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
    sql_files = sorted(migrations_dir.glob("*.sql"))
    for migration_sql in sql_files:
        raw = migration_sql.read_text()
        for stmt in raw.split(";"):
            stmt = "\n".join(
                line for line in stmt.splitlines()
                if line.strip() and not line.strip().startswith("--")
            ).strip()
            if not stmt:
                continue
            # ALTER TYPE ADD VALUE runs in _run_enum_migrations (AUTOCOMMIT)
            if "ADD VALUE" in stmt.upper():
                continue
            await conn.execute(sa.text(stmt))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Enum DDL (must run before create_all)
    await _run_enum_migrations()
    await _migrate_pipeline_job_status()
    # 2. Schema sync + SQL migrations
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)
    # 3. T03: recover any pipelines that were mid-run when the server crashed
    await _recover_stuck_pipelines()
    yield


app = FastAPI(
    title="PipelinePro API",
    description="WooCommerce import pipeline — Python backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router, prefix="/api")
app.include_router(stores.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(sunsky.router, prefix="/api")
app.include_router(content.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(csv_import.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(map_step.router, prefix="/api")
app.include_router(enrich_router.router, prefix="/api")
app.include_router(attr_rules.router, prefix="/api")
app.include_router(attr_profiles.router, prefix="/api")
app.include_router(inventory_mapping.router, prefix="/api")

# Serve processed images publicly so WooCommerce can sideload them
# URL pattern: {SERVER_BASE_URL}/media/images/{sku}_{pos}.webp
app.mount("/media/images", StaticFiles(directory=IMAGES_DIR), name="processed_images")


@app.get("/api/healthz")
async def health():
    return {"status": "ok", "runtime": "python"}


# Serve built React frontend (production / VPS mode)
# Run: pnpm --filter @workspace/dashboard build  — then restart this server
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file = STATIC_DIR / full_path
        if file.exists() and file.is_file():
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
