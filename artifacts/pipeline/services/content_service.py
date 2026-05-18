"""
Production-grade Content Generation Service.

Architecture:
  - Registry-driven: all fields declared with mode, dependencies, validators
  - DAG execution: logic → ai → derive phases
  - Validation engine: per-field rules
  - Retry + exponential backoff for AI calls (3 attempts)
  - Observability: structured logging with field-level metrics
  - No circular imports: imports only from pipeline.* (never from routers.*)

Execution phases per product:
  1. logic  (parallel) — title, tags, and any field explicitly set to logic
  2. ai     (parallel, with retry+backoff) — description, any field set to ai
  3. derive (sequential, dep-ordered) — slug, image_alt, meta_title,
             image_names, short_description, meta_description
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Field registry
# ─────────────────────────────────────────────────────────────────────────────

FIELD_LIST = [
    "title",              # logic — runs first, CSV title priority
    "tags",               # logic — independent
    "description",        # ai   — independent (falls back to logic)
    "slug",               # derive ← title
    "image_alt",          # derive ← title + attributes
    "meta_title",         # derive ← title
    "image_names",        # derive ← slug
    "short_description",  # derive ← description
    "meta_description",   # derive ← description
]

FIELD_DEFAULT_MODE: dict[str, str] = {
    "title":             "logic",
    "tags":              "logic",
    "description":       "ai",
    "slug":              "derive",
    "image_alt":         "derive",
    "meta_title":        "derive",
    "image_names":       "derive",
    "short_description": "derive",
    "meta_description":  "derive",
}

FIELD_DEPS: dict[str, list[str]] = {
    "slug":              ["title"],
    "image_alt":         ["title"],
    "meta_title":        ["title"],
    "image_names":       ["slug"],
    "short_description": ["description"],
    "meta_description":  ["description"],
}

FIELD_ATTR: dict[str, str] = {
    "title":             "name",
    "description":       "description",
    "short_description": "short_description",
    "slug":              "slug",
    "meta_title":        "meta_title",
    "meta_description":  "meta_description",
    "tags":              "tags",
    "image_alt":         "image_alt",
    "image_names":       "image_names",
}

# ─────────────────────────────────────────────────────────────────────────────
# Validators
# ─────────────────────────────────────────────────────────────────────────────

VALIDATORS: dict[str, dict] = {
    "title":             {"non_empty": True, "max_chars": 120},
    "slug":              {"non_empty": True, "max_chars": 70},
    "tags":              {"non_empty": True, "max_items": 3},
    "image_alt":         {"non_empty": True, "max_chars": 125},
    "image_names":       {"non_empty": True, "max_chars": 70},
    "description":       {"min_words": 50, "max_words": 300,
                          "banned_words": ["the best", "100%", "guarantee"]},
    "short_description": {"non_empty": True, "max_chars": 400},
    "meta_title":        {"non_empty": True, "max_chars": 60},
    "meta_description":  {"min_chars": 80, "max_chars": 160},
}

# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = html.unescape(text)
    return re.sub(r"<[^>]+>", "", text).strip()


def _slugify(text: str) -> str:
    try:
        text = text.encode("ascii", "ignore").decode()
    except Exception:
        pass
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def _parse_params_table(html_str: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for m in re.finditer(
        r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        html_str, re.DOTALL | re.IGNORECASE,
    ):
        k = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        v = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if k and v:
            pairs[k] = v
    return pairs


def _get_raw(product: dict) -> dict:
    return product.get("raw_data") or product.get("rawData") or {}


def _get_brand(specs: dict) -> str:
    return (
        specs.get("Compatible Brand")
        or specs.get("Brand")
        or specs.get("Manufacturer")
        or ""
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validation engine
# ─────────────────────────────────────────────────────────────────────────────

def _validate(field: str, value: str, rules: dict) -> tuple[bool, str]:
    """Returns (passed, warning_message). Validation warnings don't block output."""
    warnings: list[str] = []

    if rules.get("non_empty") and not value.strip():
        return False, f"[{field}] empty value"

    if "max_chars" in rules and len(value) > rules["max_chars"]:
        warnings.append(f"exceeds max {rules['max_chars']} chars ({len(value)})")

    if "min_chars" in rules and len(value) < rules["min_chars"]:
        warnings.append(f"below min {rules['min_chars']} chars ({len(value)})")

    if "min_words" in rules:
        wc = len(value.split())
        if wc < rules["min_words"]:
            warnings.append(f"word count {wc} below min {rules['min_words']}")

    if "max_words" in rules:
        wc = len(value.split())
        if wc > rules["max_words"]:
            warnings.append(f"word count {wc} above max {rules['max_words']}")

    if "max_items" in rules:
        items = [i for i in value.split(",") if i.strip()]
        if len(items) > rules["max_items"]:
            warnings.append(f"{len(items)} items, max {rules['max_items']}")

    if "banned_words" in rules:
        low = value.lower()
        found = [w for w in rules["banned_words"] if w.lower() in low]
        if found:
            warnings.append(f"banned words: {found}")

    return True, "; ".join(warnings) if warnings else ""


