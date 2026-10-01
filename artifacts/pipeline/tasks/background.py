"""Keep strong references to fire-and-forget asyncio tasks.

Client report (PL-159, before the model fixes): "I tried again to
regenerate the content but pipeline stuck on generated content from 13:54
and doesn't make any regenerates after that." Every pipeline run, resume,
re-generate and background loop was started with a bare
asyncio.create_task(...) whose result was discarded. Python's docs: the
event loop keeps only WEAK references to tasks -- "save a reference to the
result of this function, to avoid a task disappearing mid-execution". A
task garbage-collected mid-run leaves its pipeline in "running" with the log
frozen, and every later Re-generate is refused ("Can only re-generate
content from Content Review (current status: running)").
"""
from __future__ import annotations

import asyncio
import traceback

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _on_done(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # surfaced in pm2 logs instead of disappearing silently
        print(f"[background] task {task.get_name()} crashed: {exc!r}")
        traceback.print_exception(type(exc), exc, exc.__traceback__)


def spawn(coro, name: str | None = None) -> asyncio.Task:
    """asyncio.create_task with a strong reference held until the task ends."""
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def running_count() -> int:
    return len(_BACKGROUND_TASKS)
