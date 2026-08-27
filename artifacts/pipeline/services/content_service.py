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
import zlib
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

    result = "".join(kept).strip()

    # Client feedback confirmed live: "description doesn't follow the
    # rule for max characters" -- the rule above always keeps the FIRST
    # block whole with no upper bound of its own, so a description whose
    # only generated content is one intro sentence could exceed the
    # configured limit by any amount (149 chars against a 100-char
    # setting, confirmed). If that single kept block is a simple <p>,
    # truncate its inner TEXT at a word boundary instead of accepting it
    # unconditionally oversized. Only for <p> -- <ul> lists are riskier
    # to safely truncate mid-item without confusing partial output.
    #
    # This strips any inline tags (<strong>, <em>) from the block's
    # content when truncation actually triggers -- re-truncating text
    # that still contains an inline tag risks leaving it unclosed
    # (broken HTML), and description's own intro template (the block
    # this most commonly applies to) doesn't use inline tags anyway.
    if len(kept) == 1 and len(_strip_html(result)) > max_chars:
        m = re.fullmatch(r"<p>(.*)</p>\s*", result, re.DOTALL)
        if m and len(_strip_html(m.group(1))) > max_chars:
            visible = _strip_html(m.group(1))
            result = f"<p>{_truncate_no_mid_word(visible, max_chars)}</p>"

    return result


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


# Client feedback item #16 follow-up: Focus Keyword (and Meta Title,
# Image Alt, which all derive from resolved["title"]) showed English
# text even with Target Language=Bulgarian, because _logic_title never
# had any language logic at all -- it just cleaned/truncated the raw
# Sunsky name. Fixed via a real, deterministic word/phrase glossary
# translator (see _translate_title_bg below), NOT an AI call -- Logic
# mode's "never uses AI" guarantee (verified and told to the client
# during the Lock Specs Table work) stays true. This is a curated
# glossary, not full machine translation: word order and Bulgarian
# grammatical agreement (adjective gender matching the noun that
# follows) won't always be perfect, but the meaning comes through
# correctly and brand/model names are never touched.
#
# Longest phrase first (checked before single words) so multi-word
# terms translate as a unit instead of word-by-word ("screen
# protector" as one concept, not "screen" + "protector" separately).
_EN_BG_PHRASES: list[tuple[str, str]] = [
    ("screen protector", "протектор за екран"),
    ("tempered glass", "закалено стъкло"),
    ("full coverage", "пълно покритие"),
    ("fast charging", "бързо зареждане"),
    ("charging cable", "кабел за зареждане"),
    ("power bank", "външна батерия"),
    ("phone case", "калъф за телефон"),
    ("card holder", "поставка за карти"),
    ("card slot", "гнездо за карта"),
    ("memory card", "карта с памет"),
    ("micro sd", "micro SD"),
    ("sim tray", "поставка за SIM"),
    ("sim card", "SIM карта"),
    ("noise cancellation", "шумопотискане"),
    ("wireless earphones", "безжични слушалки"),
    ("wireless charging", "безжично зареждане"),
    ("wireless charger", "безжично зарядно"),
    ("shock proof", "удароустойчив"),
    ("shockproof", "удароустойчив"),
    ("water proof", "водоустойчив"),
    ("waterproof", "водоустойчив"),
    ("dust proof", "прахоустойчив"),
    ("scratch resistant", "устойчив на надрасквания"),
    ("anti scratch", "устойчив на надраскване"),
]
_EN_BG_WORDS: dict[str, str] = {
    "case": "калъф", "cover": "покритие", "protective": "защитен",
    "protector": "протектор", "protection": "защита",
    "wireless": "безжичен", "bluetooth": "Bluetooth",
    "charger": "зарядно", "charging": "зареждане", "cable": "кабел",
    "adapter": "адаптер", "battery": "батерия",
    "earphones": "слушалки", "earbuds": "слушалки", "headphones": "слушалки",
    "speaker": "тонколона", "microphone": "микрофон",
    "watch": "часовник", "band": "каишка", "strap": "каишка",
    "holder": "поставка", "mount": "стойка", "stand": "стойка",
    "bag": "чанта", "pouch": "калъфче", "sleeve": "калъф",
    "shell": "черупка", "bumper": "бъмпер",
    "silicone": "силиконов", "leather": "кожен", "metal": "метален",
    "plastic": "пластмасов", "glass": "стъклен",
    "durable": "издръжлив", "premium": "премиум", "universal": "универсален",
    "compatible": "съвместим", "portable": "преносим", "foldable": "сгъваем",
    "mini": "мини", "slim": "тънък", "ultra-thin": "ултра тънък",
    "replacement": "резервен", "spare": "резервен",
    "set": "комплект", "kit": "комплект", "pack": "пакет",
    "phone": "телефон", "tablet": "таблет", "laptop": "лаптоп",
    "wallet": "портфейл",
    "screen": "екран", "camera": "камера", "lens": "обектив",
    "with": "с", "for": "за", "and": "и",
    # Client feedback confirmed live: a real test product (Insta360
    # action camera housing) exposed that the original word list was
    # heavily biased toward phone-case vocabulary and had zero coverage
    # for camera/action-camera/drone accessories, a large share of
    # Sunsky's actual catalog. Housing/Diving/etc. stayed untranslated
    # not because the mechanism failed, but because none of these
    # words existed in the glossary at all.
    "housing": "корпус", "diving": "гмуркане", "underwater": "подводен",
    "filter": "филтър", "gimbal": "джимбал", "stabilizer": "стабилизатор",
    "tripod": "статив", "monopod": "монопод", "selfie": "селфи",
    "remote": "дистанционно", "controller": "контролер",
    "drone": "дрон", "propeller": "перка", "motor": "мотор",
    "memory": "памет", "storage": "съхранение", "card": "карта",
    "action": "екшън", "sport": "спортен", "outdoor": "външен",
    "waterproof": "водоустойчив", "shockproof": "удароустойчив",
    "adjustable": "регулируем", "rotatable": "въртящ се",
    "rechargeable": "презареждаем", "lightweight": "лек",
    "quick": "бърз", "fast": "бърз", "smart": "умен",
    "digital": "цифров", "wired": "кабелен", "magnetic": "магнитен",
    "clip": "щипка", "hook": "кука", "ring": "пръстен",
    "grip": "захват", "handle": "дръжка", "cap": "капачка",
    "tray": "поставка", "dock": "док", "station": "станция",
    "power": "захранване", "bank": "банка", "hub": "хъб",
}


