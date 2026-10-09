"""
Content Generation router — /api/generate/*

All heavy lifting is delegated to services.content_service (no logic lives here).
Routers only handle HTTP: validation, serialization, error mapping.

Endpoints:
  GET  /api/generate/config         — field list + default config
  GET  /api/generate/saved-config   — persisted config
  POST /api/generate/saved-config   — persist config
  GET  /api/generate/providers      — AI provider status
  POST /api/generate/preview        — preview one field (inline, no job)
  POST /api/generate/run            — run all fields (inline, returns results)
  GET  /api/generate/job/{id}       — poll an async generation job
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.content_service import (
    FIELD_LIST,
    FIELD_DEFAULT_MODE,
    FIELD_DEPS,
    run_field,
    generate_product,
)

router = APIRouter(prefix="/generate", tags=["content"])

_CONFIG_DIR = Path(__file__).parent.parent / "config_store"
_SAVED_CONFIG_PATH = _CONFIG_DIR / "content_gen_config.json"

_jobs: dict[str, dict] = {}

# ─────────────────────────────────────────────────────────────────────────────
# Default config (shipped to the UI on first load)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "globalSettings": {
        "ai_enabled": False,
        "ai_provider": "openai",
        "ai_model": "",
        "ai_providers_enabled": {"openai": True, "anthropic": True, "gemini": True, "openrouter": True},
        "max_calls_per_product": 3,
        "keyword_strategy": "auto",
        "fallback_strategy": "safe",
    },
    "fields": {
        "title": {
            "enabled": True,
            "mode": "logic",
            "options": {"max_chars": 120},
        },
        "tags": {
            "enabled": True,
            "mode": "logic",
            "options": {"max_tags": 3, "include_specs": True},
        },
        "description": {
            "enabled": True,
            "mode": "ai",
            "options": {
                "structure": ["intro", "features", "benefits", "compatibility", "closing"],
                "keyword_source": "auto",
            },
        },
        "slug": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 70, "ensure_unique": True},
        },
        "image_alt": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 125, "include_sku": True},
        },
        "meta_title": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 60},
        },
        "image_names": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 70},
        },
        # Client feedback confirmed live: Image Caption/Description
        # showed empty even with the patch adding this feature already
        # applied. Root cause: DEFAULT_CONFIG here was never updated to
        # include these two fields at all when that patch was built --
        # meaning generation never even attempted them, for EVERY
        # operator (not just ones with a stale saved settings file --
        # a brand-new install falling straight back to DEFAULT_CONFIG
        # would have had the identical gap).
        "image_caption": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 125},
        },
        "image_description": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 300},
        },
        "short_description": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 400},
        },
        "meta_description": {
            "enabled": True,
            "mode": "derive",
            "options": {"max_chars": 160},
        },
    },
    "overrides": {},
}

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas (API layer only)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/config")
async def get_default_config():
    """Return the field list, default modes, dependencies, and default config."""
    return {
        "fields": FIELD_LIST,
        "fieldDefaultModes": FIELD_DEFAULT_MODE,
        "fieldDeps": FIELD_DEPS,
        "defaultConfig": DEFAULT_CONFIG,
    }


def _migrate_config(raw: dict) -> dict:
    """
    Forward-migrate a saved config to the current schema:
    - "hybrid" mode → "derive"
    - Missing fields filled from DEFAULT_CONFIG
    - Missing options dict filled with {}
    """
    fields: dict = {}
    for field in FIELD_LIST:
        saved_field = raw.get("fields", {}).get(field)
        default_field = DEFAULT_CONFIG["fields"].get(field, {
            "enabled": True,
            "mode": FIELD_DEFAULT_MODE.get(field, "logic"),
            "options": {},
        })
        if not saved_field:
            fields[field] = default_field
        else:
            mode = saved_field.get("mode", default_field.get("mode", "logic"))
            if mode == "hybrid":
                mode = "derive"
            fields[field] = {
                "enabled": saved_field.get("enabled", True),
                "mode": mode,
                "options": saved_field.get("options") or default_field.get("options", {}),
            }
    return {
        "globalSettings": {**DEFAULT_CONFIG["globalSettings"], **raw.get("globalSettings", {})},
        "fields": fields,
        "overrides": raw.get("overrides", {}),
    }


def _store_config_path(store_id: int):
    return _CONFIG_DIR / f"content_gen_config_store_{int(store_id)}.json"


@router.get("/saved-config")
async def get_saved_config(store_id: Optional[int] = None):
    """The persisted generation config, migrated to current schema. With
    store_id: that store's custom settings if it has them, else the global
    ones (what its pipelines use)."""
    paths = ([_store_config_path(store_id)] if store_id else []) + [_SAVED_CONFIG_PATH]
    for path in paths:
        if path.exists():
            try:
                return _migrate_config(json.loads(path.read_text()))
            except Exception:
                pass
    return DEFAULT_CONFIG


@router.get("/store-configs")
async def list_store_configs():
    """Store ids that have their own Content Generation settings."""
    ids = []
    if _CONFIG_DIR.exists():
        for f in _CONFIG_DIR.glob("content_gen_config_store_*.json"):
            try:
                ids.append(int(f.stem.rsplit("_", 1)[1]))
            except ValueError:
                pass
    return {"store_ids": sorted(ids)}


@router.post("/saved-config")
async def save_config(config: GenerateConfig, store_id: Optional[int] = None):
    """Persist the generation config so pipelines load it automatically.
    With store_id: custom settings for that store only."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = _store_config_path(store_id) if store_id else _SAVED_CONFIG_PATH
    path.write_text(json.dumps(config.model_dump(), indent=2))
    return {"saved": True, "path": str(path), "store_id": store_id}


