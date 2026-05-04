from datetime import datetime, timezone
import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from database import get_db
from models.models import PipelineJob, PipelineLog, Job, JobType, Store

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

ACTIVE_STATUSES = ("running", "review")


def _pl_dict(pl: PipelineJob, step_jobs: list = None) -> dict:
    d = {
        "id": pl.id,
        "pl_id": f"PL-{str(pl.id).zfill(3)}",
        "store_id": pl.store_id,
        "fetch_job_id": pl.fetch_job_id,
        "status": pl.status,
        "current_step": pl.current_step,
        "config": pl.config,
        "stats_json": pl.stats_json,
        "error_message": pl.error_message,
        "created_at": pl.created_at.isoformat() if pl.created_at else None,
        "updated_at": pl.updated_at.isoformat() if pl.updated_at else None,
    }
    if step_jobs is not None:
        d["step_jobs"] = [
            {
                "id": j.id,
                "type": j.type.value,
                "status": j.status.value,
                "total_items": j.total_items,
                "processed_items": j.processed_items,
                "failed_items": j.failed_items,
                "progress_percent": j.progress_percent,
                "error_message": j.error_message,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in step_jobs
        ]
    return d


class PipelineCreateRequest(BaseModel):
    store_id: int
    fetch_job_id: int
    include_generate: bool = False
    force_rerun: bool = False
    process_config: dict = {}
    upload_config: dict = {}
    sync_config: dict = {}
    content_gen_config: dict = {}


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_pipelines(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    store_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(PipelineJob).order_by(PipelineJob.created_at.desc())
    if store_id:
        q = q.where(PipelineJob.store_id == store_id)
    if status:
        q = q.where(PipelineJob.status == status)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    pls = (await db.execute(q.offset((page - 1) * limit).limit(limit))).scalars().all()

    # Queue counts per store (for banner)
    from sqlalchemy import distinct
    queued_by_store: dict[int, int] = {}
    running_by_store: dict[int, int] = {}
    for pl in (
        await db.execute(
            select(PipelineJob).where(PipelineJob.status.in_(["queued", "running", "review"]))
        )
    ).scalars().all():
        if pl.status == "queued":
            queued_by_store[pl.store_id] = queued_by_store.get(pl.store_id, 0) + 1
        else:
            running_by_store[pl.store_id] = pl.id  # currently running pl_id

    return {
        "pipelines": [_pl_dict(pl) for pl in pls],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, math.ceil(total / limit)),
        "queue_info": {
            "queued_by_store": queued_by_store,
            "running_by_store": running_by_store,
        },
    }


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("")
async def create_pipeline(body: PipelineCreateRequest, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, body.store_id)
    if not store:
        raise HTTPException(400, f"Store #{body.store_id} not found")

    fetch_job = await db.get(Job, body.fetch_job_id)
    if not fetch_job:
        raise HTTPException(400, f"Job #{body.fetch_job_id} not found")
    if fetch_job.type != JobType.fetch:
        raise HTTPException(400, f"Job #{body.fetch_job_id} is not a fetch job")

    # Queue check: is another pipeline running/in-review for this store?
    active = (
        await db.execute(
            select(PipelineJob)
            .where(
                PipelineJob.store_id == body.store_id,
                PipelineJob.status.in_(list(ACTIVE_STATUSES)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    initial_status = "queued" if active else "running"

    pl = PipelineJob(
        store_id=body.store_id,
        fetch_job_id=body.fetch_job_id,
        status=initial_status,
        config={
            "include_generate": body.include_generate,
            "force_rerun": body.force_rerun,
            "process_config": body.process_config,
            "upload_config": body.upload_config,
            "sync_config": body.sync_config,
            "content_gen_config": body.content_gen_config,
        },
    )
    db.add(pl)
    await db.commit()
    await db.refresh(pl)

    if initial_status == "running":
        from tasks.pipeline_tasks import run_pipeline_job
        run_pipeline_job.delay(pl.id)
    else:
        from models.models import PipelineLog
        db.add(PipelineLog(
            pipeline_job_id=pl.id,
            level="info",
            message=f"Pipeline queued — waiting for PL-{str(active.id).zfill(3)} to finish",
        ))
        await db.commit()

    return _pl_dict(pl)


# ── Get single ────────────────────────────────────────────────────────────────

@router.get("/{pl_id}")
async def get_pipeline(pl_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")

    step_jobs = (
        await db.execute(
            select(Job)
            .where(Job.pipeline_job_id == pl_id)
            .order_by(Job.id.asc())
        )
    ).scalars().all()

    return _pl_dict(pl, list(step_jobs))


# ── Resume (from review) ─────────────────────────────────────────────────────

@router.post("/{pl_id}/resume")
async def resume_pipeline(pl_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")
    if pl.status != "review":
        raise HTTPException(400, "Pipeline is not in review state")

    from tasks.pipeline_tasks import resume_pipeline_job
    resume_pipeline_job.delay(pl.id)

    return {"message": f"PL-{str(pl_id).zfill(3)} resuming — upload step starting"}


# ── Cancel ───────────────────────────────────────────────────────────────────

@router.post("/{pl_id}/cancel")
async def cancel_pipeline(pl_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")
    if pl.status in ("completed", "failed", "cancelled"):
        raise HTTPException(400, "Pipeline cannot be cancelled")

    pl.status = "cancelled"
    pl.updated_at = datetime.now(timezone.utc)
    await db.commit()

    from models.models import PipelineLog
    db.add(PipelineLog(
        pipeline_job_id=pl.id, level="warn",
        message="Pipeline cancelled by user",
    ))
    await db.commit()

    from tasks.pipeline_tasks import _advance_queue
    await _advance_queue(db, pl.store_id, pl.id)

    return _pl_dict(pl)


# ── Retry (creates a fresh run with same config) ──────────────────────────────

@router.post("/{pl_id}/retry")
async def retry_pipeline(pl_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")
    if pl.status not in ("failed", "cancelled"):
        raise HTTPException(400, "Only failed or cancelled pipelines can be retried")

    active = (
        await db.execute(
            select(PipelineJob)
            .where(
                PipelineJob.store_id == pl.store_id,
                PipelineJob.status.in_(list(ACTIVE_STATUSES)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    initial_status = "queued" if active else "running"

    new_pl = PipelineJob(
        store_id=pl.store_id,
        fetch_job_id=pl.fetch_job_id,
        status=initial_status,
        config=pl.config,
    )
    db.add(new_pl)
    await db.commit()
    await db.refresh(new_pl)

    if initial_status == "running":
        from tasks.pipeline_tasks import run_pipeline_job
        run_pipeline_job.delay(new_pl.id)

    return _pl_dict(new_pl)


# ── Logs ─────────────────────────────────────────────────────────────────────

@router.get("/{pl_id}/logs")
async def get_pipeline_logs(
    pl_id: int,
    limit: int = Query(300, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")

    logs = (
        await db.execute(
            select(PipelineLog)
            .where(PipelineLog.pipeline_job_id == pl_id)
            .order_by(PipelineLog.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    return {
        "pipeline_id": pl_id,
        "logs": [
            {
                "id": log.id,
                "step": log.step,
                "level": log.level,
                "message": log.message,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