def _translate_title_bg(text: str) -> str:
    """Real, deterministic word/phrase substitution -- see the module-
    level comment above _EN_BG_PHRASES for why this exists and its
    honest limitations (curated glossary, not full machine translation).
    Case-insensitive matching; unmatched tokens (brand names, model
    numbers, technical codes) pass through completely unchanged, which
    is exactly the desired behavior for preserving brand/model names.

    Uses \\b word-boundary regex substitution rather than manual
    split(" ") + strip-punctuation tokenizing -- confirmed live the
    latter misses words with INTERNAL punctuation like "Case(Silver)"
    (the parenthesis sits mid-token, not at the edges), the exact same
    bug class already found and fixed in Tags logic (patch 44). \\b
    already correctly treats the boundary before "(" as a word edge
    without needing any manual tokenization at all.
    """
    result = text
    for en, bg in _EN_BG_PHRASES:
        result = re.sub(re.escape(en), bg, result, flags=re.IGNORECASE)
    for en, bg in _EN_BG_WORDS.items():
        result = re.sub(r"\b" + re.escape(en) + r"\b", bg, result, flags=re.IGNORECASE)
    return result


def _get_model(specs: dict) -> str:
    return (
        specs.get("Model Number")
        or specs.get("Model")
        or specs.get("Model No")
        or specs.get("Model No.")
        or ""
    )


