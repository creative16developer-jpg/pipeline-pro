"""
Enrich service — AI-assisted attribute extraction and variant grouping.

Provides two public async functions:
  extract_attributes(product, gen_cfg) → list[AttrResult]
  suggest_variant_groups(products, gen_cfg) → list[GroupSuggestion]

Falls back to rule-based paramsTable parsing when no AI provider is configured.
"""
from __future__ import annotations

import json
import re
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Types (plain dicts — no pydantic to avoid import cycles)
# ─────────────────────────────────────────────────────────────────────────────

# {attribute: str, raw_value: str, confidence: float}
AttrResult = dict
# {attribute: str, product_ids: list[int], pattern: str|None, confidence: float}
GroupSuggestion = dict

_KNOWN_ATTRS = [
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
        results.append({"attribute": k, "raw_value": v, "confidence": 0.75})
    return results


def _build_extract_prompt(product: dict) -> str:
    raw = product.get("raw_data") or product
    name = product.get("name", "")
    params = _parse_params_table(raw.get("paramsTable", ""))
    specs_text = "\n".join(f"  {k}: {v}" for k, v in list(params.items())[:20]) or "  (none)"
    hint = ", ".join(_KNOWN_ATTRS)
    return (
        f"Extract product attributes from the title and spec table below.\n"
        f"Focus on: {hint}.\n"
        f"Return a JSON array. Each element: {{\"attribute\": \"Color\", \"raw_value\": \"Black\", \"confidence\": 0.92}}\n"
        f"confidence is 0.0–1.0 (your certainty the extraction is correct).\n"
        f"Only return the JSON array — no explanation.\n\n"
        f"Title: {name}\n"
        f"Specs:\n{specs_text}"
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

async def extract_attributes(product: dict, gen_cfg: dict) -> list[AttrResult]:
    """
    Extract attributes from a single product.
    Returns list of AttrResult dicts sorted by confidence desc.
    """
    prompt = _build_extract_prompt(product)
    raw = await _call_ai(prompt, gen_cfg)
    parsed = _parse_json_array(raw)

    if parsed:
        results = []
        for item in parsed:
            if isinstance(item, dict) and item.get("attribute") and item.get("raw_value"):
                results.append({
                    "attribute": str(item["attribute"]).strip(),
                    "raw_value": str(item["raw_value"]).strip(),
                    "confidence": float(item.get("confidence", 0.7)),
                })
        if results:
            return sorted(results, key=lambda x: -x["confidence"])

    return _rule_based_extract(product)


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
