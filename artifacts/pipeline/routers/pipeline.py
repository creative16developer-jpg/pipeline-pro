from datetime import datetime, timezone
import asyncio
import math
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, String
from pydantic import BaseModel
from database import get_db
from models.models import PipelineJob, PipelineLog, Job, JobType, Store

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

ACTIVE_STATUSES = ("running", "review", "enrich_review", "content_review")


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
    include_enrich: bool = False
    include_generate: bool = False
    force_rerun: bool = False
    automatic_review_pause: Optional[bool] = None
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
        q = q.where(cast(PipelineJob.status, String) == status)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    pls = (await db.execute(q.offset((page - 1) * limit).limit(limit))).scalars().all()

    # Queue counts per store (for banner)
    from sqlalchemy import distinct
    queued_by_store: dict[int, int] = {}
    running_by_store: dict[int, int] = {}
    for pl in (
        await db.execute(
            select(PipelineJob).where(cast(PipelineJob.status, String).in_(["queued", "running", "review"]))
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
    if fetch_job.type not in (JobType.fetch, JobType.csv_import):
        raise HTTPException(400, f"Job #{body.fetch_job_id} is not a valid source job")

    # Queue check: is another pipeline running/in-review for this store?
    active = (
        await db.execute(
            select(PipelineJob)
            .where(
                PipelineJob.store_id == body.store_id,
                cast(PipelineJob.status, String).in_(list(ACTIVE_STATUSES)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    initial_status = "queued" if active else "running"

    automatic_review_pause = body.automatic_review_pause
    if automatic_review_pause is None:
        # Not sent explicitly — fall back to the saved
        # Settings -> Default Pipeline Options value (spec Section 4.1:
        # "Default from Settings -> Default Pipeline Options").
        try:
            from routers.settings import _PIPELINE_DEFAULTS_PATH, DEFAULT_PIPELINE_DEFAULTS
            import json as _json
            saved = {}
            if _PIPELINE_DEFAULTS_PATH.exists():
                saved = _json.loads(_PIPELINE_DEFAULTS_PATH.read_text())
            automatic_review_pause = {**DEFAULT_PIPELINE_DEFAULTS, **saved}.get("auto_review_pause", True)
        except Exception:
            automatic_review_pause = True

    pl = PipelineJob(
        store_id=body.store_id,
        fetch_job_id=body.fetch_job_id,
        status=initial_status,
        config={
            "include_enrich":   body.include_enrich,
            "include_generate": body.include_generate,
            "force_rerun": body.force_rerun,
            "automatic_review_pause": automatic_review_pause,
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
        from tasks.pipeline_tasks import _execute_pipeline
        asyncio.create_task(_execute_pipeline(pl.id))
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
    if pl.status not in ("review", "content_review"):
        raise HTTPException(400, f"Pipeline is not in a resumable state (current: {pl.status})")

    # Same fix as content_confirm above: mark this pipeline's extracted
    # attributes as confirmed so upload actually picks them up. This
    # generic /resume endpoint is a second, independent way to reach
    # upload without going through content_confirm's explicit review step
    # (e.g. resuming directly from a category-only 'review' pause with no
    # separate content review at all) -- same gap, same fix needed.
    from models.models import ProductEnrichAttr
    from sqlalchemy import update as _sa_update
    await db.execute(
        _sa_update(ProductEnrichAttr)
        .where(ProductEnrichAttr.pipeline_job_id == pl.id)
        .values(confirmed=True)
    )

    if pl.status == "content_review":
        pl.status = "review"
        pl.updated_at = datetime.now(timezone.utc)
        await db.commit()
    else:
        await db.commit()

    from tasks.pipeline_tasks import _resume_pipeline
    asyncio.create_task(_resume_pipeline(pl.id))

    return {"message": f"PL-{str(pl_id).zfill(3)} resuming — upload step starting"}


# ── Content Review data ───────────────────────────────────────────────────────

@router.get("/{pl_id}/content-data")
async def get_content_data(pl_id: int, db: AsyncSession = Depends(get_db)):
    """Return products from this pipeline's fetch job for content review."""
    from models.models import Product, Image
    from config import get_settings
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")

    products = (
        await db.execute(
            select(Product)
            .where(Product.fetch_job_id == pl.fetch_job_id)
            .order_by(Product.id.asc())
            .limit(200)
        )
    ).scalars().all()

    # Real thumbnail URLs for the review screen — previously this endpoint
    # only returned image_count, and the frontend rendered that many empty
    # placeholder boxes with no actual <img> or URL at all (labeled "img1",
    # "img2"...). Fetch every Image row for this batch in one query and
    # build servable URLs the same way job_tasks.py already does for the
    # WordPress-upload fallback (settings.server_base_url + /media/images/).
    product_ids = [p.id for p in products]
    images_by_product: dict[int, list[str]] = {}
    if product_ids:
        settings = get_settings()
        base = (settings.server_base_url or "").rstrip("/")
        img_rows = (
            await db.execute(
                select(Image)
                .where(Image.product_id.in_(product_ids))
                .order_by(Image.product_id, Image.position)
            )
        ).scalars().all()
        for img in img_rows:
            url = None
            if img.processed_path and base:
                url = f"{base}/media/images/{Path(img.processed_path).name}"
            elif img.original_url and img.original_url.startswith(("http://", "https://")):
                # Fall back to Sunsky's original remote URL when there's no
                # processed/local copy yet (or no server_base_url configured)
                # -- but only when it's actually a real, browser-fetchable
                # URL. Some products' original_url is a "sunsky-zip://..."
                # marker instead (images that arrived bundled in a ZIP
                # rather than individually fetchable) -- that's an internal
                # reference, not a real address, and rendering it as an
                # <img src> just produces a permanently broken image icon.
                url = img.original_url
            if url:
                images_by_product.setdefault(img.product_id, []).append(url)

    product_list = []
    for p in products:
        has_description = bool(p.description)
        product_list.append({
            "id": p.id,
            "sku": p.sku,
            "name": p.name,
            "description": p.description or "",
            "short_description": p.short_description or "",
            "price": p.price or "",
            "status": p.status.value if hasattr(p.status, "value") else str(p.status),
            "image_count": p.image_count,
            "image_urls": images_by_product.get(p.id, []),
            "category_id": p.category_id or "",
            "error_message": p.error_message or "",
            "content_source": p.content_source or {},
            "needs_attention": not has_description or bool(p.error_message),
        })

    ready = sum(1 for p in product_list if not p["needs_attention"])
    return {
        "pipeline_id": pl_id,
        "total": len(product_list),
        "needs_attention": len(product_list) - ready,
        "ready": ready,
        "products": product_list,
    }


# ── Content Confirm (Review B → start upload) ─────────────────────────────────

@router.post("/{pl_id}/content-confirm")
async def content_confirm(pl_id: int, db: AsyncSession = Depends(get_db)):
    """Confirm content review and start the upload step."""
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")
    if pl.status != "content_review":
        raise HTTPException(400, f"Pipeline is not in content review state (current: {pl.status})")

    # Mark every attribute extracted during this pipeline's Enrich step as
    # confirmed. tasks/job_tasks.py's upload step ONLY pushes
    # ProductEnrichAttr rows to WooCommerce where confirmed=True (see the
    # 'AI-extracted enrich attributes' block there) -- but this endpoint,
    # the one actually reached from the combined Review pause in the
    # current pipeline flow (Enrich auto-completes, no separate
    # enrich_review pause), never marked anything confirmed. Only a
    # different, older endpoint (enrich-confirm, gated on a distinct
    # 'enrich_review' pipeline status this flow never reaches) did that.
    # Result: extraction was correct, but almost nothing ever reached
    # WooCommerce, because nothing was ever marked as reviewed/approved.
    # The Review UI has no per-attribute confirm checkboxes at this stage --
    # a single 'Confirm extraction — continue' action approves everything
    # shown, so a bulk update here matches what the UI actually represents.
    from models.models import ProductEnrichAttr
    from sqlalchemy import update as _sa_update
    await db.execute(
        _sa_update(ProductEnrichAttr)
        .where(ProductEnrichAttr.pipeline_job_id == pl.id)
        .values(confirmed=True)
    )

    # Set back to "review" so _resume_pipeline passes its guard check
    # (_resume_pipeline handles the running/upload transition itself)
    pl.status = "review"
    pl.current_step = "review"
    pl.updated_at = datetime.now(timezone.utc)

    db.add(PipelineLog(
        pipeline_job_id=pl.id, level="info",
        message="Content review confirmed — starting upload",
    ))
    await db.commit()

    from tasks.pipeline_tasks import _resume_pipeline
    asyncio.create_task(_resume_pipeline(pl.id))

    return {"ok": True, "message": f"PL-{str(pl_id).zfill(3)} upload starting"}


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{pl_id}")
async def delete_pipeline(pl_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")
    if pl.status in ("running", "review", "enrich_review", "content_review", "queued"):
        raise HTTPException(400, "Cannot delete an active or queued pipeline — cancel it first")

    await db.execute(
        __import__("sqlalchemy", fromlist=["delete"]).delete(PipelineLog).where(PipelineLog.pipeline_job_id == pl_id)
    )
    await db.delete(pl)
    await db.commit()
    return {"ok": True, "message": f"PL-{str(pl_id).zfill(3)} deleted"}


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

@router.post("/{pl_id}/continue")
async def continue_pipeline(pl_id: int, db: AsyncSession = Depends(get_db)):
    """Resume a cancelled/failed pipeline in-place from the step it was on."""
    pl = await db.get(PipelineJob, pl_id)
    if not pl:
        raise HTTPException(404, f"Pipeline #{pl_id} not found")
    if pl.status not in ("failed", "cancelled"):
        raise HTTPException(400, "Only failed or cancelled pipelines can be continued")

    current_step = pl.current_step or "process"

    # Review / enrich states — just flip status back so the review UI reappears
    if current_step == "review":
        pl.status = "review"
        pl.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return _pl_dict(pl)

    if current_step == "enrich":
        pl.status = "enrich_review"
        pl.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return _pl_dict(pl)

    # All other steps — re-execute in-place from current_step
    active = (
        await db.execute(
            select(PipelineJob)
            .where(
                PipelineJob.store_id == pl.store_id,
                cast(PipelineJob.status, String).in_(list(ACTIVE_STATUSES)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if active:
        pl.status = "queued"
    else:
        pl.status = "running"
    pl.updated_at = datetime.now(timezone.utc)
    await db.commit()

    if pl.status == "running":
        from tasks.pipeline_tasks import _continue_pipeline
        asyncio.create_task(_continue_pipeline(pl.id, current_step))

    return _pl_dict(pl)


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
                cast(PipelineJob.status, String).in_(list(ACTIVE_STATUSES)),
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
        from tasks.pipeline_tasks import _execute_pipeline
        asyncio.create_task(_execute_pipeline(new_pl.id))

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
