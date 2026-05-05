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

import os
import re
from typing import Optional


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
    sku = product.get("sku", "")
    desc = product.get("description", "")
    specs = _extract_specs(product)
    specs_text = (
        "\n".join(f"  - {k}: {v}" for k, v in list(specs.items())[:15])
        if specs else "  (none available)"
    )
    return f"Product Name: {name}\nSKU: {sku}\nDescription: {desc or '(none)'}\nSpecifications:\n{specs_text}"


def _build_prompt(field: str, product: dict, options: dict) -> str:
    ctx = _build_product_context(product)

    if field == "description":
        structure = options.get("structure", ["intro", "features", "benefits", "compatibility"])
        return (
            f"Write an HTML product description for a WooCommerce store.\n"
            f"Use <p> and <ul><li> tags only. Structure: {', '.join(structure)}.\n"
            f"Keep it under 200 words. Be factual, professional, and SEO-friendly.\n\n"
            f"{ctx}\n\nReturn ONLY the HTML. No explanation, no markdown."
        )

    if field == "short_description":
        max_words = options.get("max_words", 30)
        return (
            f"Write a short product description for a WooCommerce store listing.\n"
            f"Maximum {max_words} words. Plain text only (no HTML). Professional and factual.\n\n"
            f"{ctx}\n\nReturn ONLY the short description text."
        )

    if field == "slug":
        return (
            f"Generate a SEO-friendly URL slug for this product.\n"
            f"Rules: lowercase, hyphens only, no special characters, include SKU at the end, max 80 characters.\n\n"
            f"{ctx}\n\nReturn ONLY the slug."
        )

    if field == "meta_title":
        max_chars = options.get("max_chars", 60)
        return (
            f"Write an SEO meta title for this product. Max {max_chars} characters.\n"
            f"Plain text only. Include the product name and a key feature.\n\n"
            f"{ctx}\n\nReturn ONLY the meta title."
        )

    if field == "meta_description":
        max_chars = options.get("max_chars", 155)
        return (
            f"Write an SEO meta description for this product. Max {max_chars} characters.\n"
            f"Plain text only. Compelling, factual, includes a soft call to action.\n\n"
            f"{ctx}\n\nReturn ONLY the meta description."
        )

    if field == "tags":
        max_tags = options.get("max_tags", 8)
        return (
            f"Generate {max_tags} product tags for WooCommerce.\n"
            f"Tags should be short keywords or phrases relevant to the product.\n\n"
            f"{ctx}\n\nReturn ONLY a comma-separated list of tags."
        )

    if field == "image_alt":
        return (
            f"Write an image alt text for this product photo. Max 125 characters.\n"
            f"Include the product name and SKU. Plain text only.\n\n"
            f"{ctx}\n\nReturn ONLY the alt text."
        )

    if field == "image_names":
        return (
            f"Generate a filename-safe image name for this product.\n"
            f"Lowercase, hyphens only, include SKU, max 80 chars, no file extension.\n\n"
            f"{ctx}\n\nReturn ONLY the filename."
        )

    return (
        f'Generate content for the "{field}" field for this product.\n\n'
        f"{ctx}\n\nReturn ONLY the content value."
    )


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

async def _generate_openai(prompt: str, model: Optional[str]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AIGenerationError("OPENAI_API_KEY not configured")
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
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIGenerationError("ANTHROPIC_API_KEY not configured")
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


async def _generate_gemini(prompt: str, model: Optional[str]) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise AIGenerationError("GEMINI_API_KEY not configured")
    try:
        import google.generativeai as genai
    except ImportError:
        raise AIGenerationError("google-generativeai package not installed — run: pip install google-generativeai")

    genai.configure(api_key=api_key)
    model_obj = genai.GenerativeModel(model or "gemini-1.5-flash")
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
    """
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
            "configured": bool(os.getenv("OPENAI_API_KEY")),
            "label": "OpenAI",
            "default_model": "gpt-4o-mini",
            "models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
        },
        "anthropic": {
            "configured": bool(os.getenv("ANTHROPIC_API_KEY")),
            "label": "Anthropic (Claude)",
            "default_model": "claude-3-haiku-20240307",
            "models": [
                "claude-3-haiku-20240307",
                "claude-3-5-sonnet-20241022",
                "claude-3-opus-20240229",
            ],
        },
        "gemini": {
            "configured": bool(os.getenv("GEMINI_API_KEY")),
            "label": "Google Gemini",
            "default_model": "gemini-1.5-flash",
            "models": ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"],
        },
    }
