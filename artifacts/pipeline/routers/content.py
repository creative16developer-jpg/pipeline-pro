"""
Content Generation router — /api/generate/*

Provides:
  POST /api/generate/preview   — preview one field for a product
  POST /api/generate/run       — run full generation (sync, returns results)
  GET  /api/generate/job/{id}  — poll a generation job by task_id

The generation engine is logic-based and config-driven.
All field logic reads from the JSON config sent by the UI — nothing is
hard-coded per field.
"""

from __future__ import annotations

import asyncio
import re
import uuid
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/generate", tags=["content"])

# ---------------------------------------------------------------------------
# Saved config path  (persists to disk so pipelines always have a config)
# ---------------------------------------------------------------------------
_CONFIG_DIR = Path(__file__).parent.parent / "config_store"
_SAVED_CONFIG_PATH = _CONFIG_DIR / "content_gen_config.json"

# ---------------------------------------------------------------------------
# In-memory job store  (process-local; fine for this use case)
# ---------------------------------------------------------------------------
_jobs: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Field catalogue
# ---------------------------------------------------------------------------
FIELD_LIST = [
    "description",
    "short_description",
    "slug",
    "meta_title",
    "meta_description",
    "tags",
    "image_alt",
    "image_names",
]

# Default config shipped to the UI on first load
DEFAULT_CONFIG: dict = {
    "globalSettings": {
        "ai_enabled": False,
        "ai_provider": "openai",
        "ai_model": "",
        "ai_providers_enabled": {"openai": True, "anthropic": True, "gemini": True},
        "max_calls_per_product": 3,
        "keyword_strategy": "auto",
        "fallback_strategy": "safe",
    },
    "fields": {
        "description": {
            "enabled": True,
            "mode": "hybrid",
            "options": {
                "structure": ["intro", "features", "benefits", "compatibility"],
                "keyword_source": "auto",
            },
        },
        "short_description": {
            "enabled": True,
            "mode": "logic",
            "options": {"max_words": 30},
        },
        "slug": {
            "enabled": True,
            "mode": "logic",
            "options": {"transliterate": True, "ensure_unique": True},
        },
        "meta_title": {
            "enabled": True,
            "mode": "logic",
            "options": {"max_chars": 60},
        },
        "meta_description": {
            "enabled": True,
            "mode": "logic",
            "options": {"max_chars": 155},
        },
        "tags": {
            "enabled": True,
            "mode": "logic",
            "options": {"max_tags": 8, "include_specs": True},
        },
        "image_alt": {
            "enabled": True,
            "mode": "logic",
            "options": {"include_sku": True},
        },
        "image_names": {
            "enabled": True,
            "mode": "logic",
            "options": {"pattern": "{sku}-{name}"},
        },
    },
    "overrides": {},
}

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class GenerateConfig(BaseModel):
    globalSettings: dict = {}
    fields: dict = {}
    overrides: dict = {}


class PreviewRequest(BaseModel):
    product: dict
    template: GenerateConfig
    field: str


class GenerateRequest(BaseModel):
    product: dict
    template: GenerateConfig


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _parse_params_table(html_str: str) -> dict[str, str]:
    """Extract key-value spec pairs from a Sunsky paramsTable HTML blob."""
    pairs: dict[str, str] = {}
    for m in re.finditer(
        r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        html_str,
        re.DOTALL | re.IGNORECASE,
    ):
        k = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        v = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if k and v:
            pairs[k] = v
    return pairs


def _get_raw(product: dict) -> dict:
    return product.get("rawData") or product.get("raw_data") or {}


def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


# ---------------------------------------------------------------------------
# Per-field generators
# ---------------------------------------------------------------------------