def _get_brand_and_model_phrase(brand: str, model: str, name: str) -> str:
    """The combined Brand+Model chunk to place in the middle of a
    reordered title. If an explicit Model spec exists, just brand+model.
    Otherwise, real Sunsky product names almost always place the model/
    product-line immediately after the brand in the free text itself
    ("Samsung Galaxy S26 5G...", "GoPro Hero 12...") rather than as a
    separate spec field -- confirmed live this is the common case, not
    the exception, for phone/electronics accessories.

    Captures words immediately following the brand, but stops the
    moment it hits a word already recognized as a descriptive term
    (glossary word, stopword, or color) -- confirmed live the naive
    "always grab up to 3 words" version incorrectly absorbed real
    descriptive words like "Waterproof" and "Electroplated" into the
    model phrase just because they happened to sit near the brand.
    Only genuinely unrecognized words (numbers, product-line names like
    "Galaxy"/"Hero"/"ROSSINI") get absorbed now.
    """
    if not brand:
        return model
    if model:
        return f"{brand} {model}"
    m = re.search(r"\b" + re.escape(brand) + r"\b", name, flags=re.IGNORECASE)
    if not m:
        return brand
    rest_words = re.findall(r"[A-Za-z0-9]+", name[m.end():])
    model_words: list[str] = []
    for w in rest_words[:3]:
        w_lower = w.lower()
        if w_lower in _EN_BG_WORDS or w_lower in _TAG_STOPWORDS or w_lower in _TAG_COLOR_WORDS:
            break
        model_words.append(w)
    return (brand + " " + " ".join(model_words)).strip() if model_words else brand


def _get_variant(specs: dict, name: str) -> str:
    """Color/size variant, for the Title reorder formula. Prefers the
    Color spec field (most reliable); falls back to detecting a known
    color word in the name itself (reusing _TAG_COLOR_WORDS, same set
    already used for Tags' color exclusion)."""
    color = specs.get("Color") or specs.get("Colour") or ""
    if color:
        return color
    for w in re.findall(r"[A-Za-z]+", name):
        if w.lower() in _TAG_COLOR_WORDS:
            return w
    return ""


def _logic_title(product: dict, options: dict, resolved: dict) -> str:
    csv_title = (product.get("csv_title") or "").strip()
    if csv_title:
        return csv_title[:120]

    name = _strip_html(product.get("name", ""))
    if name:
        name = name[0].upper() + name[1:]

    lang = options.get("target_language", "bg")

    # Client feedback item (Title word order): "[Product Type] +
    # [Brand] + [Model] + [Key Technical Specification] + [Variant]."
    # First confirmed and fixed for AI mode (patch 74) -- AI mode's
    # prompt was leading with Brand+Model because that's what my own
    # hardcoded example showed it. Client then explicitly asked for the
    # same fix in Logic mode too. Logic mode has no prompt to fix, so
    # this deterministically extracts Brand/Model/Variant from specs
    # (reusing the same helpers as Tags and other Logic-mode fields)
    # and removes their exact word matches from the raw name, leaving
    # everything else (Type + Key Spec, kept together since reliably
    # telling them apart from pure text isn't possible without AI) as
    # the leading chunk -- then reassembles in the requested order.
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    brand = _get_brand(specs)
    model = _get_model(specs)
    variant = _get_variant(specs, name)
    brand_model_phrase = _get_brand_and_model_phrase(brand, model, name)

    remainder = name
    for token in (brand_model_phrase, brand, variant):
        if token:
            remainder = re.sub(r"\b" + re.escape(token) + r"\b", "", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"\(\s*\)", "", remainder)  # empty parens left behind after variant removal
    remainder = re.sub(r"[\s\-,]+", " ", remainder).strip(" -,")
    # Strip a leading stopword left dangling at the start ("For Honor..."
    # -> brand phrase removed from later in the string -> "For" alone
    # remains at the front, which reads badly as a title's first word).
    remainder_words = remainder.split(" ")
    if remainder_words and remainder_words[0].lower() in _TAG_STOPWORDS:
        remainder = " ".join(remainder_words[1:])

    if lang == "bg" and remainder:
        remainder = _translate_title_bg(remainder)

    parts = [p for p in [remainder, brand_model_phrase] if p]
    title = " ".join(parts)
    if variant:
        title = f"{title} - {variant}" if title else variant
    if not title:
        title = name  # nothing extracted at all -- fall back to the raw name rather than an empty title

    max_chars = int(options.get("max_chars", 120))
    return _truncate_no_mid_word(title, max_chars)


