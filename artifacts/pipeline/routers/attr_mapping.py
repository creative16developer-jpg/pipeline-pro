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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
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
    # "and title contains" (client point 2): comma-separated words, any
    title_contains: Optional[str] = ""
    sort_order:     int = 0


def _tidy_title_words(v) -> str:
    """" Frame ,Cage ,, " -> "Frame, Cage" (same tidy-up as Category Mapping)."""
    return ", ".join(w.strip() for w in str(v or "").split(",") if w.strip())


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
    title_contains: str = ""
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
            title_contains=getattr(r, "title_contains", "") or "",
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


def _validate_rule(body: RuleIn) -> None:
    """A Fixed value rule with no value is skipped by Enrich
    (apply_mapping_rules: `if not rule["fixed_value"]: continue`), so it
    silently does nothing -- client screenshot showed exactly such a rule
    ("—" value). Reject it instead of storing it."""
    if not body.woo_attr_name.strip():
        raise HTTPException(400, "WooCommerce attribute name is required")
    if body.rule_type == "fixed_value" and not (body.fixed_value or "").strip():
        raise HTTPException(400, "A Fixed value rule needs at least one value")
    # An "If category" rule with no category never matches (enrich_service
    # returns False) -- a silently dead rule, same as an empty Fixed value.
    from services.enrich_service import condition_category_values
    if body.condition_type == "if_category" and not condition_category_values(body.condition_value):
        raise HTTPException(400, "An If category rule needs at least one category")


@router.post("/attr-mapping", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db)):
    _validate_rule(body)
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
        title_contains=_tidy_title_words(body.title_contains),
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
    _validate_rule(body)

    rule.store_id       = body.store_id
    rule.woo_attr_name  = body.woo_attr_name.strip()
    rule.rule_type      = body.rule_type
    rule.source_field   = body.source_field
    rule.fixed_value    = body.fixed_value
    rule.instruction    = body.instruction
    rule.condition_type = body.condition_type
    rule.condition_value= body.condition_value
    rule.title_contains = _tidy_title_words(body.title_contains)
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


EXPORT_COLUMNS = [
    "id", "store", "woo_attr_name", "rule_type", "source_field",
    "fixed_value", "instruction",
    "condition_type", "condition_value", "title_contains", "sort_order",
]
_RULE_TYPES = {"from_sunsky", "ai_extract", "fixed_value"}
_CONDITION_TYPES = {"always", "if_category"}


