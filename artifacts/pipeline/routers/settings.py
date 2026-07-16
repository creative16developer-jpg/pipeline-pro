"""
Settings router — /api/settings/*

Manages AI provider API keys stored server-side in config_store/api_keys.json.
Environment variable keys (OPENAI_API_KEY etc.) always take priority over stored keys.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/settings", tags=["settings"])

_CONFIG_DIR = Path(__file__).parent.parent / "config_store"
_KEYS_PATH = _CONFIG_DIR / "api_keys.json"

_PROVIDERS = {
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini":    "GEMINI_API_KEY",
}


def _load_stored() -> dict:
    if _KEYS_PATH.exists():
        try:
            return json.loads(_KEYS_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_stored(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _KEYS_PATH.write_text(json.dumps(data, indent=2))


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
    """
    Save one or more API keys.
    Body: { openai?: str, anthropic?: str, gemini?: str }
    Empty string for a provider clears that key from storage.
    """
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


_IMAGE_SETTINGS_PATH  = _CONFIG_DIR / "image_settings.json"
_PIPELINE_DEFAULTS_PATH = _CONFIG_DIR / "pipeline_defaults.json"

DEFAULT_IMAGE_SETTINGS: dict = {
    "output_format": "webp",
    "max_width": 1200,
    "max_height": 1200,
    "keep_original_size": False,
    "compression_enabled": True,
    "compression_quality": 85,
    "max_images_per_product": 5,
    "skip_last_image": False,
}

DEFAULT_PIPELINE_DEFAULTS: dict = {
    "include_enrich": True,
    "include_generate": True,
    "force_rerun": False,
    "auto_review_pause": True,
    "compression": True,
}


@router.get("/image-settings")
async def get_image_settings():
    if _IMAGE_SETTINGS_PATH.exists():
        try:
            stored = json.loads(_IMAGE_SETTINGS_PATH.read_text())
            return {**DEFAULT_IMAGE_SETTINGS, **stored}
        except Exception:
            pass
    return DEFAULT_IMAGE_SETTINGS


@router.post("/image-settings")
async def save_image_settings(body: dict):
    merged = {**DEFAULT_IMAGE_SETTINGS, **body}
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _IMAGE_SETTINGS_PATH.write_text(json.dumps(merged, indent=2))
    return {"saved": True}


@router.get("/pipeline-defaults")
async def get_pipeline_defaults():
    if _PIPELINE_DEFAULTS_PATH.exists():
        try:
            stored = json.loads(_PIPELINE_DEFAULTS_PATH.read_text())
            return {**DEFAULT_PIPELINE_DEFAULTS, **stored}
        except Exception:
            pass
    return DEFAULT_PIPELINE_DEFAULTS


@router.post("/pipeline-defaults")
async def save_pipeline_defaults(body: dict):
    merged = {**DEFAULT_PIPELINE_DEFAULTS, **body}
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _PIPELINE_DEFAULTS_PATH.write_text(json.dumps(merged, indent=2))
    return {"saved": True}


@router.delete("/api-keys/{provider}")
async def delete_api_key(provider: str):
    """Remove a stored API key for a provider."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    stored = _load_stored()
    if provider in stored:
        del stored[provider]
        _save_stored(stored)
    return {"deleted": True, "provider": provider}
