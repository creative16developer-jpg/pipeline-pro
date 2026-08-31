"""
AI content generation — provider-agnostic wrapper.

Supports: OpenAI, Anthropic (Claude), Google Gemini.
Falls back gracefully when a provider is unavailable.

Usage:
    value = await generate_with_ai(
        field="description",
        product=product_dict,
        provider="openai",        # "openai" | "anthropic" | "gemini"
        model=None,               # None = use provider default
        options=field_options,
    )
    # Returns a string or raises AIGenerationError
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

# Path to config-store API keys (fallback when env vars not set)
_KEYS_PATH = Path(__file__).parent.parent / "config_store" / "api_keys.json"

# T04 — AI prompt templates externalised to prompts.json (was 9 hardcoded
# strings in _build_prompt below). Loaded once at import time; a missing or
# malformed file is a hard failure so it's never silently using empty prompts.
_PROMPTS_PATH = Path(__file__).parent / "prompts.json"


def _load_prompts() -> dict[str, str]:
    try:
        raw = _PROMPTS_PATH.read_text()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"ai_generator: prompts.json not found at {_PROMPTS_PATH} — "
            f"AI content generation cannot start without prompt templates."
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"ai_generator: prompts.json is malformed JSON ({exc}) — fix the file at {_PROMPTS_PATH}."
        ) from exc
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"ai_generator: prompts.json must be a non-empty object of field -> template.")
    if "default" not in data:
        raise RuntimeError(f"ai_generator: prompts.json is missing the required 'default' template.")
    return data


PROMPT_TEMPLATES: dict[str, str] = _load_prompts()


def _get_api_key(env_var: str, provider: str) -> str | None:
    """Read API key: env var first, then config-store file."""
    key = os.getenv(env_var)
    if key:
        return key
    try:
        if _KEYS_PATH.exists():
            data = json.loads(_KEYS_PATH.read_text())
            return data.get(provider) or None
    except Exception:
        pass
    return None


class AIGenerationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Per-field prompt builders
# ---------------------------------------------------------------------------

def _extract_specs(product: dict) -> dict[str, str]:
    raw = product.get("rawData") or product.get("raw_data") or {}
    params_html = raw.get("paramsTable", "")
    specs: dict[str, str] = {}
    for m in re.finditer(
        r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        params_html, re.DOTALL | re.IGNORECASE,
    ):
        k = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        v = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if k and v:
            specs[k] = v
    return specs


def _build_product_context(product: dict) -> str:
    name = product.get("name", "Product")
    sku = product.get("site_sku") or product.get("sku", "")
    desc = product.get("description", "")
    # Client feedback: "such costs for a 500-character description
    # aren't realistic ... you're the one who needs to figure that
    # out." Found the concrete cause: this raw description was
    # included in EVERY AI prompt with zero truncation. Sunsky's raw
    # descriptions are frequently long HTML/marketing blocks -- since
    # AI pricing is driven primarily by input tokens, not output
    # length, an untruncated multi-thousand-character raw description
    # could add substantial cost to every single AI call regardless of
    # how short the actual requested output was. The AI only needs
    # enough context to understand the product, not the full raw
    # marketing copy (which is also often repetitive/promotional
    # filler rather than genuinely useful product detail).
    MAX_RAW_DESC_CHARS = 600
    if len(desc) > MAX_RAW_DESC_CHARS:
        desc = desc[:MAX_RAW_DESC_CHARS].rsplit(" ", 1)[0] + "…"
    specs = _extract_specs(product)
    specs_text = (
        "\n".join(f"  - {k}: {v}" for k, v in list(specs.items())[:15])
        if specs else "  (none available)"
    )
    return f"Product Name: {name}\nSKU: {sku}\nDescription: {desc or '(none)'}\nSpecifications:\n{specs_text}"


def _language_instruction(options: dict) -> str:
    """Client feedback item #16: global target-language toggle (Bulgarian
    default). Never applied to slug/image_names -- content_service.py's
    run_field only injects options["target_language"] for fields other
    than those two, so this function is simply never invoked for them
    (their prompt templates don't reference {language_instruction} at all).

    Brand/model names must never be translated even in Bulgarian output:
    "We need to lock logic to not generate brand names or models in
    bulgarian. For example GoPro, Apple Iphone, Samsung Galaxy aways
    need to be in english, not translated. Input from sunsky is always
    in english."
    """
    lang = options.get("target_language", "bg")
    if lang == "en":
        return "Write all content in English."
    return (
        "Write all content in Bulgarian (Cyrillic script), natural and "
        "fluent for a Bulgarian e-commerce audience. EXCEPTION: brand "
        "names, model names, and product line names (e.g. GoPro, Apple "
        "iPhone, Samsung Galaxy, Xiaomi, Honor, FMFXTR) must NEVER be "
        "translated or transliterated into Cyrillic -- always keep them "
        "in their original English/Latin form exactly as given in the "
        "product data, even though the surrounding sentence is in "
        "Bulgarian."
    )


def _build_prompt(field: str, product: dict, options: dict) -> str:
    """Fill in the externalised template for `field` from PROMPT_TEMPLATES
    (pipeline/prompts.json). Falls back to the 'default' template for any
    field without its own entry."""
    ctx = _build_product_context(product)
    template = PROMPT_TEMPLATES.get(field, PROMPT_TEMPLATES["default"])

    format_args = {
        "ctx": ctx,
        "field": field,
        "max_chars": options.get("max_chars", 120 if field == "title" else 60 if field == "meta_title" else 2000 if field == "description" else 155),
        "max_words": options.get("max_words", 30),
        "max_tags": options.get("max_tags", 8),
        "structure": ", ".join(options.get("structure", ["intro", "features", "benefits", "compatibility"])),
        "language_instruction": _language_instruction(options),
    }
    return template.format(**format_args)


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

async def _generate_openai(prompt: str, model: Optional[str]) -> str:
    api_key = _get_api_key("OPENAI_API_KEY", "openai")
    if not api_key:
        raise AIGenerationError("OPENAI_API_KEY not configured — add it in Settings")
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise AIGenerationError("openai package not installed — run: pip install openai")

    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model or "gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.7,
    )
    return (response.choices[0].message.content or "").strip()


_ANTHROPIC_DEPRECATED: dict[str, str] = {
    # Client feedback: confirmed live via preview panel that Title/
    # Description generation was falling back to logic mode
    # ("logic:fallback"), with the Model dropdown defaulted to
    # claude-3-haiku-20240307 -- a March 2024 model, very likely
    # deprecated/retired given the extensive Claude model lineup
    # evolution since then (3 -> 3.5 -> 3.7 -> 4 -> 4.5 -> 4.6 -> 4.7
    # -> 4.8 -> 5 -> Fable 5). Same safety-net pattern as
    # _GEMINI_DEPRECATED (patch 91) -- redirects known-old model names
    # to current equivalents so an existing saved selection keeps
    # working even before the operator explicitly re-selects a new
    # model from the refreshed dropdown.
    "claude-3-haiku-20240307":    "claude-haiku-4-5-20251001",
    "claude-3-5-sonnet-20241022": "claude-sonnet-5",
    "claude-3-opus-20240229":     "claude-opus-5",
    "claude-3-sonnet-20240229":   "claude-sonnet-5",
}


async def _generate_anthropic(prompt: str, model: Optional[str]) -> str:
    api_key = _get_api_key("ANTHROPIC_API_KEY", "anthropic")
    if not api_key:
        raise AIGenerationError("ANTHROPIC_API_KEY not configured — add it in Settings")
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise AIGenerationError("anthropic package not installed — run: pip install anthropic")

    client = AsyncAnthropic(api_key=api_key)
    raw_model = model or "claude-sonnet-5"
    resolved_model = _ANTHROPIC_DEPRECATED.get(raw_model, raw_model)
    message = await client.messages.create(
        model=resolved_model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


_GEMINI_DEPRECATED: dict[str, str] = {
    # Client feedback: "Please update the models here, some are out of
    # date." Verified directly against Google's own official docs
    # (ai.google.dev/gemini-api/docs/models, updated 2026-08-26):
    # gemini-2.0-flash/-lite are explicitly marked "(Shut down)"; the
    # entire gemini-1.5 series is confirmed completely shut down
    # (Google's Firebase AI Logic docs: "all requests to these models
    # return a 404 error"). This table is what actually kept the
    # client's account working despite selecting a dead model in the
    # UI -- silently redirecting it here rather than the call failing
    # outright.
    #
    # CORRECTION: originally redirected to the newer gemini-3.x series,
    # but multiple current sources confirm the ENTIRE Gemini 3.x series
    # requires paid billing and is not available on the free tier at
    # all -- the client explicitly wants free-tier usage (confirmed via
    # their own separate working tool, which defaults to
    # gemini-2.5-flash). Redirecting to gemini-2.5-flash instead avoids
    # silently moving them from one non-working state (dead model) to
    # another (a model requiring billing they don't want).
    "gemini-1.5-flash-latest": "gemini-2.5-flash",
    "gemini-1.5-pro":          "gemini-2.5-pro",
    "gemini-1.5-flash":        "gemini-2.5-flash",
    "gemini-1.5-flash-8b":     "gemini-2.5-flash-lite",
    "gemini-2.0-flash":        "gemini-2.5-flash",
    "gemini-2.0-flash-lite":   "gemini-2.5-flash-lite",
    "gemini-1.0-pro":          "gemini-2.5-pro",
}


async def _generate_gemini(prompt: str, model: Optional[str]) -> str:
    api_key = _get_api_key("GEMINI_API_KEY", "gemini")
    if not api_key:
        raise AIGenerationError("GEMINI_API_KEY not configured — add it in Settings")
    try:
        import google.generativeai as genai
    except ImportError:
        raise AIGenerationError("google-generativeai package not installed — run: pip install google-generativeai")

    raw_model = model or "gemini-2.5-flash"
    # Silently redirect deprecated/removed models to their current equivalent
    resolved_model = _GEMINI_DEPRECATED.get(raw_model, raw_model)
    genai.configure(api_key=api_key)
    model_obj = genai.GenerativeModel(resolved_model)
    response = await model_obj.generate_content_async(prompt)
    return response.text.strip()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def generate_with_ai(
    field: str,
    product: dict,
    provider: str,
    model: Optional[str],
    options: dict,
) -> str:
    """
    Generate content for `field` using the specified AI provider.
    Raises AIGenerationError on any failure (caller should fall back to logic).

    If options contains ``_prompt_override``, that string is used as the prompt
    verbatim (used by the enrich step for raw JSON extraction prompts).
    """
    if "_prompt_override" in options:
        prompt = str(options["_prompt_override"])
    else:
        prompt = _build_prompt(field, product, options)

    if provider == "openai":
        return await _generate_openai(prompt, model)
    elif provider == "anthropic":
        return await _generate_anthropic(prompt, model)
    elif provider == "gemini":
        return await _generate_gemini(prompt, model)
    else:
        raise AIGenerationError(f"Unknown AI provider: '{provider}'")


def get_provider_status() -> dict:
    """Return which providers have API keys configured and available models."""
    return {
        "openai": {
            "configured": bool(_get_api_key("OPENAI_API_KEY", "openai")),
            "label": "OpenAI",
            "default_model": "gpt-4o-mini",
            "models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
        },
        "anthropic": {
            "configured": bool(_get_api_key("ANTHROPIC_API_KEY", "anthropic")),
            "label": "Anthropic (Claude)",
            "default_model": "claude-sonnet-5",
            "models": [
                "claude-fable-5",
                "claude-opus-5",
                "claude-sonnet-5",
                "claude-haiku-4-5-20251001",
            ],
        },
        "gemini": {
            "configured": bool(_get_api_key("GEMINI_API_KEY", "gemini")),
            "label": "Google Gemini",
            "default_model": "gemini-2.5-flash",
            "models": [
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.5-flash-lite",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-pro-preview",
                "gemini-3.1-flash-lite",
            ],
        },
    }
