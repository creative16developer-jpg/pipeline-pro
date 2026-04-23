#!/bin/bash
# Start the Celery worker with correct PYTHONPATH and thread pool.
# Run from any directory: bash /path/to/pipeline/start_worker.sh
#
# Thread pool (-P threads) is required so each task gets its own asyncio
# event loop — this prevents "Future attached to a different loop" errors
# when multiple async jobs run concurrently.
#
# Use -c N to set concurrency (parallel tasks). Default: 4.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"

CONCURRENCY="${CELERY_CONCURRENCY:-4}"

echo "Starting Celery worker (threads, concurrency=${CONCURRENCY})"
echo "PYTHONPATH=${SCRIPT_DIR}"
cd "${SCRIPT_DIR}"

exec celery -A celery_app worker \
    --loglevel=info \
    --pool=threads \
    --concurrency="${CONCURRENCY}" \
    "$@"
