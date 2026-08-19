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
    "focus_keyword",      # derive ← title (basic Yoast/RankMath SEO field)
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
    "focus_keyword":      "derive",
}

FIELD_DEPS: dict[str, list[str]] = {
    "slug":              ["title"],
    "image_alt":         ["title"],
    "meta_title":        ["title"],
    "image_names":       ["slug"],
    "short_description": ["description"],
    "meta_description":  ["description"],
    "focus_keyword":     ["title"],
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
    "focus_keyword":     "focus_keyword",
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
    # max_chars default here is deliberately generous (2000) -- Description
    # had NO configurable length limit at all before this (client feedback:
    # "Description need to have an option for Max Characters"), only a
    # hardcoded "under 200 words" instruction inside the AI prompt that
    # nothing on the backend actually enforced. The operator's own
    # Settings -> Content Generation value (options.max_chars) always wins
    # over this default -- see the max_chars lookup below.
    "description":       {"min_words": 50, "max_words": 300, "max_chars": 2000,
                          "banned_words": ["the best", "100%", "guarantee"]},
    "short_description": {"non_empty": True, "max_chars": 400},
    "meta_title":        {"non_empty": True, "max_chars": 60},
    "meta_description":  {"min_chars": 80, "max_chars": 160},
    # Yoast/RankMath convention: a short, specific phrase (not a full
    # sentence) — 2-4 words is typical, kept generous at 60 chars so a
    # legitimate longer product phrase still fits.
    "focus_keyword":     {"non_empty": True, "max_chars": 60},
}

# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = html.unescape(text)
    return re.sub(r"<[^>]+>", "", text).strip()


def _truncate_html_blocks(value: str, max_chars: int) -> str:
    """Shorten HTML content to at most max_chars of VISIBLE text, by
    dropping whole trailing top-level block elements (</p>, </ul>, </ol>) --
    never by slicing raw characters, which would risk cutting a tag in
    half and producing broken HTML. Used for the 'description' field only
    (the one generated field that's actual HTML, not plain text).

    If even the first block already exceeds max_chars on its own, that
    block is kept whole rather than mangled -- matching the client's
    'never truncate mid-word' request: a slightly-over-budget complete
    block beats a broken one.
    """
    if len(_strip_html(value)) <= max_chars:
        return value

    # Split only on TOP-LEVEL block-closing tags (</p>, </ul>, </ol>).
    # </li> is deliberately excluded -- <li> elements are never top-level
    # in this generator's output, always nested inside <ul>/<ol>, so
    # treating </li> as a split boundary would let a kept block end with
    # <ul> opened but not yet closed by its later </ul>. The whole
    # <ul>...</ul> (all its <li> children together) is kept or dropped as
    # one atomic unit instead.
    parts = re.split(r"(</(?:p|ul|ol)>)", value)
    blocks: list[str] = []
    buf = ""
    for part in parts:
        buf += part
        if re.fullmatch(r"</(?:p|ul|ol)>", part):
            blocks.append(buf)
            buf = ""
    if buf:
        blocks.append(buf)

    kept: list[str] = []
    running_len = 0
    for block in blocks:
        block_text_len = len(_strip_html(block))
        if not kept:
            # Always keep at least one block, even if it alone exceeds
            # max_chars -- an empty description is worse than a slightly
            # long one, and this mirrors the "show the last word/block in
            # full rather than cut it" rule from client feedback.
            kept.append(block)
            running_len += block_text_len
            continue
        if running_len + block_text_len > max_chars:
            break
        kept.append(block)
        running_len += block_text_len
    return "".join(kept).strip()


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

