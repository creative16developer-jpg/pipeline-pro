from datetime import datetime, timezone
import asyncio
import logging
import math
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, String
from pydantic import BaseModel
from database import get_db
from models.models import PipelineJob, PipelineLog, Job, JobType, Store

logger = logging.getLogger(__name__)
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

    # Attributes and resolved category name — same data we've been checking
    # via raw SQL all session, now visible directly in this review card
    # instead. Client's own original feedback: "no clarity regarding what
    # is being uploaded... like Baselinker, where you can see every detail
    # of every product." One batched query each, not per-product, to keep
    # this endpoint fast for a full batch.
    from models.models import ProductEnrichAttr, SunskyCategoryMapping as _SCM_CD
    attrs_by_product: dict[int, list[dict]] = {}
    if product_ids:
        attr_rows = (
            await db.execute(
                select(ProductEnrichAttr)
                .where(
                    ProductEnrichAttr.product_id.in_(product_ids),
                    ProductEnrichAttr.pipeline_job_id == pl_id,
                )
                .order_by(ProductEnrichAttr.product_id, ProductEnrichAttr.attribute)
            )
        ).scalars().all()
        for a in attr_rows:
            attrs_by_product.setdefault(a.product_id, []).append({
                "attribute": a.attribute,
                "raw_value": a.raw_value or "",
                "source": a.source,
                "flagged": a.flagged,
            })

    by_name: dict[str, str] = {}
    by_cat_id: dict[str, str] = {}
    sunsky_name_by_product: dict[int, str] = {}
    sunsky_id_by_product: dict[int, str] = {}
    try:
        from services.enrich_service import extract_sunsky_category
        # Deliberately NOT using get_effective_category_name_map() here --
        # that function always falls back to sunsky_client's live,
        # rate-limited full category tree walk whenever a category isn't
        # in the starred set, with up to a 20s wait. It's fine to pay that
        # cost once per pipeline run (where it's actually called), but this
        # endpoint gets polled repeatedly by the review screen -- confirmed
        # live: every poll re-triggered a fresh 20s tree walk against
        # Sunsky's API, making the whole page sluggish for no benefit here.
        # This display card only needs the fast, zero-API-call starred-
        # category source; an un-starred category just shows its raw
        # Sunsky name/ID here instead of the mapped WooCommerce name --
        # informational only, doesn't affect what Upload actually applies.
        from sqlalchemy import select as _sel_star
        from models.models import StarredSunskyCategory
        starred_rows = (await db.execute(_sel_star(StarredSunskyCategory))).scalars().all()
        starred_only_map = {r.cat_id: r.name for r in starred_rows}
        for p in products:
            raw = p.raw_data or {}
            sunsky_name_by_product[p.id] = extract_sunsky_category(raw, starred_only_map)
            sunsky_id_by_product[p.id] = str(raw.get("categoryId") or raw.get("catId") or raw.get("category_id") or "").strip()
    except Exception as _name_e:
        logger.warning(f"[content-data] category name resolution failed: {_name_e}")

    # Separate try block on purpose: a failure here previously could hide
    # behind the name-resolution try above sharing one broad except, making
    # a real mapping-lookup bug look identical to "genuinely unmapped" --
    # confirmed live: category name resolved correctly but still showed
    # "unmapped" despite a saved mapping existing for that exact name.
    try:
        cat_names_present = {n for n in sunsky_name_by_product.values() if n}
        if cat_names_present:
            map_rows = (
                await db.execute(
                    select(_SCM_CD).where(_SCM_CD.store_id == pl.store_id)
                )
            ).scalars().all()
            # Was: `if m.woo_cat_name` -- but confirmed live that a row can
            # have a real, working woo_cat_id (what uploads actually use)
            # with woo_cat_name left blank (empty string, not NULL) from
            # however it got saved originally. That's a real gap in the
            # save path worth fixing separately, but for this display card
            # the fix is simpler: woo_cat_id is what's authoritative and
            # always populated, so check that instead. Store True as the
            # marker (we don't have the real WooCommerce name to show) and
            # let the per-product loop fall back to the Sunsky category
            # name, which is still accurate and informative either way.
            #
            # by_cat_id is now the PRIMARY lookup: sunsky_cat_id is a stable
            # numeric ID that doesn't depend on which of the several category
            # NAME resolvers (starred-only here vs. starred+live-fallback in
            # Category Review vs. Sync's own BFS walk) happened to produce
            # which string. Matching by name (by_name) is kept only as a
            # fallback for mapping rows saved before sunsky_cat_id existed.
            by_cat_id = {
                m.sunsky_cat_id: (m.woo_cat_name or True)
                for m in map_rows if m.woo_cat_id and m.sunsky_cat_id
            }
            by_name = {
                m.sunsky_cat.strip().lower(): (m.woo_cat_name or True)
                for m in map_rows if m.woo_cat_id
            }
            logger.info(f"[content-data] pl={pl_id} store_id={pl.store_id} "
                        f"loaded {len(by_name)} category mappings, "
                        f"looking for: {sorted(cat_names_present)}")
    except Exception as _map_e:
        logger.warning(f"[content-data] category mapping lookup failed: {_map_e}")

    product_list = []
    for p in products:
        has_description = bool(p.description)
        sunsky_cat_name = sunsky_name_by_product.get(p.id, "")
        sunsky_cat_id = sunsky_id_by_product.get(p.id, "")
        _mapped_val = by_cat_id.get(sunsky_cat_id) if sunsky_cat_id else None
        if _mapped_val is None and sunsky_cat_name:
            _mapped_val = by_name.get(sunsky_cat_name.strip().lower())
        resolved_woo_cat = _mapped_val if isinstance(_mapped_val, str) else None
        is_mapped = _mapped_val is not None
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
            "category_name": resolved_woo_cat or sunsky_cat_name or "",
            "category_mapped": is_mapped,
            "attributes": attrs_by_product.get(p.id, []),
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

from pydantic import BaseModel as _BaseModel

class ContentConfirmRequest(_BaseModel):
    excluded_product_ids: list[int] = []


@router.post("/{pl_id}/content-confirm")
async def content_confirm(pl_id: int, body: ContentConfirmRequest = ContentConfirmRequest(), db: AsyncSession = Depends(get_db)):
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

    # "Exclude from upload" in Content Review previously did NOTHING on the
    # backend -- it was local React state (`excluded` Set in
    # ContentReviewSection) that only filtered what displayed in that one
    # browser tab. Upload All sent no body at all, and _run_upload selects
    # products purely by status + fetch_job_id, with zero awareness of
    # which products the operator had flagged. Confirmed live: two
    # "LC.IMEEKE" products marked excluded were uploaded anyway, landing in
    # WooCommerce as broken Draft/Uncategorized/0-stock listings.
    #
    # Fix: persist the excluded IDs into this pipeline's own config (no
    # schema change / migration needed -- PipelineJob.config is already a
    # JSON column) under upload_config, which _resume_pipeline already
    # merges into the Upload Job's own config verbatim. _run_upload reads
    # it from there and excludes those product IDs from its query.
    if body.excluded_product_ids:
        pl_cfg = dict(pl.config or {})
        upload_cfg = dict(pl_cfg.get("upload_config", {}))
        upload_cfg["excluded_product_ids"] = body.excluded_product_ids
        pl_cfg["upload_config"] = upload_cfg
        pl.config = pl_cfg

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
