"""
AI Extraction Rules router — /api/attr-rules

CRUD for AIExtractionRule rows.  Each rule controls how AI extracts one
WooCommerce attribute from a product title / spec table.

Client feedback confirmed live: "Extraction rules need to be individual
for each site / Right now they are same for each site." Rules are now
per-store, following the same optional-override pattern as the sibling
AttributeMappingRule model: store_id=None is a global rule (the
fallback, applied when no store-specific rule exists for that
attribute name); a specific store_id overrides the global rule for
just that store.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.models import AIExtractionRule

router = APIRouter(tags=["attr-rules"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class RuleIn(BaseModel):
    woo_attr_name:        str
    source_fields:        str = "both"       # "title" | "specs" | "both"
    instruction:          str = ""
    confidence_threshold: float = 0.7
    if_not_found:         str = "flag"       # "leave_blank" | "flag" | "use_default"
    default_value:        Optional[str] = None
    sort_order:           int = 0
    selector:             Optional[str] = None
    # None = global rule (applies to every store that has no override
    # of its own for this attribute name). A specific store_id creates
    # an override for just that store.
    store_id:             Optional[int] = None


class RuleOut(BaseModel):
    id:                   int
    woo_attr_name:        str
    source_fields:        str
    instruction:          str
    confidence_threshold: float
    if_not_found:         str
    default_value:        Optional[str]
    sort_order:           int
    selector:             Optional[str]
    store_id:             Optional[int]
    is_override:          bool
    # Client feedback confirmed live: "why global not showing." True
    # only on a GLOBAL row (store_id is None) when the store currently
    # being viewed has its OWN override for the same attribute name --
    # meaning this global row exists and is still returned, but isn't
    # the one actually applied for this store. Lets the frontend show
    # it visually de-emphasized with a note, instead of the previous
    # behavior of hiding it from the list entirely.
    is_overridden:        bool = False
    created_at:           str
    updated_at:           str

    @classmethod
    def from_orm(cls, r: AIExtractionRule) -> "RuleOut":
        return cls(
            id=r.id,
            woo_attr_name=r.woo_attr_name,
            source_fields=r.source_fields,
            instruction=r.instruction,
            confidence_threshold=r.confidence_threshold,
            if_not_found=r.if_not_found,
            default_value=r.default_value,
            sort_order=r.sort_order,
            selector=r.selector,
            store_id=r.store_id,
            is_override=r.store_id is not None,
            created_at=r.created_at.isoformat() if r.created_at else "",
            updated_at=r.updated_at.isoformat() if r.updated_at else "",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/attr-rules")
async def list_rules(
    store_id: Optional[int] = Query(None, description="Show this store's overrides plus the global fallback rules. Omit to see every rule (global admin view)."),
    db: AsyncSession = Depends(get_db),
):
    q = select(AIExtractionRule).order_by(AIExtractionRule.sort_order, AIExtractionRule.woo_attr_name)
    if store_id is not None:
        # Client feedback confirmed live: the Settings page's store
        # dropdown existed already but was purely decorative for this
        # list -- every store saw the identical, fully global rule set
        # regardless of which one was selected. Now genuinely filters:
        # this store's own overrides, plus any global (store_id IS
        # NULL) rule for an attribute name this store has NOT
        # overridden -- so the dropdown actually changes what's shown.
        from sqlalchemy import or_
        rows = (
            await db.execute(
                q.where(or_(AIExtractionRule.store_id == store_id, AIExtractionRule.store_id.is_(None)))
            )
        ).scalars().all()
        # Client feedback confirmed live: "I have saved 2 rules one
        # for global and one for per store then why global not
        # showing." The earlier version of this endpoint HID the
        # global rule entirely whenever a store-specific override
        # existed for the same attribute name, on the assumption that
        # only showing "what's effectively active" was clearer -- in
        # practice this meant an operator had no way to see or edit
        # the underlying global rule at all while an override was in
        # place, without switching to a different store first. Now
        # returns BOTH rows, with is_overridden marking the global one
        # as not currently the one that wins for this store (the
        # frontend can render that visually, e.g. dimmed / a small
        # "overridden by This store" note) rather than hiding it.
        active_names = {r.woo_attr_name for r in rows if r.store_id == store_id}
        rows = sorted(rows, key=lambda r: (r.sort_order, r.woo_attr_name, 0 if r.store_id == store_id else 1))
        results = []
        for r in rows:
            out = RuleOut.from_orm(r)
            out.is_overridden = (r.store_id is None and r.woo_attr_name in active_names)
            results.append(out)
        return {"rules": results}
    else:
        rows = (await db.execute(q)).scalars().all()
    return {"rules": [RuleOut.from_orm(r) for r in rows]}


@router.post("/attr-rules", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(
            select(AIExtractionRule).where(
                AIExtractionRule.woo_attr_name == body.woo_attr_name,
                AIExtractionRule.store_id == body.store_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        scope = f"store {body.store_id}" if body.store_id is not None else "the global default"
        raise HTTPException(400, f"A rule for '{body.woo_attr_name}' already exists for {scope}")

    rule = AIExtractionRule(
        woo_attr_name=body.woo_attr_name.strip(),
        source_fields=body.source_fields,
        instruction=body.instruction,
        confidence_threshold=body.confidence_threshold,
        if_not_found=body.if_not_found,
        default_value=body.default_value,
        sort_order=body.sort_order,
        selector=body.selector,
        store_id=body.store_id,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return RuleOut.from_orm(rule)


@router.put("/attr-rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AIExtractionRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")

    # Check the (store_id, woo_attr_name) pair for a clash if either changed
    if rule.woo_attr_name != body.woo_attr_name.strip() or rule.store_id != body.store_id:
        clash = (
            await db.execute(
                select(AIExtractionRule).where(
                    AIExtractionRule.woo_attr_name == body.woo_attr_name.strip(),
                    AIExtractionRule.store_id == body.store_id,
                    AIExtractionRule.id != rule_id,
                )
            )
        ).scalar_one_or_none()
        if clash:
            scope = f"store {body.store_id}" if body.store_id is not None else "the global default"
            raise HTTPException(400, f"A rule for '{body.woo_attr_name}' already exists for {scope}")

    rule.woo_attr_name        = body.woo_attr_name.strip()
    rule.source_fields        = body.source_fields
    rule.instruction          = body.instruction
    rule.confidence_threshold = body.confidence_threshold
    rule.if_not_found         = body.if_not_found
    rule.default_value        = body.default_value
    rule.sort_order           = body.sort_order
    rule.selector             = body.selector
    rule.store_id             = body.store_id
    rule.updated_at           = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(rule)
    return RuleOut.from_orm(rule)


@router.delete("/attr-rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AIExtractionRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    await db.delete(rule)
    await db.commit()
