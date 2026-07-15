from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database import get_db
from models.models import Job, JobStatus, JobType, ProductStatus, StarredSunskyCategory
from schemas.schemas import SunskyFetchRequest, SunskyFetchResult, SunskyCategoryOut
from pipeline import sunsky_client
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/sunsky", tags=["sunsky"])


# ─────────────────────────────────────────────────────────────────────────────
# Starred Sunsky categories
# ─────────────────────────────────────────────────────────────────────────────

class StarCategoryBody(BaseModel):
    id: str
    name: str
    parentName: Optional[str] = None


@router.get("/starred-categories")
async def list_starred_categories(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(StarredSunskyCategory).order_by(StarredSunskyCategory.name)
        )
    ).scalars().all()
    return [{"id": r.cat_id, "name": r.name, "parentName": r.parent_name} for r in rows]


@router.post("/starred-categories", status_code=200)
async def star_category(body: StarCategoryBody, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(
            select(StarredSunskyCategory).where(StarredSunskyCategory.cat_id == body.id)
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(StarredSunskyCategory(cat_id=body.id, name=body.name, parent_name=body.parentName))
        await db.commit()
    return {"ok": True}


@router.delete("/starred-categories/{cat_id}", status_code=200)
async def unstar_category(cat_id: str, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(StarredSunskyCategory).where(StarredSunskyCategory.cat_id == cat_id)
        )
    ).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return {"ok": True}



@router.get("/categories", response_model=list[SunskyCategoryOut])
async def get_categories(parent_id: str = Query(default="0")):
    """
    Fetch ONE level of Sunsky categories.
    Pass parent_id=0 (default) to get root categories.
    Pass parent_id=<id> to get direct children of that category.
    This is a single API call — fast and lazy.
    """
    try:
        cats = await sunsky_client.get_categories(parent_id=parent_id)
    except Exception as e:
        raise HTTPException(502, f"Sunsky API error fetching categories: {e}")
    return [
        SunskyCategoryOut(id=c["id"], name=c["name"], parent_id=c.get("parent_id"))
        for c in cats
    ]


@router.post("/fetch", response_model=SunskyFetchResult)
async def fetch_products(body: SunskyFetchRequest, db: AsyncSession = Depends(get_db)):
    """
    Fetch products from Sunsky using one or more criteria (OR logic).
    Supported criteria: category, keyword, comma-separated SKU/SPU list.
    All active criteria are searched in parallel and results are deduplicated.
    """
    import asyncio
    from sqlalchemy import select
    from models.models import Product

    # ── Parse SKU list ───────────────────────────────────────────────────────
    sku_list = [s.strip() for s in (body.skus or "").split(",") if s.strip()]

    # ── Build parallel search tasks ──────────────────────────────────────────
    # Each task returns either a list[dict] (SPU path) or a search-result dict
    async def _cat_search():
        return await sunsky_client.search_products(
            category_id=body.category_id,
            page=body.page,
            page_size=body.limit,
        )

    async def _kw_search():
        return await sunsky_client.search_products(
            keyword=body.keyword,
            page=body.page,
            page_size=body.limit,
        )

    tasks = []
    if body.category_id:
        tasks.append(_cat_search())
    if body.keyword:
        tasks.append(_kw_search())
    if sku_list:
        tasks.append(sunsky_client.get_products_by_spus(sku_list))

    # Fall back to a plain (unconstrained) page fetch if no criteria given
    if not tasks:
        tasks.append(_cat_search())

    # ── Record job ───────────────────────────────────────────────────────────
    job = Job(
        type=JobType.fetch,
        status=JobStatus.running,
        store_id=body.store_id,
        started_at=datetime.now(timezone.utc),
        config={
            "category_id": body.category_id,
            "keyword":     body.keyword,
            "skus":        sku_list or None,
            "page_size":   body.limit,
            "page":        body.page,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        job.status = JobStatus.failed
        job.error_message = str(e)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(502, f"Sunsky API error: {e}")

    # ── Merge + deduplicate ───────────────────────────────────────────────────
    seen_ids: set[str] = set()
    products: list[dict] = []
    errors: list[str] = []
    for r in raw_results:
        if isinstance(r, Exception):
            errors.append(str(r))
            continue
        batch: list[dict] = r if isinstance(r, list) else r.get("products", [])
        for p in batch:
            pid = str(p.get("id", ""))
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                products.append(p)

    if errors and not products:
        job.status = JobStatus.failed
        job.error_message = "; ".join(errors)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(502, f"Sunsky API error(s): {'; '.join(errors)}")

    # ── Persist products ─────────────────────────────────────────────────────
    saved = skipped = updated = 0

    for p in products:
        sunsky_id = str(p["id"])
        existing = (
            await db.execute(select(Product).where(Product.sunsky_id == sunsky_id))
        ).scalar_one_or_none()

        images   = p.get("images", [])
        raw_data = p.get("raw_data", {})

        if existing:
            changed = False
            if p["name"] and existing.name != p["name"]:
                existing.name = p["name"]; changed = True
            if p.get("price") and existing.price != p["price"]:
                existing.price = p["price"]; changed = True
            if p.get("stock_status") and existing.stock_status != p["stock_status"]:
                existing.stock_status = p["stock_status"]; changed = True

            if changed:
                existing.raw_data = raw_data
                updated += 1
            else:
                skipped += 1
        else:
            db.add(Product(
                sunsky_id=sunsky_id,
                sku=p["sku"],
                name=p["name"],
                description=p.get("description", ""),
                price=p.get("price", "0"),
                stock_status=p.get("stock_status", "in_stock"),
                category_id=p.get("category_id", ""),
                image_count=len(images),
                raw_data=raw_data,
                status=ProductStatus.pending,
                fetch_job_id=job.id,
            ))
            saved += 1

    job.status = JobStatus.completed
    job.total_items = len(products)
    job.processed_items = saved + updated
    job.failed_items = 0
    job.progress_percent = 100.0
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return SunskyFetchResult(
        fetched=len(products),
        saved=saved,
        skipped=skipped,
        job_id=job.id,
    )
