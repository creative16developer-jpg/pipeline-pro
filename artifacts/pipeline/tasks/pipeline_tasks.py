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


async def _unmapped_sunsky_categories(db, pl) -> list[str]:
    """
    Return the distinct Sunsky categories in this pipeline's product batch
    that have NO saved row in SunskyCategoryMapping for this store yet.

    Developer Guidelines v2.0, Section 5.3: the Category Review pause should
    only trigger when at least one such category exists — once an operator
    has mapped a category, every future batch with that category resolves
    silently (Section 5's "Favourites model — set up once, applied
    automatically"). Before this fix, every one of the three places the
    pipeline enters the 'review' status did so unconditionally, so operators
    were asked to confirm category mapping on every single run even when
    every category involved was already mapped from a previous run.
    """
    from sqlalchemy import select
    from models.models import Product, SunskyCategoryMapping
    from services.enrich_service import extract_sunsky_category, get_effective_category_name_map

    products = (
        await db.execute(select(Product).where(Product.fetch_job_id == pl.fetch_job_id))
    ).scalars().all()

    category_name_map = await get_effective_category_name_map(db)

    categories: set[str] = set()
    for p in products:
        cat = extract_sunsky_category(p.raw_data or {}, category_name_map)
        if cat:
            categories.add(cat)

    if not categories:
        return []

    mapped_rows = (
        await db.execute(
            select(SunskyCategoryMapping.sunsky_cat).where(
                SunskyCategoryMapping.store_id == pl.store_id,
                SunskyCategoryMapping.sunsky_cat.in_(categories),
            )
        )
    ).scalars().all()
    mapped = set(mapped_rows)

    return sorted(categories - mapped)


