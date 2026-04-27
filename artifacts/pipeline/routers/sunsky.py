from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models.models import Job, JobStatus, JobType, ProductStatus
from schemas.schemas import SunskyFetchRequest, SunskyFetchResult, SunskyCategoryOut
from pipeline import sunsky_client
from datetime import datetime, timezone

router = APIRouter(prefix="/sunsky", tags=["sunsky"])


@router.get("/categories", response_model=list[SunskyCategoryOut])
async def get_categories():
    """
    Fetch the full Sunsky category tree (all levels).
    Returns a flat list sorted by parent/child order.
    Raises 502 if the API call fails (no mock fallback).
    """
    try:
        cats = await sunsky_client.get_category_tree()
    except Exception as e:
        raise HTTPException(502, f"Sunsky API error fetching categories: {e}")
    return [
        SunskyCategoryOut(id=c["id"], name=c["name"], parent_id=c.get("parent_id"))
        for c in cats
    ]


@router.post("/fetch", response_model=SunskyFetchResult)
async def fetch_products(body: SunskyFetchRequest, db: AsyncSession = Depends(get_db)):
    """
    Immediately fetch products from Sunsky (synchronous, in-request).
    For large imports use the /api/jobs endpoint to queue a background Fetch job instead.
    """
    from sqlalchemy import select
    from models.models import Product

    job = Job(
        type=JobType.fetch,
        status=JobStatus.running,
        started_at=datetime.now(timezone.utc),
        config={
            "category_id": body.category_id,
            "keyword":     body.keyword,
            "page_size":   body.limit,
            "max_pages":   1,  # Sync endpoint: one page only (use background job for all)
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        result = await sunsky_client.search_products(
            category_id=body.category_id,
            keyword=body.keyword,
            page=body.page,
            page_size=body.limit,
        )
    except Exception as e:
        job.status = JobStatus.failed
        job.error_message = str(e)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(502, f"Sunsky API error: {e}")

    products = result.get("products", [])
    saved = skipped = updated = 0

    for p in products:
        sunsky_id = str(p["id"])
        existing = (
            await db.execute(select(Product).where(Product.sunsky_id == sunsky_id))
        ).scalar_one_or_none()

        images   = p.get("images", [])
        raw_data = p.get("raw_data", {})

        if existing:
            # Compare and update if changed
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
            product = Product(
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
            )
            db.add(product)
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