def _gen_description(product: dict, options: dict) -> str:
    name = product.get("name", "Product")
    desc = product.get("description", "")
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))

    structure = options.get("structure", ["intro", "features", "benefits", "compatibility"])
    parts: list[str] = []

    if "intro" in structure:
        body = desc or f"A quality product designed for everyday use."
        parts.append(f"<p><strong>{name}</strong> — {body}</p>")

    if "features" in structure and specs:
        items = "".join(
            f"<li><strong>{k}:</strong> {v}</li>"
            for k, v in list(specs.items())[:8]
        )
        parts.append(f"<ul>{items}</ul>")

    if "benefits" in structure:
        parts.append(
            "<p>Built to the highest quality standards, "
            "offering outstanding value and reliable performance.</p>"
        )

    if "compatibility" in structure:
        brand = specs.get("Compatible Brand") or specs.get("Brand") or ""
        if brand:
            parts.append(f"<p><em>Compatible with: {brand}</em></p>")

    return "\n".join(parts) if parts else (desc or name)


def _gen_short_description(product: dict, options: dict) -> str:
    desc = product.get("description", "") or product.get("name", "")
    max_words = int(options.get("max_words", 30))
    words = desc.split()
    trimmed = " ".join(words[:max_words])
    return trimmed + ("…" if len(words) > max_words else "")


def _gen_slug(product: dict, options: dict) -> str:
    source = product.get("name") or product.get("sku") or ""
    slug = _slugify(source)
    if options.get("ensure_unique"):
        sku = product.get("sku", "")
        if sku and sku.lower() not in slug:
            slug = f"{slug}-{sku.lower()}"
    return slug[:200]


def _gen_meta_title(product: dict, options: dict) -> str:
    name = product.get("name", "")
    max_c = int(options.get("max_chars", 60))
    return (name[:max_c - 3] + "...") if len(name) > max_c else name


def _gen_meta_description(product: dict, options: dict) -> str:
    desc = product.get("description", "") or product.get("name", "")
    max_c = int(options.get("max_chars", 155))
    return (desc[:max_c - 3] + "...") if len(desc) > max_c else desc


def _gen_tags(product: dict, options: dict) -> str:
    raw = _get_raw(product)
    specs = _parse_params_table(raw.get("paramsTable", ""))
    name = product.get("name", "")
    max_tags = int(options.get("max_tags", 8))

    tags: list[str] = []
    if name:
        tags.append(name)
    if options.get("include_specs", True):
        for v in specs.values():
            if isinstance(v, str) and 2 < len(v) < 40:
                tags.append(v)
            if len(tags) >= max_tags:
                break
    return ", ".join(tags[:max_tags])


def _gen_image_alt(product: dict, options: dict) -> str:
    name = product.get("name", "")
    sku = product.get("sku", "")
    if options.get("include_sku") and sku:
        return f"{name} — {sku}"
    return name


def _gen_image_names(product: dict, options: dict) -> str:
    sku = product.get("sku", "")
    name_slug = _slugify(product.get("name", "product"))
    pattern = options.get("pattern", "{sku}-{name}")
    return pattern.replace("{sku}", sku.lower()).replace("{name}", name_slug)[:200]


_GENERATORS: dict[str, Any] = {
    "description": _gen_description,
    "short_description": _gen_short_description,
    "slug": _gen_slug,
    "meta_title": _gen_meta_title,
    "meta_description": _gen_meta_description,
    "tags": _gen_tags,
    "image_alt": _gen_image_alt,
    "image_names": _gen_image_names,
}

# ---------------------------------------------------------------------------
# Core generation helper
# ---------------------------------------------------------------------------

