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
from routers import dashboard, stores, products, jobs, sunsky, content, pipeline
import models.models  # noqa: F401 — registers all ORM models with Base

STATIC_DIR = Path(__file__).parent.parent / "dashboard" / "dist" / "public"
IMAGES_DIR = Path(__file__).parent / "images" / "processed"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

settings = get_settings()


async def _run_migrations(conn):
    """Run any pending SQL migrations idempotently on startup."""
    import sqlalchemy as sa
    migration_sql = Path(__file__).parent / "migrations" / "migrate_pipeline_jobs.sql"
    if migration_sql.exists():
        await conn.execute(sa.text(migration_sql.read_text()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)
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