# Client feedback item #3 (doc): "The logic option at the moment works
# as Derive option and just copy part of the description." Confirmed:
# _logic_description's old intro did `body = desc or fallback` -- when
# Sunsky's raw description existed, it was used VERBATIM as the "logic"
# output, which is genuinely a "derive" (extract-and-clean) behavior,
# not "logic" (rule-based composition). These templates compose a real
# sentence from structured data (product name) instead, so Logic mode
# never reproduces the raw source text for this field. Several variants
# per language, chosen deterministically per product (via SKU hash) so
# regenerating the same product is stable, but different products in a
# batch don't all read identically.
_INTRO_TEMPLATES_EN = [
    "{name} is designed to deliver reliable performance and lasting value for everyday use.",
    "Meet the {name} — built with care to combine practicality, durability, and everyday convenience.",
    "The {name} offers a dependable, well-made solution for anyone looking for quality without compromise.",
    "Discover the {name}, crafted to provide reliable performance backed by thoughtful, practical design.",
]
_INTRO_TEMPLATES_BG = [
    "{name} е създаден да предложи надеждна работа и трайна стойност за ежедневна употреба.",
    "Запознайте се с {name} — изработен внимателно, съчетаващ практичност, издръжливост и удобство.",
    "{name} предлага надеждно и добре изработено решение за всеки, който търси качество без компромис.",
    "Открийте {name}, създаден да осигури надеждна работа, подкрепена от практичен и обмислен дизайн.",
]


def _pick_variant(templates: list[str], seed: str) -> str:
    """Deterministic (stable across re-generations of the same product)
    but varied (different products land on different variants) pick --
    Python's built-in hash() is randomized per-process by default and
    would silently break the "stable per product" property, so this
    uses zlib.crc32 instead.
    """
    idx = zlib.crc32(seed.encode("utf-8")) % len(templates)
    return templates[idx]


_TAG_STOPWORDS = {
    "for", "new", "the", "a", "an", "with", "and", "or", "of", "to", "in",
    "original", "genuine", "hot", "sale", "1pc", "2pcs", "3pcs", "set",
}

# Client feedback item #2 (Tags) confirmed live: excluding Color from the
# specs-based lookup wasn't enough on its own -- a trailing "(Silver)"/
# "(Black)" etc. in the product NAME itself (an extremely common Sunsky
# naming pattern) was still reaching tags via the first/last-word
# name-extraction fallback. Filtered out of the word list itself so
# neither path can surface a color.
_TAG_COLOR_WORDS = {
    "black", "white", "silver", "gold", "blue", "red", "green", "yellow",
    "pink", "purple", "orange", "grey", "gray", "brown", "beige", "clear",
    "transparent", "rose", "navy", "khaki", "camo", "multicolor",
}

# Client feedback item #2 (Tags): "some are wrong – exclude from the
# logic to use color/pattern, include in the logic the use brand,
# model, type of the product." Color/Colour was previously the TOP
# priority tag source; now removed entirely. Brand was already
# included; Model and product Type are new. Deliberately a pure
# allowlist now (previously also had a fallback to "any non-measurement
# spec value" when none of the preferred keys matched -- exactly how
# Color/Pattern could sneak in as a tag even without being explicitly
# requested) -- safer by construction, not just excluded by name.
_TAG_PREFERRED_SPEC_KEYS = (
    "brand", "compatible brand", "manufacturer",
    "model", "model number", "model no",
    "type", "product type",
)


def _clean_tag_word(w: str) -> str:
    """Strip stray punctuation a raw title word can carry -- e.g. a
    trailing "Blue)" -> "Blue". Does NOT handle parenthesis-as-word-
    boundary (e.g. "Case(Silver)"); that's split out before this runs.
    """
    return re.sub(r"[^\w\s-]", "", w).strip()


