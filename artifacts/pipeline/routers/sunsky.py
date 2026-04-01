from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models.models import Job, JobStatus, JobType, ProductStatus
from schemas.schemas import SunskyFetchRequest, SunskyFetchResult, SunskyCategoryOut
from pipeline import sunsky_client
from datetime import datetime, timezone

router = APIRouter(prefix="/sunsky", tags=["sunsky"])


@router.get("/categories", response_model=list[SunskyCategoryOut])
async def get_categories():
    cats = await sunsky_client.get_categories(parent_id="0")
    return [SunskyCategoryOut(
        id=c["id"],
        name=c["name"],
        parent_id=c.get("parent_id"),
    ) for c in cats]


@router.post("/fetch", response_model=SunskyFetchResult)
async def fetch_products(body: SunskyFetchRequest, db: AsyncSession = Depends(get_db)):
    job = Job(
        type=JobType.fetch,
        status=JobStatus.running,
        started_at=datetime.now(timezone.utc),
        config={
            "category_id": body.category_id,
            "keyword": body.keyword,
            "page": body.page,
            "limit": body.limit,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    result = await sunsky_client.search_products(
        category_id=body.category_id,
        keyword=body.keyword,
        page=body.page,
        limit=body.limit,
    )
    products = result.get("products", [])

    saved = 0
    skipped = 0

    for p in products:
        from sqlalchemy import select
        from models.models import Product
        existing = (
            await db.execute(select(Product).where(Product.sunsky_id == str(p["id"])))
        ).scalar_one_or_none()
        if existing:
            skipped += 1
        else:
            product = Product(
                sunsky_id=str(p["id"]),
                sku=p["sku"],
                name=p["name"],
                description=p.get("description", ""),
                price=p.get("price", "0"),
                stock_status=p.get("stock_status", "in_stock"),
                category_id=p.get("category_id", ""),
                image_count=len(p.get("images", [])),
                raw_data=p.get("raw_data", {}),
                status=ProductStatus.pending,
            )
            db.add(product)
            saved += 1

    job.status = JobStatus.completed
    job.total_items = len(products)
    job.processed_items = saved
    job.progress_percent = 100.0
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return SunskyFetchResult(
        fetched=len(products),
        saved=saved,
        skipped=skipped,
        job_id=job.id,
    )
