#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then set -a; source .env; set +a; fi
# Chemistry workers process CPU-bound RDKit jobs and individual docking chunks.
exec celery -A app.scale.worker:celery_app worker -n "chem@%h" -Q control,chem,docking,celery --concurrency "${WORKER_CONCURRENCY:-4}" --prefetch-multiplier=1 --loglevel "${LOG_LEVEL:-INFO}"
