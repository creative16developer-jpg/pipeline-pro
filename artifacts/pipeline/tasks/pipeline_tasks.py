"""
Pipeline orchestration tasks.

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
from sqlalchemy import cast, String


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
                cast(PipelineJob.status, String) == "queued",
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
        asyncio.create_task(_execute_pipeline(next_pl.id))


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
                from tasks.job_tasks import _run_process  # noqa: keep import here
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

                # ── Step 1.5: Enrich (optional) ───────────────────────────
                include_enrich = cfg.get("include_enrich", False)
                if include_enrich:
                    pl.current_step = "enrich"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _plog(db, pl.id, "enrich", "info",
                                "Enrich step: AI attribute extraction starting…")
                    enrich_count = await _run_enrich_extraction(db, pl, cfg)
                    await _plog(db, pl.id, "enrich", "info",
                                f"Attribute extraction complete — {enrich_count} attrs extracted. "
                                f"Pausing for review.")
                    pl.status = "enrich_review"
                    pl.current_step = "enrich"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    return  # Resumed by enrich_resume_pipeline_job after user confirms

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
    Content generation step — DAG-aware field generation via services.content_service.
    Saves results back to each Product row so the upload step uses them.
    Returns stats dict: {total, ok, fallback, failed}.
    """
    from models.models import Product, CsvMapping
    from sqlalchemy import select
    import json
    from pathlib import Path

    # ── Load generation config ────────────────────────────────────────────────
    gen_cfg = cfg.get("content_gen_config", {})
    if not gen_cfg:
        saved_path = Path(__file__).parent.parent / "config_store" / "content_gen_config.json"
        if saved_path.exists():
            try:
                gen_cfg = json.loads(saved_path.read_text())
                await _plog(db, pl.id, "generate", "info", "Loaded saved content generation config")
            except Exception:
                pass
        if not gen_cfg:
            from routers.content import DEFAULT_CONFIG
            gen_cfg = DEFAULT_CONFIG
            await _plog(db, pl.id, "generate", "info", "Using default content generation config")

    # Validate/normalise the config
    template: dict = gen_cfg if isinstance(gen_cfg, dict) else {}
    gs = (template.get("globalSettings") or {})
    ai_enabled = gs.get("ai_enabled", False)
    ai_provider = gs.get("ai_provider", "openai")

    # ── Import service (no circular dep — service never imports from routers) ──
    from services.content_service import generate_product, FIELD_ATTR

    # ── Build CSV mapping lookup dict ─────────────────────────────────────────
    csv_q = await db.execute(select(CsvMapping))
    csv_entries = csv_q.scalars().all()
    csv_lookup: dict[str, CsvMapping] = {e.sunsky_sku: e for e in csv_entries}
    if csv_lookup:
        await _plog(db, pl.id, "generate", "info",
                    f"CSV mappings loaded: {len(csv_lookup)} entries")

    # ── Load products ─────────────────────────────────────────────────────────
    products = (
        await db.execute(
            select(Product).where(Product.fetch_job_id == pl.fetch_job_id)
        )
    ).scalars().all()

    total = len(products)
    await _plog(db, pl.id, "generate", "info",
                f"Content generation: {total} products | "
                f"AI={'on (' + ai_provider + ')' if ai_enabled else 'off (logic only)'}")

    ok_count = fallback_count = failed_count = 0

    for product in products:
        try:
            raw = product.raw_data or {}

            # Apply CSV mapping if available
            csv_entry = csv_lookup.get(product.sku)
            csv_title = ""
            site_sku = ""
            if csv_entry:
                csv_title = csv_entry.csv_title or ""
                site_sku = csv_entry.site_sku or ""
                # Apply both directly — same pattern, same priority.
                # Content generation runs AFTER this, so it sees the updated
                # name but csv_title in prod_dict ensures logic mode also
                # returns it. Post-generate we re-assert csv_title so AI
                # mode can't silently overwrite it.
                if site_sku:
                    product.site_sku = site_sku
                if csv_title:
                    product.name = csv_title  # direct apply like site_sku

            prod_dict = {
                "name":        product.name or "",
                "sku":         product.sku or "",
                "description": product.description or "",
                "price":       product.price or "0",
                "csv_title":   csv_title,
                "site_sku":    site_sku,
                **raw,
            }

            sources: dict = product.content_source or {}
            prod_failed = False

            # Run all enabled fields via DAG engine
            results = await generate_product(prod_dict, template)

            for field, result in results.items():
                attr = FIELD_ATTR.get(field)
                if not attr:
                    continue
                if result.get("status") == "failed":
                    await _plog(db, pl.id, "generate", "warn",
                                f"  {product.sku} [{field}]: {result.get('error', 'failed')}")
                    prod_failed = True
                    continue
                value = result.get("value", "")
                source = result.get("source", "logic")
                if value:
                    setattr(product, attr, value)
                    sources[field] = source
                    if source.startswith("logic:fallback"):
                        err_detail = result.get("error") or "AI call failed"
                        await _plog(db, pl.id, "generate", "warn",
                                    f"  {product.sku} [{field}]: logic fallback — {err_detail}")

            product.content_source = sources

            # CSV title always wins — re-assert after content gen in case
            # AI mode overwrote it.
            if csv_title:
                product.name = csv_title

            if prod_failed:
                fallback_count += 1
            else:
                ok_count += 1

        except Exception as e:
            await _plog(db, pl.id, "generate", "error",
                        f"  {product.sku}: generation failed — {e}")
            failed_count += 1

        if (ok_count + fallback_count + failed_count) % 10 == 0:
            await db.commit()

    await db.commit()

    await _plog(db, pl.id, "generate", "info",
                f"Content generation complete — "
                f"{ok_count} ok | {fallback_count} partial | {failed_count} failed")
    return {
        "total": total,
        "ok": ok_count,
        "fallback": fallback_count,
        "failed": failed_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Enrich extraction helper
# ─────────────────────────────────────────────────────────────────────────────

async def _run_enrich_extraction(db, pl, cfg: dict) -> int:
    """
    Run AI attribute extraction for all products in this pipeline's fetch job.
    Saves results to product_enrich_attrs and variant_groups tables.
    Returns total attr count extracted.
    """
    from models.models import Product, ProductEnrichAttr, VariantGroup
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from services.enrich_service import extract_attributes, suggest_variant_groups
    import json
    from pathlib import Path

    gen_cfg = cfg.get("content_gen_config", {})
    if not gen_cfg:
        saved_path = Path(__file__).parent.parent / "config_store" / "content_gen_config.json"
        if saved_path.exists():
            try:
                gen_cfg = json.loads(saved_path.read_text())
            except Exception:
                pass

    products = (
        await db.execute(
            select(Product).where(Product.fetch_job_id == pl.fetch_job_id)
        )
    ).scalars().all()

    total_attrs = 0
    product_dicts = []
    for product in products:
        raw = product.raw_data or {}
        prod_dict = {"id": product.id, "name": product.name or "", **raw}
        product_dicts.append(prod_dict)

        attrs = await extract_attributes(prod_dict, gen_cfg, db=db)
        for a in attrs:
            stmt = (
                pg_insert(ProductEnrichAttr)
                .values(
                    pipeline_job_id=pl.id,
                    product_id=product.id,
                    attribute=a["attribute"],
                    raw_value=a["raw_value"],
                    confidence=a.get("confidence"),
                    source=a.get("source", "rule_based"),
                    flagged=a.get("flagged", False),
                    confirmed=False,
                )
                .on_conflict_do_update(
                    index_elements=["pipeline_job_id", "product_id", "attribute"],
                    set_={
                        "raw_value":  a["raw_value"],
                        "confidence": a.get("confidence"),
                        "source":     a.get("source", "rule_based"),
                        "flagged":    a.get("flagged", False),
                    },
                )
            )
            await db.execute(stmt)
            total_attrs += 1

    await db.commit()

    # Suggest variant groups
    suggestions = await suggest_variant_groups(product_dicts, gen_cfg)
    for sg in suggestions:
        vg = VariantGroup(
            pipeline_job_id=pl.id,
            attribute=sg["attribute"],
            product_ids=sg["product_ids"],
            pattern=sg.get("pattern"),
            confirmed=False,
        )
        db.add(vg)
    await db.commit()

    await _plog(db, pl.id, "enrich", "info",
                f"  {len(products)} products · {total_attrs} attributes · "
                f"{len(suggestions)} variant group suggestion(s)")
    return total_attrs


# ─────────────────────────────────────────────────────────────────────────────
# Enrich resume: continues from enrich_review → Generate (opt) → Review pause
# ─────────────────────────────────────────────────────────────────────────────

async def _enrich_resume_pipeline(pipeline_job_id: int):
    from database import make_session_factory
    from models.models import PipelineJob

    CelerySession, celery_engine = make_session_factory()
    try:
        async with CelerySession() as db:
            pl = await db.get(PipelineJob, pipeline_job_id)
            if not pl or pl.status not in ("enrich_review", "running"):
                return

            cfg = pl.config or {}
            include_generate = cfg.get("include_generate", False)

            try:
                # ── Step 2: Generate (optional) ───────────────────────────
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
                    from models.models import Job, JobType
                    from sqlalchemy import select
                    process_job = (
                        await db.execute(
                            select(Job).where(
                                Job.pipeline_job_id == pl.id,
                                Job.type == JobType.process,
                            ).order_by(Job.id.desc()).limit(1)
                        )
                    ).scalar_one_or_none()
                    pl.stats_json = {
                        "total":    process_job.total_items     if process_job else 0,
                        "ok":       (process_job.processed_items - process_job.failed_items) if process_job else 0,
                        "fallback": 0,
                        "failed":   process_job.failed_items    if process_job else 0,
                        "note":     "Content generation skipped",
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
                    f"Confirm category mapping and click Resume.",
                )

            except Exception as e:
                pl.status = "failed"
                pl.error_message = str(e)
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, pl.current_step or "enrich", "error",
                            f"Pipeline failed after enrich resume: {e}")
                await _advance_queue(db, pl.store_id, pl.id)
    finally:
        await celery_engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Continue execution from a specific step (cancelled/failed pipeline)