def _truncate_no_mid_word(value: str, max_chars: int, boundary: str = " ") -> str:
    """Never cut a word (or hyphen-token, for slugs) in half: if the cut
    point at max_chars doesn't already land on `boundary`, extend forward
    to the next occurrence of it instead of chopping mid-word. Matches
    client feedback: 'priority should be given to displaying the last
    word in full, even if it exceeds the setting by a few characters.'

    Used by every field generator below instead of each doing its own
    (previously inconsistent, often mid-word/ellipsis) truncation --
    confirmed live that _logic_title's old `name[:max_chars-1] + "…"`
    style cuts produced things like 'Premium Wireless Bluetooth He…',
    chopping "Headphones" in half, on every run, regardless of the
    canonical word-boundary-safe logic added to the shared enforcement
    step in run_field() -- because the value was already <= max_chars by
    the time it got there, having been pre-truncated (badly) here first.
    """
    if len(value) <= max_chars:
        return value
    if value[max_chars] == boundary:
        return value[:max_chars].rstrip(boundary)
    next_b = value.find(boundary, max_chars)
    return (value if next_b == -1 else value[:next_b]).rstrip(boundary)


def _logic_title(product: dict, options: dict, resolved: dict) -> str:
    csv_title = (product.get("csv_title") or "").strip()
    if csv_title:
        return csv_title[:120]

    name = _strip_html(product.get("name", ""))
    if name:
        name = name[0].upper() + name[1:]

    max_chars = int(options.get("max_chars", 120))
    return _truncate_no_mid_word(name, max_chars)


_TAG_STOPWORDS = {
    "for", "new", "the", "a", "an", "with", "and", "or", "of", "to", "in",
    "original", "genuine", "hot", "sale", "1pc", "2pcs", "3pcs", "set",
}

# Values that look like a raw measurement/quantity rather than a real,
# taggable product attribute (weight, dimensions, package counts) --
# these show up constantly in Sunsky's spec tables and are meaningless
# as tags on their own.
_TAG_MEASUREMENT_RE = re.compile(
    r"^\s*[\d.]+\s*(kgs?|g|lbs?|oz|cm|mm|inch(es)?|pcs?|pieces?)\s*$", re.IGNORECASE
)
# Spec keys worth preferring, in priority order, over an arbitrary first
# value. Includes common synonyms (mirrors _get_brand's own key list above)
# so e.g. "Compatible Brand" is recognized just as reliably as "Brand".
_TAG_PREFERRED_SPEC_KEYS = (
    "color", "colour", "brand", "compatible brand", "manufacturer",
    "material", "style",
)


def _clean_tag_word(w: str) -> str:
    """Strip stray punctuation a raw title word can carry -- e.g. a
    trailing "Blue)" -> "Blue". Does NOT handle parenthesis-as-word-
    boundary (e.g. "Case(Silver)"); that's split out before this runs.
    """
    return re.sub(r"[^\w\s-]", "", w).strip()


def _tag_case(w: str) -> str:
    """Title Case, except genuine all-caps brand/model codes (FMFXTR,
    ZTTO, etc.) -- common in Sunsky product titles -- which .title()
    would otherwise mangle into "Fmfxtr", damaging the actual brand name.
    """
    if len(w) >= 3 and w.isupper():
        return w
    return w.title()


def _logic_tags(product: dict, options: dict, resolved: dict) -> str:
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    name = product.get("name", "")
    # Split on whitespace AND parenthesis/bracket boundaries -- confirmed
    # live that "Case(Silver)" was reaching WooCommerce as a merged,
    # malformed "Casesilver" tag when parentheses were only stripped as
    # characters instead of treated as separating two distinct words.
    raw_words = re.split(r"[\s()\[\]]+", name)
    words = [_clean_tag_word(w) for w in raw_words]
    words = [w for w in words if w and w.lower() not in _TAG_STOPWORDS]

    tags: list[str] = []
    cat = product.get("category", "")
    if cat:
        tags.append(_tag_case(cat.strip()))
    elif words:
        tags.append(_tag_case(words[0]))

    if len(words) > 1:
        last = _tag_case(words[-1])
        if last not in tags:
            tags.append(last)

    # Prefer a known-useful spec key (Color/Brand/Material/Style) over an
    # arbitrary first value in raw HTML table order, which could just as
    # easily be a package weight or dimension -- confirmed live picking up
    # "0.08Kgs" as a "tag" this way.
    spec_tag = None
    specs_lower = {k.strip().lower(): v for k, v in specs.items()}
    for key in _TAG_PREFERRED_SPEC_KEYS:
        v = specs_lower.get(key)
        if isinstance(v, str) and v.strip():
            spec_tag = _tag_case(v.strip())
            break
    if not spec_tag:
        for v in specs.values():
            if isinstance(v, str) and 2 < len(v) < 30 and not _TAG_MEASUREMENT_RE.match(v):
                spec_tag = _tag_case(v.strip())
                break
    if spec_tag and spec_tag not in tags:
        tags.append(spec_tag)

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
    sku = product.get("site_sku") or product.get("sku", "")
    max_chars = int(options.get("max_chars", 70))

    slug = _slugify(title)
    if not slug:
        fb = f"product-{sku[-8:].lower()}" if sku else "product"
        return fb[:max_chars]

    slug = _truncate_no_mid_word(slug, max_chars, boundary="-")
    if sku and sku[-4:].lower() not in slug:
        suffix = f"-{sku[-4:].lower()}"
        if len(slug) + len(suffix) <= max_chars:
            slug += suffix

    return slug


