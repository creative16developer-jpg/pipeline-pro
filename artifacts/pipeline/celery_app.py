import os
import sys
from pathlib import Path

# Ensure the pipeline package directory is always on the path,
# regardless of where the Celery worker is launched from.
_pkg_dir = str(Path(__file__).parent.resolve())
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from celery import Celery
from dotenv import load_dotenv
load_dotenv(Path(_pkg_dir) / ".env")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "pipeline",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks.job_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