# ─────────────────────────────────────────────────────────────────────────────
# Logic generators
# ─────────────────────────────────────────────────────────────────────────────

def _logic_title(product: dict, options: dict, resolved: dict) -> str:
    csv_title = (product.get("csv_title") or "").strip()
    if csv_title:
        return csv_title[:120]

    name = _strip_html(product.get("name", ""))
    if name:
        name = name[0].upper() + name[1:]

    max_chars = int(options.get("max_chars", 120))
    return (name[:max_chars - 1] + "…") if len(name) > max_chars else name


def _logic_tags(product: dict, options: dict, resolved: dict) -> str:
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    name = product.get("name", "")
    words = name.split()

    tags: list[str] = []
    cat = product.get("category", "")
    if cat:
        tags.append(cat.strip().title())
    elif words:
        tags.append(words[0].strip().title())

    if len(words) > 1:
        last = words[-1].strip().title()
        if last not in tags:
            tags.append(last)

    for v in specs.values():
        if isinstance(v, str) and 2 < len(v) < 30:
            cleaned = v.strip().title()
            if cleaned not in tags:
                tags.append(cleaned)
                break

    max_tags = int(options.get("max_tags", 3))
    return ", ".join(tags[:max_tags])


def _logic_description(product: dict, options: dict, resolved: dict) -> str:
    name = product.get("name", "Product")
    desc = _strip_html(product.get("description", ""))
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))

    structure = options.get("structure", ["intro", "features", "benefits", "compatibility", "closing"])
    parts: list[str] = []

    if "intro" in structure:
        body = desc or "A quality product designed for reliable performance."
        parts.append(f"<p><strong>{name}</strong> — {body}</p>")

    if "features" in structure and specs:
        items = "".join(
            f"<li><strong>{k}:</strong> {v}</li>"
            for k, v in list(specs.items())[:8]
        )
        parts.append(f"<ul>{items}</ul>")

    if "benefits" in structure:
        parts.append(
            "<p>Built to high quality standards, "
            "offering outstanding value and reliable performance.</p>"
        )

    if "compatibility" in structure:
        brand = _get_brand(specs)
        if brand:
            parts.append(f"<p><em>Compatible with: {brand}</em></p>")

    if "closing" in structure:
        parts.append(f"<p>Order your {name} today and experience the difference quality makes.</p>")

    return "\n".join(parts) if parts else (desc or name)


# ─────────────────────────────────────────────────────────────────────────────
# Derive generators (consume resolved field values)
# ─────────────────────────────────────────────────────────────────────────────

def _derive_slug(product: dict, options: dict, resolved: dict) -> str:
    title = resolved.get("title", "") or product.get("name", "")
    sku = product.get("sku", "")
    max_chars = int(options.get("max_chars", 70))

    slug = _slugify(title)
    if not slug:
        fb = f"product-{sku[-8:].lower()}" if sku else "product"
        return fb[:max_chars]

    slug = slug[:max_chars]
    if sku and sku[-4:].lower() not in slug:
        suffix = f"-{sku[-4:].lower()}"
        if len(slug) + len(suffix) <= max_chars:
            slug += suffix

    return slug


def _derive_image_alt(product: dict, options: dict, resolved: dict) -> str:
    title = resolved.get("title", "") or product.get("name", "")
    sku = product.get("sku", "")
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    brand = _get_brand(specs)

    primary_attr = ""
    for v in specs.values():
        if isinstance(v, str) and 2 < len(v) < 30:
            primary_attr = v
            break

    if primary_attr and brand:
        alt = f"{title} – {primary_attr} – {brand}"
    elif primary_attr:
        alt = f"{title} – {primary_attr}"
    elif brand:
        alt = f"{title} – {brand}"
    else:
        alt = f"{title} – {sku}" if sku else title

    if len(alt) > 125:
        alt = alt[:125].rsplit(" ", 1)[0]

    return alt


