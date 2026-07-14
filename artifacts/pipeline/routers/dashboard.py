from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db
from models.models import Product, Job, Store, ProductStatus, JobStatus, PipelineJob, PipelineJobStatus
from schemas.schemas import DashboardStats, JobOut, PipelineRunOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_WAITING_STATUSES = {
    PipelineJobStatus.review,
    PipelineJobStatus.enrich_review,
    PipelineJobStatus.category_review,
}
_ACTIVE_STATUSES = {
    PipelineJobStatus.running,
    PipelineJobStatus.queued,
}


async def _count(db: AsyncSession, model, where=None):
    q = select(func.count(model.id))
    if where is not None:
        q = q.where(where)
    return (await db.execute(q)).scalar_one()


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)

    # Legacy product/job stats (kept for backward compat)
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

    # Pipeline-focused stats
    active_pipelines = await _count(
        db, PipelineJob,
        PipelineJob.status.in_(list(_ACTIVE_STATUSES)),
    )
    waiting_for_input = await _count(
        db, PipelineJob,
        PipelineJob.status.in_(list(_WAITING_STATUSES)),
    )

    # Products uploaded / failed in the last 30 days
    uploaded_30d = await _count(
        db, Product,
        (Product.status == ProductStatus.uploaded) & (Product.updated_at >= cutoff_30d),
    )
    failed_30d = await _count(
        db, Product,
        (Product.status == ProductStatus.failed) & (Product.updated_at >= cutoff_30d),
    )

    # Recent 10 pipeline runs with store name
    raw_runs = (await db.execute(
        select(PipelineJob, Store.name.label("store_name"))
        .join(Store, PipelineJob.store_id == Store.id)
        .order_by(PipelineJob.created_at.desc())
        .limit(10)
    )).all()

    pipeline_runs: list[PipelineRunOut] = []
    for pj, store_name in raw_runs:
        status_val = pj.status.value if hasattr(pj.status, "value") else str(pj.status)

        # Sum totals from all step jobs linked to this pipeline
        job_totals = (await db.execute(
            select(
                func.sum(Job.total_items).label("total"),
                func.sum(Job.processed_items).label("uploaded"),
                func.sum(Job.failed_items).label("failed"),
            ).where(
                Job.pipeline_job_id == pj.id,
            )
        )).one()

        pipeline_runs.append(PipelineRunOut(
            id=pj.id,
            store_name=store_name,
            status=status_val,
            products_total=job_totals.total or 0,
            products_uploaded=job_totals.uploaded or 0,
            products_failed=job_totals.failed or 0,
            created_at=pj.created_at,
            is_waiting=status_val in ("review", "enrich_review", "category_review"),
        ))

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
        active_pipelines=active_pipelines,
        waiting_for_input=waiting_for_input,
        uploaded_30d=uploaded_30d,
        failed_30d=failed_30d,
        recent_pipeline_runs=pipeline_runs,
    )
