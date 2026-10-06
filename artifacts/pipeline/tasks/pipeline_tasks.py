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
from tasks.background import spawn as _spawn_bg

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

    # Decided PER PRODUCT (milestone point 2, "IF title contains"): one
    # Sunsky category can have several rules, so it may be covered for some
    # of its products (e.g. frames) but not others (e.g. silicone cases). A
    # category needs mapping if ANY of its products is covered by no rule,
    # or by a store rule whose WooCommerce category no longer exists
    # (earlier broken-rule fix; Upload would use that rule, so no global
    # fallback) -- via the same _cat_rule_for_product the Cat. Review
    # screen and its Confirm use. A product with no applicable store rule
    # falls back to global rules (earlier global-rule fix).
    from tasks.job_tasks import _cat_rule_for_product, _broken_rule_ids, _product_titles
    store_rules = (
        await db.execute(
            select(SunskyCategoryMapping).where(
                SunskyCategoryMapping.store_id == pl.store_id,
                SunskyCategoryMapping.sunsky_cat.in_(categories),
            )
        )
    ).scalars().all()
    rules_by_cat: dict[str, list] = {}
    for r in store_rules:
        rules_by_cat.setdefault(r.sunsky_cat, []).append(r)
    broken_ids = await _broken_rule_ids(db, pl.store_id, store_rules)

    unmapped: set[str] = set()
    for p in products:
        cat = extract_sunsky_category(p.raw_data or {}, category_name_map)
        if not cat or cat in unmapped:
            continue
        try:
            status, _, _ = await _cat_rule_for_product(
                db, pl.store_id, cat, _product_titles(p), rules_by_cat.get(cat, []), broken_ids
            )
        except Exception:
            status = None
        if status not in ("store", "global"):
            unmapped.add(cat)
    return sorted(unmapped)


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
        _spawn_bg(_execute_pipeline(next_pl.id))


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
                    if stats.get("stopped"):
                        return  # cancelled, or a newer run took over
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


