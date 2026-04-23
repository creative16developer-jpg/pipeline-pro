from datetime import datetime, timezone
import math
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
    type: str = Query(None, description="Filter by job type: fetch|process|upload|sync"),
    status: str = Query(None, description="Filter by status: pending|running|completed|failed"),
    db: AsyncSession = Depends(get_db),
):
    q = select(Job)
    if type:
        try:
            q = q.where(Job.type == JobType(type))
        except ValueError:
            pass
    if status:
        try:
            q = q.where(Job.status == JobStatus(status))
        except ValueError:
            pass

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    jobs = (
        await db.execute(
            q.order_by(Job.created_at.desc()).offset((page - 1) * limit).limit(limit)
        )
    ).scalars().all()

    return JobListOut(
        jobs=[JobOut.model_validate(j) for j in jobs],
        total=total,
        page=page,
        limit=limit,
        total_pages=max(1, math.ceil(total / limit)),
    )


@router.post("", response_model=JobOut)
async def create_job(body: JobCreate, db: AsyncSession = Depends(get_db)):
    try:
        job_type = JobType(body.type)
    except ValueError:
        raise HTTPException(400, f"Invalid job type: {body.type}")

    # Validate source job exists if provided
    if body.source_job_id:
        source = await db.get(Job, body.source_job_id)
        if not source:
            raise HTTPException(400, f"Source job #{body.source_job_id} not found")

    job = Job(
        type=job_type,
        status=JobStatus.pending,
        store_id=body.store_id,
        config=body.config or {},
        source_job_id=body.source_job_id,
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


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    logs = (
        await db.execute(
            select(JobLog)
            .where(JobLog.job_id == job_id)
            .order_by(JobLog.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    return {
        "job_id": job_id,
        "logs": [
            {
                "id": log.id,
                "level": log.level,
                "message": log.message,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


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