def _tag_case(w: str) -> str:
    """Title Case, except:
    - genuine all-caps brand/model codes (FMFXTR, ZTTO, etc.), which
      .title() would otherwise mangle into "Fmfxtr", damaging the brand
    - genuine mixed-case brand names (GoPro, iPhone) that already carry
      meaningful internal capitalization -- confirmed live .title() was
      flattening "GoPro" into "Gopro", same class of problem.
    """
    if len(w) >= 3 and w.isupper():
        return w
    # Has an uppercase letter somewhere after the first character ->
    # deliberate internal capitalization (GoPro, iPhone), not just
    # "the source text happened to be capitalized". Leave it exactly
    # as given rather than re-casing.
    if any(c.isupper() for c in w[1:]):
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
    words = [w for w in words if w and w.lower() not in _TAG_STOPWORDS and w.lower() not in _TAG_COLOR_WORDS]

    tags: list[str] = []

    # Brand / Model / Type -- in that priority order, only from these
    # specific spec keys. No fallback to "whatever spec value looks
    # reasonable" anymore, which is what let Color/Pattern in before.
    specs_lower = {k.strip().lower(): v for k, v in specs.items()}
    for key in _TAG_PREFERRED_SPEC_KEYS:
        v = specs_lower.get(key)
        if isinstance(v, str) and v.strip():
            tag = _tag_case(v.strip())
            if tag not in tags:
                tags.append(tag)

    # Product "type" fallback when there's no explicit Type/Product Type
    # spec field: a meaningful word from the product's own category/name
    # still communicates what kind of product this is, just less
    # precisely than a real Type spec would.
    cat = product.get("category", "")
    if cat:
        cat_tag = _tag_case(cat.strip())
        if cat_tag not in tags:
            tags.append(cat_tag)
    elif words:
        first_tag = _tag_case(words[0])
        if first_tag not in tags:
            tags.append(first_tag)

    # Client feedback confirmed live (twice): a "last word from the
    # product name" tag was previously appended here too, but Sunsky's
    # naming convention structurally puts color/pattern/variant info at
    # the END of product names ("...Case(Silver)", "...Crystal
    # Blossom") -- first "Silver" leaked through this way, then
    # "Blossom" (a pattern name no color blocklist would ever contain)
    # leaked through the exact same path. No word blocklist can be
    # complete against an unbounded set of possible pattern names --
    # removed the unreliable extraction itself rather than continuing
    # to patch individual words into a list that will always have gaps.
    # Brand/Model/Type (from specs) and the first name-word (reliably
    # the brand/core product identity in this naming convention, not a
    # trailing variant qualifier) are enough on their own.

    max_tags = int(options.get("max_tags", 3))
    return ", ".join(tags[:max_tags])


# Client feedback confirmed live via screenshot: Description's
# "features" bullet list was dumping raw shipping/logistics metadata
# verbatim -- "Package Weight", "Carton Weight", "Carton Size",
# "Loading Container: 20GP: 290 cartons * 120 pcs = 34800 pcs". This is
# internal supplier warehouse/shipping data, never meant for customer-
# facing content, and leaked regardless of Lock Specs Table (a
# completely separate, already-working mechanism) since the features
# list took the first 8 raw spec entries with zero relevance filtering
# at all. Matched by substring since Sunsky's exact key phrasing varies
# ("Package Weight" vs "One Package Weight" vs "Carton Weight" etc.).
_SPEC_KEY_EXCLUDE_SUBSTRINGS = (
    "package", "carton", "container", "loading", "moq", "lead time",
    "warehouse", "shipping", "freight", "pallet",
)


def _is_logistics_spec_key(key: str) -> bool:
    key_lower = key.strip().lower()
    return any(sub in key_lower for sub in _SPEC_KEY_EXCLUDE_SUBSTRINGS)


