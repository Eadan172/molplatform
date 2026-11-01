#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then set -a; source .env; set +a; fi
printf 'Python: '; python --version
printf '\nRDKit: '; python - <<'PY'
try:
 import rdkit; print(rdkit.__version__)
except Exception as e: print('NOT AVAILABLE:',e)
PY
for v in "${VINA_BINARY:-vina}" "${OBABEL_BINARY:-obabel}" "${GROMACS_BINARY:-gmx}"; do
  printf '%s: ' "$v"
  if command -v "$v" >/dev/null 2>&1; then command -v "$v"; else echo 'NOT ON PATH (set exact path in .env if installed elsewhere)'; fi
done
printf '\nGPU: '; if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=name,driver_version --format=csv,noheader; else echo 'nvidia-smi not found'; fi
printf '\nRedis: '; (redis-cli -h 127.0.0.1 ping 2>/dev/null || true)
printf 'Postgres: '; (pg_isready -h 127.0.0.1 2>/dev/null || true)
