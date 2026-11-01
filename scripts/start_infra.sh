#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; echo "已创建 .env；请编辑 POSTGRES_PASSWORD、DATABASE_URL 与 API_TOKEN 后重跑。"; exit 2; fi
set -a; source .env; set +a
docker compose up -d postgres redis
