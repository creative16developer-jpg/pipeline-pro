"""
Attribute Mapping Rules router — /api/attr-mapping

CRUD for AttributeMappingRule rows.
Each rule defines how one WooCommerce attribute is derived from Sunsky data.
rule_type: "from_sunsky" | "ai_extract" | "fixed_value"
condition_type: "always" | "if_category"
store_id = None → global (applies to all stores)
"""
from __future__ import annotations

import io
import csv
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.models import AttributeMappingRule

router = APIRouter(tags=["attr-mapping"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class RuleIn(BaseModel):
    store_id:       Optional[int] = None
    woo_attr_name:  str
    rule_type:      str = "fixed_value"
    source_field:   Optional[str] = None
    fixed_value:    Optional[str] = None
    instruction:    Optional[str] = None
    condition_type: str = "always"
    condition_value: Optional[str] = None
    sort_order:     int = 0


class RuleOut(BaseModel):
    id:             int
    store_id:       Optional[int]
    woo_attr_name:  str
    rule_type:      str
    source_field:   Optional[str]
    fixed_value:    Optional[str]
    instruction:    Optional[str]
    condition_type: str
    condition_value: Optional[str]
    sort_order:     int
    created_at:     str
    updated_at:     str

    @classmethod
    def from_orm(cls, r: AttributeMappingRule) -> "RuleOut":
        return cls(
            id=r.id,
            store_id=r.store_id,
            woo_attr_name=r.woo_attr_name,
            rule_type=r.rule_type,
            source_field=r.source_field,
            fixed_value=r.fixed_value,
            instruction=r.instruction,
            condition_type=r.condition_type,
            condition_value=r.condition_value,
            sort_order=r.sort_order,
            created_at=r.created_at.isoformat() if r.created_at else "",
            updated_at=r.updated_at.isoformat() if r.updated_at else "",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/attr-mapping")
async def list_rules(
    store_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(AttributeMappingRule).order_by(
        AttributeMappingRule.sort_order, AttributeMappingRule.woo_attr_name
    )
    if store_id is not None:
        q = q.where(
            or_(
                AttributeMappingRule.store_id == store_id,
                AttributeMappingRule.store_id.is_(None),
            )
        )
    rows = (await db.execute(q)).scalars().all()
    return {"rules": [RuleOut.from_orm(r) for r in rows]}


@router.post("/attr-mapping", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db)):
    # A new rule for an attribute that already has rules goes LAST in that
    # attribute's priority order (first match wins), rather than jumping
    # ahead of rules the operator has already ordered. Only when the caller
    # didn't ask for a specific position (sort_order 0 = default).
    sort_order = body.sort_order
    if not sort_order:
        from sqlalchemy import func as _f
        _max = (await db.execute(
            select(_f.max(AttributeMappingRule.sort_order)).where(
                _f.lower(_f.trim(AttributeMappingRule.woo_attr_name)) == body.woo_attr_name.strip().lower()
            )
        )).scalar()
        if _max is not None:
            sort_order = _max + 10
    rule = AttributeMappingRule(
        store_id=body.store_id,
        woo_attr_name=body.woo_attr_name.strip(),
        rule_type=body.rule_type,
        source_field=body.source_field,
        fixed_value=body.fixed_value,
        instruction=body.instruction,
        condition_type=body.condition_type,
        condition_value=body.condition_value,
        sort_order=sort_order,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return RuleOut.from_orm(rule)


@router.put("/attr-mapping/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AttributeMappingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")

    rule.store_id       = body.store_id
    rule.woo_attr_name  = body.woo_attr_name.strip()
    rule.rule_type      = body.rule_type
    rule.source_field   = body.source_field
    rule.fixed_value    = body.fixed_value
    rule.instruction    = body.instruction
    rule.condition_type = body.condition_type
    rule.condition_value= body.condition_value
    rule.sort_order     = body.sort_order
    rule.updated_at     = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(rule)
    return RuleOut.from_orm(rule)


class ReorderIn(BaseModel):
    rule_ids: list[int]


@router.post("/attr-mapping/reorder")
async def reorder_rules(body: ReorderIn, db: AsyncSession = Depends(get_db)):
    """Set the priority order of an attribute's rules: the given ids get
    sort_order 10, 20, 30, ... in that order. Enrich evaluates rules in
    sort_order, id order and the first matching rule per attribute wins,
    so this is the order shown (and edited) in the attribute's screen.
    All ids must exist and belong to the same attribute."""
    if not body.rule_ids or len(set(body.rule_ids)) != len(body.rule_ids):
        raise HTTPException(400, "rule_ids must be a non-empty list without duplicates")
    rows = (await db.execute(
        select(AttributeMappingRule).where(AttributeMappingRule.id.in_(body.rule_ids))
    )).scalars().all()
    by_id = {r.id: r for r in rows}
    missing = [i for i in body.rule_ids if i not in by_id]
    if missing:
        raise HTTPException(404, f"Rules not found: {missing}")
    if len({r.woo_attr_name.strip().lower() for r in rows}) != 1:
        raise HTTPException(400, "All rules must belong to the same attribute")
    now = datetime.now(timezone.utc)
    for i, rid in enumerate(body.rule_ids):
        by_id[rid].sort_order = (i + 1) * 10
        by_id[rid].updated_at = now
    await db.commit()
    return {"rules": [RuleOut.from_orm(by_id[i]) for i in body.rule_ids]}


@router.delete("/attr-mapping/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AttributeMappingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    await db.delete(rule)
    await db.commit()


@router.get("/attr-mapping/export-csv")
async def export_csv(
    store_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(AttributeMappingRule).order_by(
        AttributeMappingRule.sort_order, AttributeMappingRule.woo_attr_name
    )
    if store_id is not None:
        q = q.where(
            or_(
                AttributeMappingRule.store_id == store_id,
                AttributeMappingRule.store_id.is_(None),
            )
        )
    rows = (await db.execute(q)).scalars().all()

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "woo_attr_name", "rule_type", "source_field",
        "fixed_value", "instruction",
        "condition_type", "condition_value", "sort_order",
    ])
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "woo_attr_name":  r.woo_attr_name,
            "rule_type":      r.rule_type,
            "source_field":   r.source_field or "",
            "fixed_value":    r.fixed_value or "",
            "instruction":    r.instruction or "",
            "condition_type": r.condition_type,
            "condition_value":r.condition_value or "",
            "sort_order":     r.sort_order,
        })

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=attribute_mapping_rules.csv"},
    )


@router.get("/attr-mapping/attribute-terms")
async def get_attribute_terms_for_picker(
    store_id: int = Query(...),
    attribute_name: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Client feedback confirmed live via screenshot: "if I write делти
    instead of делта for example this will override wrong value and
    in other hand I don't know all values. Which mean I need to copy
    paste from Woo to Pipeline... Once select attribute click on the
    value field and you can see only values for the following
    attribute." Explicit reference to the existing Category Mapping
    picker (a checkbox tree of REAL, existing WooCommerce categories)
    as the exact UI pattern wanted here instead of a free-text Fixed
    Value box, which risks a typo silently creating a new, wrong,
    never-matching WooCommerce term rather than reusing an existing
    one -- confirmed as a genuine, real risk, not a hypothetical one.

    Returns the REAL terms already defined for this specific
    WooCommerce attribute (by name, case-insensitive), so the
    frontend's picker can offer only genuine, existing options --
    matching get_attribute_terms, the exact same WooCommerce API
    function job_tasks.py's own Upload/Sync attribute-assignment code
    already relies on for this identical lookup, reused here rather
    than reimplemented separately so the two can never drift apart.
    """
    from models.models import Store
    from pipeline.woo_client import get_all_woo_attributes, get_attribute_terms

    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")

    try:
        woo_attrs = await get_all_woo_attributes(store)
    except Exception as e:
        raise HTTPException(502, f"Could not load WooCommerce attributes: {e}")

    target = attribute_name.strip().lower()
    matched = next((a for a in woo_attrs if str(a.get("name", "")).strip().lower() == target), None)
    if not matched:
        # Not an error -- a genuinely new attribute (not yet created in
        # WooCommerce at all) simply has no terms to offer yet. The
        # frontend can fall back to free-text entry in this case.
        return {"attribute_found": False, "terms": []}

    try:
        terms = await get_attribute_terms(store, matched["id"])
    except Exception as e:
        raise HTTPException(502, f"Could not load terms for attribute {attribute_name!r}: {e}")

    return {
        "attribute_found": True,
        "attribute_id": matched["id"],
        "terms": [{"id": t["id"], "name": t["name"]} for t in terms],
    }