def _derive_image_alt(product: dict, options: dict, resolved: dict) -> str:
    title = resolved.get("title", "") or product.get("name", "")
    sku = product.get("site_sku") or product.get("sku", "")
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

    max_chars = int(options.get("max_chars", 125))
    if len(alt) > max_chars:
        alt = _truncate_no_mid_word(alt, max_chars)

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
        return _truncate_no_mid_word(title, max_chars)

    return meta


def _derive_image_names(product: dict, options: dict, resolved: dict) -> str:
    slug = resolved.get("slug", "") or _slugify(product.get("name", "product"))
    max_chars = int(options.get("max_chars", 70))
    suffix = "-1.webp"
    # Truncate only the slug portion, on a hyphen boundary -- never slice
    # through the suffix itself, which would produce a broken filename
    # with no valid extension (e.g. "...-1.we" instead of "...-1.webp").
    slug = _truncate_no_mid_word(slug, max(max_chars - len(suffix), 1), boundary="-")
    return f"{slug}{suffix}"


_FOCUS_KEYWORD_STOPWORDS = {
    "for", "with", "and", "the", "a", "an", "of", "to", "in", "on",
}


def _derive_focus_keyword(product: dict, options: dict, resolved: dict) -> str:
    """
    Sensible, deterministic default focus keyword: brand + the first
    couple of meaningful nouns from the title, filler words like "For"
    stripped out. E.g. "For Samsung Galaxy S26 5G LC.IMEEKE ... Phone
    Case(Black)" -> "LC.IMEEKE Samsung Galaxy S26 Phone Case" style
    phrase -- a real search-shaped phrase rather than the whole title.
    Client can always override with an AI-mode instruction for something
    more tailored; this just means a real, non-empty value ships by
    default rather than leaving Yoast's focus keyword blank.
    """
    title = resolved.get("title", "") or product.get("name", "")
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    brand = _get_brand(specs)
    max_chars = int(options.get("max_chars", 60))

    words = [w for w in re.split(r"\s+", title.strip()) if w]
    kept = [w for w in words if w.lower() not in _FOCUS_KEYWORD_STOPWORDS]
    phrase_words = (([brand] if brand else []) + kept)[:5]
    phrase = " ".join(dict.fromkeys(phrase_words))  # de-dupe, preserve order

    if not phrase:
        phrase = _truncate_no_mid_word(title, max_chars)
    elif len(phrase) > max_chars:
        phrase = _truncate_no_mid_word(phrase, max_chars)
    return phrase.strip()


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
    "focus_keyword":     _derive_focus_keyword,
}

