from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db
from models.models import Job, JobStatus, JobType, JobLog
from schemas.schemas import JobCreate, JobOut, JobListOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListOut)
async def list_jobs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count(Job.id)))).scalar_one()
    jobs = (
        await db.execute(
            select(Job).order_by(Job.created_at.desc()).offset((page - 1) * limit).limit(limit)
        )
    ).scalars().all()
    return JobListOut(
        jobs=[JobOut.model_validate(j) for j in jobs],
        total=total,
        page=page,
        limit=limit,
    )


@router.post("", response_model=JobOut)
async def create_job(body: JobCreate, db: AsyncSession = Depends(get_db)):
    try:
        job_type = JobType(body.type)
    except ValueError:
        raise HTTPException(400, f"Invalid job type: {body.type}")

    job = Job(
        type=job_type,
        status=JobStatus.pending,
        store_id=body.store_id,
        config=body.config or {},
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch to Celery worker
    from tasks.job_tasks import run_job
    run_job.delay(job.id)

    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JobOut.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in (JobStatus.pending, JobStatus.running):
        raise HTTPException(400, "Job cannot be cancelled")

    job.status = JobStatus.cancelled
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return JobOut.model_validate(job)
