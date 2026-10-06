"""
Map step router — /api/pipelines/{id}/map-data  +  /api/pipelines/{id}/map-confirm
                  /api/stores/{id}/category-mappings

The Map step sits inside the existing Review pause.  The client confirms category
mappings via map-confirm, which also triggers the pipeline resume (Upload → Sync).

Multi-category support: each Sunsky category maps to a list of WooCommerce categories
with one designated as primary.  The full set is stored in woo_cats_json (JSON).
Backward-compat columns woo_cat_id / woo_cat_name mirror the primary category.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.models import (
    PipelineJob, Product, SunskyCategoryMapping, WooCategory, AttributeProfile, Job, JobType
)

router = APIRouter(tags=["map-step"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class WooCatEntry(BaseModel):
    id: int
    name: str


class MappingEntry(BaseModel):
    sunsky_cat: str
    woo_cats: list[WooCatEntry] = []
    primary_woo_cat_id: Optional[int] = None
    profile_id: Optional[int] = None
    save_as_rule: bool = True


class MapConfirmRequest(BaseModel):
    mappings: list[MappingEntry] = []


class CategoryMappingUpdate(BaseModel):
    sunsky_cat: str
    woo_cats: list[WooCatEntry] = []
    primary_woo_cat_id: Optional[int] = None
    profile_id: Optional[int] = None
    # "IF title contains" words (milestone point 2); "" = ordinary rule.
    title_contains: str = ""
    # Set when editing an existing rule: that exact row is updated (so
    # changing its title words edits it instead of adding a second rule).
    id: Optional[int] = None


def _norm_title_contains(value) -> str:
    """'frame,  Cage ,' -> 'frame, Cage' (display form; matching is
    case-insensitive -- job_tasks._title_terms)."""
    return ", ".join(t.strip() for t in str(value or "").split(",") if t.strip())


async def _save_category_rule(db, store_id: Optional[int], entry: "CategoryMappingUpdate") -> None:
    """Insert or update one Category Mapping rule (store rule, or global
    when store_id is None). Rules are unique per (store, Sunsky category,
    title words); with entry.id the given rule is edited in place."""
    title = _norm_title_contains(entry.title_contains)
    primary_id = entry.primary_woo_cat_id or (entry.woo_cats[0].id if entry.woo_cats else None)
    primary_cat = next((c for c in entry.woo_cats if c.id == primary_id), entry.woo_cats[0] if entry.woo_cats else None)
    fields = {
        "woo_cat_id":         primary_cat.id if primary_cat else None,
        "woo_cat_name":       primary_cat.name if primary_cat else None,
        "woo_cats_json":      json.dumps([{"id": c.id, "name": c.name} for c in entry.woo_cats]),
        "primary_woo_cat_id": primary_id,
        "profile_id":         entry.profile_id,
        "updated_at":         datetime.now(timezone.utc),
    }
    scope = (SunskyCategoryMapping.store_id.is_(None) if store_id is None
             else SunskyCategoryMapping.store_id == store_id)
    # Title words match case-insensitively (job_tasks._title_terms), so
    # "Waterproof" and "waterproof" are the SAME rule -- compare lower-cased
    # (the unique index itself is case-sensitive).
    same_words = func.lower(SunskyCategoryMapping.title_contains) == title.lower()
    edit_id = entry.id
    if not edit_id:
        # Adding words that an existing rule already has (any case) updates
        # that rule instead of creating a duplicate.
        edit_id = (await db.execute(select(SunskyCategoryMapping.id).where(
            scope, SunskyCategoryMapping.sunsky_cat == entry.sunsky_cat, same_words,
        ))).scalars().first()
    if edit_id:
        row = await db.get(SunskyCategoryMapping, edit_id)
        if row is None or row.store_id != store_id:
            raise HTTPException(404, "Rule not found")
        clash = (await db.execute(select(SunskyCategoryMapping.id).where(
            scope,
            SunskyCategoryMapping.sunsky_cat == entry.sunsky_cat,
            same_words,
            SunskyCategoryMapping.id != row.id,
        ))).first()
        if clash:
            raise HTTPException(409, "A rule for this Sunsky category with the same title words already exists")
        row.sunsky_cat = entry.sunsky_cat
        row.title_contains = title
        for k, v in fields.items():
            setattr(row, k, v)
        return
    stmt = pg_insert(SunskyCategoryMapping).values(
        store_id=store_id, sunsky_cat=entry.sunsky_cat, title_contains=title, times_used=0, **fields,
    )
    if store_id is None:
        stmt = stmt.on_conflict_do_update(
            index_elements=["sunsky_cat", "title_contains"],
            index_where=SunskyCategoryMapping.store_id.is_(None),
            set_=fields,
        )
    else:
        stmt = stmt.on_conflict_do_update(index_elements=["store_id", "sunsky_cat", "title_contains"], set_=fields)
    await db.execute(stmt)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_sunsky_cat(raw: dict, name_map: Optional[dict[str, str]] = None) -> str:
    """Best-effort extraction of Sunsky category NAME from product raw_data.

    Real Sunsky product responses only include a numeric categoryId, not a
    name field — confirmed against live data (2026-08-01). When no name
    field is present, resolves the ID through `name_map` (built from
    sunsky_client.get_category_name_map()) if provided. This must produce
    the exact same value that gets saved as SunskyCategoryMapping.sunsky_cat
    below, or future pipeline runs won't recognize this category as already
    mapped — see services/enrich_service.py's extract_sunsky_category() for
    the sibling copy of this same fix.
    """
    for key in ("catName", "categoryName", "category_name", "cat_name"):
        v = str(raw.get(key) or "").strip()
        if v:
            return v
    cat_id = str(raw.get("categoryId") or raw.get("catId") or raw.get("category_id") or "").strip()
    if cat_id and name_map:
        name = name_map.get(cat_id)
        if name:
            return name
    return cat_id


def _extract_sunsky_cat_id(raw: dict) -> str:
    """Raw numeric Sunsky category ID from a product's raw_data — the stable
    match key sunsky_category_mappings.sunsky_cat_id exists for (see model
    docstring). Mirrors job_tasks.py's _get_sunsky_cat_id so Category Review,
    Content Review, and Sync all agree on the same value for the same product.
    """
    return str(raw.get("categoryId") or raw.get("catId") or raw.get("category_id") or "").strip()


def _mapping_woo_cats(m: SunskyCategoryMapping) -> list[dict]:
    """Return list of {id, name} dicts from a saved mapping row."""
    if m.woo_cats_json:
        try:
            return json.loads(m.woo_cats_json)
        except Exception:
            pass
    if m.woo_cat_id:
        return [{"id": m.woo_cat_id, "name": m.woo_cat_name or ""}]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline-scoped endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def _category_coverage(db, pl, products, category_name_map) -> dict:
    """Per Sunsky category in this pipeline: which products are covered by a
    Category Mapping rule and which are not -- decided per PRODUCT with
    job_tasks._cat_rule_for_product (the same decision the pause check
    makes), since one category can have several "IF title contains" rules
    (milestone point 2). Returns {cat: {"products", "unresolved",
    "rules_used": {key: {"rule", "global", "count"}}, "broken": [(rule,
    missing_ids)], "store_rules": [...]}}."""
    from tasks.job_tasks import _cat_rule_for_product, _broken_rule_ids, _product_titles
    store_rules = (await db.execute(
        select(SunskyCategoryMapping).where(SunskyCategoryMapping.store_id == pl.store_id)
    )).scalars().all()
    by_cat: dict[str, list] = {}
    for r in store_rules:
        by_cat.setdefault(r.sunsky_cat, []).append(r)
    broken_ids = await _broken_rule_ids(db, pl.store_id, store_rules)
    cov: dict[str, dict] = {}
    for p in products:
        cat = _extract_sunsky_cat(p.raw_data or {}, category_name_map)
        if not cat:
            continue
        c = cov.setdefault(cat, {"products": [], "unresolved": [], "rules_used": {}, "broken": [],
                                 "store_rules": by_cat.get(cat, [])})
        c["products"].append(p)
        try:
            status, rule, g = await _cat_rule_for_product(
                db, pl.store_id, cat, _product_titles(p), by_cat.get(cat, []), broken_ids
            )
        except Exception as _cov_e:
            logger.warning(f"[map-data] rule check failed for {cat!r}: {_cov_e}")
            status, rule, g = None, None, None
        if status in ("store", "global"):
            key = ("s", rule.id) if rule is not None else ("g", g.get("rule_id"))
            u = c["rules_used"].setdefault(key, {"rule": rule, "global": g, "count": 0})
            u["count"] += 1
        else:
            c["unresolved"].append(p)
            if status == "broken":
                c["broken"].append((rule, broken_ids.get(rule.id, [])))
    return cov


@router.get("/pipelines/{pipeline_id}/map-data")
async def get_map_data(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    """
    Returns unique Sunsky categories found in this pipeline's product batch,
    merged with any saved mappings for this store.

    woo_options includes parent_id so the frontend can render a hierarchy tree.
    Each category entry has:
      - woo_cats: list of {id, name} (all assigned WooCommerce cats)
      - primary_woo_cat_id: which one is primary
      - is_new: True when no saved rule exists (needs manual assignment)
    """
    pl = await db.get(PipelineJob, pipeline_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Load products for this pipeline's fetch job
    products = (
        await db.execute(
            select(Product).where(Product.fetch_job_id == pl.fetch_job_id)
        )
    ).scalars().all()

    from services.enrich_service import get_effective_category_name_map
    category_name_map = await get_effective_category_name_map(db)

    # Extract unique Sunsky categories, and which SKUs fall into each --
    # client feedback: "On Cat.Review step don't see the selected products
    # ... if I don't see the products on cat.review how to determine the
    # categories." Previously this only tracked counts, with no way to
    # see which specific products they represented.
    cat_counts: dict[str, int] = {}
    cat_skus: dict[str, list[str]] = {}
    # Client feedback: "it should automatically show selected in review
    # step all [Sunsky's own ancestor categories] with selected and it
    # should assign as well." Confirmed the client's chosen approach
    # (Option A): match against EXISTING WooCommerce categories only by
    # name -- never auto-create a category here, unlike the separate
    # auto-create-taxonomy fallback that already exists elsewhere for a
    # genuinely different scenario (no mapping at all yet). Tracks each
    # Sunsky category's own raw numeric id too (not just its resolved
    # name), needed to walk sunsky_client's own disk-cached category
    # tree via build_category_path below.
    cat_raw_id: dict[str, str] = {}
    for p in products:
        raw = p.raw_data or {}
        cat = _extract_sunsky_cat(raw, category_name_map)
        if cat:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            cat_skus.setdefault(cat, []).append(p.site_sku or p.sku or f"#{p.id}")
            if cat not in cat_raw_id:
                cat_raw_id[cat] = str(raw.get("categoryId") or raw.get("catId") or raw.get("category_id") or "").strip()

    # Load saved mappings for this store
    saved_rows = (
        await db.execute(
            select(SunskyCategoryMapping).where(
                SunskyCategoryMapping.store_id == pl.store_id
            )
        )
    ).scalars().all()
    # Ordinary rules (no "IF title contains" words) by Sunsky category --
    # used for the synthetic CSV Import / Uncategorised Products card.
    saved: dict[str, SunskyCategoryMapping] = {
        r.sunsky_cat: r for r in saved_rows if not (r.title_contains or "").strip()
    }

    # Load WooCommerce categories — include parent_id for tree display
    woo_cats = (
        await db.execute(
            select(WooCategory).where(WooCategory.store_id == pl.store_id)
        )
    ).scalars().all()

    # Load attribute profiles for the panel B dropdown
    from sqlalchemy.orm import selectinload
    profiles = (
        await db.execute(
            select(AttributeProfile)
            .options(selectinload(AttributeProfile.attributes))
            .order_by(AttributeProfile.name)
        )
    ).scalars().all()

    # Total product count in this batch (products may have no extractable category)
    total_products = (await db.execute(
        select(func.count(Product.id)).where(Product.fetch_job_id == pl.fetch_job_id)
    )).scalar_one()

    categories = []
    # Client feedback: "it should automatically show selected in review
    # step all [Sunsky's own ancestor categories]... and it should
    # assign as well." Case-insensitive name lookup against this
    # store's OWN already-loaded WooCommerce category tree -- Option A
    # (client's explicit choice): only ever matches an EXISTING
    # WooCommerce category, never creates one here.
    woo_cat_by_name = {c.name.strip().lower(): c for c in woo_cats}
    woo_name_by_id = {c.woo_id: c.name for c in woo_cats}
    # Global (store_id IS NULL) rules -- see the global fallback below.
    global_rows = (
        await db.execute(
            select(SunskyCategoryMapping).where(SunskyCategoryMapping.store_id.is_(None))
        )
    ).scalars().all()

    from tasks.job_tasks import _broken_store_rules, _title_terms
    from pipeline.sunsky_client import build_category_path as _build_sunsky_path
    # Cards are built from per-PRODUCT coverage ("IF title contains" rules,
    # milestone point 2): a category is "already mapped" only if every one
    # of its products is covered by some rule (store, global, or a title
    # rule); otherwise the card asks for a category for the UNCOVERED
    # products only -- confirming saves the ordinary rule (no title words)
    # for the category, which then covers them. Earlier fixes are kept:
    # global rules count when no store rule applies; a store rule whose
    # WooCommerce category no longer exists does not count.
    coverage = await _category_coverage(db, pl, products, category_name_map)
    global_saved: dict[int, SunskyCategoryMapping] = {r.id: r for r in global_rows}
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        cov = coverage.get(cat) or {"products": [], "unresolved": [], "rules_used": {}, "broken": [], "store_rules": []}
        unresolved = cov["unresolved"]
        used = list(cov["rules_used"].values())
        ordinary = next((r for r in cov["store_rules"] if not _title_terms(r.title_contains)), None)
        broken_ids: list[int] = []
        broken_titles: list[str] = []
        for _br, _missing in cov["broken"]:
            broken_ids.extend(i for i in _missing if i not in broken_ids)
            if (_br.title_contains or "").strip() and _br.title_contains not in broken_titles:
                broken_titles.append(_br.title_contains)

        def _cats_of(u):
            if u["rule"] is not None:
                return _mapping_woo_cats(u["rule"]), u["rule"].primary_woo_cat_id
            ids = u["global"]["woo_cat_ids"]
            return [{"id": i, "name": woo_name_by_id.get(i, "")} for i in ids], u["global"]["primary_woo_cat_id"]

        title_rules = []
        for u in used:
            r = u["rule"] or global_saved.get(u["global"].get("rule_id"))
            if r is not None and (r.title_contains or "").strip():
                _tr_cats, _tr_primary = _cats_of(u)
                title_rules.append({"title_contains": r.title_contains, "product_count": u["count"],
                                    "woo_cats": _tr_cats,
                                    # ★ main category -- what the summary shows (the list is
                                    # in click order, so its last entry can be a parent)
                                    "primary_woo_cat_id": _tr_primary or (_tr_cats[-1]["id"] if _tr_cats else None),
                                    "source": "store" if u["rule"] is not None else "global"})

        if not unresolved and used:
            # shown: the ordinary rule if it was used, else the most-used rule
            main = next((u for u in used if u["rule"] is not None and u["rule"] is ordinary), None) \
                or max(used, key=lambda u: u["count"])
            woo_cat_list, primary_id = _cats_of(main)
            primary_id = primary_id or (woo_cat_list[-1]["id"] if woo_cat_list else None)
            main_rule = main["rule"] or global_saved.get(main["global"].get("rule_id"))
            source = "store" if main["rule"] is not None else "global"
            profile_id = main_rule.profile_id if main_rule else None
            times_used = main_rule.times_used if main_rule else 0
            shown_count, shown_skus = count, cat_skus.get(cat, [])[:10]
        else:
            woo_cat_list, primary_id, source = [], None, None
            profile_id = ordinary.profile_id if ordinary else None
            times_used = ordinary.times_used if ordinary else 0
            shown_count = len(unresolved) or count
            shown_skus = [p.site_sku or p.sku or f"#{p.id}" for p in unresolved][:10] or cat_skus.get(cat, [])[:10]

        sunsky_ancestor_matches: list[dict] = []
        if source is None and cat_raw_id.get(cat):
            try:
                sunsky_path = _build_sunsky_path(cat_raw_id[cat])
                for ancestor in sunsky_path[:-1]:
                    match = woo_cat_by_name.get(str(ancestor.get("name", "")).strip().lower())
                    if match:
                        sunsky_ancestor_matches.append({"id": match.woo_id, "name": match.name})
            except Exception as _sap_e:
                logger.warning(f"[map-data] Sunsky ancestor match failed for {cat!r}: {_sap_e}")
        categories.append({
            "sunsky_cat":         cat,
            "product_count":      shown_count,
            "sample_skus":        shown_skus,
            "woo_cats":           woo_cat_list,
            "primary_woo_cat_id": primary_id,
            "profile_id":         profile_id,
            "is_new":             source is None,
            "times_used":         times_used,
            "sunsky_ancestor_matches": sunsky_ancestor_matches,
            "source":             source,
            "broken_missing_ids": broken_ids,
            "broken_title_contains": broken_titles,
            "title_rules":        title_rules,
            "covered_by_title_rules": sum(t["product_count"] for t in title_rules) if source is None else 0,
            "total_in_category":  count,
        })

    categorized_count = sum(c["product_count"] for c in categories)
    uncategorized_count = total_products - categorized_count
    if uncategorized_count > 0:
        fetch_job = await db.get(Job, pl.fetch_job_id) if pl.fetch_job_id else None
        is_csv = fetch_job and fetch_job.type == JobType.csv_import
        label = "CSV Import" if is_csv else "Uncategorised Products"
        m = saved.get(label)
        label_broken = (await _broken_store_rules(db, pl.store_id, [m])).get(label) if m is not None else None
        woo_cat_list = _mapping_woo_cats(m) if m and not label_broken else []
        primary_id = (m.primary_woo_cat_id if m and not label_broken
                      else (woo_cat_list[0]["id"] if woo_cat_list else None))
        categories.append({
            "sunsky_cat":         label,
            "product_count":      uncategorized_count,
            "sample_skus":        [],
            "woo_cats":           woo_cat_list,
            "primary_woo_cat_id": primary_id,
            "profile_id":         m.profile_id if m else None,
            "is_new":             m is None or bool(label_broken),
            "times_used":         m.times_used if m else 0,
            "broken_missing_ids": label_broken or [],
        })

    return {
        "pipeline_id":    pipeline_id,
        "store_id":       pl.store_id,
        "total_products": total_products,
        "categories":     categories,
        "woo_options": [
            {"id": c.woo_id, "name": c.name, "parent_id": c.parent_id or 0}
            for c in sorted(woo_cats, key=lambda x: x.name)
        ],
        "profiles": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "attributes": [
                    {"woo_attr_name": a.woo_attr_name, "required": a.required}
                    for a in (p.attributes or [])
                ],
            }
            for p in profiles
        ],
    }


@router.post("/pipelines/{pipeline_id}/map-confirm")
async def map_confirm(
    pipeline_id: int,
    req: MapConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Save multi-category mappings and resume the pipeline (Upload → Sync).

    Each entry carries woo_cats (full set) + primary_woo_cat_id.
    Backward-compat columns woo_cat_id / woo_cat_name are updated from the primary.
    Only entries with save_as_rule=True are persisted to the dictionary.
    All entries (saved or not) are applied to this pipeline run.
    """
    pl = await db.get(PipelineJob, pipeline_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pl.status != "review":
        raise HTTPException(status_code=400, detail=f"Pipeline is not in review state (current: {pl.status})")

    # Build resolved-name → raw Sunsky category ID, from this pipeline's own
    # products, so each mapping row can be saved with sunsky_cat_id filled in
    # immediately rather than staying NULL until Sync's Step A runs and
    # backfills it after the fact. Without this, Content Review's display
    # (which matches by ID, see pipeline.py's content-data) can't find a
    # mapping that was, in fact, just saved correctly.
    from services.enrich_service import get_effective_category_name_map
    category_name_map = await get_effective_category_name_map(db)
    name_to_cat_id: dict[str, str] = {}
    batch_products = (
        await db.execute(select(Product).where(Product.fetch_job_id == pl.fetch_job_id))
    ).scalars().all()
    for p in batch_products:
        raw = p.raw_data or {}
        resolved_name = _extract_sunsky_cat(raw, category_name_map)
        cat_id = _extract_sunsky_cat_id(raw)
        if resolved_name and cat_id:
            name_to_cat_id.setdefault(resolved_name.strip().lower(), cat_id)

    # Store rows that already exist for this store -- used below to leave
    # categories covered ONLY by a global rule untouched.
    # Per-product coverage -- the same decision map-data showed. A category
    # whose products are ALL covered (by "IF title contains" rules and/or a
    # global rule) and that has no ordinary store rule is skipped: the
    # frontend sends every category back on confirm, and saving here would
    # create an ordinary store rule nobody chose (and, for global-only
    # categories, silently override the global rule -- earlier fix).
    # Otherwise the ordinary rule (title_contains '') is upserted: it covers
    # the products no title rule matched, or repairs a broken ordinary rule.
    coverage = await _category_coverage(db, pl, batch_products, category_name_map)
    from tasks.job_tasks import _title_terms

    for entry in req.mappings:
        if not entry.sunsky_cat or not entry.woo_cats:
            continue
        _cov = coverage.get(entry.sunsky_cat)
        if _cov is not None:
            _has_ordinary = any(not _title_terms(r.title_contains) for r in _cov["store_rules"])
            if not _cov["unresolved"] and not _has_ordinary:
                continue

        # Resolve primary category
        primary_id = entry.primary_woo_cat_id or (entry.woo_cats[0].id if entry.woo_cats else None)
        primary_cat = next((c for c in entry.woo_cats if c.id == primary_id), entry.woo_cats[0] if entry.woo_cats else None)

        cats_json = json.dumps([{"id": c.id, "name": c.name} for c in entry.woo_cats])
        profile_id = entry.profile_id or None
        sunsky_cat_id = name_to_cat_id.get(entry.sunsky_cat.strip().lower())

        if entry.save_as_rule:
            stmt = (
                pg_insert(SunskyCategoryMapping)
                .values(
                    store_id=pl.store_id,
                    sunsky_cat=entry.sunsky_cat,
                    sunsky_cat_id=sunsky_cat_id,
                    woo_cat_id=primary_cat.id if primary_cat else None,
                    woo_cat_name=primary_cat.name if primary_cat else None,
                    woo_cats_json=cats_json,
                    primary_woo_cat_id=primary_id,
                    profile_id=profile_id,
                    times_used=1,
                    last_used_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_update(
                    index_elements=["store_id", "sunsky_cat", "title_contains"],
                    set_={
                        "sunsky_cat_id":      sunsky_cat_id,
                        "woo_cat_id":         primary_cat.id if primary_cat else None,
                        "woo_cat_name":       primary_cat.name if primary_cat else None,
                        "woo_cats_json":      cats_json,
                        "primary_woo_cat_id": primary_id,
                        "profile_id":         profile_id,
                        "times_used":         SunskyCategoryMapping.__table__.c.times_used + 1,
                        "last_used_at":       datetime.now(timezone.utc),
                        "updated_at":         datetime.now(timezone.utc),
                    },
                )
            )
            await db.execute(stmt)
        else:
            stmt = (
                pg_insert(SunskyCategoryMapping)
                .values(
                    store_id=pl.store_id,
                    sunsky_cat=entry.sunsky_cat,
                    sunsky_cat_id=sunsky_cat_id,
                    woo_cat_id=primary_cat.id if primary_cat else None,
                    woo_cat_name=primary_cat.name if primary_cat else None,
                    woo_cats_json=cats_json,
                    primary_woo_cat_id=primary_id,
                    profile_id=profile_id,
                    times_used=0,
                    last_used_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_update(
                    index_elements=["store_id", "sunsky_cat", "title_contains"],
                    set_={
                        "sunsky_cat_id":      sunsky_cat_id,
                        "woo_cats_json":      cats_json,
                        "primary_woo_cat_id": primary_id,
                        "woo_cat_id":         primary_cat.id if primary_cat else None,
                        "woo_cat_name":       primary_cat.name if primary_cat else None,
                        "profile_id":         profile_id,
                        "updated_at":         datetime.now(timezone.utc),
                    },
                )
            )
            await db.execute(stmt)

    await db.commit()

    # Transition to content_review so the user can review generated content before upload
    pl.status = "content_review"
    # Previously never set here (or anywhere) -- current_step stayed stuck
    # at whatever it was before ("generate"), so a Cancel + "Continue from
    # last step" on a pipeline paused here had no way to know it was
    # actually sitting at Content Review, and fell through to a generic
    # re-execute path instead. Client feedback: "Cancel+Continue doesn't
    # resume where left off, goes back to Cat.Review and re-waits."
    pl.current_step = "content_review"
    pl.updated_at = datetime.now(timezone.utc)
    await db.commit()

    from models.models import PipelineLog
    db.add(PipelineLog(
        pipeline_job_id=pipeline_id, level="info",
        message="Category mapping confirmed — pausing for content review before upload",
    ))
    await db.commit()

    return {"ok": True, "pipeline_id": pipeline_id, "mapped": len(req.mappings)}


# ─────────────────────────────────────────────────────────────────────────────
# Store-scoped endpoints (Settings page — Category mapping dictionary)
# ─────────────────────────────────────────────────────────────────────────────

async def _sunsky_name_to_ids(db) -> dict[str, list[str]]:
    """Sunsky category name (lower-case) -> its IDs. Client: "add sunsky
    category ID as new column". Names aren't unique in Sunsky's tree
    (e.g. "Protection & Cases" under several parents), so a list."""
    try:
        from services.enrich_service import get_effective_category_name_map
        id_to_name = await get_effective_category_name_map(db)
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for cid, name in (id_to_name or {}).items():
        out.setdefault(str(name).strip().lower(), []).append(str(cid))
    return out


def _sunsky_ids_for(r, name_to_ids: dict[str, list[str]]) -> list[str]:
    """The rule's own Sunsky ID if saved; a rule saved by ID; else the
    IDs whose name matches."""
    if getattr(r, "sunsky_cat_id", None):
        return [str(r.sunsky_cat_id)]
    cat = str(r.sunsky_cat or "").strip()
    if cat.isdigit():
        return [cat]
    return sorted(name_to_ids.get(cat.lower(), []), key=lambda x: int(x) if x.isdigit() else 0)


@router.get("/stores/{store_id}/category-mappings")
async def list_category_mappings(store_id: int, db: AsyncSession = Depends(get_db)):
    from models.models import AttributeProfile

    rows = (
        await db.execute(
            select(SunskyCategoryMapping)
            .where(SunskyCategoryMapping.store_id == store_id)
            .order_by(SunskyCategoryMapping.sunsky_cat)
        )
    ).scalars().all()

    # Client feedback: "lets do whichever is necessary to do for per
    # store thing... global and per store both rules options are
    # there." Merge in global (store_id IS NULL) rules for any
    # sunsky_cat this store hasn't overridden with its own rule --
    # same store-wins-over-global merge pattern already used for
    # Extraction Rules. A global row's woo_cats -- unlike a per-store
    # row's -- are a NAME PATH re-resolved per store at actual upload
    # time (see job_tasks.py's _resolve_category_path_for_store), not
    # directly-usable IDs; shown here as-is (the names/IDs it was
    # originally captured with) purely for display in this list.
    global_rows = (
        await db.execute(
            select(SunskyCategoryMapping)
            .where(SunskyCategoryMapping.store_id.is_(None))
            .order_by(SunskyCategoryMapping.sunsky_cat)
        )
    ).scalars().all()
    own_cats = {(r.sunsky_cat, r.title_contains or "") for r in rows}
    # Client feedback confirmed live: "why global not showing" (same
    # question, same underlying pattern, already fixed once for
    # Extraction Rules). Previously this hid a global rule entirely
    # whenever a store-specific rule existed for the same sunsky_cat,
    # showing only the override -- meaning an operator couldn't view
    # or edit the underlying global rule at all while a per-store
    # override was in place, without switching to a different store
    # that has no override first. Now returns BOTH rows; is_overridden
    # marks the global one as not currently the one that wins for this
    # store, so the frontend can show it de-emphasized instead of
    # hiding it.
    merged = list(rows) + list(global_rows)
    _name_to_ids = await _sunsky_name_to_ids(db)

    profile_ids = {r.profile_id for r in merged if r.profile_id}
    profile_names: dict[int, str] = {}
    if profile_ids:
        profile_rows = (
            await db.execute(select(AttributeProfile).where(AttributeProfile.id.in_(profile_ids)))
        ).scalars().all()
        profile_names = {p.id: p.name for p in profile_rows}

    return {
        "store_id": store_id,
        "mappings": [
            {
                "id":                 r.id,
                "sunsky_cat":         r.sunsky_cat,
                "woo_cats":           _mapping_woo_cats(r),
                "primary_woo_cat_id": r.primary_woo_cat_id or r.woo_cat_id,
                "profile_id":         r.profile_id,
                "profile_name":       profile_names.get(r.profile_id) if r.profile_id else None,
                "times_used":         r.times_used or 0,
                "last_used_at":       r.last_used_at.isoformat() if r.last_used_at else None,
                "updated_at":         r.updated_at.isoformat() if r.updated_at else None,
                "is_global":          r.store_id is None,
                "is_overridden":      r.store_id is None and (r.sunsky_cat, r.title_contains or "") in own_cats,
                "title_contains":     r.title_contains or "",
                "sunsky_cat_ids":     _sunsky_ids_for(r, _name_to_ids),
            }
            for r in merged
        ],
    }


@router.put("/stores/{store_id}/category-mappings")
async def update_category_mappings(
    store_id: int,
    entries: list[CategoryMappingUpdate],
    db: AsyncSession = Depends(get_db),
):
    for entry in entries:
        if not entry.sunsky_cat:
            continue
        await _save_category_rule(db, store_id, entry)
    await db.commit()
    return {"ok": True, "saved": len(entries)}


@router.post("/stores/{store_id}/category-mappings/import")
async def import_category_mappings_file(
    store_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Import category mappings from an Excel (.xlsx) or CSV file.

    Required columns (case-insensitive header matching):
      - "Sunsky Category"  — the Sunsky category ID or name
      - "Woo Category"     — the WooCommerce category name (must already be synced)

    Multiple Woo categories per Sunsky category can be specified by repeating
    the Sunsky Category value on consecutive rows — all rows for the same
    Sunsky category are merged into a single multi-category mapping.
    """
    content = await file.read()
    filename = (file.filename or "").lower()

    # ── Parse rows from file ─────────────────────────────────────────────────
    raw_rows: list[tuple[str, str]] = []   # (sunsky_cat, woo_name)
    parse_error: Optional[str] = None

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            headers = [str(cell.value or "").strip().lower() for cell in next(ws.iter_rows(min_row=1, max_row=1))]
            sunsky_col = next((i for i, h in enumerate(headers) if "sunsky" in h), None)
            woo_col    = next((i for i, h in enumerate(headers) if "woo" in h), None)
            if sunsky_col is None or woo_col is None:
                parse_error = "Excel must have columns 'Sunsky Category' and 'Woo Category'"
            else:
                for row in ws.iter_rows(min_row=2, values_only=True):
                    s = str(row[sunsky_col] or "").strip()
                    w = str(row[woo_col]    or "").strip()
                    if s and w:
                        raw_rows.append((s, w))
        except Exception as exc:
            parse_error = f"Could not read Excel file: {exc}"
    elif filename.endswith(".csv"):
        try:
            reader = csv.reader(io.StringIO(content.decode("utf-8-sig")))
            headers = [h.strip().lower() for h in next(reader, [])]
            sunsky_col = next((i for i, h in enumerate(headers) if "sunsky" in h), None)
            woo_col    = next((i for i, h in enumerate(headers) if "woo" in h), None)
            if sunsky_col is None or woo_col is None:
                parse_error = "CSV must have columns 'Sunsky Category' and 'Woo Category'"
            else:
                for row in reader:
                    if len(row) > max(sunsky_col, woo_col):
                        s = row[sunsky_col].strip()
                        w = row[woo_col].strip()
                        if s and w:
                            raw_rows.append((s, w))
        except Exception as exc:
            parse_error = f"Could not read CSV file: {exc}"
    else:
        parse_error = "Unsupported file type — upload .xlsx or .csv"

    if parse_error:
        raise HTTPException(400, parse_error)

    if not raw_rows:
        raise HTTPException(400, "No data rows found in the file")

    # ── Load WooCommerce categories for matching ──────────────────────────────
    woo_cats = (
        await db.execute(select(WooCategory).where(WooCategory.store_id == store_id))
    ).scalars().all()
    woo_by_name: dict[str, WooCategory] = {c.name.strip().lower(): c for c in woo_cats}

    # ── Group rows by Sunsky category (support multi-Woo-cat per Sunsky cat) ─
    from collections import OrderedDict
    grouped: dict[str, list[WooCategory]] = OrderedDict()
    skipped: list[str] = []

    for sunsky_cat, woo_name in raw_rows:
        woo_cat = woo_by_name.get(woo_name.strip().lower())
        if not woo_cat:
            skipped.append(f"Row skipped — Woo category '{woo_name}' not found for Sunsky '{sunsky_cat}'")
            continue
        if sunsky_cat not in grouped:
            grouped[sunsky_cat] = []
        # Avoid duplicate Woo cats for the same Sunsky cat
        if not any(c.woo_id == woo_cat.woo_id for c in grouped[sunsky_cat]):
            grouped[sunsky_cat].append(woo_cat)

    # ── Upsert mappings ───────────────────────────────────────────────────────
    imported = 0
    for sunsky_cat, woo_cat_list in grouped.items():
        primary_cat = woo_cat_list[0]
        cats_json = json.dumps([{"id": c.woo_id, "name": c.name} for c in woo_cat_list])
        stmt = (
            pg_insert(SunskyCategoryMapping)
            .values(
                store_id=store_id,
                sunsky_cat=sunsky_cat,
                woo_cat_id=primary_cat.woo_id,
                woo_cat_name=primary_cat.name,
                woo_cats_json=cats_json,
                primary_woo_cat_id=primary_cat.woo_id,
                times_used=0,
                updated_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=["store_id", "sunsky_cat", "title_contains"],
                set_={
                    "woo_cat_id":         primary_cat.woo_id,
                    "woo_cat_name":       primary_cat.name,
                    "woo_cats_json":      cats_json,
                    "primary_woo_cat_id": primary_cat.woo_id,
                    "updated_at":         datetime.now(timezone.utc),
                },
            )
        )
        await db.execute(stmt)
        imported += 1

    await db.commit()
    return {
        "ok":         True,
        "imported":   imported,
        "skipped":    skipped,
        "total_rows": len(raw_rows),
    }


@router.delete("/stores/{store_id}/category-mappings/{mapping_id}")
async def delete_category_mapping(
    store_id: int,
    mapping_id: int,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(SunskyCategoryMapping, mapping_id)
    if not row or row.store_id != store_id:
        raise HTTPException(status_code=404, detail="Mapping not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Global Category Mapping rules (store_id IS NULL)
# ─────────────────────────────────────────────────────────────────────────────
# Client feedback: "lets do whichever is necessary to do for per store
# thing... global and per store both rules options are there, so if
# client want to do per store or global that is his choice." A global
# rule's woo_cats, unlike a per-store rule's, is a NAME PATH re-resolved
# against each store's own category tree at real upload time -- see
# job_tasks.py's _resolve_category_path_for_store for the actual
# resolution logic used during a pipeline run. This endpoint just
# stores whatever {id, name} pairs the operator picked (from whichever
# store's tree they were looking at while creating the rule) -- the
# names are what get reused; the ids are only ever meaningful again on
# that same original store, and are never trusted directly for any
# other store at resolution time.

@router.get("/category-mappings/global")
async def list_global_category_mappings(db: AsyncSession = Depends(get_db)):
    from models.models import AttributeProfile

    rows = (
        await db.execute(
            select(SunskyCategoryMapping)
            .where(SunskyCategoryMapping.store_id.is_(None))
            .order_by(SunskyCategoryMapping.sunsky_cat)
        )
    ).scalars().all()

    profile_ids = {r.profile_id for r in rows if r.profile_id}
    profile_names: dict[int, str] = {}
    if profile_ids:
        profile_rows = (
            await db.execute(select(AttributeProfile).where(AttributeProfile.id.in_(profile_ids)))
        ).scalars().all()
        profile_names = {p.id: p.name for p in profile_rows}

    _name_to_ids = await _sunsky_name_to_ids(db)
    return {
        "store_id": None,
        "mappings": [
            {
                "id":                 r.id,
                "sunsky_cat":         r.sunsky_cat,
                "sunsky_cat_ids":     _sunsky_ids_for(r, _name_to_ids),
                "woo_cats":           _mapping_woo_cats(r),
                "primary_woo_cat_id": r.primary_woo_cat_id or r.woo_cat_id,
                "profile_id":         r.profile_id,
                "profile_name":       profile_names.get(r.profile_id) if r.profile_id else None,
                "times_used":         r.times_used or 0,
                "last_used_at":       r.last_used_at.isoformat() if r.last_used_at else None,
                "updated_at":         r.updated_at.isoformat() if r.updated_at else None,
                "is_global":          True,
                "title_contains":     r.title_contains or "",
            }
            for r in rows
        ],
    }


@router.put("/category-mappings/global")
async def update_global_category_mappings(
    entries: list[CategoryMappingUpdate],
    db: AsyncSession = Depends(get_db),
):
    for entry in entries:
        if not entry.sunsky_cat:
            continue
        await _save_category_rule(db, None, entry)
    await db.commit()
    return {"ok": True, "saved": len(entries)}


@router.delete("/category-mappings/global/{mapping_id}")
async def delete_global_category_mapping(mapping_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(SunskyCategoryMapping, mapping_id)
    if not row or row.store_id is not None:
        raise HTTPException(status_code=404, detail="Global mapping not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
