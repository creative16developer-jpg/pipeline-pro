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
from routers import attr_rules, attr_profiles, inventory_mapping, attr_mapping as attr_mapping_router
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
        # Strip full-line comments FIRST, across the whole file, before
        # splitting on ";". Previously this split on ";" first and only
        # then dropped "--" lines per chunk -- so any semicolon anywhere
        # inside a comment (including ordinary sentence punctuation, not
        # just SQL-looking text) fractured a statement mid-comment and the
        # trailing fragment of that comment line (no longer starting with
        # "--" in its own chunk) got sent to Postgres as real SQL. Hit in
        # practice: a migration file comment reading "...column); the
        # 10/0 heuristic remains..." caused a startup crash because of the
        # semicolon before "the".
        code_only = "\n".join(
            line for line in raw.splitlines()
            if line.strip() and not line.strip().startswith("--")
        )
        for stmt in code_only.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            # ALTER TYPE ADD VALUE runs in _run_enum_migrations (AUTOCOMMIT)
            if "ADD VALUE" in stmt.upper():
                continue
            await conn.execute(sa.text(stmt))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enum value additions must run outside a transaction (AUTOCOMMIT)
    await _run_enum_migrations()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)
    # T03: recover stuck pipelines — any pipeline left in 'running' from a
    # previous server process is interrupted; mark it failed so the queue
    # can auto-start the next queued pipeline for that store.
    import sqlalchemy as _sa
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await db.execute(
            _sa.text(
                "UPDATE pipeline_jobs "
                "SET status = 'failed', "
                "    error_message = 'Interrupted by server restart' "
                "WHERE status = 'running'"
            )
        )
        # Watermarking was removed from the pipeline per client requirement.
        # Backfill any images left in the old 'watermarked' status (written
        # by older code) to 'compressed', which is now the terminal
        # pre-upload status. Idempotent — no-op once no rows remain.
        await db.execute(
            _sa.text(
                "UPDATE images SET status = 'compressed' WHERE status = 'watermarked'"
            )
        )
        await db.commit()

    # Pre-warm the Sunsky category name map in the background, off the
    # critical path of both startup and any pipeline run. Walking the whole
    # category tree can take longer than any reasonable in-pipeline timeout
    # (rate-limit pacing + retries) — a live pipeline should never be the
    # first thing that triggers this fetch. Disk-persisted cache (see
    # pipeline/sunsky_client.py) means this is a no-op if already warm and
    # fresh; only actually refetches when genuinely stale/missing.
    async def _prewarm_category_cache():
        try:
            from pipeline.sunsky_client import get_category_name_map
            m = await get_category_name_map()
            print(f"[main] Category name cache pre-warmed: {len(m)} categories.")
        except Exception as exc:
            print(f"[main] Category name cache pre-warm failed (non-fatal): {exc}")

    import asyncio as _asyncio
    _asyncio.create_task(_prewarm_category_cache())

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
app.include_router(attr_mapping_router.router, prefix="/api")

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