@router.delete("/saved-config")
async def delete_store_config(store_id: int):
    """Remove a store's custom settings -- it uses the global ones again."""
    path = _store_config_path(store_id)
    existed = path.exists()
    if existed:
        path.unlink()
    return {"deleted": existed, "store_id": store_id}


@router.get("/openrouter-models")
async def get_openrouter_models(refresh: bool = False):
    """OpenRouter's text models with prices per 1M tokens (input / output)
    for the Content Generation model picker. Public catalogue -- works
    before an OpenRouter key is saved. Cached for an hour; ?refresh=true
    reloads."""
    from pipeline.ai_generator import list_openrouter_models, AIGenerationError
    try:
        models = await list_openrouter_models(force=refresh)
    except AIGenerationError as e:
        raise HTTPException(502, str(e))
    return {"models": models, "count": len(models)}


@router.get("/providers")
async def get_providers():
    """Return which AI providers are configured and their available models."""
    from pipeline.ai_generator import get_provider_status_live
    return await get_provider_status_live()


@router.post("/preview")
async def preview_field(req: PreviewRequest):
    """
    Generate content for a single field and return the result immediately.
    For derive fields, resolved deps are derived on the fly from the product dict.
    """
    if req.field not in FIELD_LIST:
        raise HTTPException(status_code=400, detail=f"Unknown field: {req.field!r}")

    template = req.template.model_dump()

    # For derive fields, resolve deps on-the-fly so the preview is meaningful
    resolved: dict[str, str] = {}
    for dep in FIELD_DEPS.get(req.field, []):
        dep_result = await run_field(dep, req.product, template, resolved)
        resolved[dep] = dep_result.get("value", "")

    return await run_field(req.field, req.product, template, resolved)


@router.post("/run")
async def run_generation(req: GenerateRequest):
    """
    Run generation for all enabled fields using the DAG engine.
    Returns results immediately (synchronous).
    """
    task_id = str(uuid.uuid4())
    _jobs[task_id] = {
        "taskId": task_id,
        "status": "running",
        "startedAt": datetime.utcnow().isoformat(),
        "fields": {},
    }

    template = req.template.model_dump()
    results = await generate_product(req.product, template)

    _jobs[task_id].update({
        "status": "done",
        "fields": results,
        "totalFields": len(results),
        "doneFields": len(results),
    })
    return _jobs[task_id]


@router.get("/job/{task_id}")
async def get_job(task_id: str):
    """Poll a generation job by task_id."""
    job = _jobs.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
