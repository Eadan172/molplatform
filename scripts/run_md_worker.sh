#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then set -a; source .env; set +a; fi
# A separate GPU worker prevents multiple MD jobs from oversubscribing one GPU.
exec celery -A app.scale.worker:celery_app worker -n "md@%h" -Q md --concurrency "${MD_WORKER_CONCURRENCY:-1}" --prefetch-multiplier=1 --loglevel "${LOG_LEVEL:-INFO}"
