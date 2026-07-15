from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from datetime import datetime, timedelta, timezone
from database import get_db
from models.models import Product, Job, Store, PipelineJob, ProductStatus, JobStatus
from schemas.schemas import DashboardStats, PipelineJobOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _count(db: AsyncSession, model, where=None):
    q = select(func.count(model.id))
    if where is not None:
        q = q.where(where)
    return (await db.execute(q)).scalar_one()


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

    # Pipeline-level stats
    active_pipelines = await _count(
        db, PipelineJob, PipelineJob.status == "running"
    )
    waiting_for_input = (await db.execute(
        select(func.count(PipelineJob.id)).where(
            PipelineJob.status.in_(["review", "enrich_review", "category_review"])
        )
    )).scalar_one()
    uploaded_30d = await _count(
        db, Product,
        (Product.status == ProductStatus.uploaded) &
        (Product.updated_at >= thirty_days_ago)
    )
    failed_30d = (await db.execute(
        select(func.count(PipelineJob.id)).where(
            (PipelineJob.status == "failed") &
            (PipelineJob.updated_at >= thirty_days_ago)
        )
    )).scalar_one()

    # Per-store breakdown (used when >1 store connected)
    total_stores = await _count(db, Store)

    # Recent pipeline runs (last 10), newest first
    recent_pipelines_rows = (await db.execute(
        select(PipelineJob).order_by(PipelineJob.created_at.desc()).limit(10)
    )).scalars().all()

    return DashboardStats(
        active_pipelines=active_pipelines,
        waiting_for_input=waiting_for_input,
        uploaded_30d=uploaded_30d,
        failed_30d=failed_30d,
        total_stores=total_stores,
        recent_pipelines=[PipelineJobOut.model_validate(p) for p in recent_pipelines_rows],
    )