def _logic_description(product: dict, options: dict, resolved: dict) -> str:
    name = product.get("name", "Product")
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    lang = options.get("target_language", "bg")

    structure = options.get("structure", ["intro", "features", "benefits", "compatibility", "closing"])
    parts: list[str] = []

    if "intro" in structure:
        # {name} is interpolated verbatim regardless of language -- it's
        # Sunsky's own English product name/brand text, which client
        # feedback item #16 explicitly says must never be translated:
        # "We need to lock logic to not generate brand names or models
        # in bulgarian... Input from sunsky is always in english."
        #
        # Deliberately does NOT read product["description"] at all --
        # that was the exact bug (client feedback item #3): Logic mode
        # was reproducing Sunsky's raw source text verbatim instead of
        # composing anything. seed uses the product's own SKU (falling
        # back to name) so the same product consistently gets the same
        # variant across re-generations, while different products in a
        # batch land on different ones.
        templates = _INTRO_TEMPLATES_BG if lang == "bg" else _INTRO_TEMPLATES_EN
        seed = str(product.get("site_sku") or product.get("sku") or name)
        intro_text = _pick_variant(templates, seed).format(name=name)
        parts.append(f"<p>{intro_text}</p>")

    if "features" in structure and specs:
        customer_specs = {k: v for k, v in specs.items() if not _is_logistics_spec_key(k)}
        items = "".join(
            f"<li><strong>{k}:</strong> {v}</li>"
            for k, v in list(customer_specs.items())[:8]
        )
        if items:
            parts.append(f"<ul>{items}</ul>")

    if "benefits" in structure:
        benefits_text = (
            "Произведен по високи стандарти за качество, предлагащ отлична стойност и надеждна работа."
            if lang == "bg" else
            "Built to high quality standards, offering outstanding value and reliable performance."
        )
        parts.append(f"<p>{benefits_text}</p>")

    if "compatibility" in structure:
        brand = _get_brand(specs)
        if brand:
            label = "Съвместим с" if lang == "bg" else "Compatible with"
            parts.append(f"<p><em>{label}: {brand}</em></p>")

    if "closing" in structure:
        closing_text = (
            f"Поръчайте своя {name} днес и усетете разликата, която качеството прави."
            if lang == "bg" else
            f"Order your {name} today and experience the difference quality makes."
        )
        parts.append(f"<p>{closing_text}</p>")

    # Note: intentionally NOT "parts else (desc or name)" -- falling back
    # to the raw Sunsky description here would reintroduce the exact
    # copy-the-source bug this function was just fixed for for the intro
    # section specifically. If structure excludes every section (an
    # unusual config), fall back to just the name.
    return "\n".join(parts) if parts else f"<p>{name}</p>"


# ─────────────────────────────────────────────────────────────────────────────
# Derive generators (consume resolved field values)
# ─────────────────────────────────────────────────────────────────────────────