def _template_skipping_generated_fields(product, template: dict) -> tuple[dict, list[str]]:
    """
    Client feedback confirmed live: navigating back a step (e.g. Enrich ->
    Process) and continuing forward re-ran Generate from scratch every
    time -- 9 Anthropic requests to regenerate a SINGLE already-generated
    product, just from going back and forth, with zero actual content
    changes in between. Client's own framing: "when generate once just
    record the data in the product, so this become static information...
    no need to regenerate them unless you choose to regenerate them or
    make manual corrections."

    Root cause: _run_generate always called generate_product/
    get_batchable_ai_fields for every enabled field on every product,
    with no check for whether that field's column already held a value
    (from a prior run OR a manual edit in Content Review -- both look
    identical here, which is exactly the behavior wanted: a manual edit
    IS a value, so it's equally protected from being silently clobbered
    by a re-run, without needing a separate manual-edit-tracking
    mechanism at all).

    CORRECTION (client feedback confirmed live via pipeline log): the
    very first version of this fix checked raw column truthiness for
    EVERY field, including "title" (FIELD_ATTR -> "name") and
    "description". Those two columns are NOT exclusively written by
    content generation -- job_tasks.py's own fetch/process step writes
    the raw Sunsky name/description into these same columns the moment
    a product is first fetched, well before Generate ever runs (see
    "existing.name = p['name']" there). So a genuinely brand-new,
    never-generated product already had a truthy "name" and
    "description" from Process alone, and got incorrectly skipped on
    its very first Generate pass -- title/description were silently
    never AI-generated at all for new products, confirmed via a live
    pipeline log showing "skipping already-generated title,
    description" on products that had only ever been through Process.

    Fix: "title" and "description" are checked against product.
    content_source instead -- a dict populated ONLY by _run_generate's
    own result-application loop (confirmed via grep: zero references
    to content_source anywhere in job_tasks.py's fetch/process code),
    so an entry there is genuine proof this field went through actual
    generation (ai, logic, or derive), never just a side effect of
    fetching raw source data. Every OTHER field in FIELD_ATTR (slug,
    meta_title, meta_description, tags, image_alt, image_names,
    short_description, focus_keyword) has no such collision -- fetch/
    process never touches any of those columns (confirmed via the same
    grep) -- so raw column truthiness remains the correct, simpler
    check for them, and additionally covers manual edits made directly
    to those columns (which don't update content_source either, but
    have no raw-fetch value to be confused with in the first place).

    Returns a per-product COPY of the template with template["overrides"]
    populated for any field found already-recorded by the rule above,
    plus the list of field names skipped this way (for logging). Uses
    the SAME "overrides" mechanism run_field already checks first
    (before any AI/logic call, source="override") rather than disabling
    the field outright -- disabling would drop it from generate_
    product's dependency-depth calculation and its `resolved` dict
    entirely, which would silently break any OTHER field that depends
    on this one's value (e.g. an enabled Meta Title needing the real,
    already-generated Title text). Routing through "overrides" instead
    keeps the field fully present in the DAG -- correct depth, correct
    `resolved[field]` value for dependents -- while still costing zero
    AI calls, exactly like an operator's own manual override already
    does today.

    An operator's own pre-existing override for a field always wins
    and is left untouched (never treated as "generated", never
    reported as skipped -- it was never going to call AI regardless).

    KNOWN LIMITATION: a manual edit to title or description specifically
    (via the Content Review "fields" endpoint) does NOT set content_
    source, since that endpoint just writes the column directly. Such
    an edit currently looks identical to "still holds the untouched raw
    Sunsky value" for these two fields only, and would be regenerated
    (overwriting the manual edit) on a later Generate re-run. This is a
    pre-existing gap in that endpoint (it was never tracking provenance
    for ANY field, not something this fix introduces or worsens for the
    other 8 fields) -- flagged here rather than silently left unhandled,
    but not fixed in this change, since it needs its own dedicated
    provenance write in that endpoint, not a workaround here.
    """
    import copy as _copy
    from services.content_service import FIELD_ATTR

    # Fields whose column doubles as a raw-fetch storage target --
    # column truthiness alone can't tell "generated" apart from "still
    # just the raw Sunsky value from Process". Checked via content_source
    # instead. Every other FIELD_ATTR entry keeps the simpler, original
    # column-truthiness check.
    _FETCH_COLLIDES = {"title", "description"}

    filtered = _copy.deepcopy(template)
    overrides = filtered.setdefault("overrides", {})
    fields_cfg = filtered.get("fields") or {}
    sources = product.content_source or {}
    skipped: list[str] = []
    for field, attr in FIELD_ATTR.items():
        if field in overrides:
            continue  # operator's own explicit override already wins
        if not fields_cfg.get(field, {}).get("enabled", True):
            continue  # field disabled entirely -- never runs anyway, not a "skip"
        if field in _FETCH_COLLIDES:
            if field not in sources:
                continue  # no generation record yet -- still just the raw fetched value
        # Client feedback (PL-159, OpenRouter free model rate-limited): a
        # field whose AI call FAILED holds only template text
        # ("logic:fallback") or nothing ("ai:failed") -- not generated
        # content. Treating it as "already generated" meant every later run
        # (Back to Enrich/Process/Fetch, a new pipeline) skipped it and kept
        # the fallback forever: "same result - fallback". Retry it instead.
        # (An operator's own edit of such a field is recorded as "manual"
        # by PATCH /products/{id}/fields, so it is still protected.)
        if sources.get(field) in ("logic:fallback", "ai:failed"):
            continue
        existing_value = getattr(product, attr, None)
        if existing_value:
            overrides[field] = existing_value
            skipped.append(field)
    return filtered, skipped


def _apply_csv_entry(product, csv_entry) -> str:
    """Applies a CSV import row (Site SKU + Product Title) directly onto
    the product, exactly like _run_generate's synchronous loop always
    has, and returns the CSV title ("" if none) for prod_dict["csv_title"].

    Client feedback confirmed live on PL-148 (hdcam.bg): all 10 products
    from the client's CSV import (e.g. DOP4080B -> "Рамка за DJI Osmo
    Action 6, Метална, Черна") showed AI-generated titles in Content
    Review instead of the CSV titles. PL-148 used Claude batch mode
    ("Batch submitted to Claude -- 18 request(s)"). Root cause: only the
    synchronous path ever looked up the CSV row -- the batch SUBMIT
    loop and the batch RESULT-APPLY loop (_poll_batch_pipelines) both
    built prod_dict with no "csv_title", so _prepare_field_context's
    "CSV title wins over any mode" check (earlier CSV-title fix) never
    saw a CSV title: the title was sent to Claude and the AI result was
    written into product.name, with nothing re-asserting the CSV title.
    Writing the title onto product.name BEFORE the already-generated
    skip is computed matters too: a previously generated title becomes
    an "override" of product.name, checked before the CSV title, so
    this makes that override the CSV title (same as the sync path).
    """
    if not csv_entry:
        return ""
    # Client decision (Enrich-step editing): a title / Site SKU the operator
    # edited by hand (content_source "manual", set by PATCH
    # /products/{id}/fields) WINS over the CSV -- the newest, deliberate
    # choice. Returning "" keeps the CSV title from winning later in
    # _prepare_field_context too.
    _cs = product.content_source or {}
    csv_title = (csv_entry.csv_title or "").strip()
    site_sku = csv_entry.site_sku or ""
    if site_sku and _cs.get("site_sku") != "manual":
        product.site_sku = site_sku
    if _cs.get("title") == "manual":
        return ""
    if csv_title:
        product.name = csv_title
    return csv_title


