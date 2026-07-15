"""
Settings router — /api/settings/*

Manages:
  - AI provider API keys  → config_store/api_keys.json
  - Image processing cfg  → config_store/image_settings.json
  - Pipeline defaults     → config_store/pipeline_defaults.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/settings", tags=["settings"])

_CONFIG_DIR = Path(__file__).parent.parent / "config_store"
_KEYS_PATH = _CONFIG_DIR / "api_keys.json"
_IMAGE_PATH = _CONFIG_DIR / "image_settings.json"
_PIPELINE_PATH = _CONFIG_DIR / "pipeline_defaults.json"

_PROVIDERS = {
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini":    "GEMINI_API_KEY",
}

# ── defaults ────────────────────────────────────────────────────────────────

IMAGE_DEFAULTS: dict = {
    "webp_quality": 85,
    "max_width": 1200,
    "max_height": 1200,
    "watermark_text": "",
    "watermark_opacity": 80,
}

PIPELINE_DEFAULTS: dict = {
    "auto_review_pause": True,
    "ai_enrich_enabled": True,
    "skip_image_processing": False,
    "force_rerun": False,
    "fetch_limit_default": 50,
    "process_limit_default": 200,
    "upload_limit_default": 50,
    "max_concurrent_per_store": 1,
}

# ── helpers ──────────────────────────────────────────────────────────────────

def _load(path: Path, defaults: dict) -> dict:
    if path.exists():
        try:
            return {**defaults, **json.loads(path.read_text())}
        except Exception:
            pass
    return dict(defaults)


def _save(path: Path, data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _load_stored() -> dict:
    return _load(_KEYS_PATH, {})


def _save_stored(data: dict) -> None:
    _save(_KEYS_PATH, data)


# ── public helpers (used by other modules) ───────────────────────────────────

def get_image_settings() -> dict:
    """Return merged image settings (saved + defaults)."""
    return _load(_IMAGE_PATH, IMAGE_DEFAULTS)


def get_pipeline_defaults() -> dict:
    """Return merged pipeline defaults (saved + defaults)."""
    return _load(_PIPELINE_PATH, PIPELINE_DEFAULTS)


# ── API-key endpoints ────────────────────────────────────────────────────────

@router.get("/api-keys")
async def get_api_keys():
    """Return provider key status — never returns actual key values."""
    stored = _load_stored()
    result: dict = {}
    for provider, env_var in _PROVIDERS.items():
        in_env = bool(os.getenv(env_var))
        stored_key: str = stored.get(provider, "")
        in_file = bool(stored_key)
        masked = ("•" * 8 + stored_key[-4:]) if (in_file and not in_env) else None
        result[provider] = {
            "configured": in_env or in_file,
            "source": "env" if in_env else ("file" if in_file else "none"),
            "masked": masked,
        }
    return result


@router.post("/api-keys")
async def save_api_keys(body: dict):
    stored = _load_stored()
    saved: list[str] = []
    for provider in _PROVIDERS:
        key = body.get(provider)
        if key is None:
            continue
        key = key.strip()
        if key:
            stored[provider] = key
            saved.append(provider)
        elif provider in stored:
            del stored[provider]
            saved.append(f"{provider} (cleared)")
    _save_stored(stored)
    return {"saved": True, "providers": saved}


@router.delete("/api-keys/{provider}")
async def delete_api_key(provider: str):
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    stored = _load_stored()
    if provider in stored:
        del stored[provider]
        _save_stored(stored)
    return {"deleted": True, "provider": provider}


# ── Image settings endpoints ──────────────────────────────────────────────────

@router.get("/image")
async def get_image_settings_api():
    return get_image_settings()


@router.put("/image")
async def save_image_settings(body: dict):
    allowed = set(IMAGE_DEFAULTS.keys())
    current = get_image_settings()
    for k, v in body.items():
        if k in allowed:
            current[k] = v
    # basic validation
    current["webp_quality"] = max(1, min(100, int(current.get("webp_quality", 85))))
    current["max_width"] = max(100, min(4000, int(current.get("max_width", 1200))))
    current["max_height"] = max(100, min(4000, int(current.get("max_height", 1200))))
    current["watermark_opacity"] = max(0, min(255, int(current.get("watermark_opacity", 80))))
    _save(_IMAGE_PATH, current)
    return current


# ── Pipeline defaults endpoints ───────────────────────────────────────────────

@router.get("/pipeline-defaults")
async def get_pipeline_defaults_api():
    return get_pipeline_defaults()


@router.put("/pipeline-defaults")
async def save_pipeline_defaults(body: dict):
    allowed = set(PIPELINE_DEFAULTS.keys())
    current = get_pipeline_defaults()
    for k, v in body.items():
        if k in allowed:
            current[k] = v
    current["fetch_limit_default"] = max(1, min(500, int(current.get("fetch_limit_default", 50))))
    current["process_limit_default"] = max(1, min(2000, int(current.get("process_limit_default", 200))))
    current["upload_limit_default"] = max(1, min(500, int(current.get("upload_limit_default", 50))))
    current["max_concurrent_per_store"] = max(1, min(5, int(current.get("max_concurrent_per_store", 1))))
    _save(_PIPELINE_PATH, current)
    return current
