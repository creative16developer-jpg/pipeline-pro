---
name: AI prompts file
description: prompts.json location, loading pattern, and _build_prompt behaviour after T04.
---

## File location

`artifacts/pipeline/pipeline/prompts.json`

Keys: title, description, short_description, slug, meta_title, meta_description, tags, image_alt, image_names, _fallback

Each value is a Python str.format()-compatible template using named placeholders:
{context}, {field}, {max_chars}, {max_words}, {max_tags}, {structure}

## Loading (ai_generator.py)

```python
_PROMPTS_FILE = Path(__file__).parent / "prompts.json"

def _load_prompts() -> dict: ...   # raises RuntimeError if file missing

_PROMPTS: dict = _load_prompts()   # loaded at module startup
```

If prompts.json is deleted, the FastAPI server will fail to start with a clear RuntimeError message.

## _build_prompt

```python
def _build_prompt(field, product, options):
    ctx = _build_product_context(product)
    template = _PROMPTS.get(field) or _PROMPTS.get("_fallback", "...")
    vars_ = {context, field, max_chars, max_words, max_tags, structure}
    return template.format(**vars_)
```

**Why:** Client wants to be able to edit prompt templates without touching Python code. prompts.json is the single source of truth.
**How to apply:** To add a new field, add a key to prompts.json and the template will be used automatically. To edit wording, edit prompts.json only.
