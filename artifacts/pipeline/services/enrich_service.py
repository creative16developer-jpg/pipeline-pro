"""
Enrich service — AI-assisted attribute extraction and variant grouping.

Provides two public async functions:
  extract_attributes(product, gen_cfg, db) → list[AttrResult]
  suggest_variant_groups(products, gen_cfg) → list[GroupSuggestion]

When AIExtractionRule rows exist in the DB they control:
  - which attributes to extract
  - what natural-language instruction guides the AI
  - which source fields to include (title / specs / both)
  - confidence threshold for flagging
  - what to do when value is missing (leave_blank / flag / use_default)

Falls back to rule-based paramsTable parsing when no AI provider is configured.
"""
from __future__ import annotations

import json
import re
from typing import Optional, TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ─────────────────────────────────────────────────────────────────────────────
# Types (plain dicts — no pydantic to avoid import cycles)
# ─────────────────────────────────────────────────────────────────────────────

# {attribute: str, raw_value: str, confidence: float, source: str, flagged: bool}
AttrResult = dict
# {attribute: str, product_ids: list[int], pattern: str|None, confidence: float}
GroupSuggestion = dict

# Default fallback attribute list used when no DB rules are configured
_DEFAULT_ATTRS = [
    "Color", "Brand", "Compatible With", "Material",
    "Size", "Weight", "Connectivity", "Capacity",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_params_table(html: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for m in re.finditer(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", html, re.S):
        k = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        v = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if k and v:
            pairs[k] = v
    return pairs


def _rule_based_extract(product: dict) -> list[AttrResult]:
    """
    Parse paramsTable directly. Confidence 0.75 (medium — rule-based, no AI).
    """
    raw = product.get("raw_data") or product
    params = _parse_params_table(raw.get("paramsTable", ""))
    results: list[AttrResult] = []
    for k, v in params.items():
        if not k or not v or len(v) > 120:
            continue
        results.append({
            "attribute": k,
            "raw_value": v,
            "confidence": 0.75,
            "source": "rule_based",
            "flagged": False,
        })
    return results


async def _load_rules(db: Optional["AsyncSession"]) -> list[dict]:
    """Load AIExtractionRule rows from DB, sorted by sort_order."""
    if db is None:
        return []
    try:
        from sqlalchemy import select
        from models.models import AIExtractionRule
        rows = (
            await db.execute(
                select(AIExtractionRule).order_by(AIExtractionRule.sort_order, AIExtractionRule.woo_attr_name)
            )
        ).scalars().all()
        return [
            {
                "woo_attr_name":        r.woo_attr_name,
                "source_fields":        r.source_fields,
                "instruction":          r.instruction,
                "confidence_threshold": r.confidence_threshold,
                "if_not_found":         r.if_not_found,
                "default_value":        r.default_value,
            }
            for r in rows
        ]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Attribute Mapping Rules (Settings → Attribute Mapping) — Section 6 of the
# Developer Guidelines. Previously these rules were only ever read back by
# their own CRUD endpoint and had no effect on extraction; this wires them
# into the real Enrich step per the Section 6.2 priority rules:
#   Priority 1 — non-AI rule match (fixed_value / from_sunsky) — always wins,
#                regardless of AI confidence.
#   Priority 2 — AI extraction (this product's rule_type == "ai_extract" rows
#                are merged into the same AI call as AIExtractionRule entries).
#   Priority 3 (manual default, set on product detail page) and Priority 4
#   (left blank) are unaffected — handled elsewhere / not applicable here.
# ─────────────────────────────────────────────────────────────────────────────

async def _load_mapping_rules(db: Optional["AsyncSession"], store_id: Optional[int]) -> list[dict]:
    """Load AttributeMappingRule rows: global (store_id IS NULL) + this store's,
    sorted by sort_order so 'first matching rule wins' has a stable order."""
    if db is None:
        return []
    try:
        from sqlalchemy import select, or_
        from models.models import AttributeMappingRule
        q = select(AttributeMappingRule).order_by(
            AttributeMappingRule.sort_order, AttributeMappingRule.id
        )
        if store_id is not None:
            q = q.where(or_(
                AttributeMappingRule.store_id == store_id,
                AttributeMappingRule.store_id.is_(None),
            ))
        rows = (await db.execute(q)).scalars().all()
        return [
            {
                "woo_attr_name":   r.woo_attr_name,
                "rule_type":       r.rule_type,
                "source_field":    r.source_field,
                "fixed_value":     r.fixed_value,
                "instruction":     r.instruction,
                "condition_type":  r.condition_type,
                "condition_value": r.condition_value,
            }
            for r in rows
        ]
    except Exception:
        return []


def _rule_matches_product(rule: dict, sunsky_category: str) -> bool:
    if rule["condition_type"] == "always":
        return True
    if rule["condition_type"] == "if_category":
        cond = (rule.get("condition_value") or "").strip().lower()
        return bool(cond) and cond == (sunsky_category or "").strip().lower()
    # Any other condition_type isn't in the current schema (only "always" /
    # "if_category" are supported by the Attribute Mapping UI) — treat as
    # no-match rather than silently applying a rule outside its configured scope.
    return False


def _find_sunsky_value(product: dict, source_field: Optional[str]) -> str:
    """Look up a raw Sunsky field by name for a 'from_sunsky' rule — checks
    the parsed spec table first (case-insensitive), then a literal top-level
    raw_data field of the same name."""
    if not source_field:
        return ""
    raw = product.get("raw_data") or product
    params = _parse_params_table(raw.get("paramsTable", ""))
    if source_field in params:
        return params[source_field]
    needle = source_field.strip().lower()
    for k, v in params.items():
        if k.strip().lower() == needle:
            return v
    val = raw.get(source_field)
    return str(val) if val is not None else ""


def apply_mapping_rules(
    product: dict, rules: list[dict], sunsky_category: str
) -> tuple[list[AttrResult], list[dict]]:
    """
    Evaluate AttributeMappingRule rows against one product.

    Returns (resolved, ai_extract_rules):
      resolved         — Priority-1 non-AI results (fixed_value / from_sunsky).
                          These win regardless of AI confidence (Section 6.2).
      ai_extract_rules — matched rule_type == "ai_extract" rows, reshaped into
                          the same dict shape _load_rules() returns, so they
                          can be merged into the same AI extraction call.

    First matching rule (by sort_order, already applied by _load_mapping_rules)
    wins per attribute — later rules for an attribute already resolved are skipped.
    """
    resolved: list[AttrResult] = []
    ai_extract_rules: list[dict] = []
    seen_attrs: set[str] = set()

    for rule in rules:
        attr_key = rule["woo_attr_name"].strip().lower()
        if attr_key in seen_attrs:
            continue
        if not _rule_matches_product(rule, sunsky_category):
            continue

        if rule["rule_type"] == "fixed_value":
            if not rule["fixed_value"]:
                continue
            resolved.append({
                "attribute": rule["woo_attr_name"],
                "raw_value": rule["fixed_value"],
                "confidence": 1.0,
                "source": "mapping_rule",
                "flagged": False,
            })
            seen_attrs.add(attr_key)

        elif rule["rule_type"] == "from_sunsky":
            val = _find_sunsky_value(product, rule["source_field"])
            if not val:
                continue
            resolved.append({
                "attribute": rule["woo_attr_name"],
                "raw_value": val,
                "confidence": 1.0,
                "source": "mapping_rule",
                "flagged": False,
            })
            seen_attrs.add(attr_key)

        elif rule["rule_type"] == "ai_extract":
            ai_extract_rules.append({
                "woo_attr_name":        rule["woo_attr_name"],
                "source_fields":        "both",
                "instruction":          rule["instruction"] or "",
                "confidence_threshold": 0.7,
                "if_not_found":         "flag",
                "default_value":        None,
            })
            seen_attrs.add(attr_key)
        # "leave_empty" or any other future rule_type: intentionally no-op —
        # the attribute simply isn't resolved by this rule.

    return resolved, ai_extract_rules


def _build_extract_prompt(product: dict, rules: list[dict]) -> str:
    raw = product.get("raw_data") or product
    name = product.get("name", "")
    params = _parse_params_table(raw.get("paramsTable", ""))
    specs_text = "\n".join(f"  {k}: {v}" for k, v in list(params.items())[:20]) or "  (none)"

    if rules:
        attr_lines = []
        for r in rules:
            hint = ""
            if r["instruction"]:
                hint = f' — {r["instruction"]}'
            src = r["source_fields"]
            src_note = "" if src == "both" else f" [from {src} only]"
            attr_lines.append(f'  "{r["woo_attr_name"]}"{hint}{src_note}')
        attrs_block = "\n".join(attr_lines)
        attr_section = f"Extract ONLY these attributes:\n{attrs_block}"
    else:
        hint = ", ".join(_DEFAULT_ATTRS)
        attr_section = f"Focus on: {hint}."

    # Build source sections based on rules
    include_title = True
    include_specs = True
    if rules and all(r["source_fields"] == "specs" for r in rules):
        include_title = False
    if rules and all(r["source_fields"] == "title" for r in rules):
        include_specs = False

    source_block = ""
    if include_title:
        source_block += f"Title: {name}\n"
    if include_specs:
        source_block += f"Specs:\n{specs_text}"

    return (
        f"Extract product attributes from the product information below.\n"
        f"{attr_section}\n"
        f"Return a JSON array. Each element: {{\"attribute\": \"Color\", \"raw_value\": \"Black\", \"confidence\": 0.92}}\n"
        f"confidence is 0.0–1.0 (your certainty the extraction is correct).\n"
        f"Only return the JSON array — no explanation.\n\n"
        f"{source_block}"
    )


def _build_group_prompt(products: list[dict]) -> str:
    lines = []
    for p in products[:40]:
        lines.append(f"  id={p['id']} name={p.get('name','')!r}")
    product_list = "\n".join(lines)
    return (
        f"These products may be variants of the same base product (e.g. same case in different colors).\n"
        f"Suggest variant groups: products that should merge into one WooCommerce variable product.\n"
        f"Return a JSON array. Each element:\n"
        f"  {{\"attribute\": \"Color\", \"product_ids\": [1, 2, 3], \"pattern\": \"Case for {{Compatible With}}, {{Color}}\"}}\n"
        f"Only include groups with 2+ products. Ungrouped products are omitted.\n"
        f"Only return the JSON array — no explanation.\n\n"
        f"Products:\n{product_list}"
    )


async def _call_ai(prompt: str, gen_cfg: dict) -> Optional[str]:
    try:
        from pipeline.ai_generator import generate_with_ai, AIGenerationError
        gs = gen_cfg.get("globalSettings") or {}
        if not gs.get("ai_enabled", False):
            return None
        provider = gs.get("ai_provider", "openai")
        model = gs.get("ai_model") or None
        return await generate_with_ai("_raw", {}, provider, model, {"_prompt_override": prompt})
    except Exception:
        return None


def _parse_json_array(raw: Optional[str]) -> Optional[list]:
    if not raw:
        return None
    try:
        text = raw.strip()
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            return json.loads(m.group(0))
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def extract_attributes(
    product: dict,
    gen_cfg: dict,
    db: Optional["AsyncSession"] = None,
    store_id: Optional[int] = None,
    sunsky_category: Optional[str] = None,
) -> list[AttrResult]:
    """
    Extract attributes from a single product.

    Priority order (Developer Guidelines v2.0, Section 6.2):
      1. Non-AI Attribute Mapping rules (fixed_value / from_sunsky) — always
         win, regardless of AI confidence.
      2. AI extraction — using AIExtractionRule (Settings → Extraction Rules)
         merged with any rule_type == "ai_extract" Attribute Mapping rules
         that matched this product (the latter take precedence for the same
         attribute name, since they were configured for this specific
         category rather than as a store-wide default).
    Priority 3 (manual default) and 4 (left blank) aren't decided here.

    Returns list of AttrResult dicts sorted by confidence desc.
    """
    rules = await _load_rules(db)
    mapping_rules = await _load_mapping_rules(db, store_id)

    resolved, ai_extract_from_mapping = apply_mapping_rules(product, mapping_rules, sunsky_category or "")
    resolved_lower = {r["attribute"].strip().lower() for r in resolved}

    # Attribute Mapping's ai_extract rows override an Extraction Rules entry
    # for the same attribute name (more specific — it matched this product's
    # category); anything not overridden falls back to Extraction Rules.
    rule_map = {r["woo_attr_name"].strip().lower(): r for r in rules}
    for r in ai_extract_from_mapping:
        rule_map[r["woo_attr_name"].strip().lower()] = r
    # Never ask the AI for an attribute a non-AI rule already resolved —
    # guarantees Priority 1 can't be overridden regardless of AI confidence.
    active_rules = [r for k, r in rule_map.items() if k not in resolved_lower]

    prompt = _build_extract_prompt(product, active_rules)
    raw = await _call_ai(prompt, gen_cfg)
    parsed = _parse_json_array(raw)

    ai_results: list[AttrResult] = []
    if parsed:
        active_rule_map = {r["woo_attr_name"].lower(): r for r in active_rules}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            attr = str(item.get("attribute", "")).strip()
            val  = str(item.get("raw_value", "")).strip()
            if not attr or not val:
                continue
            if attr.strip().lower() in resolved_lower:
                continue  # Priority 1 already won this attribute

            conf   = float(item.get("confidence", 0.7))
            rule   = active_rule_map.get(attr.lower())
            thresh = rule["confidence_threshold"] if rule else 0.7
            flagged = conf < thresh

            ai_results.append({
                "attribute":  attr,
                "raw_value":  val,
                "confidence": conf,
                "source":     "ai",
                "flagged":    flagged,
            })

        # Apply if_not_found rules for attributes the AI skipped
        if active_rules:
            found_lower = {r["attribute"].lower() for r in ai_results} | resolved_lower
            for rule in active_rules:
                if rule["woo_attr_name"].lower() not in found_lower:
                    action = rule["if_not_found"]
                    if action == "leave_blank":
                        pass
                    elif action == "use_default" and rule["default_value"]:
                        ai_results.append({
                            "attribute":  rule["woo_attr_name"],
                            "raw_value":  rule["default_value"],
                            "confidence": 1.0,
                            "source":     "default",
                            "flagged":    False,
                        })
                    elif action == "flag":
                        ai_results.append({
                            "attribute":  rule["woo_attr_name"],
                            "raw_value":  "",
                            "confidence": 0.0,
                            "source":     "ai",
                            "flagged":    True,
                        })

        if not ai_results:
            # AI returned parsable JSON but nothing usable — fall back to
            # rule-based paramsTable parsing, same as the no-AI-response path.
            ai_results = [
                r for r in _rule_based_extract(product)
                if r["attribute"].strip().lower() not in resolved_lower
            ]
            if active_rules:
                active_rule_map = {r["woo_attr_name"].lower(): r for r in active_rules}
                for item in ai_results:
                    rule = active_rule_map.get(item["attribute"].lower())
                    if rule:
                        item["flagged"] = item["confidence"] < rule["confidence_threshold"]
    else:
        ai_results = [
            r for r in _rule_based_extract(product)
            if r["attribute"].strip().lower() not in resolved_lower
        ]
        if active_rules:
            active_rule_map = {r["woo_attr_name"].lower(): r for r in active_rules}
            for item in ai_results:
                rule = active_rule_map.get(item["attribute"].lower())
                if rule:
                    item["flagged"] = item["confidence"] < rule["confidence_threshold"]

    combined = resolved + ai_results
    return sorted(combined, key=lambda x: -x["confidence"])


def extract_sunsky_category(raw: dict, name_map: Optional[dict[str, str]] = None) -> str:
    """Best-effort extraction of the Sunsky category NAME from a product's
    raw_data — mirrors routers/map_step.py's _extract_sunsky_cat so category
    matching stays consistent between the Category Mapping/Map Step flow and
    Attribute Mapping/Profile lookups done here.

    Real Sunsky product responses only include a numeric categoryId, not a
    name field — confirmed against live data (2026-08-01). So when no name
    field is present, this resolves the ID through `name_map` (built from
    sunsky_client.get_category_name_map()) if one is provided. Falls back to
    returning the raw ID as a last resort (e.g. if the category tree fetch
    failed) — this keeps behavior no worse than before for edge cases, while
    fixing the common case where a name lookup succeeds.
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


async def load_profile_attrs_for_category(
    db: Optional["AsyncSession"], store_id: Optional[int], sunsky_category: str
) -> list[str]:
    """Return the woo_attr_name list for the AttributeProfile assigned (via
    Category Mapping / the Map Step) to this Sunsky category — Section 6.3.
    Returns [] if there's no saved mapping for this category, or the mapping
    has no profile assigned. Used to surface 'Panel B' unset-attribute rows
    per Attribute_mapping.docx: attributes the product's profile expects but
    that no rule or AI extraction produced a value for."""
    if db is None or not store_id or not sunsky_category:
        return []
    try:
        from sqlalchemy import select
        from models.models import SunskyCategoryMapping, ProfileAttribute
        mapping = (
            await db.execute(
                select(SunskyCategoryMapping).where(
                    SunskyCategoryMapping.store_id == store_id,
                    SunskyCategoryMapping.sunsky_cat == sunsky_category,
                )
            )
        ).scalar_one_or_none()
        if not mapping or not mapping.profile_id:
            return []
        rows = (
            await db.execute(
                select(ProfileAttribute).where(ProfileAttribute.profile_id == mapping.profile_id)
            )
        ).scalars().all()
        return [r.woo_attr_name for r in rows]
    except Exception:
        return []


async def suggest_variant_groups(products: list[dict], gen_cfg: dict) -> list[GroupSuggestion]:
    """
    Suggest variant groups across a batch of products.
    Returns list of GroupSuggestion dicts.
    """
    if len(products) < 2:
        return []

    prompt = _build_group_prompt(products)
    raw = await _call_ai(prompt, gen_cfg)
    parsed = _parse_json_array(raw)

    if parsed:
        results = []
        for item in parsed:
            if isinstance(item, dict) and item.get("product_ids"):
                ids = [int(x) for x in item["product_ids"] if str(x).isdigit()]
                if len(ids) >= 2:
                    results.append({
                        "attribute": str(item.get("attribute", "Variant")).strip(),
                        "product_ids": ids,
                        "pattern": item.get("pattern"),
                        "confidence": 0.8,
                    })
        return results

    return _rule_based_group(products)


def _rule_based_group(products: list[dict]) -> list[GroupSuggestion]:
    """
    Simple heuristic grouping: products whose titles differ only in a trailing
    parenthesised value or a trailing single word (assumed to be a colour/variant).
    """
    import re as _re

    def base_title(name: str) -> str:
        t = _re.sub(r"\s*\(.*?\)\s*$", "", name.strip())
        t = _re.sub(r"\s+\S+$", "", t.strip())
        return t.lower().strip()

    groups: dict[str, list[int]] = {}
    for p in products:
        bt = base_title(p.get("name", ""))
        if bt:
            groups.setdefault(bt, []).append(p["id"])

    return [
        {"attribute": "Variant", "product_ids": ids, "pattern": None, "confidence": 0.5}
        for ids in groups.values()
        if len(ids) >= 2
    ]