def parse_rule_import_rows(headers: list, rows: list, store_ids_by_name: dict,
                           default_store_id: Optional[int]) -> tuple[list[dict], list[dict]]:
    """Client request: "Don't see an option for import, but need have"
    (Attribute Mapping had Export CSV only). Turns a sheet (header row +
    data rows, same columns as the export) into rule dicts -- pure, so it
    can be tested without a database. Returns (rules, errors); errors are
    {"row": sheet_row_number, "error": text}.

    - Columns matched by name, case-insensitive; woo_attr_name required.
    - "store": store name (any case) -> that store; "" or "Global" ->
      global rule; column absent -> default_store_id (the store selected in
      the UI). Unknown store name -> error.
    - "id": existing rule to update (checked by the caller).
    - rule_type defaults to fixed_value, condition_type to always (or
      if_category when condition_value is filled).
    - condition_value: several categories one per line, or separated by
      "|" (easier to type in Excel). Commas are NOT separators -- real
      WooCommerce names contain them ("Маунтове, Монтажи, Стойки").
    - Same checks as the rule editor: Fixed value needs a value,
      If category needs a category.
    """
    idx = {str(h or "").strip().lower(): i for i, h in enumerate(headers)}
    if "woo_attr_name" not in idx:
        return [], [{"row": 1, "error": "Missing required column 'woo_attr_name'"}]

    def cell(row, name):
        i = idx.get(name)
        if i is None or i >= len(row) or row[i] is None:
            return ""
        v = row[i]
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()

    rules, errors = [], []
    for n, row in enumerate(rows, start=2):
        if not any(str(c or "").strip() for c in row):
            continue
        name = cell(row, "woo_attr_name")
        if not name:
            errors.append({"row": n, "error": "woo_attr_name is empty"})
            continue
        rt = (cell(row, "rule_type") or "fixed_value").lower()
        if rt not in _RULE_TYPES:
            errors.append({"row": n, "error": f"Unknown rule_type {rt!r} (use from_sunsky, ai_extract or fixed_value)"})
            continue
        cond_raw = cell(row, "condition_value").replace("|", "\n")
        cond_vals = [v.strip() for v in cond_raw.split("\n") if v.strip()]
        ct = (cell(row, "condition_type") or ("if_category" if cond_vals else "always")).lower()
        if ct not in _CONDITION_TYPES:
            errors.append({"row": n, "error": f"Unknown condition_type {ct!r} (use always or if_category)"})
            continue
        if "store" in idx:
            sname = cell(row, "store")
            if not sname or sname.lower() == "global":
                store_id = None
            elif sname.lower() in store_ids_by_name:
                store_id = store_ids_by_name[sname.lower()]
            else:
                errors.append({"row": n, "error": f"Unknown store {sname!r}"})
                continue
        else:
            store_id = default_store_id
        fixed = cell(row, "fixed_value")
        if rt == "fixed_value" and not fixed:
            errors.append({"row": n, "error": "A Fixed value rule needs at least one value"})
            continue
        if ct == "if_category" and not cond_vals:
            errors.append({"row": n, "error": "An If category rule needs at least one category"})
            continue
        rid = cell(row, "id")
        so = cell(row, "sort_order")
        try:
            rid_i = int(rid) if rid else None
            so_i = int(so) if so else None
        except ValueError:
            errors.append({"row": n, "error": "id and sort_order must be whole numbers"})
            continue
        rules.append({
            "row": n, "id": rid_i, "store_id": store_id, "store_from_file": "store" in idx,
            "woo_attr_name": name, "rule_type": rt,
            "source_field": cell(row, "source_field") or None,
            "fixed_value": fixed or None,
            "instruction": cell(row, "instruction") or None,
            "condition_type": ct,
            "condition_value": "\n".join(cond_vals) if ct == "if_category" else None,
            "title_contains": _tidy_title_words(cell(row, "title_contains")),
            "sort_order": so_i,
        })
    return rules, errors


def _rule_signature(d) -> tuple:
    g = (lambda k: d.get(k)) if isinstance(d, dict) else (lambda k: getattr(d, k))
    return (
        g("store_id"), (g("woo_attr_name") or "").strip().lower(), g("rule_type"),
        (g("source_field") or "").strip(), (g("fixed_value") or "").strip(), (g("instruction") or "").strip(),
        g("condition_type"), (g("condition_value") or "").strip().lower() if g("condition_type") == "if_category" else "",
        (g("title_contains") or "").strip().lower(),
    )