def _derive_slug(product: dict, options: dict, resolved: dict) -> str:
    # Deliberately does NOT use resolved["title"] -- client feedback
    # item #16 explicit exception: "except url slug (keep logic as is
    # right now)". Title can now be Bulgarian (Cyrillic) via
    # _logic_title's real translation; _slugify() does
    # text.encode("ascii","ignore"), which would silently strip every
    # Cyrillic character and produce an empty/garbled slug if this used
    # the resolved (possibly-Bulgarian) title instead of the raw,
    # always-English Sunsky product name.
    title = product.get("name", "") or resolved.get("title", "")
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
    # Bulgarian equivalents -- Title can now produce Bulgarian text (see
    # _translate_title_bg), which uses these exact words for the same
    # filler terms.
    "за", "с", "и", "в", "на",
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
    # Client feedback confirmed live: a title that DID translate
    # correctly for Target Language=Bulgarian ("...водоустойчив...
    # калъф" appearing later in the string) still produced an ALL-
    # ENGLISH Focus Keyword, because the plain "first 5 words by
    # position" selection let measurement/spec tokens (50m, 4K, 5G,
    # 196ft) and the brand name (already prepended separately, so
    # redundant here) fill the entire 5-word budget before ever
    # reaching a real, translatable descriptive word. Skips both so
    # genuine vocabulary gets a real chance to appear regardless of
    # where it sits in the title.
    brand_lower = brand.lower() if brand else None
    kept = [
        w for w in kept
        if not re.match(r"^\d+[a-zA-Z]*$", w)  # 50m, 4K, 5G, 196ft, 2024
        and (not brand_lower or w.lower() != brand_lower)
    ]
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
    lang = options.get("target_language", "bg")

    if len(text) < 80:
        cta = (
            " Пазарувайте сега за най-добрия избор и премиум качество."
            if lang == "bg" else
            " Shop now for the best selection and premium quality."
        )
        return (text + cta)[:160]

    if len(text) <= 160:
        return text

    # Client feedback item #2 confirmed live: the old approach (raw
    # character slice at 159, back off to the last space) produced
    # grammatically nonsensical fragments like "Произведен по." --
    # no single WORD was cut mid-way, but the result was still an
    # incomplete, meaningless sentence fragment with an artificial
    # period tacked on. Prefer whole SENTENCE boundaries instead, same
    # pattern already used successfully in _derive_short_description.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    result = ""
    for s in sentences:
        candidate = (result + " " + s).strip() if result else s
        if len(candidate) <= 160:
            result = candidate
        else:
            break

    if result:
        return result

    # Even the FIRST sentence alone exceeds 160 chars -- fall back to
    # word-boundary-safe truncation of that one sentence rather than
    # returning nothing.
    return _truncate_no_mid_word(sentences[0], 160) if sentences else text[:160]


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

    # Client feedback item #16: global target-language toggle (Bulgarian
    # default), applied to every generated field EXCEPT slug and
    # image_names -- "except url slug (keep logic as is right now) and
    # image file names." Injected into options here (same single-point
    # pattern as the specs-table lock above) so both AI mode (via
    # _build_prompt's {language_instruction}) and Logic/Derive mode
    # generators can read options.get("target_language") uniformly with
    # no separate plumbing needed. Excluded fields never see it at all,
    # rather than relying on every generator to remember to ignore it.
    if field not in ("slug", "image_names"):
        options = {**options, "target_language": gs.get("target_language", "bg")}

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
    Generate all enabled fields using DEPENDENCY-DEPTH-ordered execution.

    Client feedback confirmed live via screenshot: Title set to "ai"
    mode, but Slug/Focus Keyword/Meta Description set to "logic" mode
    all produced garbage derived from the raw, untranslated product
    name instead of the actual resolved (translated) title. Root cause:
    the OLD implementation ran ALL "logic"-mode fields first, THEN all
    "ai"-mode fields, THEN "derive"-mode fields -- a rigid MODE-based
    phase order, not a true DEPENDENCY-based one. Since Title was
    "ai" (phase 2) but its dependents (Slug/Focus Keyword/Meta
    Description) were "logic" (phase 1), those dependents ran and
    fell back to raw product data BEFORE Title had even been generated
    yet, regardless of what FIELD_DEPS actually said they depended on.

    Fields now run in "waves" based on their actual position in the
    dependency graph (FIELD_DEPS) -- a field only runs once every field
    it depends on has already resolved, REGARDLESS of whether it's set
    to logic/ai/derive itself. Within each wave, fields are still
    grouped and executed by mode (logic fields in parallel via
    asyncio.gather, then ai fields in parallel, then derive fields
    sequentially) -- identical concurrency/retry/error-handling
    behavior to before, just correctly ordered by real dependency
    instead of by an unrelated mode grouping.

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

    # Depth 0 = no dependencies among enabled fields (title, tags).
    # Depth N = depends only on fields at depth < N. image_names is
    # depth 2 (depends on slug, which is depth 1, which depends on
    # title, which is depth 0) -- a genuine multi-level chain, not
    # just a single title->everything fan-out.
    def _depth(f: str, _chain: frozenset = frozenset()) -> int:
        if f in _chain:
            return 0  # defensive cycle guard; FIELD_DEPS has none today
        deps = [d for d in FIELD_DEPS.get(f, []) if d in enabled]
        if not deps:
            return 0
        return 1 + max(_depth(d, _chain | {f}) for d in deps)

    depths = {f: _depth(f) for f in enabled}
    max_depth = max(depths.values()) if depths else 0

    for wave in range(max_depth + 1):
        wave_fields = [f for f in enabled if depths[f] == wave]
        if not wave_fields:
            continue

        logic_group = [f for f in wave_fields if _mode(f) == "logic"]
        ai_group = [f for f in wave_fields if _mode(f) == "ai"]
        derive_group = [f for f in wave_fields if _mode(f) not in ("logic", "ai")]

        if logic_group:
            phase_results = await asyncio.gather(
                *[run_field(f, product, template, resolved) for f in logic_group],
                return_exceptions=True,
            )
            for f, r in zip(logic_group, phase_results):
                if isinstance(r, Exception):
                    results[f] = {"field": f, "value": "", "source": "logic",
                                   "status": "failed", "error": str(r)}
                    resolved[f] = ""
                else:
                    results[f] = r
                    resolved[f] = r.get("value", "")

        if ai_group:
            phase_results = await asyncio.gather(
                *[run_field(f, product, template, resolved) for f in ai_group],
                return_exceptions=True,
            )
            for f, r in zip(ai_group, phase_results):
                if isinstance(r, Exception):
                    results[f] = {"field": f, "value": "", "source": "ai",
                                   "status": "failed", "error": str(r)}
                    resolved[f] = ""
                else:
                    results[f] = r
                    resolved[f] = r.get("value", "")

        for f in derive_group:
            r = await run_field(f, product, template, resolved)
            results[f] = r
            resolved[f] = r.get("value", "")

    return results
