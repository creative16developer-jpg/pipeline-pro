"""
Pipeline orchestration Celery tasks.

Each PipelineJob runs: Process → Generate (optional) → Review (pause) → Upload → Sync

Queue rule: only ONE pipeline per store may be running/in-review at a time.
The next queued pipeline auto-starts when the current one finishes/fails/is cancelled.
"""
import sys
from pathlib import Path

_pkg_dir = str(Path(__file__).parent.parent.resolve())
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

import asyncio
from datetime import datetime, timezone
from typing import Optional

from celery_app import celery_app


# ─────────────────────────────────────────────────────────────────────────────
# Celery tasks
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(bind=True, name="tasks.run_pipeline_job")
def run_pipeline_job(self, pipeline_job_id: int):
    asyncio.run(_execute_pipeline(pipeline_job_id))


@celery_app.task(bind=True, name="tasks.resume_pipeline_job")
def resume_pipeline_job(self, pipeline_job_id: int):
    asyncio.run(_resume_pipeline(pipeline_job_id))


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _plog(db, pipeline_job_id: int, step: Optional[str], level: str, message: str):
    from models.models import PipelineLog
    db.add(PipelineLog(pipeline_job_id=pipeline_job_id, step=step, level=level, message=message))
    await db.commit()


async def _run_step(db, pl_id: int, step_name: str, job, step_fn):
    """
    Run a single step function with proper status tracking.
    Updates job.status and raises on failure.
    """
    from models.models import JobStatus
    job.status = JobStatus.running
    job.started_at = datetime.now(timezone.utc)
    await db.commit()

    try:
        await step_fn(db, job)
        job.status = JobStatus.completed
        job.progress_percent = 100.0
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await _plog(db, pl_id, step_name, "info",
                    f"[{step_name}] done — {job.processed_items}/{job.total_items} items "
                    f"({job.failed_items} failed)")
    except Exception as e:
        job.status = JobStatus.failed
        job.error_message = str(e)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise


async def _is_cancelled(db, pipeline_job_id: int) -> bool:
    from models.models import PipelineJob
    pl = await db.get(PipelineJob, pipeline_job_id)
    await db.refresh(pl)
    return pl is None or pl.status == "cancelled"