@router.post("/attr-mapping/import")
async def import_rules(
    file: UploadFile = File(...),
    store_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Import Attribute Mapping rules from CSV or Excel -- the export's
    format. ALL OR NOTHING: every row is checked first; if any row is
    invalid nothing is saved and the errors are returned (400). Rows with
    an existing id update that rule; other rows are created, except rows
    identical to an existing rule (same store, attribute, type, value and
    condition), which are skipped -- re-importing a file creates no
    duplicates. store_id = the store selected in the UI, used only when
    the file has no "store" column."""
    from models.models import Store
    content = await file.read()
    fname = (file.filename or "").lower()
    try:
        if fname.endswith(".xlsx"):
            import openpyxl
            ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
            all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
        elif fname.endswith(".csv"):
            all_rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        else:
            raise HTTPException(400, "Please upload a .csv or .xlsx file")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not read the file: {exc}")
    if not all_rows:
        raise HTTPException(400, "The file is empty")

    stores = (await db.execute(select(Store))).scalars().all()
    by_name = {st.name.strip().lower(): st.id for st in stores}
    rules, errors = parse_rule_import_rows(all_rows[0], all_rows[1:], by_name, store_id)

    existing = (await db.execute(select(AttributeMappingRule))).scalars().all()
    by_id = {r.id: r for r in existing}
    for r in rules:
        if r["id"] is not None and r["id"] not in by_id:
            errors.append({"row": r["row"], "error": f"Rule id {r['id']} does not exist (leave id empty to create a new rule)"})
    if errors:
        raise HTTPException(400, {"message": "Nothing was imported -- fix these rows and try again", "errors": sorted(errors, key=lambda e: e["row"])})

    sigs = {_rule_signature(r) for r in existing}
    # Files WITHOUT a "store" column (e.g. exports made before it existed)
    # carry no scope: a row identical to an existing rule in ANY store or
    # global counts as existing -- otherwise re-importing such a file would
    # copy every store rule as a new rule for the selected scope.
    sigs_any_store = {_rule_signature(r)[1:] for r in existing}
    created = updated = skipped = 0
    now = datetime.now(timezone.utc)
    for r in rules:
        fields = {k: r[k] for k in ("store_id", "woo_attr_name", "rule_type", "source_field", "fixed_value",
                                    "instruction", "condition_type", "condition_value", "title_contains")}
        if r["id"] is not None:
            row = by_id[r["id"]]
            for k, v in fields.items():
                setattr(row, k, v)
            if r["sort_order"] is not None:
                row.sort_order = r["sort_order"]
            row.updated_at = now
            updated += 1
            continue
        if _rule_signature(fields) in sigs or (
            not r["store_from_file"] and _rule_signature(fields)[1:] in sigs_any_store
        ):
            skipped += 1
            continue
        sort_order = r["sort_order"]
        if sort_order is None:
            same = [x.sort_order for x in existing if x.woo_attr_name.strip().lower() == fields["woo_attr_name"].lower()]
            sort_order = (max(same) + 10) if same else 0
        new = AttributeMappingRule(**fields, sort_order=sort_order)
        db.add(new)
        existing.append(new)
        sigs.add(_rule_signature(fields))
        sigs_any_store.add(_rule_signature(fields)[1:])
        created += 1
    await db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


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
    from models.models import Store as _ExStore
    store_names = {st.id: st.name for st in (await db.execute(select(_ExStore))).scalars().all()}

    buf = io.StringIO()
    # "id" and "store" added so an exported file can be edited and imported
    # back (POST /attr-mapping/import): id -> update that rule, store name ->
    # scope ("" = Global). Older exports without them still import.
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "id":             r.id,
            "store":          store_names.get(r.store_id, "") if r.store_id is not None else "",
            "woo_attr_name":  r.woo_attr_name,
            "rule_type":      r.rule_type,
            "source_field":   r.source_field or "",
            "fixed_value":    r.fixed_value or "",
            "instruction":    r.instruction or "",
            "condition_type": r.condition_type,
            "condition_value":r.condition_value or "",
            "title_contains": getattr(r, "title_contains", "") or "",
            "sort_order":     r.sort_order,
        })

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=attribute_mapping_rules.csv"},
    )


@router.get("/attr-mapping/attribute-terms")
async def get_attribute_terms_for_picker(
    store_id: Optional[int] = Query(None),
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

    async def _terms_for_store(store):
        woo_attrs = await get_all_woo_attributes(store)
        target = attribute_name.strip().lower()
        matched = next((a for a in woo_attrs if str(a.get("name", "")).strip().lower() == target), None)
        if not matched:
            return None, []
        return matched["id"], await get_attribute_terms(store, matched["id"])

    if store_id is not None:
        store = await db.get(Store, store_id)
        if not store:
            raise HTTPException(404, "Store not found")
        try:
            attr_id, terms = await _terms_for_store(store)
        except Exception as e:
            raise HTTPException(502, f"Could not load terms for attribute {attribute_name!r}: {e}")
        if attr_id is None:
            return {"attribute_found": False, "terms": []}
        return {
            "attribute_found": True,
            "attribute_id": attr_id,
            "terms": [{"id": t["id"], "name": t["name"]} for t in terms],
        }

    # No store = a GLOBAL rule. Client screenshot: a global "Тип продукт"
    # rule's Fixed value picker said "No existing values yet" -- the
    # frontend skipped this call without a store, and this endpoint
    # required one. Merge every store's terms (same name in any case listed
    # once); a store that can't be reached is skipped, not fatal.
    stores = (await db.execute(select(Store))).scalars().all()
    merged: dict[str, dict] = {}
    found = False
    for st in stores:
        try:
            attr_id, terms = await _terms_for_store(st)
        except Exception:
            continue
        if attr_id is None:
            continue
        found = True
        for t in terms:
            key = str(t.get("name", "")).strip().lower()
            if key and key not in merged:
                merged[key] = {"id": t["id"], "name": t["name"]}
    return {
        "attribute_found": found,
        "terms": sorted(merged.values(), key=lambda t: t["name"].lower()),
    }
