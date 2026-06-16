"""
AI Extraction Rules router — /api/attr-rules

CRUD for AIExtractionRule rows.  Each rule controls how AI extracts one
WooCommerce attribute from a product title / spec table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
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


class RuleOut(BaseModel):
    id:                   int
    woo_attr_name:        str
    source_fields:        str
    instruction:          str
    confidence_threshold: float
    if_not_found:         str
    default_value:        Optional[str]
    sort_order:           int
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
            created_at=r.created_at.isoformat() if r.created_at else "",
            updated_at=r.updated_at.isoformat() if r.updated_at else "",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/attr-rules")
async def list_rules(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(AIExtractionRule).order_by(AIExtractionRule.sort_order, AIExtractionRule.woo_attr_name))
    ).scalars().all()
    return {"rules": [RuleOut.from_orm(r) for r in rows]}


@router.post("/attr-rules", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(
            select(AIExtractionRule).where(AIExtractionRule.woo_attr_name == body.woo_attr_name)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(400, f"Rule for '{body.woo_attr_name}' already exists")

    rule = AIExtractionRule(
        woo_attr_name=body.woo_attr_name.strip(),
        source_fields=body.source_fields,
        instruction=body.instruction,
        confidence_threshold=body.confidence_threshold,
        if_not_found=body.if_not_found,
        default_value=body.default_value,
        sort_order=body.sort_order,
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

    # Check unique name if changing
    if rule.woo_attr_name != body.woo_attr_name.strip():
        clash = (
            await db.execute(
                select(AIExtractionRule).where(AIExtractionRule.woo_attr_name == body.woo_attr_name.strip())
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(400, f"Rule for '{body.woo_attr_name}' already exists")

    rule.woo_attr_name        = body.woo_attr_name.strip()
    rule.source_fields        = body.source_fields
    rule.instruction          = body.instruction
    rule.confidence_threshold = body.confidence_threshold
    rule.if_not_found         = body.if_not_found
    rule.default_value        = body.default_value
    rule.sort_order           = body.sort_order
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
