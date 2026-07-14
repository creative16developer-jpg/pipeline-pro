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

# ── T04: Load prompt templates from prompts.json on startup ──────────────────
_PROMPTS_FILE = Path(__file__).parent / "prompts.json"

def _load_prompts() -> dict:
    if not _PROMPTS_FILE.exists():
        raise RuntimeError(
            f"AI prompt templates file not found: {_PROMPTS_FILE}\n"
            "This file is required for AI content generation. "
            "Restore it from the repository and restart the server."
        )
    return json.loads(_PROMPTS_FILE.read_text(encoding="utf-8"))

_PROMPTS: dict = _load_prompts()


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
    specs = _extract_specs(product)
    specs_text = (
        "\n".join(f"  - {k}: {v}" for k, v in list(specs.items())[:15])
        if specs else "  (none available)"
    )
    return f"Product Name: {name}\nSKU: {sku}\nDescription: {desc or '(none)'}\nSpecifications:\n{specs_text}"


def _build_prompt(field: str, product: dict, options: dict) -> str:
    """Build the AI prompt for `field` using templates from prompts.json."""
    ctx = _build_product_context(product)
    template = _PROMPTS.get(field) or _PROMPTS.get("_fallback", "Generate content for \"{field}\".\n\n{context}\n\nReturn ONLY the content value.")

    vars_: dict = {
        "context":   ctx,
        "field":     field,
        "max_chars": options.get("max_chars", 120 if field == "title" else 60 if field == "meta_title" else 155),
        "max_words": options.get("max_words", 30),
        "max_tags":  options.get("max_tags", 8),
        "structure": ", ".join(options.get("structure", ["intro", "features", "benefits", "compatibility"])),
    }
    return template.format(**vars_)


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


async def _generate_anthropic(prompt: str, model: Optional[str]) -> str:
    api_key = _get_api_key("ANTHROPIC_API_KEY", "anthropic")
    if not api_key:
        raise AIGenerationError("ANTHROPIC_API_KEY not configured — add it in Settings")
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise AIGenerationError("anthropic package not installed — run: pip install anthropic")

    client = AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=model or "claude-3-haiku-20240307",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


_GEMINI_DEPRECATED: dict[str, str] = {
    "gemini-1.5-flash-latest": "gemini-2.5-flash",
    "gemini-2.0-flash":        "gemini-2.5-flash",
    "gemini-2.0-flash-lite":   "gemini-2.5-flash",
    "gemini-1.0-pro":          "gemini-1.5-pro",
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
            "default_model": "claude-3-haiku-20240307",
            "models": [
                "claude-3-haiku-20240307",
                "claude-3-5-sonnet-20241022",
                "claude-3-opus-20240229",
            ],
        },
        "gemini": {
            "configured": bool(_get_api_key("GEMINI_API_KEY", "gemini")),
            "label": "Google Gemini",
            "default_model": "gemini-2.5-flash",
            "models": [
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.0-flash",
                "gemini-2.0-flash-lite",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
                "gemini-1.5-flash-8b",
            ],
        },
    }
