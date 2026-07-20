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
    rule = AttributeMappingRule(
        store_id=body.store_id,
        woo_attr_name=body.woo_attr_name.strip(),
        rule_type=body.rule_type,
        source_field=body.source_field,
        fixed_value=body.fixed_value,
        instruction=body.instruction,
        condition_type=body.condition_type,
        condition_value=body.condition_value,
        sort_order=body.sort_order,
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
