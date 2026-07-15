from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db
from models.models import Product, Job, Store, ProductStatus, JobStatus
from schemas.schemas import DashboardStats, JobOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _count(db: AsyncSession, model, where=None):
    q = select(func.count(model.id))
    if where is not None:
        q = q.where(where)
    return (await db.execute(q)).scalar_one()


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    total = await _count(db, Product)
    pending = await _count(db, Product, Product.status == ProductStatus.pending)
    processing = await _count(db, Product, Product.status == ProductStatus.processing)
    processed = await _count(db, Product, Product.status == ProductStatus.processed)
    uploaded = await _count(db, Product, Product.status == ProductStatus.uploaded)
    failed = await _count(db, Product, Product.status == ProductStatus.failed)
    active_jobs = await _count(db, Job, Job.status == JobStatus.running)
    total_stores = await _count(db, Store)

    recent_jobs = (await db.execute(
        select(Job).order_by(Job.created_at.desc()).limit(10)
    )).scalars().all()

    return DashboardStats(
        total_products=total,
        pending_products=pending,
        processing_products=processing,
        processed_products=processed,
        uploaded_products=uploaded,
        failed_products=failed,
        active_jobs=active_jobs,
        total_stores=total_stores,
        recent_jobs=[JobOut.model_validate(j) for j in recent_jobs],
    )