def _derive_meta_title(product: dict, options: dict, resolved: dict) -> str:
    title = resolved.get("title", "") or product.get("name", "")
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    brand = _get_brand(specs)
    max_chars = int(options.get("max_chars", 60))

    meta = f"{title} | {brand}" if brand else title
    if len(meta) > max_chars:
        if len(title) <= max_chars:
            return title
        return title[:max_chars - 1] + "…"

    return meta


def _derive_image_names(product: dict, options: dict, resolved: dict) -> str:
    slug = resolved.get("slug", "") or _slugify(product.get("name", "product"))
    return f"{slug}-1.webp"[:70]


def _derive_short_description(product: dict, options: dict, resolved: dict) -> str:
    desc = resolved.get("description", "") or product.get("description", "")
    text = _strip_html(desc)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    result = ""
    for s in sentences[:3]:
        candidate = (result + " " + s).strip()
        if len(candidate) <= 400:
            result = candidate
        else:
            break

    if not result and text:
        result = text[:400]

    return result.strip()


def _derive_meta_description(product: dict, options: dict, resolved: dict) -> str:
    desc = resolved.get("description", "") or product.get("description", "")
    text = _strip_html(desc)

    if len(text) < 80:
        cta = " Shop now for the best selection and premium quality."
        text = (text + cta)[:160]

    if len(text) > 160:
        text = text[:159].rsplit(" ", 1)[0]
        if not text.endswith((".", "!", "?")):
            text += "."

    return text


# ─────────────────────────────────────────────────────────────────────────────
# Generator registries
# ─────────────────────────────────────────────────────────────────────────────

_LOGIC_GENERATORS: dict[str, Any] = {
    "title":             _logic_title,
    "tags":              _logic_tags,
    "description":       _logic_description,
    "slug":              _derive_slug,
    "image_alt":         _derive_image_alt,
    "meta_title":        _derive_meta_title,
    "image_names":       _derive_image_names,
    "short_description": _derive_short_description,
    "meta_description":  _derive_meta_description,
}

_DERIVE_GENERATORS: dict[str, Any] = {
    "slug":              _derive_slug,
    "image_alt":         _derive_image_alt,
    "meta_title":        _derive_meta_title,
    "image_names":       _derive_image_names,
    "short_description": _derive_short_description,
    "meta_description":  _derive_meta_description,
}

# ─────────────────────────────────────────────────────────────────────────────
# AI with retry + exponential backoff
# ─────────────────────────────────────────────────────────────────────────────

