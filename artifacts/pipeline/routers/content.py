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
        "ai_providers_enabled": {"openai": True, "anthropic": True, "gemini": True},
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


@router.get("/saved-config")
async def get_saved_config():
    """Return the persisted generation config, migrated to current schema."""
    if _SAVED_CONFIG_PATH.exists():
        try:
            raw = json.loads(_SAVED_CONFIG_PATH.read_text())
            return _migrate_config(raw)
        except Exception:
            pass
    return DEFAULT_CONFIG


@router.post("/saved-config")
async def save_config(config: GenerateConfig):
    """Persist the generation config to disk so pipelines load it automatically."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _SAVED_CONFIG_PATH.write_text(json.dumps(config.model_dump(), indent=2))
    return {"saved": True, "path": str(_SAVED_CONFIG_PATH)}


@router.get("/providers")
async def get_providers():
    """Return which AI providers are configured and their available models."""
    from pipeline.ai_generator import get_provider_status
    return get_provider_status()


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
