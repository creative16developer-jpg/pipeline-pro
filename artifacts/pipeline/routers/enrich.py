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
from models.models import (
    PipelineJob, Product,
    ProductEnrichAttr, NormalisationDict, VariantGroup,
)

router = APIRouter(tags=["enrich"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class AttrConfirmEntry(BaseModel):
    product_id: int
    attribute: str
    normalised_value: Optional[str] = None
    confirmed: bool = True


class NormEntry(BaseModel):
    attribute: str
    raw_value: str
    woo_term: str


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

    # Load product names
    product_ids = list({a.product_id for a in attrs})
    products = {}
    if product_ids:
        rows = (await db.execute(
            select(Product).where(Product.id.in_(product_ids))
        )).scalars().all()
        products = {p.id: p for p in rows}

    # Build per-product attr list
    by_product: dict[int, list] = {}
    for a in attrs:
        entry = {
            "id":               a.id,
            "attribute":        a.attribute,
            "raw_value":        a.raw_value,
            "normalised_value": a.normalised_value,
            "confidence":       a.confidence,
            "confirmed":        a.confirmed,
            "norm_suggestion":  norm_lookup.get((a.attribute.lower(), a.raw_value.lower())),
        }
        by_product.setdefault(a.product_id, []).append(entry)

    result = []
    for pid, attr_list in by_product.items():
        p = products.get(pid)
        result.append({
            "product_id":   pid,
            "product_name": p.name if p else f"#{pid}",
            "product_sku":  p.site_sku or p.sku if p else "",
            "attrs":        attr_list,
        })

    return {
        "pipeline_id":   pipeline_id,
        "status":        pl.status,
        "products":      result,
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

    # Upsert new normalisation dict entries
    for ne in req.new_norm_entries:
        stmt = (
            pg_insert(NormalisationDict)
            .values(
                store_id=pl.store_id,
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