async def _run_field(field: str, product: dict, template: GenerateConfig) -> dict:
    override = (template.overrides or {}).get(field)
    if override is not None:
        return {"field": field, "value": str(override), "source": "override", "status": "ok"}

    field_cfg = (template.fields or {}).get(field, {})
    options = field_cfg.get("options", {})
    mode = field_cfg.get("mode", "logic")

    gs = template.globalSettings or {}
    ai_enabled = gs.get("ai_enabled", False)
    ai_provider = gs.get("ai_provider", "openai") or "openai"
    ai_model = gs.get("ai_model", "") or None
    fallback_strategy = gs.get("fallback_strategy", "safe")

    # ── AI modes ────────────────────────────────────────────────────────────
    if mode in ("ai", "hybrid") and ai_enabled:
        try:
            from pipeline.ai_generator import generate_with_ai
            value = await generate_with_ai(
                field=field,
                product=product,
                provider=ai_provider,
                model=ai_model,
                options=options,
            )
            return {"field": field, "value": value, "source": f"ai:{ai_provider}", "status": "ok"}
        except Exception as ai_err:
            ai_error_msg = str(ai_err)
            if mode == "ai":
                if fallback_strategy == "skip":
                    return {"field": field, "value": "", "source": "none", "status": "skipped",
                            "error": ai_error_msg}
                if fallback_strategy == "empty":
                    return {"field": field, "value": "", "source": "ai:failed", "status": "ok",
                            "error": ai_error_msg}
                # "safe" — fall through to logic generator below

    # ── Logic generation ────────────────────────────────────────────────────
    gen = _GENERATORS.get(field)
    if not gen:
        return {"field": field, "value": "", "source": "none", "status": "skipped",
                "error": f"No generator for field '{field}'"}
    try:
        value = gen(product, options)
        source = "logic" if mode != "ai" else "logic:fallback"
        return {"field": field, "value": value, "source": source, "status": "ok"}
    except Exception as exc:
        return {"field": field, "value": "", "source": "logic", "status": "failed",
                "error": str(exc)}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_default_config():
    """Return the default generation config (field list + defaults)."""
    return {"fields": FIELD_LIST, "defaultConfig": DEFAULT_CONFIG}


@router.get("/saved-config")
async def get_saved_config():
    """Return the saved generation config, or DEFAULT_CONFIG if none saved yet."""
    if _SAVED_CONFIG_PATH.exists():
        try:
            return json.loads(_SAVED_CONFIG_PATH.read_text())
        except Exception:
            pass
    return DEFAULT_CONFIG


@router.post("/saved-config")
async def save_config(config: GenerateConfig):
    """Persist the generation config to disk so pipelines can load it automatically."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _SAVED_CONFIG_PATH.write_text(json.dumps(config.model_dump(), indent=2))
    return {"saved": True}


@router.get("/providers")
async def get_providers():
    """Return which AI providers are configured (have API keys set)."""
    from pipeline.ai_generator import get_provider_status
    return get_provider_status()


@router.post("/preview")
async def preview_field(req: PreviewRequest):
    """
    Generate content for a single field and return the result immediately.
    No job is created — result is returned inline.
    """
    if req.field not in FIELD_LIST:
        raise HTTPException(status_code=400, detail=f"Unknown field: {req.field}")
    return await _run_field(req.field, req.product, req.template)


@router.post("/run")
async def run_generation(req: GenerateRequest):
    """
    Run generation for all enabled fields and return results immediately.
    Fields are generated in parallel for speed.
    """
    task_id = str(uuid.uuid4())
    started = datetime.utcnow().isoformat()

    enabled_fields = [
        f for f in FIELD_LIST
        if (req.template.fields or {}).get(f, {}).get("enabled", True)
    ]

    results = await asyncio.gather(
        *[_run_field(f, req.product, req.template) for f in enabled_fields],
        return_exceptions=False,
    )
    field_results: dict[str, dict] = {r["field"]: r for r in results}

    job = {
        "taskId": task_id,
        "status": "completed",
        "startedAt": started,
        "completedAt": datetime.utcnow().isoformat(),
        "totalFields": len(enabled_fields),
        "doneFields": len(field_results),
        "fields": field_results,
    }
    _jobs[task_id] = job
    return job


@router.get("/job/{task_id}")
async def get_generation_job(task_id: str):
    """Poll a generation job by its task_id."""
    job = _jobs.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return job
