#!/bin/bash
# Start the Celery worker with the correct PYTHONPATH.
# Run from any directory: bash /path/to/pipeline/start_worker.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"

echo "Starting Celery worker with PYTHONPATH=${SCRIPT_DIR}"
cd "${SCRIPT_DIR}"
exec celery -A celery_app worker --loglevel=info "$@"