async def _confirm_all_enrich_attrs(db, pl_id: int) -> None:
    """
    Mark every ProductEnrichAttr row for this pipeline as confirmed.
    tasks/job_tasks.py's upload step only pushes attributes to WooCommerce
    where confirmed=True -- when Automatic Review Pause is off, there's no
    human review step at all, so this represents the operator's choice to
    fully automate as implicit approval of whatever was extracted. Same fix
    as routers/pipeline.py's content_confirm and resume_pipeline endpoints,
    which need it for the equivalent reason when a human DOES click confirm.
    """
    from sqlalchemy import update
    from models.models import ProductEnrichAttr
    await db.execute(
        update(ProductEnrichAttr)
        .where(ProductEnrichAttr.pipeline_job_id == pl_id)
        .values(confirmed=True)
    )


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
    from sqlalchemy import select

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
                    enrich_count, enrich_products = await _run_enrich_extraction(db, pl, cfg)
                    if enrich_products == 0:
                        fetch_job = (await db.execute(
                            select(Job).where(Job.id == pl.fetch_job_id)
                        )).scalar_one_or_none() if pl.fetch_job_id else None
                        if fetch_job is None or fetch_job.total_items == 0:
                            reason = "Sunsky returned 0 products for this fetch (check category / page / limit settings)."
                        elif fetch_job.processed_items == 0:
                            reason = (f"All {fetch_job.total_items} product(s) fetched from Sunsky were already in the "
                                      f"database with no changes — nothing new to enrich.")
                        else:
                            reason = (f"{fetch_job.total_items} product(s) from Sunsky — "
                                      f"{fetch_job.processed_items} updated existing record(s), "
                                      f"0 newly saved — no new products linked to this run to enrich.")
                        await _plog(db, pl.id, "enrich", "warn",
                                    f"0 products to enrich. {reason} Skipping review pause.")
                    else:
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
                    if stats.get("batch_submitted"):
                        return  # Resumed by the batch-polling task once results are ready
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

                # ── Pause at Review (only if categories still need mapping) ─
                unmapped = await _unmapped_sunsky_categories(db, pl)
                stats = pl.stats_json or {}
                if unmapped:
                    pl.status = "review"
                    pl.current_step = "review"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _plog(
                        db, pl.id, "review", "info",
                        f"Pipeline paused for review — "
                        f"{stats.get('total', 0)} total | "
                        f"{stats.get('ok', 0)} OK | "
                        f"{stats.get('fallback', 0)} fallback | "
                        f"{stats.get('failed', 0)} failed. "
                        f"{len(unmapped)} Sunsky categor{'y' if len(unmapped) == 1 else 'ies'} "
                        f"need mapping ({', '.join(unmapped[:5])}"
                        f"{'…' if len(unmapped) > 5 else ''}). "
                        f"Click Resume to continue with Upload.",
                    )
                else:
                    auto_pause = cfg.get("automatic_review_pause", True)
                    if auto_pause:
                        # Still pause at the Category Review panel even
                        # though nothing is unmapped — the frontend already
                        # renders a distinct "✓ Already mapped — applied
                        # automatically" summary with a one-click Confirm &
                        # Continue button in this case, so the operator sees
                        # confirmation instead of the stage silently
                        # vanishing. Only a fully automatic run (Automatic
                        # Review Pause off) skips this entirely.
                        pl.status = "review"
                        pl.current_step = "review"
                        pl.updated_at = datetime.now(timezone.utc)
                        await db.commit()
                        await _plog(
                            db, pl.id, "review", "info",
                            f"All Sunsky categories in this batch are already mapped — "
                            f"showing confirmation, no changes needed — "
                            f"{stats.get('total', 0)} total | "
                            f"{stats.get('ok', 0)} OK | "
                            f"{stats.get('fallback', 0)} fallback | "
                            f"{stats.get('failed', 0)} failed.",
                        )
                    else:
                        # Categories already mapped AND Automatic Review Pause
                        # is off for this run — go straight to Upload/Sync by
                        # reusing _resume_pipeline (the same code path
                        # content_confirm uses), rather than duplicating the
                        # upload/sync logic inline here.
                        pl.status = "review"
                        pl.current_step = "review"
                        pl.updated_at = datetime.now(timezone.utc)
                        await _confirm_all_enrich_attrs(db, pl.id)
                        await db.commit()
                        await _plog(
                            db, pl.id, "review", "info",
                            f"All Sunsky categories already mapped and Automatic "
                            f"Review Pause is off — skipping straight to Upload — "
                            f"{stats.get('total', 0)} total | "
                            f"{stats.get('ok', 0)} OK | "
                            f"{stats.get('fallback', 0)} fallback | "
                            f"{stats.get('failed', 0)} failed.",
                        )
                        await _resume_pipeline(pl.id)

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