async def _run_ai_with_retry(
    field: str,
    product: dict,
    provider: str,
    model: str | None,
    options: dict,
    max_retries: int = 3,
) -> str:
    from pipeline.ai_generator import generate_with_ai, AIGenerationError

    delay = 1.0
    last_err: Exception = RuntimeError(f"AI generation failed for {field}")

    for attempt in range(max_retries):
        try:
            return await generate_with_ai(
                field=field,
                product=product,
                provider=provider,
                model=model,
                options=options,
            )
        except AIGenerationError as e:
            last_err = e
            logger.warning(f"[{field}] AI attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
        except Exception as e:
            last_err = e
            logger.error(f"[{field}] AI unexpected error: {e}")
            break

    raise last_err


# ─────────────────────────────────────────────────────────────────────────────
# Core: run one field
# ─────────────────────────────────────────────────────────────────────────────

async def run_field(
    field: str,
    product: dict,
    template: dict,
    resolved: dict | None = None,
) -> dict:
    """
    Generate content for a single field.
    template is a plain dict (not Pydantic) with keys: globalSettings, fields, overrides.
    Returns: {field, value, source, status, error?}
    """
    if resolved is None:
        resolved = {}

    override = (template.get("overrides") or {}).get(field)
    if override is not None:
        return {"field": field, "value": str(override), "source": "override", "status": "ok"}

    field_cfg = (template.get("fields") or {}).get(field, {})
    options = field_cfg.get("options", {})
    mode = field_cfg.get("mode") or FIELD_DEFAULT_MODE.get(field, "logic")

    gs = template.get("globalSettings") or {}
    ai_enabled = gs.get("ai_enabled", False)
    ai_provider = gs.get("ai_provider", "openai") or "openai"
    ai_model = gs.get("ai_model") or None
    fallback_strategy = gs.get("fallback_strategy", "safe")

    value = ""
    source = "logic"
    error_msg: str | None = None

    if mode == "ai" and ai_enabled:
        try:
            value = await _run_ai_with_retry(field, product, ai_provider, ai_model, options)
            source = f"ai:{ai_provider}"
        except Exception as ai_err:
            error_msg = str(ai_err)
            logger.warning(f"[{field}] AI failed, applying '{fallback_strategy}' fallback: {ai_err}")
            if fallback_strategy == "skip":
                return {"field": field, "value": "", "source": "none",
                        "status": "skipped", "error": error_msg}
            if fallback_strategy == "empty":
                return {"field": field, "value": "", "source": "ai:failed",
                        "status": "ok", "error": error_msg}
            mode = "logic"

    if mode == "derive":
        gen = _DERIVE_GENERATORS.get(field)
        if gen:
            try:
                value = gen(product, options, resolved)
                source = "derive"
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[{field}] derive failed: {e}")
                mode = "logic"
        else:
            mode = "logic"

    if mode == "logic" or (not value and mode not in ("ai",)):
        gen = _LOGIC_GENERATORS.get(field)
        if not gen:
            return {"field": field, "value": "", "source": "none",
                    "status": "skipped", "error": f"No generator for '{field}'"}
        try:
            value = gen(product, options, resolved)
            source = "logic" if not error_msg else "logic:fallback"
        except Exception as e:
            return {"field": field, "value": "", "source": "logic",
                    "status": "failed", "error": str(e)}

    rules = VALIDATORS.get(field, {})
    if rules and value:
        passed, warn = _validate(field, value, rules)
        if not passed:
            logger.warning(f"[{field}] validation: {warn}")
        elif warn:
            logger.debug(f"[{field}] validation warnings: {warn}")

    result: dict = {"field": field, "value": value, "source": source, "status": "ok"}
    if error_msg:
        result["error"] = error_msg
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Core: generate all fields for one product (DAG-aware)
# ─────────────────────────────────────────────────────────────────────────────

async def generate_product(product: dict, template: dict) -> dict:
    """
    Generate all enabled fields using DAG-ordered execution.

    Phases:
      1. logic  (parallel) — title, tags, any field set to logic
      2. ai     (parallel, retry+backoff) — description, any field set to ai
      3. derive (sequential, dep-ordered) — slug, image_alt, meta_title,
                 image_names, short_description, meta_description

    Returns: {field: FieldResult} for all enabled fields.
    """
    fields_cfg = template.get("fields") or {}

    def _mode(f: str) -> str:
        return fields_cfg.get(f, {}).get("mode") or FIELD_DEFAULT_MODE.get(f, "logic")

    def _enabled(f: str) -> bool:
        return fields_cfg.get(f, {}).get("enabled", True)

    enabled = [f for f in FIELD_LIST if _enabled(f)]
    resolved: dict[str, str] = {}
    results: dict[str, dict] = {}

    logic_phase = [f for f in enabled if _mode(f) == "logic"]
    if logic_phase:
        phase_results = await asyncio.gather(
            *[run_field(f, product, template, resolved) for f in logic_phase],
            return_exceptions=True,
        )
        for f, r in zip(logic_phase, phase_results):
            if isinstance(r, Exception):
                results[f] = {"field": f, "value": "", "source": "logic",
                               "status": "failed", "error": str(r)}
                resolved[f] = ""
            else:
                results[f] = r
                resolved[f] = r.get("value", "")

    ai_phase = [f for f in enabled if _mode(f) == "ai"]
    if ai_phase:
        phase_results = await asyncio.gather(
            *[run_field(f, product, template, resolved) for f in ai_phase],
            return_exceptions=True,
        )
        for f, r in zip(ai_phase, phase_results):
            if isinstance(r, Exception):
                results[f] = {"field": f, "value": "", "source": "ai",
                               "status": "failed", "error": str(r)}
                resolved[f] = ""
            else:
                results[f] = r
                resolved[f] = r.get("value", "")

    derive_phase = [f for f in FIELD_LIST if f in enabled and _mode(f) == "derive"]
    for f in derive_phase:
        for dep in FIELD_DEPS.get(f, []):
            if dep not in resolved:
                resolved[dep] = results.get(dep, {}).get("value", "")
        r = await run_field(f, product, template, resolved)
        results[f] = r
        resolved[f] = r.get("value", "")

    return results
