from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from database import get_db
from models.models import Job, JobStatus, JobType

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

VALID_STEPS = ["fetch", "process", "upload", "sync"]


class PipelineRunRequest(BaseModel):
    steps: list[str]
    store_id: Optional[int] = None
    force_rerun: bool = False
    fetch_config: dict = {}
    process_config: dict = {}
    upload_config: dict = {}
    sync_config: dict = {}


@router.post("/run")
async def start_pipeline_run(body: PipelineRunRequest, db: AsyncSession = Depends(get_db)):
    steps = [s for s in body.steps if s in VALID_STEPS]
    if not steps:
        raise HTTPException(400, "No valid steps provided")

    needs_store = any(s in steps for s in ["upload", "sync"])
    if needs_store and not body.store_id:
        raise HTTPException(400, "store_id is required when upload or sync steps are included")

    from tasks.job_tasks import run_job

    created_jobs: list[dict] = []
    prev_job_id: Optional[int] = None

    for step in steps:
        per_step: dict = {}
        if step == "fetch":
            per_step = {**body.fetch_config}
        elif step == "process":
            per_step = {**body.process_config}
        elif step == "upload":
            per_step = {**body.upload_config}
        elif step == "sync":
            per_step = {**body.sync_config}

        per_step["force_rerun"] = body.force_rerun

        job = Job(
            type=JobType(step),
            status=JobStatus.pending,
            store_id=body.store_id if step in ("upload", "sync") else None,
            config=per_step,
            source_job_id=prev_job_id,
            started_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        created_jobs.append({"step": step, "job_id": job.id})
        prev_job_id = job.id

    # Start only the FIRST job; the auto-chain in _execute_job will trigger the rest
    if created_jobs:
        run_job.delay(created_jobs[0]["job_id"])

    return {
        "run_id": created_jobs[0]["job_id"] if created_jobs else None,
        "jobs": created_jobs,
    }


@router.get("/run/{first_job_id}")
async def get_pipeline_run_status(first_job_id: int, db: AsyncSession = Depends(get_db)):
    """
    Return the status of every job in a pipeline chain by following
    source_job_id links forward from first_job_id.
    """
    first_job = await db.get(Job, first_job_id)
    if not first_job:
        raise HTTPException(404, f"Job #{first_job_id} not found")

    jobs_out = []
    visited: set[int] = set()

    def _job_dict(j: Job) -> dict:
        return {
            "job_id": j.id,
            "step": j.type.value,
            "status": j.status.value,
            "progress_percent": j.progress_percent,
            "total_items": j.total_items,
            "processed_items": j.processed_items,
            "failed_items": j.failed_items,
            "error_message": j.error_message,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            "source_job_id": j.source_job_id,
        }

    # Collect the chain by following source_job_id forward
    # We collect all jobs that have source_job_id pointing to any job in the chain
    chain = [first_job]
    visited.add(first_job.id)
    current_id = first_job.id
    for _ in range(10):  # max depth guard
        next_job = (
            await db.execute(
                select(Job).where(
                    Job.source_job_id == current_id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if not next_job or next_job.id in visited:
            break
        chain.append(next_job)
        visited.add(next_job.id)
        current_id = next_job.id

    jobs_out = [_job_dict(j) for j in chain]

    all_statuses = [j["status"] for j in jobs_out]
    if all(s == "completed" for s in all_statuses):
        overall = "completed"
    elif any(s == "failed" for s in all_statuses):
        overall = "failed"
    elif any(s in ("running", "pending") for s in all_statuses):
        overall = "running"
    else:
        overall = "unknown"

    return {"run_id": first_job_id, "overall_status": overall, "jobs": jobs_out}