# ─────────────────────────────────────────────────────────────────────────────

async def _continue_pipeline(pipeline_job_id: int, from_step: str):
    """Re-execute a cancelled/failed pipeline in-place from a specific step."""
    from database import make_session_factory
    from models.models import PipelineJob, Job, JobType, JobStatus
    from sqlalchemy import select

    STEP_ORDER = ["process", "enrich", "generate", "review", "upload", "sync"]
    try:
        from_idx = STEP_ORDER.index(from_step)
    except ValueError:
        from_idx = 0

    CelerySession, celery_engine = make_session_factory()
    try:
        async with CelerySession() as db:
            pl = await db.get(PipelineJob, pipeline_job_id)
            if not pl or pl.status != "running":
                return

            cfg = pl.config or {}
            force_rerun = cfg.get("force_rerun", False)
            await _plog(db, pl.id, None, "info",
                        f"{_make_pl_id(pl.id)} continuing from step '{from_step}'")

            try:
                process_job = None

                # ── Process ────────────────────────────────────────────────
                if from_idx == 0:
                    pl.current_step = "process"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    from tasks.job_tasks import _run_process  # noqa
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
                    await _plog(db, pl.id, "process", "info", f"Process job #{process_job.id} created")
                    await _run_step(db, pl.id, "process", process_job, _run_process)
                    if await _is_cancelled(db, pl.id):
                        return
                else:
                    # Locate the most recent process job from this pipeline
                    process_job = (await db.execute(
                        select(Job).where(
                            Job.pipeline_job_id == pl.id,
                            Job.type == JobType.process,
                        ).order_by(Job.id.desc()).limit(1)
                    )).scalar_one_or_none()

                # ── Enrich (optional) ──────────────────────────────────────
                include_enrich = cfg.get("include_enrich", False)
                if from_idx <= 1 and include_enrich:
                    pl.current_step = "enrich"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _plog(db, pl.id, "enrich", "info",
                                "Enrich step: AI attribute extraction starting…")
                    enrich_count = await _run_enrich_extraction(db, pl, cfg)
                    await _plog(db, pl.id, "enrich", "info",
                                f"Attribute extraction complete — {enrich_count} attrs extracted. "
                                f"Pausing for review.")
                    pl.status = "enrich_review"
                    pl.current_step = "enrich"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    return  # Resumed by enrich confirm

                # ── Generate (optional) ────────────────────────────────────
                include_generate = cfg.get("include_generate", False)
                if from_idx <= 2 and include_generate:
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
                elif from_idx <= 2:
                    if process_job:
                        pl.stats_json = {
                            "total":    process_job.total_items,
                            "ok":       (process_job.processed_items - process_job.failed_items),
                            "fallback": 0,
                            "failed":   process_job.failed_items,
                            "note":     "Content generation skipped",
                        }
                        pl.updated_at = datetime.now(timezone.utc)
                        await db.commit()

                # ── Review pause (if we haven't reached upload yet) ────────
                if from_idx < 4:
                    pl.status = "review"
                    pl.current_step = "review"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    stats = pl.stats_json or {}
                    await _plog(db, pl.id, "review", "info",
                        f"Pipeline paused for review — "
                        f"{stats.get('total', 0)} total | "
                        f"{stats.get('ok', 0)} OK | "
                        f"{stats.get('fallback', 0)} fallback | "
                        f"{stats.get('failed', 0)} failed. "
                        f"Confirm category mapping and click Resume.")
                    return  # Resumed by _resume_pipeline after user confirms

                # ── Upload ────────────────────────────────────────────────
                source_for_upload = process_job.id if process_job else pl.fetch_job_id
                pl.current_step = "upload"
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                from tasks.job_tasks import _run_upload  # noqa
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

                # ── Sync ──────────────────────────────────────────────────
                pl.current_step = "sync"
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                from tasks.job_tasks import _run_sync  # noqa
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
                await _plog(db, pl.id, "sync", "info", f"Sync job #{sync_job.id} created")
                await _run_step(db, pl.id, "sync", sync_job, _run_sync)

                # ── Complete ─────────────────────────────────────────────
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
                await _plog(db, pl.id, pl.current_step or "continue", "error",
                            f"Pipeline failed during continue: {e}")
                await _advance_queue(db, pl.store_id, pl.id)
    finally:
        await celery_engine.dispose()


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