_DERIVE_GENERATORS: dict[str, Any] = {
    "slug":              _derive_slug,
    "image_alt":         _derive_image_alt,
    "meta_title":        _derive_meta_title,
    "image_names":       _derive_image_names,
    "short_description": _derive_short_description,
    "meta_description":  _derive_meta_description,
    "focus_keyword":     _derive_focus_keyword,
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

    # Client feedback: "The logic option in different fields catch wrong/
    # unwanted data... I think we should lock the specs table for using
    # in any mode – logic/ai/derive." Every generator (logic and derive)
    # and the AI prompt builder (_build_product_context/_extract_specs in
    # ai_generator.py) all read the SAME product['raw_data']['paramsTable']
    # field -- so stripping it once here, on a local copy, before any mode
    # branch runs, covers all three modes with one change instead of
    # editing every individual generator function separately (which is
    # exactly the kind of per-path drift this whole codebase has
    # repeatedly suffered from).
    if gs.get("lock_specs_table", False):
        product = dict(product)  # shallow copy -- don't mutate the caller's dict
        for raw_key in ("raw_data", "rawData"):
            if isinstance(product.get(raw_key), dict) and "paramsTable" in product[raw_key]:
                raw_copy = dict(product[raw_key])
                raw_copy["paramsTable"] = ""
                product[raw_key] = raw_copy

    value = ""
    source = "logic"
    error_msg: str | None = None

    if mode == "ai" and ai_enabled:
        try:
            value = await _run_ai_with_retry(field, product, ai_provider, ai_model, options)
            source = f"ai:{ai_provider}"

            # Sanity check independent of prompt-following: an AI title
            # response that's suspiciously short is worse than no AI
            # response at all -- it's a real, silent quality failure that
            # doesn't raise an exception, so it slips past the normal
            # try/except fallback entirely. Confirmed live: gemini-2.5-flash
            # returned single-word/abbreviation fragments ("Skins", "MagCa",
            # "S26C") for a "concise title" prompt, on a genuinely full raw
            # product name -- not a code bug, just a model output-quality
            # issue an improved prompt alone can't fully guarantee against.
            # Any AI-mode field with a min_chars rule gets this same net;
            # falls through to the same fallback_strategy handling below.
            min_ok_chars = rules_preview.get("min_chars") if (rules_preview := VALIDATORS.get(field, {})) else None
            if min_ok_chars is None and field == "title":
                min_ok_chars = 15  # well below any real title, well above a bare fragment
            if min_ok_chars and len(value.strip()) < min_ok_chars:
                raise RuntimeError(
                    f"AI response suspiciously short ({len(value.strip())} chars, "
                    f"expected >= {min_ok_chars}): {value!r}"
                )
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

        # Previously validation only ever logged/warned about an
        # over-length value -- nothing actually shortened it, so an
        # AI-generated field (mode "ai") could sail straight past its
        # configured max_chars with nothing enforcing the limit at all.
        # Confirmed live: a slug configured for 70 chars came out at 85
        # from AI mode. "Derive" mode already truncated correctly
        # (_derive_slug does its own [:max_chars]); this brings AI/Logic
        # mode output in line with the same limit instead of just noting
        # it was broken after the fact.
        #
        # options.get("max_chars") -- the operator's actual Settings ->
        # Content Generation value -- now takes priority over rules'
        # static default. Previously this always enforced the hardcoded
        # default (e.g. slug=70) even if the operator had configured a
        # different value in Settings; their custom value only ever
        # reached the AI prompt / logic generators, never this safety net,
        # so a stricter or looser custom setting was silently ignored here.
        effective_max = options.get("max_chars", rules.get("max_chars"))
        if effective_max and len(_strip_html(value) if field == "description" else value) > effective_max:
            max_chars = int(effective_max)
            if field == "description":
                # HTML content -- never slice raw characters (risks cutting
                # a tag in half). Drop whole trailing blocks instead.
                value = _truncate_html_blocks(value, max_chars)
            elif field == "slug":
                # Same never-cut-mid-token rule as everywhere else, using
                # "-" as the boundary since that's how slugs join words.
                # Was a raw value[:max_chars] slice, which could (and did,
                # confirmed live) re-chop a slug that _derive_slug had
                # already correctly extended past max_chars to finish its
                # last word -- undoing that fix right back to a mid-word
                # cut by the time it reached WooCommerce.
                value = _truncate_no_mid_word(value, max_chars, boundary="-")
            else:
                # Never truncate mid-word (client feedback: "priority
                # should be given to displaying the last word in full,
                # even if it exceeds the setting by a few characters").
                value = _truncate_no_mid_word(value, max_chars)
            logger.info(f"[{field}] truncated to {len(value)} chars (limit {max_chars})")

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
