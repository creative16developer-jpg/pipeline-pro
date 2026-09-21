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
from html.parser import HTMLParser as _HTMLParser
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
    # Client feedback confirmed live via WordPress media library
    # screenshot: "Additional image fields you didn't put them here?
    # all these fields should be here of wordpress media." Alt Text
    # was already a first-class configurable field; Caption and
    # Description (WordPress's OWN media attachment fields, distinct
    # from the WooCommerce product's own description) previously only
    # existed as a hardcoded reuse of Alt Text's value inside
    # upload_image_to_wordpress, with no visibility or toggle in
    # Settings at all. Registered as real fields now, matching every
    # other configurable field's pattern exactly.
    "image_caption",      # derive ← image_alt (WP media Caption field)
    "image_description",  # derive ← short_description (WP media Description field)
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
    "image_caption":      "derive",
    "image_description":  "derive",
}

FIELD_DEPS: dict[str, list[str]] = {
    "slug":              ["title"],
    "image_alt":         ["title"],
    "meta_title":        ["title"],
    "image_names":       ["slug"],
    "short_description": ["description"],
    "meta_description":  ["description"],
    "focus_keyword":     ["title"],
    "image_caption":     ["image_alt"],
    "image_description": ["short_description"],
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
    "image_caption":     "image_caption",
    "image_description": "image_description",
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
    "image_caption":     {"non_empty": True, "max_chars": 125},
    "image_description": {"non_empty": False, "max_chars": 300},
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


class _SpecTableParser(_HTMLParser):
    """Robust table parser handling arbitrarily nested tables. Confirmed
    live (EDA007358326A) that Sunsky wraps some spec categories (e.g.
    "General") in an outer row whose second cell contains a WHOLE
    nested sub-table (e.g. the real "Compatible with" row lives one
    level deeper inside it) -- only the DEEPEST-level rows are genuine
    key/value pairs; a row whose own second cell contains a nested
    <table> is a section header, not a real pair, and is skipped so
    parsing can reach what it actually wraps instead. The previous
    single regex (matching one flat <tr><td>..</td><td>..</td> pattern)
    had no way to tell these apart: its own non-greedy .*? simply
    stopped at the first </td> it found, which for a nested row is
    partway through the inner table, silently pairing the OUTER label
    ("General") with the INNER table's own first cell ("Compatible
    with" itself, not its value) -- and never reaching the real pair
    at all. Confirmed this was already a genuine, pre-existing parsing
    gap, not something introduced by this session's tag/brand fixes:
    those fixes were correct on their own terms, but had nothing
    real to work with for any spec nested this way.
    """
    def __init__(self):
        super().__init__()
        self.pairs: dict[str, str] = {}
        self.in_tr = False
        self.in_td = False
        self.current_cells: list[str] = []
        self.current_cell_text: list[str] = []
        self.row_has_nested_table = False

    def handle_starttag(self, tag, attrs):
        if tag == "table" and self.in_tr:
            self.row_has_nested_table = True
        elif tag == "tr":
            self.in_tr = True
            self.current_cells = []
            self.row_has_nested_table = False
        elif tag == "td":
            self.in_td = True
            self.current_cell_text = []
        elif tag == "br" and self.in_td:
            self.current_cell_text.append(" ")

    def handle_endtag(self, tag):
        if tag == "td":
            self.in_td = False
            text = re.sub(r"\s+", " ", "".join(self.current_cell_text)).strip()
            self.current_cells.append(text)
        elif tag == "tr":
            self.in_tr = False
            if not self.row_has_nested_table and len(self.current_cells) >= 2:
                k, v = self.current_cells[0].strip(), self.current_cells[1].strip()
                if k and v:
                    self.pairs[k] = v

    def handle_data(self, data):
        if self.in_td:
            self.current_cell_text.append(data)


def _parse_params_table(html_str: str) -> dict[str, str]:
    parser = _SpecTableParser()
    try:
        parser.feed(html_str)
    except Exception:
        pass
    return parser.pairs


def _get_raw(product: dict) -> dict:
    return product.get("raw_data") or product.get("rawData") or {}


def _get_brand(specs: dict) -> str:
    return (
        specs.get("Compatible Brand")
        or specs.get("Brand")
        or specs.get("Manufacturer")
        or ""
    )


def _get_manufacturer_brand(raw: dict, specs: dict) -> str:
    """The product's OWN manufacturer -- e.g. "PULUZ" for a PULUZ-made
    GoPro accessory -- NOT "Compatible Brand" (e.g. "GoPro"), which is
    what device the accessory FITS, a genuinely different concept.
    _get_brand() above deliberately checks Compatible Brand first,
    which is correct for its own purpose (correcting a mis-spelled
    "compatible with X" mention in generated text), but would be
    semantically wrong here: assigning "GoPro" as a product's native
    WooCommerce Brand taxonomy term when the product itself is made by
    PULUZ would misrepresent who actually makes it, visibly on a live
    storefront. Used only for the native product_brand taxonomy
    assignment (Review_4.docx item #2), never for any AI-generated
    text correction, which is what _get_brand's own priority order is
    actually tuned for.

    BUG FIX (found live during store testing, PL-115 through PL-117):
    checking only the parsed paramsTable spec dict (specs.get("Brand")
    / specs.get("Manufacturer")) meant this returned nothing at all
    for the large majority of this catalog's products (waterproof
    cases, silicone cases, lens protectors, etc.) -- their paramsTable
    only ever has a "Compatible with" key (what device it fits, e.g.
    "Gopro: Fusion"), deliberately excluded above, with no separate
    Brand/Manufacturer spec key at all. Confirmed the real manufacturer
    (PULUZ, matching Sunsky's own product page's explicit "Brand:
    PULUZ" line) is available all along as a genuine, dedicated,
    top-level field on Sunsky's raw API response -- raw_data["brandName"]
    -- entirely separate from paramsTable, previously never read
    anywhere in this codebase (only ever mentioned in a comment
    elsewhere as a manual cross-reference during a past investigation,
    never actually checked in code). Checked first now, since it's a
    dedicated, structured field far more reliable than trying to infer
    a manufacturer from unstructured spec text; specs.get("Brand") /
    specs.get("Manufacturer") kept as a fallback for the rare product
    whose raw_data genuinely lacks brandName but does have one of
    those spec keys instead.
    """
    return (
        (raw.get("brandName") or "").strip()
        or specs.get("Brand", "").strip()
        or specs.get("Manufacturer", "").strip()
    )


def _levenshtein(a: str, b: str) -> int:
    """Plain edit distance, no external dependency. Only ever called on
    short brand-length tokens (see _fix_brand_spelling), so the O(n*m)
    DP table here is negligible cost regardless."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _fix_brand_spelling(text: str, product: dict) -> str:
    """Client feedback confirmed live via a generated product (PU760T):
    the real brand, confirmed correct in both raw Sunsky data
    (raw_data.brandName) and the raw Sunsky product name, is "PULUZ" --
    the AI-generated description (content_source: "ai:anthropic", a
    genuine success, not a fallback) instead read "PULYZ" throughout.
    Not a translation-instruction ambiguity like the earlier Bulgarian
    fix -- the model wasn't choosing to translate or not, it simply
    mis-transcribed one character of a short proper noun it should have
    copied verbatim. An improved prompt can reduce this kind of slip
    but can't reliably guarantee against it, the same way the existing
    AI-title-too-short sanity check above acknowledges prompt-following
    alone can't be fully guaranteed -- so this corrects it in the
    output directly rather than relying on wording alone.

    Scans the generated text for word-like tokens within a small edit
    distance of the product's own known-correct brand name (from the
    same raw specs table _get_brand already reads elsewhere in this
    file) and replaces a near-miss with the correct spelling. Distance
    capped at 2 and length capped within 1 character of the real
    brand's length specifically to avoid false-positive corrections on
    unrelated short words that happen to share some letters -- brand
    names are typically distinctive enough that this stays safe, and
    an exact case-insensitive match is left untouched (nothing to fix).
    """
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    brand = _get_brand(specs).strip()
    if not brand or len(brand) < 3 or not text:
        return text

    def _repl(m: "re.Match") -> str:
        word = m.group(0)
        if word.lower() == brand.lower():
            return word  # already correct
        if abs(len(word) - len(brand)) > 1:
            return word
        if _levenshtein(word.lower(), brand.lower()) <= 2:
            return brand
        return word

    return re.sub(r"[A-Za-z][A-Za-z0-9]*", _repl, text)


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

# Client feedback confirmed live via a real generated product
# (SYA002283914A): "russian or chinese words in the text" -- and the
# client's own follow-up test confirmed the raw Sunsky source data
# for that exact product was 100% clean English, ruling out a
# source-language leak. The model generated entire fields (Description,
# Short Description, Meta Description) fully in Russian from scratch,
# despite an explicit Bulgarian instruction. Chinese characters occupy
# a distinct, easily and reliably detectable Unicode range. Russian
# and Bulgarian both use Cyrillic, so no simple character-set check
# can tell them apart in general -- BUT Bulgarian's alphabet is
# missing three letters Russian has (Ы, Э, Ё) -- confirmed directly
# against the client's own real leaked text ("Это защитный чехол...")
# containing "Э" -- so their presence in text meant to be Bulgarian is
# a reliable, zero-false-positive signal specifically for Russian,
# without needing a full language-detection library.
def _has_wrong_language(text: str, target_language: str) -> bool:
    if target_language == "en":
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return True
    if any(ch in "ыЫэЭёЁ" for ch in text):
        return True
    return False


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
    # Client feedback confirmed live: Sunsky's Brand spec value often
    # has no space before trailing digits ("Insta360"), but the actual
    # product name/title frequently does ("Insta 360 X6") -- an exact
    # string match silently failed here, causing this to fall back to
    # just the bare brand with zero model words captured at all.
    # Builds a regex tolerant of optional whitespace between the
    # brand's letters and any trailing digits, instead of a literal
    # re.escape(brand) match.
    brand_letters_match = re.match(r"^([A-Za-z]+)(\d+)$", brand)
    if brand_letters_match:
        brand_pattern = re.escape(brand_letters_match.group(1)) + r"\s*" + re.escape(brand_letters_match.group(2))
    else:
        brand_pattern = re.escape(brand)
    m = re.search(r"\b" + brand_pattern, name, flags=re.IGNORECASE)
    if not m:
        return brand
    matched_brand_text = m.group(0)
    rest_words = re.findall(r"[A-Za-z0-9]+", name[m.end():])
    model_words: list[str] = []
    for w in rest_words[:3]:
        w_lower = w.lower()
        if w_lower in _EN_BG_WORDS or w_lower in _TAG_STOPWORDS or w_lower in _TAG_COLOR_WORDS:
            break
        model_words.append(w)
    return (matched_brand_text + " " + " ".join(model_words)).strip() if model_words else matched_brand_text


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
        # Client feedback (Review_4.docx, item #10): "Generated content
        # cut off mid-sentence." Found while auditing every raw
        # character-slice in this file for the same class of bug
        # already fixed elsewhere (see _truncate_no_mid_word's own
        # docstring) -- this one specific site was missed: a plain
        # csv_title[:120] slice with zero word-boundary protection,
        # unlike every other field generator below, which all reuse
        # _truncate_no_mid_word already. A long CSV-supplied title
        # would have been chopped mid-word here exactly like the
        # already-fixed _logic_title case the docstring describes.
        return _truncate_no_mid_word(csv_title, 120)

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
    # Client feedback confirmed live (Review_4.docx item #13, "wrong
    # generated tags -- only 'Insta360' as tag"): confirmed via direct
    # investigation of a real product's raw Sunsky spec data that
    # "compatible with" is genuinely the key Sunsky uses for a large
    # share of this catalog (waterproof cases, silicone cases, lens
    # protectors) -- these products have NO separate Brand/Manufacturer
    # spec key at all, only this one, so it was never matching any
    # existing key here and tag generation fell back to a weaker
    # method. Placed last (lowest priority): a genuine Brand/
    # Manufacturer/Type field, when one exists, is still a cleaner,
    # more direct signal than parsing it out of this field's own
    # "Brand: Model" formatting below.
    "compatible with",
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
            # Client feedback confirmed live: "compatible with"'s raw
            # value is frequently HTML-entity-encoded ("Gopro:&nbsp;
            # Fusion" -- the bold-tag markup around "Gopro:" is already
            # stripped by _parse_params_table, but HTML entities like
            # &nbsp; are not, since that function only strips actual
            # <tag> markup) and formatted as "Brand: Model" rather than
            # a clean single value the way brand/manufacturer/type
            # fields already are. Confirmed directly against the real
            # value from this exact key: html.unescape handles the
            # entity, and taking only the part before a colon (when one
            # exists) extracts just the brand ("Gopro"), not the whole
            # "Brand: Model" string, which would otherwise make an
            # oddly-specific, ugly tag.
            v_clean = html.unescape(v.strip())
            if key == "compatible with" and ":" in v_clean:
                v_clean = v_clean.split(":", 1)[0].strip()
            tag = _tag_case(v_clean)
            if tag not in tags:
                tags.append(tag)

    # Product "type" fallback when there's no explicit Type/Product Type
    # spec field: a meaningful word from the product's own category/name
    # still communicates what kind of product this is, just less
    # precisely than a real Type spec would.
    # Client feedback confirmed live via screenshot: only a single
    # "Xiaomi" tag reached WooCommerce for SYA002283914A. Traced the
    # cause directly: this branch checked product.get("category", "")
    # -- confirmed via a full codebase search that this exact key is
    # NEVER set anywhere at all, a dead branch that has never actually
    # fired. The real, already-populated equivalent is
    # product["category_name"] (added earlier this session for the AI
    # generation context feature) -- simply never wired in here too
    # when that field was added, since this function predates it.
    cat = product.get("category_name", "") or product.get("category", "")
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
        # Client feedback: "it should work for all fields" -- audited
        # every raw character-slice in this file after the earlier
        # csv_title fix; this fallback path (only reached when
        # _slugify(title) is genuinely empty, an already-rare edge
        # case) was still using an unprotected raw slice, unlike every
        # other field's truncation. Low real-world risk given how
        # short this fallback string always is relative to the
        # default max_chars, but fixed for consistency and to close
        # the gap outright rather than leave a theoretical one.
        fb = f"product-{sku[-8:].lower()}" if sku else "product"
        return _truncate_no_mid_word(fb, max_chars, boundary="-")

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


def _derive_image_caption(product: dict, options: dict, resolved: dict) -> str:
    """WordPress media library's Caption field (labelled "Short
    description" in some admin themes -- confirmed live via
    screenshot). Client feedback: "all these fields should be here of
    wordpress media" -- previously just a hardcoded reuse of Alt
    Text's value inside upload_image_to_wordpress with no field of its
    own; registered as a real, independently-toggleable field now.
    Defaults to reusing image_alt's already-resolved value -- Caption
    is conventionally shown directly under an image on many WordPress
    themes, where repeating the same short, accurate description Alt
    Text already provides is a normal, sensible default rather than a
    placeholder needing distinct text.
    """
    alt = resolved.get("image_alt", "")
    max_chars = int(options.get("max_chars", 125))
    if len(alt) > max_chars:
        alt = _truncate_no_mid_word(alt, max_chars)
    return alt


def _derive_image_description(product: dict, options: dict, resolved: dict) -> str:
    """WordPress media library's Description field. Client feedback:
    "all these fields should be here of wordpress media." Deliberately
    NOT just another copy of Alt Text/Caption's text -- reuses the
    product's own short_description instead, giving this field
    genuinely different, more detailed content than the other two
    media fields rather than three identical copies of the same short
    phrase. Falls back to image_alt only if short_description isn't
    available for some reason (e.g. that field disabled in Settings).
    """
    text = resolved.get("short_description", "") or resolved.get("image_alt", "")
    max_chars = int(options.get("max_chars", 300))
    if len(text) > max_chars:
        text = _truncate_no_mid_word(text, max_chars)
    return text


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
    model = _get_model(specs)
    max_chars = int(options.get("max_chars", 60))

    # Client feedback confirmed live: "Insta 360 X6" (a genuine 3-word
    # brand+model unit) lost its last two words ("360", "X6") entirely
    # from the Focus Keyword -- traced to the OLD flat "first 5 words"
    # selection treating every word as an independent competitor for
    # the same 5-word budget, so by the time "Insta" (word 5) was
    # reached, there was no room left for the REST of that same model
    # name. Reuses _get_brand_and_model_phrase (patch 76's Title
    # reordering logic) to capture the whole brand+model as ONE
    # cohesive unit first ("Insta 360 X6"), guaranteeing it survives
    # intact -- then fills any remaining budget with real descriptive
    # words from elsewhere in the title, rather than the old approach
    # where a multi-word model name could get arbitrarily cut off
    # mid-way depending on how many descriptive words preceded it.
    brand_model_phrase = _get_brand_and_model_phrase(brand, model, title)

    words = [w for w in re.split(r"\s+", title.strip()) if w]
    kept = [w for w in words if w.lower() not in _FOCUS_KEYWORD_STOPWORDS]
    if brand_model_phrase:
        remainder = re.sub(r"\b" + re.escape(brand_model_phrase) + r"\b", "", " ".join(kept), flags=re.IGNORECASE)
    else:
        remainder = " ".join(kept)
    remainder_words = [w for w in re.split(r"\s+", remainder.strip()) if w]
    remainder_words = [
        w for w in remainder_words
        if not re.match(r"^\d+[a-zA-Z]+$", w)  # 50m, 4K, 5G, 196ft -- NOT bare numbers like 360, 2024
    ]

    brand_model_word_count = len(brand_model_phrase.split()) if brand_model_phrase else 0
    remaining_budget = max(2, 5 - brand_model_word_count)  # always leave room for at least 2 descriptive words
    kept = ([brand_model_phrase] if brand_model_phrase else []) + remainder_words[:remaining_budget]
    phrase = " ".join(dict.fromkeys(kept))  # de-dupe, preserve order

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
        # Client feedback confirmed live via screenshot: Short
        # Description showed garbled, seemingly-cut text ("Features1.
        # ... environments. 2.") -- matches exactly this fallback path
        # (reached when the sentence-by-sentence loop above never sets
        # `result` at all, e.g. the very first sentence alone already
        # exceeds 400 chars, so the loop's own `break` fires before
        # ever assigning it) using an unprotected raw text[:400] slice
        # that could cut mid-word. Same class of bug already fixed
        # once for meta_description's own equivalent fallback -- this
        # one was missed in that earlier pass. "it should work for all
        # fields" -- audited every raw slice in this file after that
        # feedback and found this genuinely unsafe one.
        result = _truncate_no_mid_word(text, 400)

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
    "image_caption":     _derive_image_caption,
    "image_description": _derive_image_description,
}

_DERIVE_GENERATORS: dict[str, Any] = {
    "slug":              _derive_slug,
    "image_alt":         _derive_image_alt,
    "meta_title":        _derive_meta_title,
    "image_names":       _derive_image_names,
    "short_description": _derive_short_description,
    "meta_description":  _derive_meta_description,
    "focus_keyword":     _derive_focus_keyword,
    "image_caption":     _derive_image_caption,
    "image_description": _derive_image_description,
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

def _prepare_field_context(field: str, product: dict, template: dict) -> tuple[str, dict, dict, dict | None]:
    """
    Shared setup extracted from run_field: resolves the field's mode,
    builds its options dict (with target_language injected per the
    existing exclusion rules), and applies the specs-table lock when
    configured. Returns (mode, options, product, override_result) --
    override_result is non-None only when an explicit override value
    exists for this field, in which case the caller should return it
    directly without going any further.

    Shared with the new batch-mode prompt builder (get_batchable_ai_
    fields) below, so both the live-call path and the batch-prompt-
    building path apply the exact same target_language/specs-lock
    rules rather than risking the two drifting apart over time.
    """
    override = (template.get("overrides") or {}).get(field)
    if override is not None:
        return "override", {}, product, {"field": field, "value": str(override), "source": "override", "status": "ok"}

    field_cfg = (template.get("fields") or {}).get(field, {})
    options = field_cfg.get("options", {})
    mode = field_cfg.get("mode") or FIELD_DEFAULT_MODE.get(field, "logic")

    gs = template.get("globalSettings") or {}
    if field not in ("slug", "image_names"):
        options = {**options, "target_language": gs.get("target_language", "bg")}
        # Client feedback: "Do the data fields we generate have
        # sufficient access to structured category data... prior to
        # the actual generation?" Confirmed via direct code
        # investigation: category data was already resolved elsewhere
        # in the pipeline (Enrich's own attribute extraction) but never
        # threaded into the AI generation prompt context at all.
        # product["category_name"] is set once per product by
        # pipeline_tasks.py's _run_generate, the same way it already
        # sets up target_language above -- passed through here into
        # options so _build_prompt/_build_product_context in
        # ai_generator.py can include it. Excluded from slug/
        # image_names for the same reason target_language already is:
        # neither field's prompt template references category context
        # at all.
        options = {**options, "category_name": product.get("category_name", "")}

    if gs.get("lock_specs_table", False):
        product = dict(product)  # shallow copy -- don't mutate the caller's dict
        for raw_key in ("raw_data", "rawData"):
            if isinstance(product.get(raw_key), dict) and "paramsTable" in product[raw_key]:
                raw_copy = dict(product[raw_key])
                raw_copy["paramsTable"] = ""
                product[raw_key] = raw_copy

    return mode, options, product, None


def get_batchable_ai_fields(product: dict, template: dict) -> dict[str, str]:
    """
    For Claude Batch Processing: returns {field_name: prompt} for every
    field in this product's template that's eligible for batching --
    "ai" mode, AI enabled globally, and depth 0 (no dependencies on any
    other field). Depth 0 is the deliberate scope for the first version:
    Anthropic's Batch API requires every request to be fully independent
    (no live dependency resolution mid-batch is possible), so a
    dependent AI-mode field (e.g. an AI-mode Meta Title depending on an
    AI-mode Title) genuinely can't be batched without the real resolved
    title text first -- those rare cases still fall back to running
    synchronously, same as before batch mode existed. In practice this
    covers the common case cleanly, since Title/Description (depth 0)
    are by far the most commonly AI-enabled fields observed this
    session, with downstream fields like Meta Title/Focus Keyword
    typically left on logic mode.
    """
    gs = template.get("globalSettings") or {}
    if not gs.get("ai_enabled", False):
        return {}

    fields_cfg = template.get("fields") or {}

    def _enabled(f: str) -> bool:
        return fields_cfg.get(f, {}).get("enabled", True)

    def _mode(f: str) -> str:
        return fields_cfg.get(f, {}).get("mode") or FIELD_DEFAULT_MODE.get(f, "logic")

    enabled = [f for f in FIELD_LIST if _enabled(f)]

    def _depth(f: str, _chain: frozenset = frozenset()) -> int:
        if f in _chain:
            return 0
        deps = [d for d in FIELD_DEPS.get(f, []) if d in enabled]
        if not deps:
            return 0
        return 1 + max(_depth(d, _chain | {f}) for d in deps)

    from pipeline.ai_generator import _build_prompt

    prompts: dict[str, str] = {}
    for f in enabled:
        if _mode(f) != "ai" or _depth(f) != 0:
            continue
        mode, options, prod, override_result = _prepare_field_context(f, product, template)
        if override_result is not None:
            continue  # explicit override -- nothing to batch, no AI call needed at all
        prompts[f] = _build_prompt(f, prod, options)
    return prompts


async def run_field(
    field: str,
    product: dict,
    template: dict,
    resolved: dict | None = None,
    precomputed_ai: dict[str, tuple[bool, str]] | None = None,
) -> dict:
    """
    Generate content for a single field.
    template is a plain dict (not Pydantic) with keys: globalSettings, fields, overrides.
    Returns: {field, value, source, status, error?}

    precomputed_ai, when given, is {field: (succeeded, text_or_error)} --
    results already fetched from a completed Claude Message Batch (see
    get_batchable_ai_fields above). When this field is "ai" mode and has
    a precomputed entry, that result is used directly instead of making
    a live AI call -- this is what lets generate_product() apply batch
    results through the SAME dependency-aware DAG logic (patch 93) used
    for a normal synchronous run, rather than needing a separate,
    parallel result-application code path.
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
        if precomputed_ai is not None and field in precomputed_ai:
            succeeded, text_or_error = precomputed_ai[field]
            if succeeded:
                text_or_error = _fix_brand_spelling(text_or_error, product)
                # Client feedback confirmed live via a real generated
                # product: "russian or chinese words in the text" --
                # source data in a language other than English can get
                # copied verbatim into otherwise-Bulgarian output
                # instead of translated (see _language_instruction's
                # own strengthened wording for the actual prompt-side
                # fix).
                #
                # UPGRADE (confirmed live via a real generated product,
                # SYA002283914A): this specific product's raw Sunsky
                # source data was 100% clean English -- nothing to copy
                # or leak from. The model still generated ENTIRE
                # fields (Description, Short Description, Meta
                # Description) fully in Russian from scratch, despite
                # a clean English source and an explicit Bulgarian
                # instruction -- a genuine language-consistency
                # failure, not a source-language leak. A silent,
                # log-only warning isn't enough for a failure this
                # severe (a whole field in the wrong language reaching
                # the client's actual store); this now retries once,
                # live, via generate_with_ai directly, before falling
                # back to accepting the result.
                if _has_wrong_language(text_or_error, options.get("target_language", "bg")):
                    logger.warning(
                        f"[{field}] Generated text is in the wrong language despite "
                        f"target_language={options.get('target_language', 'bg')!r} -- "
                        f"retrying once: {text_or_error[:200]!r}"
                    )
                    try:
                        from pipeline.ai_generator import generate_with_ai
                        retry_text = await generate_with_ai(field, product, ai_provider, ai_model, options)
                        retry_text = _fix_brand_spelling(retry_text, product)
                        if not _has_wrong_language(retry_text, options.get("target_language", "bg")):
                            text_or_error = retry_text
                        else:
                            logger.warning(f"[{field}] Retry still in the wrong language -- using it anyway, no further retries")
                            text_or_error = retry_text
                    except Exception as _lang_retry_err:
                        logger.warning(f"[{field}] Language retry failed ({_lang_retry_err}) -- using original result")
                return {"field": field, "value": text_or_error,
                         "source": "ai:anthropic:batch", "status": "ok"}
            # Batch request failed for this field -- apply the same
            # fallback_strategy handling a live call failure would get,
            # rather than a separate, parallel failure path.
            error_msg = text_or_error
            logger.warning(f"[{field}] Batch AI failed, applying '{fallback_strategy}' fallback: {error_msg}")
            if fallback_strategy == "skip":
                return {"field": field, "value": "", "source": "none",
                        "status": "skipped", "error": error_msg}
            if fallback_strategy == "empty":
                return {"field": field, "value": "", "source": "ai:failed",
                        "status": "ok", "error": error_msg}
            mode = "logic"
        else:
            try:
                value = await _run_ai_with_retry(field, product, ai_provider, ai_model, options)
                value = _fix_brand_spelling(value, product)
                source = f"ai:{ai_provider}"

                # Client feedback confirmed live via a real generated
                # product (SYA002283914A, generated through THIS exact
                # live path via "Re-generate content"): entire fields
                # generated fully in Russian from scratch, despite
                # clean English source data and an explicit Bulgarian
                # instruction. Same fix as the batch path's equivalent
                # check just above -- retries once, live, before
                # accepting the result.
                if _has_wrong_language(value, options.get("target_language", "bg")):
                    logger.warning(
                        f"[{field}] Generated text is in the wrong language despite "
                        f"target_language={options.get('target_language', 'bg')!r} -- "
                        f"retrying once: {value[:200]!r}"
                    )
                    try:
                        retry_value = await _run_ai_with_retry(field, product, ai_provider, ai_model, options)
                        retry_value = _fix_brand_spelling(retry_value, product)
                        if _has_wrong_language(retry_value, options.get("target_language", "bg")):
                            logger.warning(f"[{field}] Retry still in the wrong language -- using it anyway, no further retries")
                        value = retry_value
                    except Exception as _lang_retry_err:
                        logger.warning(f"[{field}] Language retry failed ({_lang_retry_err}) -- using original result")

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

async def generate_product(
    product: dict,
    template: dict,
    precomputed_ai: dict[str, tuple[bool, str]] | None = None,
) -> dict:
    """
    Generate all enabled fields using DEPENDENCY-DEPTH-ordered execution.

    precomputed_ai, when given, is forwarded to every run_field call --
    see run_field's own docstring for what it does. Used by Claude Batch
    Processing: after a batch completes, results get run back through
    THIS SAME function (not a separate, parallel result-application
    path), so dependent fields (e.g. logic-mode Meta Title depending on
    the now-resolved AI-mode Title) correctly see the real batch result
    via the normal DAG wave/resolved-dict mechanism, exactly as they
    would for a live, synchronous AI call.

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
                *[run_field(f, product, template, resolved, precomputed_ai) for f in logic_group],
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
                *[run_field(f, product, template, resolved, precomputed_ai) for f in ai_group],
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
            r = await run_field(f, product, template, resolved, precomputed_ai)
            results[f] = r
            resolved[f] = r.get("value", "")

    return results