async def _run_generate(db, pl, cfg: dict, force_sync: bool = False) -> dict:
    """
    Content generation step — DAG-aware field generation via services.content_service.
    Saves results back to each Product row so the upload step uses them.
    Returns stats dict: {total, ok, fallback, failed}.

    force_sync=True bypasses batch mode entirely, even if pl.use_batch_
    processing is set -- used by the interactive "Re-generate content"
    action, where the operator is actively waiting in the UI for
    immediate feedback on a specific product, not the initial, bulk
    Generate step where an asynchronous batch actually makes sense.
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
    from services.content_service import generate_product, FIELD_ATTR, get_batchable_ai_fields

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

    # Client feedback: full-pipeline batch processing for Claude, at
    # Anthropic's 50% batch-rate discount, in exchange for asynchronous
    # turnaround. Confirmed via the reviewed build plan: opt-in per
    # pipeline (pl.use_batch_processing), OK with the pipeline pausing
    # while a batch runs. Only submits a batch when there's genuinely
    # something batchable -- get_batchable_ai_fields already returns {}
    # when AI is disabled globally or a product has no depth-0 AI-mode
    # fields, so this naturally no-ops (falls through to the normal
    # synchronous path below) rather than submitting an empty/pointless
    # batch for an all-logic template.
    if pl.use_batch_processing and not force_sync and ai_enabled and ai_provider == "anthropic":
        from pipeline.ai_generator import submit_anthropic_batch, make_batch_custom_id

        batch_requests: list[dict] = []
        for product in products:
            raw = product.raw_data or {}
            prod_dict = {
                "name": product.name or "", "sku": product.sku or "",
                "description": product.description or "", "price": product.price or "0",
                "site_sku": product.site_sku or "", **raw,
            }
            prompts = get_batchable_ai_fields(prod_dict, template)
            for field_name, prompt in prompts.items():
                batch_requests.append({
                    "custom_id": make_batch_custom_id(product.id, field_name),
                    "prompt": prompt,
                    "model": gs.get("ai_model") or None,
                })

        if batch_requests:
            batch_id = await submit_anthropic_batch(batch_requests)
            pl.status = "batch_processing"
            pl.batch_id = batch_id
            pl.batch_submitted_at = datetime.now(timezone.utc)
            pl.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await _plog(db, pl.id, "generate", "info",
                        f"Batch submitted to Claude — {len(batch_requests)} request(s) "
                        f"across {total} product(s). Usually completes within 1 hour, "
                        f"up to 24h max. Pipeline paused until results are ready.")
            return {"total": total, "ok": 0, "fallback": 0, "failed": 0, "batch_submitted": True}
        # Nothing batchable (e.g. every field is logic/derive) -- fall
        # through to the normal path below, same as batch mode being off.

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
    from services.enrich_service import (
        extract_attributes, suggest_variant_groups,
        extract_sunsky_category, load_profile_attrs_for_category,
    )
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

    # Fetch once per run, not per-product — get_category_tree() walks the
    # whole Sunsky category tree (many API calls), so this is cached
    # in-process for an hour by sunsky_client itself as a second layer too.
    from services.enrich_service import get_effective_category_name_map
    category_name_map = await get_effective_category_name_map(db)

    total_attrs = 0
    product_dicts = []
    for product in products:
        raw = product.raw_data or {}
        prod_dict = {"id": product.id, "name": product.name or "", **raw}
        product_dicts.append(prod_dict)

        sunsky_cat = extract_sunsky_category(raw, category_name_map)
        attrs = await extract_attributes(
            prod_dict, gen_cfg, db=db,
            store_id=pl.store_id, sunsky_category=sunsky_cat,
        )

        # TEMPORARY diagnostic logging — visible in both pm2 logs and the
        # Pipeline Log panel — to pin down a live-vs-isolated-test mismatch
        # where the same logic produced correct results in a standalone
        # diagnostic script but empty/fallback results in an actual
        # pipeline run. Safe to remove once root-caused.
        print(f"[enrich-debug] product={product.sku!r} store_id={pl.store_id!r} "
              f"sunsky_cat={sunsky_cat!r} category_name_map_size={len(category_name_map)} "
              f"attrs_returned={attrs}")
        await _plog(db, pl.id, "enrich", "info",
                    f"[debug] {product.sku}: store_id={pl.store_id} cat={sunsky_cat!r} "
                    f"map_size={len(category_name_map)} attrs={attrs}")

        # Attribute Profiles (Section 6.3 / "Panel B"): any attribute the
        # product's assigned profile expects, but that no rule or AI
        # extraction produced, is surfaced as an unresolved row requiring
        # manual entry in the Review step — rather than silently missing.
        expected_attrs = await load_profile_attrs_for_category(db, pl.store_id, sunsky_cat)
        if expected_attrs:
            present_lower = {a["attribute"].strip().lower() for a in attrs}
            for exp_attr in expected_attrs:
                if exp_attr.strip().lower() not in present_lower:
                    attrs.append({
                        "attribute": exp_attr,
                        "raw_value": "",
                        "confidence": 0.0,
                        "source": "profile_unset",
                        "flagged": True,
                    })

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
    return total_attrs, len(products)


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
                    if stats.get("batch_submitted"):
                        return  # Resumed by the batch-polling task once results are ready
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

                # ── Pause at Review (only if categories still need mapping) ─
                unmapped = await _unmapped_sunsky_categories(db, pl)
                stats = pl.stats_json or {}
                if unmapped:
                    pl.status = "review"
                    pl.current_step = "review"
                    pl.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _plog(
                        db, pl.id, "review", "info",
                        f"Pipeline paused for review — "
                        f"{stats.get('total', 0)} total | "
                        f"{stats.get('ok', 0)} OK | "
                        f"{stats.get('fallback', 0)} fallback | "
                        f"{stats.get('failed', 0)} failed. "
                        f"{len(unmapped)} Sunsky categor{'y' if len(unmapped) == 1 else 'ies'} "
                        f"need mapping ({', '.join(unmapped[:5])}"
                        f"{'…' if len(unmapped) > 5 else ''}). "
                        f"Confirm category mapping and click Resume.",
                    )
                else:
                    auto_pause = cfg.get("automatic_review_pause", True)
                    if auto_pause:
                        pl.status = "review"
                        pl.current_step = "review"
                        pl.updated_at = datetime.now(timezone.utc)
                        await db.commit()
                        await _plog(
                            db, pl.id, "review", "info",
                            f"All Sunsky categories in this batch are already mapped — "
                            f"showing confirmation, no changes needed — "
                            f"{stats.get('total', 0)} total | "
                            f"{stats.get('ok', 0)} OK | "
                            f"{stats.get('fallback', 0)} fallback | "
                            f"{stats.get('failed', 0)} failed.",
                        )
                    else:
                        pl.status = "review"
                        pl.current_step = "review"
                        pl.updated_at = datetime.now(timezone.utc)
                        await _confirm_all_enrich_attrs(db, pl.id)
                        await db.commit()
                        await _plog(
                            db, pl.id, "review", "info",
                            f"All Sunsky categories already mapped and Automatic "
                            f"Review Pause is off — skipping straight to Upload — "
                            f"{stats.get('total', 0)} total | "
                            f"{stats.get('ok', 0)} OK | "
                            f"{stats.get('fallback', 0)} fallback | "
                            f"{stats.get('failed', 0)} failed.",
                        )
                        await _resume_pipeline(pl.id)

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

async def _refresh_fetch_and_continue(pipeline_job_id: int):
    """Go back to (refresh) Fetch: re-pull price/stock/description for
    every product already in this pipeline from Sunsky, then continue
    forward through the rest of the pipeline exactly as Process/Enrich/
    Generate/Cat.Review would run on a first pass. Client feedback:
    full back-navigation for Fetch/Process/Enrich -- last of three, per
    the agreed rollout order. Client explicitly confirmed Fetch's scope
    when asked: "If I want different products I will start new
    pipeline" -- this refreshes the SAME products' data, it does not
    search Sunsky again or let the operator change category/page/limit
    (that would effectively be starting a new pipeline, a different,
    bigger feature this explicitly does not attempt).

    Does NOT touch product.name -- the client's own wording was
    specifically "price, stock, description", and overwriting a name
    the operator or downstream generation may have already worked with
    would be a much more disruptive change than what was asked for.
    """
    from database import make_session_factory
    from models.models import PipelineJob, Product
    from pipeline import sunsky_client
    from sqlalchemy import select

    CelerySession, celery_engine = make_session_factory()
    try:
        async with CelerySession() as db:
            pl = await db.get(PipelineJob, pipeline_job_id)
            if not pl or pl.status != "running":
                return

            products = (
                await db.execute(select(Product).where(Product.fetch_job_id == pl.fetch_job_id))
            ).scalars().all()

            await _plog(db, pl.id, "fetch", "info",
                        f"{_make_pl_id(pl.id)} refreshing {len(products)} product(s) from Sunsky…")

            refreshed = failed = 0
            for product in products:
                try:
                    fresh = await sunsky_client.get_product_detail(product.sku)
                    if not fresh:
                        failed += 1
                        await _plog(db, pl.id, "fetch", "warn",
                                    f"  {product.sku}: Sunsky returned nothing — kept existing data")
                        continue
                    product.price = fresh.get("price", product.price)
                    product.stock_status = fresh.get("stock_status", product.stock_status)
                    if fresh.get("stock_quantity") is not None:
                        product.stock_quantity = fresh["stock_quantity"]
                    if fresh.get("description"):
                        product.description = fresh["description"]
                    product.raw_data = fresh
                    refreshed += 1
                except Exception as exc:
                    failed += 1
                    await _plog(db, pl.id, "fetch", "warn", f"  {product.sku}: refresh failed — {exc}")

            await db.commit()
            await _plog(db, pl.id, "fetch", "info",
                        f"Refresh complete — {refreshed} updated, {failed} failed. Continuing to Process…")
    finally:
        await celery_engine.dispose()

    await _continue_pipeline(pipeline_job_id, "process")


async def _poll_batch_pipelines():
    """
    Checks every pipeline currently paused in "batch_processing" status,
    polls its Anthropic Message Batch for completion, and for any that
    have finished, applies the results and resumes the pipeline.

    Client feedback: full-pipeline batch processing for Claude, at
    Anthropic's 50% batch-rate discount. This is the piece that actually
    un-pauses a pipeline after patch 114's Generate step submits a batch
    and pauses -- without this running periodically, a batch-processing
    pipeline would stay paused forever, since nothing else ever checks
    on it.

    Runs as a periodic background task (see main.py's startup hook,
    same pattern as the category-cache pre-warm loop from patch 92).
    """
    from database import make_session_factory
    from models.models import PipelineJob, Product
    from pipeline.ai_generator import get_anthropic_batch_status, get_anthropic_batch_results, parse_batch_custom_id
    from services.content_service import generate_product
    from sqlalchemy import select
    import json
    from pathlib import Path

    CelerySession, celery_engine = make_session_factory()
    resumed_pipeline_ids: list[int] = []
    try:
        async with CelerySession() as db:
            pipelines = (
                await db.execute(select(PipelineJob).where(PipelineJob.status == "batch_processing"))
            ).scalars().all()

            for pl in pipelines:
                if not pl.batch_id:
                    continue
                try:
                    status = await get_anthropic_batch_status(pl.batch_id)
                except Exception as exc:
                    print(f"[batch_poll] pipeline {pl.id}: failed to check batch {pl.batch_id} status — {exc}")
                    continue

                counts = status["request_counts"]
                if status["processing_status"] != "ended":
                    print(f"[batch_poll] pipeline {pl.id}: batch {pl.batch_id} still processing "
                          f"({counts['processing']} pending, {counts['succeeded']} done)")
                    continue

                print(f"[batch_poll] pipeline {pl.id}: batch {pl.batch_id} ended — "
                      f"{counts['succeeded']} succeeded, {counts['errored']} errored, "
                      f"{counts['canceled']} canceled, {counts['expired']} expired. Applying results…")
                await _plog(db, pl.id, "generate", "info",
                            f"Batch complete — {counts['succeeded']} succeeded, "
                            f"{counts['errored'] + counts['canceled'] + counts['expired']} failed. "
                            f"Applying results and resuming…")

                try:
                    results = await get_anthropic_batch_results(pl.batch_id)
                except Exception as exc:
                    await _plog(db, pl.id, "generate", "error",
                                f"Failed to fetch batch results — {exc}")
                    print(f"[batch_poll] pipeline {pl.id}: failed to fetch batch results — {exc}")
                    continue

                # Group results by product_id, since one product can have
                # multiple batched fields (e.g. both title and description).
                by_product: dict[int, dict[str, tuple[bool, str]]] = {}
                for custom_id, (succeeded, text_or_error) in results.items():
                    try:
                        product_id, field_name = parse_batch_custom_id(custom_id)
                    except ValueError:
                        continue
                    by_product.setdefault(product_id, {})[field_name] = (succeeded, text_or_error)

                # Reload the same generation config _run_generate used to submit
                # this batch, so the DAG re-run here uses identical field modes.
                gen_cfg = pl.config.get("content_gen_config") if pl.config else None
                if not gen_cfg:
                    saved_path = Path(__file__).parent.parent / "config_store" / "content_gen_config.json"
                    if saved_path.exists():
                        try:
                            gen_cfg = json.loads(saved_path.read_text())
                        except Exception:
                            gen_cfg = {}
                    if not gen_cfg:
                        from routers.content import DEFAULT_CONFIG
                        gen_cfg = DEFAULT_CONFIG
                template: dict = gen_cfg if isinstance(gen_cfg, dict) else {}

                from services.content_service import FIELD_ATTR

                applied = 0
                for product_id, field_results in by_product.items():
                    product = await db.get(Product, product_id)
                    if not product:
                        continue
                    raw = product.raw_data or {}
                    prod_dict = {
                        "name": product.name or "", "sku": product.sku or "",
                        "description": product.description or "", "price": product.price or "0",
                        "site_sku": product.site_sku or "", **raw,
                    }
                    field_results_out = await generate_product(prod_dict, template, precomputed_ai=field_results)
                    sources = product.content_source or {}
                    for field, result in field_results_out.items():
                        if field not in field_results:
                            continue  # only apply fields that were actually part of this batch
                        attr = FIELD_ATTR.get(field)
                        value = result.get("value", "")
                        if attr and value:
                            setattr(product, attr, value)
                            sources[field] = result.get("source", "logic")
                    product.content_source = sources
                    applied += 1
                await db.commit()

                await _plog(db, pl.id, "generate", "info",
                            f"Applied batch results to {applied} product(s). Resuming pipeline…")

                pl.status = "running"
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                resumed_pipeline_ids.append(pl.id)
    finally:
        await celery_engine.dispose()

    # Continuing each resumed pipeline happens outside the DB session
    # above (each _continue_pipeline call opens its own), matching how
    # every other resume path in this file already works. Uses the
    # exact pipeline IDs resumed in THIS poll cycle (not a re-query),
    # since batch_id is never cleared after use -- a generic re-query
    # like "status == running AND batch_id is not null" could wrongly
    # match a pipeline that used batch mode earlier but is now running
    # for a completely unrelated, later reason.
    for pl_id in resumed_pipeline_ids:
        await _continue_pipeline(pl_id, "review")


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
                    enrich_count, enrich_products = await _run_enrich_extraction(db, pl, cfg)
                    if enrich_products == 0:
                        fetch_job = (await db.execute(
                            select(Job).where(Job.id == pl.fetch_job_id)
                        )).scalar_one_or_none() if pl.fetch_job_id else None
                        if fetch_job is None or fetch_job.total_items == 0:
                            reason = "Sunsky returned 0 products for this fetch (check category / page / limit settings)."
                        elif fetch_job.processed_items == 0:
                            reason = (f"All {fetch_job.total_items} product(s) fetched from Sunsky were already in the "
                                      f"database with no changes — nothing new to enrich.")
                        else:
                            reason = (f"{fetch_job.total_items} product(s) from Sunsky — "
                                      f"{fetch_job.processed_items} updated existing record(s), "
                                      f"0 newly saved — no new products linked to this run to enrich.")
                        await _plog(db, pl.id, "enrich", "warn",
                                    f"0 products to enrich. {reason} Skipping review pause.")
                    else:
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
                    if stats.get("batch_submitted"):
                        return  # Resumed by the batch-polling task once results are ready
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
                    unmapped = await _unmapped_sunsky_categories(db, pl)
                    stats = pl.stats_json or {}
                    if unmapped:
                        pl.status = "review"
                        pl.current_step = "review"
                        pl.updated_at = datetime.now(timezone.utc)
                        await db.commit()
                        await _plog(db, pl.id, "review", "info",
                            f"Pipeline paused for review — "
                            f"{stats.get('total', 0)} total | "
                            f"{stats.get('ok', 0)} OK | "
                            f"{stats.get('fallback', 0)} fallback | "
                            f"{stats.get('failed', 0)} failed. "
                            f"{len(unmapped)} Sunsky categor{'y' if len(unmapped) == 1 else 'ies'} "
                            f"need mapping ({', '.join(unmapped[:5])}"
                            f"{'…' if len(unmapped) > 5 else ''}). "
                            f"Confirm category mapping and click Resume.")
                    else:
                        auto_pause = cfg.get("automatic_review_pause", True)
                        if auto_pause:
                            pl.status = "review"
                            pl.current_step = "review"
                            pl.updated_at = datetime.now(timezone.utc)
                            await db.commit()
                            await _plog(db, pl.id, "review", "info",
                                f"All Sunsky categories in this batch are already mapped — "
                                f"showing confirmation, no changes needed — "
                                f"{stats.get('total', 0)} total | "
                                f"{stats.get('ok', 0)} OK | "
                                f"{stats.get('fallback', 0)} fallback | "
                                f"{stats.get('failed', 0)} failed.")
                        else:
                            pl.status = "review"
                            pl.current_step = "review"
                            pl.updated_at = datetime.now(timezone.utc)
                            await _confirm_all_enrich_attrs(db, pl.id)
                            await db.commit()
                            await _plog(db, pl.id, "review", "info",
                                f"All Sunsky categories already mapped and Automatic "
                                f"Review Pause is off — skipping straight to Upload — "
                                f"{stats.get('total', 0)} total | "
                                f"{stats.get('ok', 0)} OK | "
                                f"{stats.get('fallback', 0)} fallback | "
                                f"{stats.get('failed', 0)} failed.")
                            await _resume_pipeline(pl.id)
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


async def _regenerate_content(pipeline_job_id: int):
    """Re-run content generation for a pipeline currently paused at
    Content Review, then return to Content Review with the fresh results.
    Client feedback item #10: the "Re-generate content" button in Content
    Review had no onClick handler at all -- pure dead UI, same as
    "Assign category" before that fix.

    Mirrors _resume_pipeline's exact structure (fresh DB session via
    make_session_factory, try/except -> failed status on error, finally ->
    _advance_queue) for consistency with the rest of this file. Reuses
    _run_generate as-is -- the same function the main pipeline flow calls
    -- rather than duplicating content-generation logic, which is exactly
    the kind of per-path divergence that's caused real bugs elsewhere in
    this codebase this session.
    """
    from database import make_session_factory
    from models.models import PipelineJob

    CelerySession, celery_engine = make_session_factory()
    try:
        async with CelerySession() as db:
            pl = await db.get(PipelineJob, pipeline_job_id)
            if not pl or pl.status != "running" or pl.current_step != "generate":
                return

            await _plog(db, pl.id, "generate", "info",
                        f"{_make_pl_id(pl.id)} re-generating content (operator requested)")

            try:
                cfg = pl.config or {}
                stats = await _run_generate(db, pl, cfg, force_sync=True)

                pl.status = "content_review"
                pl.current_step = "content_review"
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, "content_review", "info",
                            f"Content re-generated: {stats}")

            except Exception as e:
                pl.status = "failed"
                pl.error_message = str(e)
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, "generate", "error",
                            f"Re-generate failed: {e}")

            finally:
                await _advance_queue(db, pl.store_id, pl.id)
    finally:
        await celery_engine.dispose()
