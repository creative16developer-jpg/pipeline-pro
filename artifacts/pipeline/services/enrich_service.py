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


_SELECTOR_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9]*)(?:\.([\w-]+)|#([\w-]+))?$")


def _extract_by_selector(html: str, selector: str) -> Optional[str]:
    """
    Deterministic search-area extraction — a simple tag[.class|#id] selector
    (e.g. "h2.product-main-title"), matched against raw HTML with a plain
    regex rather than a real DOM parser (keeps this dependency-free; the
    supported syntax is intentionally narrow — one tag, optionally one class
    or id — enough for the common "pull the text out of this specific
    element" case the client asked for).
    """
    if not html or not selector:
        return None
    m = _SELECTOR_RE.match(selector.strip())
    if not m:
        return None
    tag, cls, id_ = m.group(1), m.group(2), m.group(3)
    lookahead = ""
    if cls:
        lookahead = rf'(?=[^>]*\bclass\s*=\s*"[^"]*\b{re.escape(cls)}\b[^"]*")'
    elif id_:
        lookahead = rf'(?=[^>]*\bid\s*=\s*"{re.escape(id_)}")'
    pattern = rf"<{re.escape(tag)}\b{lookahead}[^>]*>(.*?)</{re.escape(tag)}>"
    match = re.search(pattern, html, re.I | re.S)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


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


async def _load_rules(db: Optional["AsyncSession"], store_id: Optional[int] = None) -> list[dict]:
    """Load AIExtractionRule rows from DB, sorted by sort_order.

    Client feedback confirmed live: "Extraction rules need to be
    individual for each site / Right now they are same for each
    site." store_id now filters to this store's own override rules
    plus any global (store_id IS NULL) rule for an attribute name this
    store hasn't overridden -- same store-specific-wins-over-global
    pattern the sibling _load_mapping_rules already uses. store_id=None
    (the default) preserves the exact previous behavior: every rule,
    unfiltered -- used by any caller that genuinely wants the full
    admin view rather than one store's effective rule set.
    """
    if db is None:
        return []
    try:
        from sqlalchemy import select
        from models.models import AIExtractionRule
        q = select(AIExtractionRule).order_by(AIExtractionRule.sort_order, AIExtractionRule.woo_attr_name)
        if store_id is not None:
            from sqlalchemy import or_
            q = q.where(or_(AIExtractionRule.store_id == store_id, AIExtractionRule.store_id.is_(None)))
        rows = (await db.execute(q)).scalars().all()
        if store_id is not None:
            by_name: dict[str, "AIExtractionRule"] = {}
            for r in rows:
                existing = by_name.get(r.woo_attr_name)
                if existing is None or (r.store_id == store_id and existing.store_id is None):
                    by_name[r.woo_attr_name] = r
            rows = sorted(by_name.values(), key=lambda r: (r.sort_order, r.woo_attr_name))
        return [
            {
                "woo_attr_name":        r.woo_attr_name,
                "source_fields":        r.source_fields,
                "instruction":          r.instruction,
                "confidence_threshold": r.confidence_threshold,
                "if_not_found":         r.if_not_found,
                "default_value":        r.default_value,
                "selector":             r.selector,
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
    sorted by sort_order so 'first matching rule wins' has a stable order.

    BUG FIX (found during a systematic multi-store QA pass, reproduced
    directly with real data before fixing): sorting purely by
    (sort_order, id) meant that for the SAME woo_attr_name, whichever
    rule was CREATED FIRST won -- id is a creation-order artifact, not
    a meaningful priority signal. A very likely, realistic workflow
    (set up a global default first, then later add a store-specific
    override for one particular store) would have the global rule's
    lower id keep it winning forever, with the store-specific override
    silently never applying at all -- confirmed by reproducing exactly
    this scenario: a global rule created first, then a store-specific
    override for the same attribute created second, returned in the
    wrong order for the override to ever be seen. Fixed by sorting a
    THIS-STORE-specific match ahead of a global one whenever both
    target the same sort_order, without disturbing sort_order's own
    intended purpose of ordering genuinely different rules (e.g.
    different attribute names, or category-conditional variants).
    """
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
        if store_id is not None:
            rows = sorted(
                rows,
                key=lambda r: (r.sort_order, 0 if r.store_id == store_id else 1, r.id),
            )
        return [
            {
                "woo_attr_name":   r.woo_attr_name,
                "rule_type":       r.rule_type,
                "source_field":    r.source_field,
                "fixed_value":     r.fixed_value,
                "instruction":     r.instruction,
                "condition_type":  r.condition_type,
                "condition_value": r.condition_value,
                "title_contains":  getattr(r, "title_contains", "") or "",
            }
            for r in rows
        ]
    except Exception:
        return []


def condition_category_values(condition_value) -> list[str]:
    """If-category condition values: one category per line, trimmed,
    lower-cased, empty lines dropped."""
    return [v.strip().lower() for v in str(condition_value or "").split("\n") if v.strip()]


def _rule_matches_product(rule: dict, sunsky_category: str, resolved_woo_category: str = "") -> bool:
    if rule["condition_type"] == "always":
        print(f"[enrich_service] rule {rule.get('woo_attr_name')!r} matched via condition_type='always'")
        return True
    if rule["condition_type"] == "if_category":
        # Several categories allowed, ONE PER LINE (client request: "multiple
        # values for Sunsky categories under condition if category"). Not
        # comma-separated: real WooCommerce category names contain commas
        # ("Маунтове, Монтажи, Стойки"). A legacy single value is one line,
        # so existing rules match exactly as before. Any listed category
        # matching (exact, case-insensitive) counts.
        conds = condition_category_values(rule.get("condition_value"))
        cond = " | ".join(conds)
        if not cond:
            return False
        # Client feedback confirmed live via screenshot: a real
        # "Waterproof and Protective Cases" rule never matched a product
        # whose Sunsky breadcrumb was actually "Protection & Cases" --
        # traced to a fundamental vocabulary mismatch. The Attribute
        # Mapping condition dropdown (patches 39/59/68/69) sources
        # suggestions from this store's WooCommerce category names, but
        # this check only ever compared against the raw Sunsky category
        # string. These are two genuinely different vocabularies (the
        # client's own store category structure vs. Sunsky's internal
        # taxonomy) that don't match string-for-string even when they're
        # conceptually the same category -- meaning every "If category"
        # rule created via that dropdown may have silently never matched
        # anything. Now checks against EITHER the raw Sunsky category OR
        # the resolved WooCommerce category name (via the same
        # SunskyCategoryMapping table already used for Content Review's
        # own category display), so rules typed against either
        # vocabulary work correctly.
        sunsky_lower = (sunsky_category or "").strip().lower()
        woo_lower = (resolved_woo_category or "").strip().lower()
        matched = sunsky_lower in conds or (bool(woo_lower) and woo_lower in conds)
        # Client feedback confirmed live (twice, both directions): a
        # rule scoped to "If category" still matched products it
        # shouldn't have (Активност on unrelated GoPro cases even after
        # being correctly scoped and saved), and separately still failed
        # to match products it should (Характеристики "not found" on
        # genuine waterproof housings), even with patch 75's resolved-
        # category fix confirmed deployed. Rather than guess further,
        # this logs exactly what's being compared on every check so a
        # real test run shows the actual values involved.
        print(f"[enrich_service] if_category check: rule condition_value={cond!r} "
              f"vs sunsky_category={sunsky_lower!r} vs resolved_woo_category={woo_lower!r} "
              f"→ {'MATCH' if matched else 'no match'}")
        return matched
    # Any other condition_type isn't in the current schema (only "always" /
    # "if_category" are supported by the Attribute Mapping UI) — treat as
    # no-match rather than silently applying a rule outside its configured scope.
    return False


_SUNSKY_FIELD_ALIASES = {
    # UI dropdown value (lowercased) -> Sunsky's real raw_data field name,
    # for cases confirmed to differ. "Brand" was diagnosed live against a
    # real Sunsky response: the dropdown offers "Brand", but Sunsky's
    # actual top-level field is "brandName" -- so the rule silently never
    # matched and fell through to AI every time (which filled the gap well
    # enough that the mismatch went unnoticed until checked directly).
    # Add further confirmed mismatches here as they're found.
    "brand": "brandName",
}


def _find_sunsky_value(product: dict, source_field: Optional[str]) -> str:
    """Look up a raw Sunsky field by name for a 'from_sunsky' rule — checks
    the parsed spec table first (case-insensitive), then a literal top-level
    raw_data field of the same name, then a known alias for that field name
    (see _SUNSKY_FIELD_ALIASES)."""
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
    if val is None:
        alias = _SUNSKY_FIELD_ALIASES.get(needle)
        if alias:
            val = raw.get(alias)
    return str(val) if val is not None else ""


def rule_title_matches(rule: dict, titles: list[str]) -> bool:
    """Attribute Mapping "and title contains" (client point 2, same rule as
    Category Mapping's job_tasks._choose_cat_rule): no words -> True; else
    True if ANY word (comma-separated, case-insensitive) is in ANY title
    (original Sunsky title or current title)."""
    words = [w.strip().lower() for w in str(rule.get("title_contains") or "").split(",") if w.strip()]
    if not words:
        return True
    tl = [t.lower() for t in titles if t]
    return any(w in t for w in words for t in tl)


def product_titles_for_rules(product: dict) -> list[str]:
    """Titles a title condition is checked against: the original Sunsky
    title (raw "name"/"title") and the product's current title
    ("_current_name", set by the Enrich step -- the raw "name" overrides
    the saved name in that product dict)."""
    out: list[str] = []
    for k in ("name", "title", "_current_name"):
        v = str(product.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def apply_mapping_rules(
    product: dict, rules: list[dict], sunsky_category: str, resolved_woo_category: str = ""
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
    _rule_titles = product_titles_for_rules(product)
    # Same as Category Mapping: rules WITH title words are checked first
    # (more specific), the rule without words is the fallback -- otherwise a
    # general rule earlier in the list would always win and a title rule
    # for the same attribute could never apply. Stable sort: the existing
    # order is kept within each group.
    rules = sorted(rules, key=lambda r: 0 if str(r.get("title_contains") or "").strip() else 1)
    resolved: list[AttrResult] = []
    ai_extract_rules: list[dict] = []
    seen_attrs: set[str] = set()

    for rule in rules:
        attr_key = rule["woo_attr_name"].strip().lower()
        if attr_key in seen_attrs:
            continue
        if not _rule_matches_product(rule, sunsky_category, resolved_woo_category):
            continue
        # "and title contains" -- a rule with title words applies only to
        # products whose title contains one of them.
        if not rule_title_matches(rule, _rule_titles):
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
                "selector":             None,
            })
            seen_attrs.add(attr_key)
        # "leave_empty" or any other future rule_type: intentionally no-op —
        # the attribute simply isn't resolved by this rule.

    return resolved, ai_extract_rules


async def _load_store_attr_terms(db: Optional["AsyncSession"], store_id: Optional[int]) -> dict[str, list[str]]:
    """{attribute name (lower): [existing value names]} from this store's
    synced WooCommerce attributes -- the same list the review screen offers
    as "Existing values"."""
    if db is None or not store_id:
        return {}
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from models.models import WooAttribute
        rows = (await db.execute(
            select(WooAttribute).options(selectinload(WooAttribute.terms))
            .where(WooAttribute.store_id == store_id)
        )).scalars().all()
        return {(wa.name or "").strip().lower(): [t.name for t in wa.terms if t.name] for wa in rows}
    except Exception as exc:
        print(f"[enrich] could not load existing attribute values: {exc}")
        return {}


def _match_existing_term(value: str, terms: list[str]) -> str:
    """The store's existing value that `value` stands for, else `value`.
    Client feedback (PL-164): the AI wrote "Osmo Action 3" while the store
    already has "DJI Osmo Action 3", so a new, wrong value was created.
    Matches (case-insensitive): the same text; or the same text with ONE
    extra leading word on either side (a brand: "DJI Osmo Action 3" vs
    "Osmo Action 3") when exactly one existing value fits."""
    norm = lambda x: " ".join(str(x).split()).lower()
    v = norm(value)
    if not v or not terms:
        return value
    for t in terms:
        if norm(t) == v:
            return t
    def one_word_prefix(longer: str, shorter: str) -> bool:
        return longer.endswith(" " + shorter) and " " not in longer[: -len(shorter) - 1].strip()
    hits = [t for t in terms if one_word_prefix(norm(t), v) or one_word_prefix(v, norm(t))]
    return hits[0] if len(hits) == 1 else value


def _merge_and_match(results: list, terms_by_attr: dict[str, list[str]]) -> list:
    """(1) Several results for the SAME attribute become one, values joined
    with ", " -- PL-164: the AI returned three "Съвместим модел" entries
    (Osmo Action 5 Pro / 4 / 3) and only the last survived, because one
    value is stored per attribute. (2) Each value is replaced by the store's
    existing value when one matches (_match_existing_term)."""
    import re as _re
    merged: dict[str, dict] = {}
    order: list[str] = []
    for r in results:
        key = r["attribute"].strip().lower()
        if key not in merged:
            merged[key] = dict(r)
            order.append(key)
            continue
        m = merged[key]
        if r.get("raw_value"):
            m["raw_value"] = f'{m["raw_value"]}, {r["raw_value"]}' if m.get("raw_value") else r["raw_value"]
        m["confidence"] = min(m.get("confidence", 1.0), r.get("confidence", 1.0))
        m["flagged"] = bool(m.get("flagged")) or bool(r.get("flagged"))
    out = []
    for key in order:
        m = merged[key]
        terms = terms_by_attr.get(key) or []
        raw = str(m.get("raw_value") or "")
        if raw and terms:
            whole = _match_existing_term(raw, terms)
            if whole != raw:
                m["raw_value"] = whole
            else:
                parts, seen = [], set()
                for part in [x.strip() for x in _re.split(r"\s+and\s+|\s*,\s*", raw, flags=_re.IGNORECASE) if x.strip()]:
                    hit = _match_existing_term(part, terms)
                    if hit.lower() not in seen:
                        seen.add(hit.lower())
                        parts.append(hit)
                m["raw_value"] = ", ".join(parts)
        out.append(m)
    return out


def _build_extract_prompt(product: dict, rules: list[dict], terms_by_attr: Optional[dict] = None) -> str:
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
            existing = (terms_by_attr or {}).get(r["woo_attr_name"].strip().lower()) or []
            ex_note = ""
            if existing:
                shown = " | ".join(existing[:150])[:3000]
                ex_note = f"\n      existing values: {shown}"
            attr_lines.append(f'  "{r["woo_attr_name"]}"{hint}{src_note}{ex_note}')
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
        # Client feedback: title "For DJI Osmo Action 5 Pro / 4 / 3 ..." gave
        # Compatible model = "Osmo Action 3" only. Titles list several models
        # in a short form; every one of them is wanted, each as a full name.
        # Comma-separated values become separate terms at upload
        # (job_tasks._split_multi_value).
        f"When the title or specs list SEVERAL values for one attribute — for example several "
        f"compatible models written in short form (\"for DJI Osmo Action 5 Pro / 4 / 3\", "
        f"\"for Insta360 X3/X4/X5\") — return ALL of them in raw_value, never just one: each as "
        f"its full name, separated by a comma and a space "
        f"(\"Osmo Action 5 Pro, Osmo Action 4, Osmo Action 3\"; \"X3, X4, X5\").\n"
        f"When an attribute lists \"existing values\", write the existing value that means the same thing, "
        f"exactly as it is spelled there (\"DJI Osmo Action 3\", not \"Osmo Action 3\"); write a new value "
        f"only when none of the existing ones fits.\n"
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


async def _call_ai(prompt: str, gen_cfg: dict, diag: Optional[dict] = None) -> Optional[str]:
    """Returns the AI's raw answer, or None. The reason for a None is put in
    diag["ai_error"] (it used to be swallowed, so a failed call was
    indistinguishable from "nothing to extract")."""
    try:
        from pipeline.ai_generator import generate_with_ai, AIGenerationError
        gs = gen_cfg.get("globalSettings") or {}
        if not gs.get("ai_enabled", False):
            if diag is not None:
                diag["ai_error"] = "AI is turned off in Content Generation settings"
            return None
        provider = gs.get("ai_provider", "openai")
        model = gs.get("ai_model") or None
        return await generate_with_ai("_raw", {}, provider, model, {"_prompt_override": prompt})
    except Exception as exc:
        if diag is not None:
            diag["ai_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return None


def _apply_not_found(ai_results: list, active_rules: list[dict], resolved_lower: set) -> None:
    """if_not_found handling for configured attributes the AI did not return."""
    found_lower = {r["attribute"].lower() for r in ai_results} | resolved_lower
    for rule in active_rules:
        if rule["woo_attr_name"].lower() in found_lower:
            continue
        action = rule["if_not_found"]
        if action == "use_default" and rule["default_value"]:
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
    diag: Optional[dict] = None,
) -> list[AttrResult]:
    """
    Extract attributes from a single product.

    diag (optional dict) is filled with "ai_asked" (attribute names sent to
    the AI) and "ai_error" (why the AI gave no usable answer), for the log.

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
    rules = await _load_rules(db, store_id)
    mapping_rules = await _load_mapping_rules(db, store_id)
    _ea_titles = [str(product.get(k) or "").strip() for k in ("name", "title") if str(product.get(k) or "").strip()]
    resolved_woo_category = await _resolve_woo_category_name(db, store_id, sunsky_category or "", _ea_titles)

    resolved, ai_extract_from_mapping = apply_mapping_rules(
        product, mapping_rules, sunsky_category or "", resolved_woo_category
    )
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

    # Deterministic selector rules run BEFORE any AI call. A match is
    # trusted outright (no AI cost, no guessing) and that attribute is
    # removed from what gets asked of the AI. No match just falls through
    # to the normal AI/instruction path below — the selector is a
    # short-circuit optimization, not a hard requirement.
    selector_results: list[AttrResult] = []
    html_source = str((product.get("raw_data") or product).get("description")
                       or (product.get("raw_data") or product).get("desc") or "")
    remaining_rules = []
    for r in active_rules:
        sel = r.get("selector")
        val = _extract_by_selector(html_source, sel) if sel else None
        if val:
            selector_results.append({
                "attribute":  r["woo_attr_name"],
                "raw_value":  val,
                "confidence": 0.9,
                "source":     "rule_selector",
                "flagged":    False,
            })
        else:
            remaining_rules.append(r)
    active_rules = remaining_rules

    # Skip the AI call entirely when every configured rule was already
    # resolved (by Priority 1 mapping rules or a selector match above) —
    # otherwise _build_extract_prompt() would treat the now-empty
    # active_rules list as "nothing configured" and fall back to its
    # free-form _DEFAULT_ATTRS hint, which is wrong here: something WAS
    # configured, it just didn't need the AI. Only genuinely unconfigured
    # stores (rule_map empty from the start) get the free-form fallback.
    have_configured_rules = bool(rule_map)
    terms_by_attr = await _load_store_attr_terms(db, store_id)
    if active_rules or not have_configured_rules:
        prompt = _build_extract_prompt(product, active_rules, terms_by_attr)
        if diag is not None:
            diag["ai_asked"] = [r["woo_attr_name"] for r in active_rules]
        raw = await _call_ai(prompt, gen_cfg, diag)
        parsed = _parse_json_array(raw)
        if parsed is None and diag is not None and raw and "ai_error" not in diag:
            diag["ai_error"] = "the AI answer was not a valid attribute list"
    else:
        raw = None
        parsed = None

    # The prompt tells the model "Extract ONLY these attributes" (or, with
    # no rules configured at all, "Focus on: <_DEFAULT_ATTRS>") -- but that
    # was only ever an instruction to the model, never enforced afterward.
    # Client feedback: "Attributes not specified in the settings are being
    # extracted" -- a model that returns an extra attribute nobody asked
    # for (ignoring "ONLY") had nothing stopping it from landing in
    # ai_results. Build the actual allow-list here and filter against it.
    allowed_attrs_lower = (
        {r["woo_attr_name"].strip().lower() for r in active_rules}
        if active_rules
        else {a.lower() for a in _DEFAULT_ATTRS}
    )

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
            if attr.strip().lower() not in allowed_attrs_lower:
                continue  # not configured — model ignored "extract ONLY"

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
            _apply_not_found(ai_results, active_rules, resolved_lower)

        if not ai_results:
            # AI returned parsable JSON but nothing usable — fall back to
            # rule-based paramsTable parsing, same as the no-AI-response path.
            # Same allow-list applies here too — paramsTable parsing is just
            # as capable of surfacing an attribute nobody configured.
            ai_results = [
                r for r in _rule_based_extract(product)
                if r["attribute"].strip().lower() not in resolved_lower
                and (not have_configured_rules or r["attribute"].strip().lower() in allowed_attrs_lower)
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
            and (not have_configured_rules or r["attribute"].strip().lower() in allowed_attrs_lower)
        ]
        if active_rules:
            active_rule_map = {r["woo_attr_name"].lower(): r for r in active_rules}
            for item in ai_results:
                rule = active_rule_map.get(item["attribute"].lower())
                if rule:
                    item["flagged"] = item["confidence"] < rule["confidence_threshold"]
            # Client feedback: "Didn't catch the compatible brand and
            # compatible model" -- with nothing at all shown for them. When
            # the AI call fails or returns nothing usable, the configured AI
            # attributes used to disappear silently (if_not_found only ran
            # when the AI answered). Now they show as "missing" for review,
            # same as when the AI answers but leaves one out.
            _apply_not_found(ai_results, active_rules, resolved_lower)

    ai_results = _merge_and_match(ai_results, terms_by_attr)
    combined = resolved + selector_results + ai_results
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


async def get_effective_category_name_map(db: Optional["AsyncSession"]) -> dict[str, str]:
    """
    Category ID -> name map, preferring the fast, zero-API-call source:
    StarredSunskyCategory already stores both cat_id and name the moment a
    category is starred in Settings — which is realistically the exact set
    of categories that matter (products get fetched from starred
    categories, and Attribute Mapping 'if category' conditions are picked
    from the starred list too).

    Falls back to sunsky_client's slower, rate-limit-paced full tree walk
    (which may be empty/stale if it hasn't finished yet — see
    get_category_name_map_safe's docstring) for any category ID that isn't
    in the starred set, e.g. an edge case where a product's category was
    never explicitly starred by the operator.
    """
    starred_map: dict[str, str] = {}
    if db is not None:
        try:
            from sqlalchemy import select
            from models.models import StarredSunskyCategory
            rows = (await db.execute(select(StarredSunskyCategory))).scalars().all()
            starred_map = {r.cat_id: r.name for r in rows}
        except Exception as exc:
            print(f"[enrich_service] get_effective_category_name_map: "
                  f"starred-category lookup failed: {exc}")

    try:
        from pipeline.sunsky_client import get_category_name_map_safe
        tree_map = await get_category_name_map_safe()
    except Exception:
        tree_map = {}

    # Starred names win on overlap — they're operator-confirmed and instant,
    # vs. the tree walk which may be stale/partial.
    return {**tree_map, **starred_map}


async def _resolve_woo_category_name(
    db: Optional["AsyncSession"], store_id: Optional[int], sunsky_category: str,
    titles: Optional[list] = None,
) -> str:
    """Resolve a raw Sunsky category name to its mapped WooCommerce
    category name for this store, via the same SunskyCategoryMapping
    table Content Review's own category display already uses. Returns
    "" if there's no saved mapping -- the caller falls back to matching
    against the raw Sunsky category only, same as before this existed.
    """
    if db is None or not store_id or not sunsky_category:
        return ""
    try:
        from sqlalchemy import select
        from models.models import SunskyCategoryMapping
        # A category can have several rules ("IF title contains",
        # milestone point 2) -- pick the one for this product, as Upload does.
        # (.scalar_one_or_none() raised on several rows, silently caught
        # below, which would have disabled this lookup for that category.)
        from tasks.job_tasks import _choose_cat_rule
        mapping = _choose_cat_rule((
            await db.execute(
                select(SunskyCategoryMapping).where(
                    SunskyCategoryMapping.store_id == store_id,
                    SunskyCategoryMapping.sunsky_cat == sunsky_category,
                )
            )
        ).scalars().all(), titles)
        return (mapping.woo_cat_name or "") if mapping else ""
    except Exception:
        return ""


async def load_profile_attrs_for_category(
    db: Optional["AsyncSession"], store_id: Optional[int], sunsky_category: str,
    titles: Optional[list] = None,
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
        # A category can have several rules ("IF title contains",
        # milestone point 2) -- pick the one for this product, as Upload does.
        # (.scalar_one_or_none() raised on several rows, silently caught
        # below, which would have disabled this lookup for that category.)
        from tasks.job_tasks import _choose_cat_rule
        mapping = _choose_cat_rule((
            await db.execute(
                select(SunskyCategoryMapping).where(
                    SunskyCategoryMapping.store_id == store_id,
                    SunskyCategoryMapping.sunsky_cat == sunsky_category,
                )
            )
        ).scalars().all(), titles)
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
