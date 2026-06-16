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
) -> list[AttrResult]:
    """
    Extract attributes from a single product.
    Uses DB-driven AIExtractionRule rows when available.
    Returns list of AttrResult dicts sorted by confidence desc.
    """
    rules = await _load_rules(db)
    prompt = _build_extract_prompt(product, rules)
    raw = await _call_ai(prompt, gen_cfg)
    parsed = _parse_json_array(raw)

    if parsed:
        # Build a fast lookup from woo_attr_name → rule
        rule_map = {r["woo_attr_name"].lower(): r for r in rules}

        results: list[AttrResult] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            attr = str(item.get("attribute", "")).strip()
            val  = str(item.get("raw_value", "")).strip()
            if not attr or not val:
                continue

            conf   = float(item.get("confidence", 0.7))
            rule   = rule_map.get(attr.lower())
            thresh = rule["confidence_threshold"] if rule else 0.7
            flagged = conf < thresh

            results.append({
                "attribute":  attr,
                "raw_value":  val,
                "confidence": conf,
                "source":     "ai",
                "flagged":    flagged,
            })

        # Apply if_not_found rules for attributes that the AI skipped
        if rules:
            found_lower = {r["attribute"].lower() for r in results}
            for rule in rules:
                if rule["woo_attr_name"].lower() not in found_lower:
                    action = rule["if_not_found"]
                    if action == "leave_blank":
                        pass
                    elif action == "use_default" and rule["default_value"]:
                        results.append({
                            "attribute":  rule["woo_attr_name"],
                            "raw_value":  rule["default_value"],
                            "confidence": 1.0,
                            "source":     "default",
                            "flagged":    False,
                        })
                    elif action == "flag":
                        results.append({
                            "attribute":  rule["woo_attr_name"],
                            "raw_value":  "",
                            "confidence": 0.0,
                            "source":     "ai",
                            "flagged":    True,
                        })

        if results:
            return sorted(results, key=lambda x: -x["confidence"])

    fallback = _rule_based_extract(product)

    # Apply flags to fallback results using rules
    if rules:
        rule_map = {r["woo_attr_name"].lower(): r for r in rules}
        for item in fallback:
            rule = rule_map.get(item["attribute"].lower())
            if rule:
                item["flagged"] = item["confidence"] < rule["confidence_threshold"]

    return fallback


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