async def _generation_context_extras(db, pl_id: int, product) -> dict:
    """Extra product-dict keys for content generation, merged AFTER **raw.

    Client request: edit title / SKU / attributes at the Enrich step,
    "because if ... some attribute is wrong and AI use them as context it
    will generate wrong data". Findings: generation never read the Enrich
    attributes, and the prod_dict put **raw last, so raw_data["name"] (the
    ORIGINAL Sunsky title) overrode the product's saved name -- an edited
    title never reached the AI. Client decisions: (1) an edited title wins
    (also over the CSV title); (2) the reviewed attributes guide the AI for
    ALL products.
    - "reviewed_attributes": this pipeline's attributes for the product as
      they stand after the Enrich review (name: value), empty values and
      "not found" left out -> _build_product_context adds them.
    - "name": the operator-edited title, when content_source title is
      "manual" (otherwise the existing behaviour is untouched).
    """
    from models.models import ProductEnrichAttr
    from sqlalchemy import select as _sel_gx
    extras: dict = {}
    rows = (await db.execute(
        _sel_gx(ProductEnrichAttr).where(
            ProductEnrichAttr.pipeline_job_id == pl_id,
            ProductEnrichAttr.product_id == product.id,
        ).order_by(ProductEnrichAttr.id)
    )).scalars().all()
    attrs = []
    for r in rows:
        name = (r.woo_attr_name or r.attribute or "").strip()
        value = (r.normalised_value or r.raw_value or "").strip()
        if name and value and value.lower() != "not found":
            attrs.append({"name": name, "value": value})
    extras["reviewed_attributes"] = attrs
    if (product.content_source or {}).get("title") == "manual" and product.name:
        extras["name"] = product.name
    return extras


def _gen_config_path(store_id):
    """Saved Content Generation settings for a store: its own file if the
    store has custom settings (client: "how can I control the content
    generation option for different store? Right now they are all
    global"), else the global file. Same files as routers/content.py."""
    base = Path(__file__).parent.parent / "config_store"
    if store_id:
        p = base / f"content_gen_config_store_{int(store_id)}.json"
        if p.exists():
            return p
    return base / "content_gen_config.json"


# Latest content-generation run per pipeline (id -> token). Client log PL-163:
# Cancel did not stop a running generation, and Continue 3 s later started a
# second one beside it -- both ran, the old one still on the old model. A run
# stops at the next product when the pipeline is cancelled or a newer run
# for the same pipeline has started.
_GEN_RUN_TOKEN: dict[int, object] = {}


async def _run_generate(db, pl, cfg: dict, force_sync: bool = False, force_regenerate: bool = False) -> dict:
    """Runs one generation pass; the Gemini Flex setting it may switch on
    (GEMINI_SERVICE_TIER) is always reset afterwards, so it can't leak into
    later steps of the same task (e.g. Enrich after Back to Enrich)."""
    from pipeline.ai_generator import GEMINI_SERVICE_TIER
    _tier_token = GEMINI_SERVICE_TIER.set("standard")
    try:
        return await _run_generate_impl(db, pl, cfg, force_sync=force_sync, force_regenerate=force_regenerate)
    finally:
        GEMINI_SERVICE_TIER.reset(_tier_token)


