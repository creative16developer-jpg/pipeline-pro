"""
Enrich step router

GET  /api/pipelines/{id}/enrich-data           — AI-extracted attrs + norm status
POST /api/pipelines/{id}/enrich-confirm        — save confirmed attrs + resume to Generate
GET  /api/pipelines/{id}/variant-groups        — AI-suggested variant groups
POST /api/pipelines/{id}/variant-groups/confirm — save confirmed groups

GET  /api/stores/{id}/normalisation-dict       — full dict (Settings page)
PUT  /api/stores/{id}/normalisation-dict       — bulk upsert (Settings page)
DELETE /api/stores/{id}/normalisation-dict/{entry_id}
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import selectinload
from models.models import (
    PipelineJob, Product,
    ProductEnrichAttr, NormalisationDict, VariantGroup,
    AIExtractionRule, WooAttribute,
)

router = APIRouter(tags=["enrich"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class AttrConfirmEntry(BaseModel):
    product_id: int
    attribute: str
    normalised_value: Optional[str] = None
    woo_attr_name: Optional[str] = None    # override WooCommerce attribute name
    confirmed: bool = True


class NormEntry(BaseModel):
    attribute: str
    raw_value: str
    woo_term: str
    woo_attr_name: Optional[str] = None   # persisted attribute-name override


class EnrichConfirmRequest(BaseModel):
    attrs: list[AttrConfirmEntry] = []
    new_norm_entries: list[NormEntry] = []


class GroupConfirmEntry(BaseModel):
    id: int
    confirmed: bool
    product_ids: Optional[list[int]] = None


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/pipelines/{id}/enrich-data
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipelines/{pipeline_id}/enrich-data")
async def get_enrich_data(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PipelineJob, pipeline_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Load extracted attrs for this pipeline
    attrs = (
        await db.execute(
            select(ProductEnrichAttr)
            .where(ProductEnrichAttr.pipeline_job_id == pipeline_id)
            .order_by(ProductEnrichAttr.product_id, ProductEnrichAttr.attribute)
        )
    ).scalars().all()

    # Load normalisation dict for this store
    norm_rows = (
        await db.execute(
            select(NormalisationDict).where(NormalisationDict.store_id == pl.store_id)
        )
    ).scalars().all()
    norm_lookup: dict[tuple, str] = {
        (r.attribute.lower(), r.raw_value.lower()): r.woo_term
        for r in norm_rows
    }
    attr_name_lookup: dict[str, str] = {}
    for r in norm_rows:
        if r.woo_attr_name and r.attribute.lower() not in attr_name_lookup:
            attr_name_lookup[r.attribute.lower()] = r.woo_attr_name

    # Load AI extraction rule confidence thresholds (keyed by woo_attr_name.lower())
    rule_rows = (
        await db.execute(select(AIExtractionRule))
    ).scalars().all()
    threshold_by_attr: dict[str, float] = {
        r.woo_attr_name.lower(): r.confidence_threshold for r in rule_rows
    }
    DEFAULT_THRESHOLD = 0.7

    # Load synced WooCommerce attributes + terms for this store
    woo_attr_rows = (
        await db.execute(
            select(WooAttribute)
            .options(selectinload(WooAttribute.terms))
            .where(WooAttribute.store_id == pl.store_id)
        )
    ).scalars().all()
    # Build lookup: name.lower() → list[term_name]
    woo_terms_by_name: dict[str, list[str]] = {
        wa.name.lower(): [t.name for t in wa.terms]
        for wa in woo_attr_rows
    }
    woo_terms_by_slug: dict[str, list[str]] = {
        wa.slug.lower(): [t.name for t in wa.terms]
        for wa in woo_attr_rows
    }

    def _attr_status(a: ProductEnrichAttr, threshold: float) -> str:
        if a.confirmed:
            return "resolved"
        src = (getattr(a, "source", None) or "ai")
        if src == "default":
            return "resolved"
        flagged = getattr(a, "flagged", False) or False
        if flagged:
            return "unset"
        if a.confidence is None:
            return "resolved"  # rule-based extraction — trusted
        if a.confidence >= threshold:
            return "resolved"
        return "low_confidence"

    def _woo_terms_for_attr(a: ProductEnrichAttr) -> list[str]:
        name_key = (a.woo_attr_name or a.attribute or "").lower()
        return woo_terms_by_name.get(name_key) or woo_terms_by_slug.get(name_key) or []

    # Load product names
    product_ids = list({a.product_id for a in attrs})
    products: dict[int, Product] = {}
    if product_ids:
        rows = (await db.execute(
            select(Product).where(Product.id.in_(product_ids))
        )).scalars().all()
        products = {p.id: p for p in rows}

    # Build per-product attr list with status + woo_terms
    by_product: dict[int, list] = {}
    stats_total = 0
    stats_from_ai = 0
    stats_from_defaults = 0
    stats_need_review = 0

    for a in attrs:
        threshold = threshold_by_attr.get(
            (a.woo_attr_name or a.attribute or "").lower(), DEFAULT_THRESHOLD
        )
        status = _attr_status(a, threshold)
        woo_terms = _woo_terms_for_attr(a)

        stats_total += 1
        src = (getattr(a, "source", None) or "ai")
        if status == "resolved":
            if src == "default":
                stats_from_defaults += 1
            elif src == "ai":
                stats_from_ai += 1
        else:
            stats_need_review += 1

        entry = {
            "id":                    a.id,
            "attribute":             a.attribute,
            "raw_value":             a.raw_value,
            "normalised_value":      a.normalised_value,
            "woo_attr_name":         a.woo_attr_name,
            "confidence":            a.confidence,
            "confirmed":             a.confirmed,
            "source":                src,
            "flagged":               getattr(a, "flagged", False) or False,
            "status":                status,
            "ai_suggestion":         a.raw_value if status == "low_confidence" else None,
            "woo_terms":             woo_terms,
            "norm_suggestion":       norm_lookup.get((a.attribute.lower(), a.raw_value.lower())),
            "woo_attr_name_suggest": attr_name_lookup.get(a.attribute.lower()),
        }
        by_product.setdefault(a.product_id, []).append(entry)

    result = []
    for pid, attr_list in by_product.items():
        p = products.get(pid)
        result.append({
            "product_id":   pid,
            "product_name": p.name if p else f"#{pid}",
            "product_sku":  (p.site_sku or p.sku) if p else "",
            "attrs":        attr_list,
        })

    return {
        "pipeline_id":    pipeline_id,
        "status":         pl.status,
        "products":       result,
        "total_products": len(result),
        "stats": {
            "total":         stats_total,
            "from_ai":       stats_from_ai,
            "from_defaults": stats_from_defaults,
            "need_review":   stats_need_review,
        },
        "norm_dict_size": len(norm_rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/pipelines/{id}/enrich-confirm
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipelines/{pipeline_id}/enrich-confirm")
async def enrich_confirm(
    pipeline_id: int,
    req: EnrichConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    pl = await db.get(PipelineJob, pipeline_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pl.status != "enrich_review":
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline is not in enrich_review state (current: {pl.status})"
        )

    # Update confirmed attrs
    for entry in req.attrs:
        attr_row = (await db.execute(
            select(ProductEnrichAttr).where(
                ProductEnrichAttr.pipeline_job_id == pipeline_id,
                ProductEnrichAttr.product_id == entry.product_id,
                ProductEnrichAttr.attribute == entry.attribute,
            )
        )).scalar_one_or_none()
        if attr_row:
            attr_row.normalised_value = entry.normalised_value or attr_row.raw_value
            attr_row.confirmed = entry.confirmed
            if entry.woo_attr_name:
                attr_row.woo_attr_name = entry.woo_attr_name

    # Upsert new normalisation dict entries
    for ne in req.new_norm_entries:
        vals: dict = dict(
            store_id=pl.store_id,
            attribute=ne.attribute,
            raw_value=ne.raw_value,
            woo_term=ne.woo_term,
        )
        updates: dict = {"woo_term": ne.woo_term}
        if ne.woo_attr_name:
            vals["woo_attr_name"] = ne.woo_attr_name
            updates["woo_attr_name"] = ne.woo_attr_name
        stmt = (
            pg_insert(NormalisationDict)
            .values(**vals)
            .on_conflict_do_update(
                index_elements=["store_id", "attribute", "raw_value"],
                set_=updates,
            )
        )
        await db.execute(stmt)

    await db.commit()

    # Dispatch background task to continue pipeline (Generate → Review)
    from tasks.pipeline_tasks import _enrich_resume_pipeline
    from datetime import datetime, timezone
    pl.status = "running"
    pl.current_step = "generate"
    pl.updated_at = datetime.now(timezone.utc)
    await db.commit()
    asyncio.create_task(_enrich_resume_pipeline(pipeline_id))

    return {"ok": True, "pipeline_id": pipeline_id}


# ─────────────────────────────────────────────────────────────────────────────
# Variant groups
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipelines/{pipeline_id}/variant-groups")
async def get_variant_groups(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PipelineJob, pipeline_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    groups = (
        await db.execute(
            select(VariantGroup)
            .where(VariantGroup.pipeline_job_id == pipeline_id)
            .order_by(VariantGroup.id)
        )
    ).scalars().all()

    # Load product names for display
    all_pids = [pid for g in groups for pid in (g.product_ids or [])]
    products: dict[int, Product] = {}
    if all_pids:
        rows = (await db.execute(
            select(Product).where(Product.id.in_(all_pids))
        )).scalars().all()
        products = {p.id: p for p in rows}

    return {
        "pipeline_id": pipeline_id,
        "groups": [
            {
                "id":        g.id,
                "attribute": g.attribute,
                "confirmed": g.confirmed,
                "pattern":   g.pattern,
                "product_ids": g.product_ids or [],
                "products": [
                    {
                        "id":   pid,
                        "name": products[pid].name if pid in products else f"#{pid}",
                        "sku":  (products[pid].site_sku or products[pid].sku) if pid in products else "",
                    }
                    for pid in (g.product_ids or [])
                ],
            }
            for g in groups
        ],
    }


@router.post("/pipelines/{pipeline_id}/variant-groups/confirm")
async def confirm_variant_groups(
    pipeline_id: int,
    entries: list[GroupConfirmEntry],
    db: AsyncSession = Depends(get_db),
):
    for entry in entries:
        g = await db.get(VariantGroup, entry.id)
        if g and g.pipeline_job_id == pipeline_id:
            g.confirmed = entry.confirmed
            if entry.product_ids is not None:
                g.product_ids = entry.product_ids
    await db.commit()
    return {"ok": True, "updated": len(entries)}


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation dict — Settings page
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stores/{store_id}/normalisation-dict")
async def get_norm_dict(store_id: int, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(NormalisationDict)
            .where(NormalisationDict.store_id == store_id)
            .order_by(NormalisationDict.attribute, NormalisationDict.raw_value)
        )
    ).scalars().all()
    return {
        "store_id": store_id,
        "entries": [
            {
                "id":        r.id,
                "attribute": r.attribute,
                "raw_value": r.raw_value,
                "woo_term":  r.woo_term,
            }
            for r in rows
        ],
    }


@router.put("/stores/{store_id}/normalisation-dict")
async def update_norm_dict(
    store_id: int,
    entries: list[NormEntry],
    db: AsyncSession = Depends(get_db),
):
    for ne in entries:
        stmt = (
            pg_insert(NormalisationDict)
            .values(
                store_id=store_id,
                attribute=ne.attribute,
                raw_value=ne.raw_value,
                woo_term=ne.woo_term,
            )
            .on_conflict_do_update(
                index_elements=["store_id", "attribute", "raw_value"],
                set_={"woo_term": ne.woo_term},
            )
        )
        await db.execute(stmt)
    await db.commit()
    return {"ok": True, "saved": len(entries)}


@router.delete("/stores/{store_id}/normalisation-dict/{entry_id}")
async def delete_norm_entry(
    store_id: int,
    entry_id: int,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(NormalisationDict, entry_id)
    if not row or row.store_id != store_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