async def _advance_queue(db, store_id: int, finished_pl_id: int):
    """Auto-start the oldest queued pipeline for this store."""
    from models.models import PipelineJob
    from sqlalchemy import select
    next_pl = (
        await db.execute(
            select(PipelineJob)
            .where(
                PipelineJob.store_id == store_id,
                PipelineJob.status == "queued",
                PipelineJob.id != finished_pl_id,
            )
            .order_by(PipelineJob.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if next_pl:
        next_pl.status = "running"
        next_pl.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await _plog(db, next_pl.id, None, "info",
                    f"Auto-started from queue — PL-{str(finished_pl_id).zfill(3)} finished")
        run_pipeline_job.delay(next_pl.id)


def _make_pl_id(n: int) -> str:
    return f"PL-{str(n).zfill(3)}"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 execution:  Process → Generate (opt) → pause at Review
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_pipeline(pipeline_job_id: int):
    from database import make_session_factory
    from models.models import PipelineJob, Job, JobType, JobStatus

    CelerySession, celery_engine = make_session_factory()
    try:
        async with CelerySession() as db:
            pl = await db.get(PipelineJob, pipeline_job_id)
            if not pl or pl.status == "cancelled":
                return

            pl.status = "running"
            pl.current_step = "process"
            pl.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await _plog(db, pl.id, None, "info",
                        f"{_make_pl_id(pl.id)} started for store #{pl.store_id}, "
                        f"fetch job #{pl.fetch_job_id}")

            cfg = pl.config or {}
            force_rerun = cfg.get("force_rerun", False)

            try:
                # ── Step 1: Process ────────────────────────────────────────
                from tasks.job_tasks import _run_process
                process_job = Job(
                    type=JobType.process,
                    status=JobStatus.pending,
                    store_id=pl.store_id,
                    config={**cfg.get("process_config", {}), "force_rerun": force_rerun},
                    source_job_id=pl.fetch_job_id,
                    pipeline_job_id=pl.id,
                    started_at=datetime.now(timezone.utc),
                )
                db.add(process_job)
                await db.commit()
                await db.refresh(process_job)

                await _plog(db, pl.id, "process", "info",
                            f"Process job #{process_job.id} created")
                await _run_step(db, pl.id, "process", process_job, _run_process)

                if await _is_cancelled(db, pl.id):
                    return

                # ── Step 2: Generate (optional) ───────────────────────────
                include_generate = cfg.get("include_generate", False)
                if include_generate:
                    pl.current_step = "generate"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _plog(db, pl.id, "generate", "info", "Content generation starting…")
                    stats = await _run_generate(db, pl, cfg)
                    pl.stats_json = stats
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    if await _is_cancelled(db, pl.id):
                        return
                else:
                    # Populate basic stats from process step for review display
                    pl.stats_json = {
                        "total": process_job.total_items,
                        "ok": process_job.processed_items - process_job.failed_items,
                        "fallback": 0,
                        "failed": process_job.failed_items,
                        "note": "Content generation skipped",
                    }
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()

                # ── Pause at Review ───────────────────────────────────────
                pl.status = "review"
                pl.current_step = "review"
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                stats = pl.stats_json or {}
                await _plog(
                    db, pl.id, "review", "info",
                    f"Pipeline paused for review — "
                    f"{stats.get('total', 0)} total | "
                    f"{stats.get('ok', 0)} OK | "
                    f"{stats.get('fallback', 0)} fallback | "
                    f"{stats.get('failed', 0)} failed. "
                    f"Click Resume to continue with Upload.",
                )

            except Exception as e:
                pl.status = "failed"
                pl.error_message = str(e)
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, pl.current_step or "process", "error",
                            f"Pipeline failed: {e}")
                await _advance_queue(db, pl.store_id, pl.id)
    finally:
        await celery_engine.dispose()


async def _run_generate(db, pl, cfg: dict) -> dict:
    """
    Content generation step.
    Currently a structured placeholder that counts products and logs.
    Returns stats dict: {total, ok, fallback, failed}.
    """
    from models.models import Product
    from sqlalchemy import select, func as sqlfunc

    total = (
        await db.execute(
            select(sqlfunc.count())
            .select_from(Product)
            .where(Product.fetch_job_id == pl.fetch_job_id)
        )
    ).scalar_one()

    await _plog(db, pl.id, "generate", "info",
                f"Content generation: {total} products in batch")

    gen_cfg = cfg.get("content_gen_config", {})
    if not gen_cfg:
        # Try loading the saved config from disk; fall back to DEFAULT_CONFIG
        from pathlib import Path
        import json
        saved_path = Path(__file__).parent.parent / "config_store" / "content_gen_config.json"
        if saved_path.exists():
            try:
                gen_cfg = json.loads(saved_path.read_text())
                await _plog(db, pl.id, "generate", "info",
                            "Loaded saved content generation config")
            except Exception:
                pass
        if not gen_cfg:
            from routers.content import DEFAULT_CONFIG
            gen_cfg = DEFAULT_CONFIG
            await _plog(db, pl.id, "generate", "info",
                        "Using default content generation config")

    await _plog(db, pl.id, "generate", "info",
                f"Content generation complete (placeholder) — {total} products processed")
    return {
        "total": total,
        "ok": total,
        "fallback": 0,
        "failed": 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 execution (resume from Review): Upload → Sync
# ─────────────────────────────────────────────────────────────────────────────

async def _resume_pipeline(pipeline_job_id: int):
    from database import make_session_factory
    from models.models import PipelineJob, Job, JobType, JobStatus
    from sqlalchemy import select

    CelerySession, celery_engine = make_session_factory()
    try:
        async with CelerySession() as db:
            pl = await db.get(PipelineJob, pipeline_job_id)
            if not pl or pl.status != "review":
                return

            pl.status = "running"
            pl.current_step = "upload"
            pl.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await _plog(db, pl.id, "upload", "info",
                        f"{_make_pl_id(pl.id)} resuming from review → upload")

            cfg = pl.config or {}
            force_rerun = cfg.get("force_rerun", False)

            try:
                # Locate the process job to use as source for upload
                process_job = (
                    await db.execute(
                        select(Job)
                        .where(
                            Job.pipeline_job_id == pl.id,
                            Job.type == JobType.process,
                        )
                        .order_by(Job.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                source_for_upload = process_job.id if process_job else pl.fetch_job_id

                # ── Step 3: Upload ─────────────────────────────────────────
                from tasks.job_tasks import _run_upload
                upload_job = Job(
                    type=JobType.upload,
                    status=JobStatus.pending,
                    store_id=pl.store_id,
                    config={**cfg.get("upload_config", {}), "force_rerun": force_rerun},
                    source_job_id=source_for_upload,
                    pipeline_job_id=pl.id,
                    started_at=datetime.now(timezone.utc),
                )
                db.add(upload_job)
                await db.commit()
                await db.refresh(upload_job)

                await _plog(db, pl.id, "upload", "info",
                            f"Upload job #{upload_job.id} created (source: #{source_for_upload})")
                await _run_step(db, pl.id, "upload", upload_job, _run_upload)

                if await _is_cancelled(db, pl.id):
                    return

                # ── Step 4: Sync ───────────────────────────────────────────
                pl.current_step = "sync"
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()

                from tasks.job_tasks import _run_sync
                sync_job = Job(
                    type=JobType.sync,
                    status=JobStatus.pending,
                    store_id=pl.store_id,
                    config={**cfg.get("sync_config", {}), "force_rerun": force_rerun},
                    source_job_id=upload_job.id,
                    pipeline_job_id=pl.id,
                    started_at=datetime.now(timezone.utc),
                )
                db.add(sync_job)
                await db.commit()
                await db.refresh(sync_job)

                await _plog(db, pl.id, "sync", "info",
                            f"Sync job #{sync_job.id} created")
                await _run_step(db, pl.id, "sync", sync_job, _run_sync)

                # ── Completed ─────────────────────────────────────────────
                pl.status = "completed"
                pl.current_step = None
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, None, "info",
                            f"{_make_pl_id(pl.id)} completed successfully!")

            except Exception as e:
                pl.status = "failed"
                pl.error_message = str(e)
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, pl.current_step or "upload", "error",
                            f"Pipeline failed: {e}")

            finally:
                await _advance_queue(db, pl.store_id, pl.id)
    finally:
        await celery_engine.dispose()