async def _run_generate_impl(db, pl, cfg: dict, force_sync: bool = False, force_regenerate: bool = False) -> dict:
    """
    Content generation step — DAG-aware field generation via services.content_service.
    Saves results back to each Product row so the upload step uses them.
    Returns stats dict: {total, ok, fallback, failed}.

    force_sync=True bypasses batch mode entirely, even if pl.use_batch_
    processing is set -- used by the interactive "Re-generate content"
    action, where the operator is actively waiting in the UI for
    immediate feedback on a specific product, not the initial, bulk
    Generate step where an asynchronous batch actually makes sense.

    force_regenerate=True disables the already-generated-fields skip
    (see _template_skipping_generated_fields above) -- set by the
    explicit "Re-generate content" action only. Every other caller (the
    main pipeline flow, resuming after Enrich Review, and re-entering
    Generate after navigating back to an earlier step) leaves this False,
    so already-populated fields are left untouched as static, already-
    recorded data rather than being silently regenerated on every pass.
    """
    from models.models import Product, CsvMapping
    from sqlalchemy import select
    import json
    from pathlib import Path

    # ── Load generation config ────────────────────────────────────────────────
    # Client feedback (PL-159): "choose gemini flash 2.5 lite ... same result
    # - fallback ... It can be some kind of cache." The pipeline kept the
    # settings COPIED at pipeline start, so a model changed afterwards never
    # applied -- on ANY path (Back to Enrich / Process / Fetch, not only
    # Re-generate). Generate now uses the CURRENT saved Content Generation
    # settings when the step runs, stores them on the pipeline, and logs the
    # change. Falls back to the pipeline's copy if nothing is saved.
    _cur_path = _gen_config_path(getattr(pl, "store_id", None))
    if _cur_path.exists():
        try:
            _current = json.loads(_cur_path.read_text())
        except Exception:
            _current = None
        if _current:
            _prev = cfg.get("content_gen_config") or {}
            _fmt = lambda c: f"{((c or {}).get('globalSettings') or {}).get('ai_provider') or '?'} · {((c or {}).get('globalSettings') or {}).get('ai_model') or 'default model'}"
            if _prev and _fmt(_prev) != _fmt(_current):
                await _plog(db, pl.id, "generate", "info",
                            f"Using the CURRENT Content Generation settings ({_fmt(_current)}) — "
                            f"this pipeline started with {_fmt(_prev)}")
            cfg = dict(cfg)
            cfg["content_gen_config"] = _current
            try:
                _plc = dict(pl.config or {})
                _plc["content_gen_config"] = _current
                pl.config = _plc
            except Exception:
                pass
    gen_cfg = cfg.get("content_gen_config", {})
    if not gen_cfg:
        saved_path = _gen_config_path(getattr(pl, "store_id", None))
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

    # Client feedback confirmed live: Image Caption/Description showed
    # empty even with the feature's own patch already applied and the
    # server restarted. Root cause (part 2 -- part 1 was
    # routers/content.py's DEFAULT_CONFIG itself never including these
    # two fields at all, fixed separately): a saved settings file from
    # BEFORE a new field existed is not empty, so the "if not gen_cfg"
    # fallback to DEFAULT_CONFIG above never triggers for an operator
    # who has ever saved Content Generation settings before -- their
    # saved file is used exactly as-is, forever, with no way for a
    # newly-added field to ever appear for them without manually
    # revisiting and re-saving that settings page. Backfills any field
    # present in the current code's DEFAULT_CONFIG but missing from
    # whatever gen_cfg was actually loaded (saved file OR the
    # DEFAULT_CONFIG fallback itself, harmless either way since a
    # merge with itself changes nothing) -- so a field added to the
    # code later always gets a sensible default, without silently
    # requiring every existing operator to notice and re-save
    # Settings for it to ever take effect at all.
    if isinstance(gen_cfg, dict):
        from routers.content import DEFAULT_CONFIG as _DEFAULT_CFG
        gen_cfg = dict(gen_cfg)
        gen_cfg["fields"] = {**_DEFAULT_CFG["fields"], **(gen_cfg.get("fields") or {})}

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

    # Client feedback: "Do the data fields we generate have sufficient
    # access to structured category data... prior to the actual
    # generation?" Confirmed via direct code investigation: category
    # data was already resolved elsewhere in the pipeline (Enrich's
    # own attribute extraction, at this exact same
    # extract_sunsky_category/get_effective_category_name_map call)
    # but never threaded into the AI generation prompt context at all.
    # Loaded once here for the whole batch, not per-product, matching
    # Enrich's own established pattern for the identical lookup.
    from services.enrich_service import extract_sunsky_category, get_effective_category_name_map
    _gen_category_name_map = await get_effective_category_name_map(db)

    # Client feedback, exact spec: "If product have brand (for example
    # MOFI) and we enable brand mapping from Sunsky the pipeline can
    # use it for generation as context. If the product don't have
    # brand or have but we disable brand mapping from Sunsky the
    # pipeline can't use it for generation as context." Confirmed via
    # direct code investigation that native brand was never passed
    # into AI generation context at all before this -- this is a new
    # addition, not a removal of something that was causing confusion,
    # since it simply wasn't there yet. Resolved once, for the whole
    # batch, matching category's own established pattern for the
    # identical kind of lookup -- a per-product query inside the main
    # generation loop below would be a real N+1 query problem.
    from models.models import Store, ProductStoreListing
    _gen_store = await db.get(Store, pl.store_id)
    _gen_listings_by_product: dict[int, ProductStoreListing] = {}
    if products:
        _gen_listing_rows = (
            await db.execute(
                select(ProductStoreListing).where(
                    ProductStoreListing.store_id == pl.store_id,
                    ProductStoreListing.product_id.in_([p.id for p in products]),
                )
            )
        ).scalars().all()
        _gen_listings_by_product = {l.product_id: l for l in _gen_listing_rows}

    def _gen_resolve_brand(product) -> str:
        """Same priority as job_tasks.py's identical Upload/Sync fix:
        a manual override always wins; otherwise the store's own
        toggle decides whether Sunsky's detected brand is used at all.
        """
        _l = _gen_listings_by_product.get(product.id)
        if _l is not None and _l.brand_source == "manual" and _l.manual_brand_name:
            return _l.manual_brand_name
        if _gen_store is not None and _gen_store.map_brand_from_sunsky:
            from services.content_service import _get_manufacturer_brand, _parse_params_table
            _raw = product.raw_data or {}
            _specs = _parse_params_table(str(_raw.get("paramsTable") or "")) if _raw.get("paramsTable") else {}
            return _get_manufacturer_brand(_raw, _specs) or ""
        return ""

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
    # "Use Batch / Flex Processing" (client: Flex inference for Gemini, "same
    # as Batch processing for Claude ... same button for all models with such
    # an option"): Claude -> Batch API below (pipeline pauses); Gemini
    # (direct key) -> Flex tier on every request (synchronous, just slower --
    # no pause); other providers have no such tier.
    _flex = bool(pl.use_batch_processing) and ai_enabled and ai_provider == "gemini"
    if _flex:
        from pipeline.ai_generator import GEMINI_SERVICE_TIER
        GEMINI_SERVICE_TIER.set("flex")
        await _plog(db, pl.id, "generate", "info",
                    "Gemini Flex tier ON — 50% cheaper; each AI request can take several minutes "
                    "(Google targets 1–15 min). The pipeline keeps running, it does not pause.")
    elif pl.use_batch_processing and ai_enabled and ai_provider not in ("anthropic", "gemini"):
        await _plog(db, pl.id, "generate", "info",
                    f"Batch / Flex processing isn't available for {ai_provider} — standard requests are used "
                    f"(Batch: Claude via Anthropic; Flex: Gemini via a Google key)")

    if pl.use_batch_processing and not force_sync and ai_enabled and ai_provider == "anthropic":
        from pipeline.ai_generator import submit_anthropic_batch, make_batch_custom_id

        batch_requests: list[dict] = []
        total_skipped_fields = 0
        for product in products:
            raw = product.raw_data or {}
            csv_title = _apply_csv_entry(product, csv_lookup.get(product.sku))
            prod_dict = {
                "name": product.name or "", "sku": product.sku or "",
                "description": product.description or "", "price": product.price or "0",
                "site_sku": product.site_sku or "",
                "csv_title": csv_title,
                "category_name": extract_sunsky_category(raw, _gen_category_name_map),
                "native_brand": _gen_resolve_brand(product),
                **raw,
            }
            prod_dict.update(await _generation_context_extras(db, pl.id, product))
            product_template = template
            if not force_regenerate:
                product_template, skipped = _template_skipping_generated_fields(product, template)
                total_skipped_fields += len(skipped)
            prompts = get_batchable_ai_fields(prod_dict, product_template)
            for field_name, prompt in prompts.items():
                batch_requests.append({
                    "custom_id": make_batch_custom_id(product.id, field_name),
                    "prompt": prompt,
                    "model": gs.get("ai_model") or None,
                })

        if total_skipped_fields:
            await _plog(db, pl.id, "generate", "info",
                        f"Skipping {total_skipped_fields} already-generated field(s) across "
                        f"{total} product(s) — already recorded, not re-requested.")

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

    _model_label = (gen_cfg.get("globalSettings") or {}).get("ai_model") or "default model"
    await _plog(db, pl.id, "generate", "info",
                f"Content generation: {total} products | "
                f"AI={'on (' + ai_provider + ' · ' + _model_label + (' · Flex tier' if _flex else '') + ')' if ai_enabled else 'off (logic only)'}")

    ok_count = fallback_count = failed_count = 0
    _my_run = object()
    _GEN_RUN_TOKEN[pl.id] = _my_run
    _stopped = ""

    for product in products:
        if _GEN_RUN_TOKEN.get(pl.id) is not _my_run:
            _stopped = "superseded"
        elif await _is_cancelled(db, pl.id):
            _stopped = "cancelled"
        if _stopped:
            await db.commit()
            await _plog(db, pl.id, "generate", "warn",
                        "Content generation stopped — "
                        + ("a newer run for this pipeline has started"
                           if _stopped == "superseded" else "the pipeline was cancelled")
                        + f" ({ok_count + fallback_count + failed_count}/{total} products done)")
            return {"total": total, "ok": ok_count, "fallback": fallback_count,
                    "failed": failed_count, "stopped": _stopped}
        try:
            raw = product.raw_data or {}

            # Apply CSV mapping if available -- the shared helper (same as
            # batch mode), which also leaves an operator-edited title / Site
            # SKU alone. Post-generate we re-assert csv_title so AI mode
            # can't silently overwrite it ("" for an edited title).
            csv_entry = csv_lookup.get(product.sku)
            csv_title = _apply_csv_entry(product, csv_entry)
            site_sku = product.site_sku or ""

            prod_dict = {
                "name":        product.name or "",
                "sku":         product.sku or "",
                "description": product.description or "",
                "price":       product.price or "0",
                "csv_title":   csv_title,
                "site_sku":    site_sku,
                "category_name": extract_sunsky_category(raw, _gen_category_name_map),
                "native_brand": _gen_resolve_brand(product),
                **raw,
            }
            prod_dict.update(await _generation_context_extras(db, pl.id, product))

            # A COPY: content_source is a plain JSON column, so changing the
            # loaded dict in place and assigning the SAME object back is
            # not detected as a change -- for a product that already had a
            # content_source, newly generated fields' provenance was never
            # saved (found testing Enrich-step edits: a batch-generated
            # description was stored but not recorded, so the next pipeline
            # would treat it as missing and generate it again).
            sources: dict = dict(product.content_source or {})
            prod_failed = False

            # Skip fields that already have a recorded value (from a prior
            # run, a prior batch, or a manual Content Review edit) unless
            # the operator explicitly clicked "Re-generate content" -- see
            # _template_skipping_generated_fields for the full rationale.
            product_template = template
            skipped_fields: list[str] = []
            if not force_regenerate:
                product_template, skipped_fields = _template_skipping_generated_fields(product, template)
                if skipped_fields:
                    await _plog(db, pl.id, "generate", "info",
                                f"  {product.sku}: skipping already-generated "
                                f"{', '.join(skipped_fields)} — already recorded")

            sources_before_skip = dict(sources)

            # Run all enabled fields via DAG engine
            results = await generate_product(prod_dict, product_template)

            for field, result in results.items():
                attr = FIELD_ATTR.get(field)
                if not attr:
                    continue
                if result.get("status") == "failed":
                    await _plog(db, pl.id, "generate", "warn",
                                f"  {product.sku} [{field}]: {result.get('error', 'failed')}")
                    prod_failed = True
                    continue
                # A field that fell back (AI failed -> template text / empty)
                # makes the product "fallback" too -- the summary said "2 ok |
                # 0 fallback" while every field had fallen back (PL-162).
                if str(result.get("source", "")) in ("logic:fallback", "ai:failed"):
                    prod_failed = True
                value = result.get("value", "")
                source = result.get("source", "logic")
                if value:
                    setattr(product, attr, value)
                    # For a field we skipped ourselves (not the operator's
                    # own pre-existing override), keep its original
                    # provenance (e.g. "ai:anthropic:batch") instead of
                    # overwriting with "override" -- this run genuinely
                    # didn't regenerate it, so the history of how it was
                    # ACTUALLY produced is worth preserving for debugging.
                    if field in skipped_fields and field in sources_before_skip:
                        sources[field] = sources_before_skip[field]
                    else:
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

    # Same as Generate (client PL-159): the AI extraction uses the CURRENT
    # saved Content Generation settings (provider/model), not the copy
    # taken at pipeline start.
    _cur_path_e = _gen_config_path(getattr(pl, "store_id", None))
    if _cur_path_e.exists():
        try:
            _cur_e = json.loads(_cur_path_e.read_text())
            if _cur_e:
                cfg = dict(cfg)
                cfg["content_gen_config"] = _cur_e
        except Exception:
            pass
    gen_cfg = cfg.get("content_gen_config", {})
    if not gen_cfg:
        saved_path = _gen_config_path(getattr(pl, "store_id", None))
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
        prod_dict = {"id": product.id, "name": product.name or "", **raw,
                     # current (CSV / edited) title too, for Attribute Mapping
                     # "title contains" rules -- **raw's "name" is the Sunsky one
                     "_current_name": product.name or ""}
        product_dicts.append(prod_dict)

        sunsky_cat = extract_sunsky_category(raw, category_name_map)
        _ex_diag: dict = {}
        attrs = await extract_attributes(
            prod_dict, gen_cfg, db=db,
            store_id=pl.store_id, sunsky_category=sunsky_cat,
            diag=_ex_diag,
        )
        if _ex_diag.get("ai_error") and _ex_diag.get("ai_asked"):
            await _plog(db, pl.id, "enrich", "warn",
                        f"{product.sku}: AI gave no answer for "
                        f"{', '.join(_ex_diag['ai_asked'])} — {_ex_diag['ai_error']}. "
                        f"Shown as \"missing\" — set them by hand or re-run.")

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
        from tasks.job_tasks import _product_titles as _ee_titles
        expected_attrs = await load_profile_attrs_for_category(db, pl.store_id, sunsky_cat, _ee_titles(product))
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

        # Client feedback confirmed live (PL-152, Test hdcam): after the
        # operator deleted Attribute Mapping rule 12 ("Product Line" =
        # fixed "X5", Always) and went Back to Fetch, the re-run
        # extraction no longer produced "Product Line" (log 13:52:57) --
        # yet the Details -> Attributes tab still listed "Product Line:
        # X5" for PL-152, and the Upload click (content_confirm's bulk
        # confirm) would have uploaded it. Saving was insert-or-update
        # only, so an attribute a previous run of THIS pipeline produced
        # but the new run doesn't was never removed. Remove it now --
        # except rows the operator added by hand (source "manual", from
        # the Content Review "add attribute" endpoint), which must
        # survive a re-extraction.
        from sqlalchemy import delete as _sa_delete
        _new_attr_names = [a["attribute"] for a in attrs]
        await db.execute(
            _sa_delete(ProductEnrichAttr).where(
                ProductEnrichAttr.pipeline_job_id == pl.id,
                ProductEnrichAttr.product_id == product.id,
                ProductEnrichAttr.source != "manual",
                ProductEnrichAttr.attribute.notin_(_new_attr_names),
            )
        )

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
                    # A value the operator typed by hand is not
                    # overwritten by a re-extraction of the same attribute.
                    where=(ProductEnrichAttr.source != "manual"),
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
                    if stats.get("stopped"):
                        return  # cancelled, or a newer run took over
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
                    saved_path = _gen_config_path(getattr(pl, "store_id", None))
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
                from models.models import CsvMapping as _CsvMapping

                applied = 0
                for product_id, field_results in by_product.items():
                    product = await db.get(Product, product_id)
                    if not product:
                        continue
                    raw = product.raw_data or {}
                    _csv_entry = (await db.execute(
                        select(_CsvMapping).where(_CsvMapping.sunsky_sku == product.sku)
                    )).scalars().first()
                    csv_title = _apply_csv_entry(product, _csv_entry)
                    prod_dict = {
                        "name": product.name or "", "sku": product.sku or "",
                        "description": product.description or "", "price": product.price or "0",
                        "site_sku": product.site_sku or "", "csv_title": csv_title, **raw,
                    }
                    prod_dict.update(await _generation_context_extras(db, pl.id, product))
                    # Same "already generated / operator-set -> keep" rule the
                    # SUBMIT step applied (batch mode never runs with
                    # force_regenerate -- that's only the sync "Re-generate
                    # content" path). Without it, every field deliberately
                    # left OUT of the batch (already generated, or a title
                    # the operator edited) was regenerated LIVE here via
                    # run_field's non-precomputed AI branch and overwritten.
                    _sources_before = dict(product.content_source or {})
                    apply_template, _apply_skipped = _template_skipping_generated_fields(product, template)
                    field_results_out = await generate_product(prod_dict, apply_template, precomputed_ai=field_results)
                    sources = dict(product.content_source or {})  # copy -- see the sync path
                    # Client feedback confirmed live via a full pipeline
                    # log: Meta Description and every other dependent
                    # field (Slug, Meta Title, Short Description, Focus
                    # Keyword, Tags, Image Alt, Image Names) came back
                    # missing in WooCommerce, even though the batch
                    # itself succeeded (6/6, 0 failed). Root cause:
                    # generate_product() above already correctly
                    # computes EVERY enabled field through the full DAG,
                    # using the batch-resolved Title/Description as
                    # input for anything that depends on them -- but
                    # this loop was previously SKIPPING every field not
                    # literally present in the original batch submission
                    # (field_results), discarding all of that correctly-
                    # computed dependent-field output before it was ever
                    # saved. Now saves everything generate_product()
                    # actually produced, not just the fields that were
                    # directly part of the batch request.
                    for field, result in field_results_out.items():
                        attr = FIELD_ATTR.get(field)
                        value = result.get("value", "")
                        if attr and value:
                            setattr(product, attr, value)
                            # kept (skipped) fields keep their original
                            # provenance -- e.g. "manual" for an operator-
                            # edited title -- same as the sync path
                            if field in _apply_skipped and field in _sources_before:
                                sources[field] = _sources_before[field]
                            else:
                                sources[field] = result.get("source", "logic")
                    product.content_source = sources
                    # CSV title always wins -- same re-assert as the
                    # synchronous path (see _apply_csv_entry).
                    if csv_title:
                        product.name = csv_title
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
                    if stats.get("stopped"):
                        return  # cancelled, or a newer run took over
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
                # Client feedback (PL-159): after switching the model from
                # google/gemma-...:free (rate-limited -> fallbacks) to
                # Gemini 2.5 Flash Lite, "Re-generate content" gave the same
                # fallbacks -- it used the settings COPIED when the pipeline
                # started (pl.config), not the current ones. Re-generate is
                # the operator's explicit "try again", so it uses the CURRENT
                # saved Content Generation settings, stores them on the
                # pipeline, and logs which provider/model it uses.
                import json
                cfg = dict(pl.config or {})
                _old_gs = (cfg.get("content_gen_config") or {}).get("globalSettings") or {}
                _saved_path = _gen_config_path(getattr(pl, "store_id", None))
                _current = None
                if _saved_path.exists():
                    try:
                        _current = json.loads(_saved_path.read_text())
                    except Exception:
                        _current = None
                if _current:
                    cfg["content_gen_config"] = _current
                    pl.config = cfg
                    _new_gs = _current.get("globalSettings") or {}
                    _fmt = lambda g: f"{g.get('ai_provider') or '?'} · {g.get('ai_model') or 'default model'}"
                    await _plog(db, pl.id, "generate", "info",
                                f"Re-generate uses the CURRENT Content Generation settings: {_fmt(_new_gs)}"
                                + (f" (this pipeline started with {_fmt(_old_gs)})" if _fmt(_old_gs) != _fmt(_new_gs) and _old_gs else ""))
                    await db.commit()
                stats = await _run_generate(db, pl, cfg, force_sync=True, force_regenerate=True)
                if stats.get("stopped"):
                    return   # cancelled / a newer run took over -- leave its status alone

                pl.status = "content_review"
                pl.current_step = "content_review"
                pl.config = {k: v for k, v in (pl.config or {}).items() if k != "regenerating"}
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, "content_review", "info",
                            f"Content re-generated: {stats}")

            except Exception as e:
                # Back to Content Review, not "failed": the pipeline was
                # reviewable before this operator-requested retry, and a
                # failed pipeline can neither be resumed nor re-generated
                # (client PL-159: "pipeline stuck ... doesn't make any
                # regenerates after that").
                pl.status = "content_review"
                pl.current_step = "content_review"
                pl.config = {k: v for k, v in (pl.config or {}).items() if k != "regenerating"}
                pl.error_message = str(e)
                pl.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await _plog(db, pl.id, "generate", "error",
                            f"Re-generate failed: {e} — back in Content Review; you can try Re-generate again")

            finally:
                await _advance_queue(db, pl.store_id, pl.id)
    finally:
        await celery_engine.dispose()
