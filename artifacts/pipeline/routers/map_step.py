"""
Map step router — /api/pipelines/{id}/map-data  +  /api/pipelines/{id}/map-confirm
                  /api/stores/{id}/category-mappings

The Map step sits inside the existing Review pause.  The client confirms category
mappings via map-confirm, which also triggers the pipeline resume (Upload → Sync).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.models import (
    PipelineJob, Product, SunskyCategoryMapping, WooCategory
)

router = APIRouter(tags=["map-step"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class MappingEntry(BaseModel):
    sunsky_cat: str
    woo_cat_id: Optional[int] = None
    woo_cat_name: Optional[str] = None


class MapConfirmRequest(BaseModel):
    mappings: list[MappingEntry] = []


class CategoryMappingUpdate(BaseModel):
    sunsky_cat: str
    woo_cat_id: Optional[int] = None
    woo_cat_name: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_sunsky_cat(raw: dict) -> str:
    """Best-effort extraction of Sunsky category string from product raw_data."""
    for key in ("catName", "categoryName", "category_name", "cat_name"):
        v = str(raw.get(key) or "").strip()
        if v:
            return v
    cat_id = str(raw.get("categoryId") or raw.get("catId") or raw.get("category_id") or "").strip()
    return cat_id


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline-scoped endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipelines/{pipeline_id}/map-data")
async def get_map_data(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    """
    Returns unique Sunsky categories found in this pipeline's product batch,
    merged with any saved mappings for this store.
    """
    pl = await db.get(PipelineJob, pipeline_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Load products for this pipeline's fetch job
    products = (
        await db.execute(
            select(Product).where(Product.fetch_job_id == pl.fetch_job_id)
        )
    ).scalars().all()

    # Extract unique Sunsky categories
    cat_counts: dict[str, int] = {}
    for p in products:
        raw = p.raw_data or {}
        cat = _extract_sunsky_cat(raw)
        if cat:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # Load saved mappings for this store
    saved_rows = (
        await db.execute(
            select(SunskyCategoryMapping).where(
                SunskyCategoryMapping.store_id == pl.store_id
            )
        )
    ).scalars().all()
    saved: dict[str, SunskyCategoryMapping] = {r.sunsky_cat: r for r in saved_rows}

    # Load WooCommerce categories for dropdown
    woo_cats = (
        await db.execute(
            select(WooCategory).where(WooCategory.store_id == pl.store_id)
        )
    ).scalars().all()

    categories = []
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        m = saved.get(cat)
        categories.append({
            "sunsky_cat":   cat,
            "product_count": count,
            "woo_cat_id":   m.woo_cat_id   if m else None,
            "woo_cat_name": m.woo_cat_name if m else None,
        })

    return {
        "pipeline_id": pipeline_id,
        "store_id":    pl.store_id,
        "categories":  categories,
        "woo_options": [
            {"id": c.woo_id, "name": c.name}
            for c in sorted(woo_cats, key=lambda x: x.name)
        ],
    }


@router.post("/pipelines/{pipeline_id}/map-confirm")
async def map_confirm(
    pipeline_id: int,
    req: MapConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Save category mappings and resume the pipeline (Upload → Sync).
    Replaces the plain /resume call when category mapping is used.
    """
    pl = await db.get(PipelineJob, pipeline_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pl.status != "review":
        raise HTTPException(status_code=400, detail=f"Pipeline is not in review state (current: {pl.status})")

    # Upsert mappings
    for entry in req.mappings:
        if not entry.sunsky_cat:
            continue
        stmt = (
            pg_insert(SunskyCategoryMapping)
            .values(
                store_id=pl.store_id,
                sunsky_cat=entry.sunsky_cat,
                woo_cat_id=entry.woo_cat_id,
                woo_cat_name=entry.woo_cat_name,
                updated_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=["store_id", "sunsky_cat"],
                set_={
                    "woo_cat_id":   entry.woo_cat_id,
                    "woo_cat_name": entry.woo_cat_name,
                    "updated_at":   datetime.now(timezone.utc),
                },
            )
        )
        await db.execute(stmt)
    await db.commit()

    # Trigger resume (same Celery task as the plain /resume endpoint)
    from tasks.pipeline_tasks import resume_pipeline_job
    pl.status = "running"
    pl.current_step = "upload"
    pl.updated_at = datetime.now(timezone.utc)
    await db.commit()
    resume_pipeline_job.delay(pipeline_id)

    return {"ok": True, "pipeline_id": pipeline_id, "mapped": len(req.mappings)}


# ─────────────────────────────────────────────────────────────────────────────
# Store-scoped endpoints (Settings page)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stores/{store_id}/category-mappings")
async def list_category_mappings(store_id: int, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(SunskyCategoryMapping)
            .where(SunskyCategoryMapping.store_id == store_id)
            .order_by(SunskyCategoryMapping.sunsky_cat)
        )
    ).scalars().all()
    return {
        "store_id": store_id,
        "mappings": [
            {
                "id":           r.id,
                "sunsky_cat":   r.sunsky_cat,
                "woo_cat_id":   r.woo_cat_id,
                "woo_cat_name": r.woo_cat_name,
                "updated_at":   r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.put("/stores/{store_id}/category-mappings")
async def update_category_mappings(
    store_id: int,
    entries: list[CategoryMappingUpdate],
    db: AsyncSession = Depends(get_db),
):
    for entry in entries:
        if not entry.sunsky_cat:
            continue
        stmt = (
            pg_insert(SunskyCategoryMapping)
            .values(
                store_id=store_id,
                sunsky_cat=entry.sunsky_cat,
                woo_cat_id=entry.woo_cat_id,
                woo_cat_name=entry.woo_cat_name,
                updated_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=["store_id", "sunsky_cat"],
                set_={
                    "woo_cat_id":   entry.woo_cat_id,
                    "woo_cat_name": entry.woo_cat_name,
                    "updated_at":   datetime.now(timezone.utc),
                },
            )
        )
        await db.execute(stmt)
    await db.commit()
    return {"ok": True, "saved": len(entries)}
