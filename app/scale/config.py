"""Configuration for the scalable MolPlatform v2 API/worker stack."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass
DATA_DIR = Path(os.getenv("MOLPLATFORM_DATA_DIR", str(ROOT / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
JOBS_DIR = DATA_DIR / "jobs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'scale.db').as_posix()}")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
API_TOKEN = os.getenv("API_TOKEN", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2048"))
CHUNK_SIZE = max(100, int(os.getenv("CHUNK_SIZE", "5000")))
DOCKING_CHUNK_SIZE = max(10, int(os.getenv("DOCKING_CHUNK_SIZE", "250")))
MAX_WORKERS_PER_NODE = max(1, int(os.getenv("MAX_WORKERS_PER_NODE", "4")))

VINA_BINARY = os.getenv("VINA_BINARY", "vina")
OBABEL_BINARY = os.getenv("OBABEL_BINARY", "obabel")
GROMACS_BINARY = os.getenv("GROMACS_BINARY", "gmx")
GROMACS_MDRUN_THREADS = max(1, int(os.getenv("GROMACS_MDRUN_THREADS", "8")))
VINA_CPU_PER_LIGAND = max(1, int(os.getenv("VINA_CPU_PER_LIGAND", "1")))
DEFAULT_VINA_EXHAUSTIVENESS = max(1, int(os.getenv("DEFAULT_VINA_EXHAUSTIVENESS", "8")))
COMMAND_TIMEOUT_SECONDS = max(60, int(os.getenv("COMMAND_TIMEOUT_SECONDS", "86400")))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()

# A shared filesystem is required for multi-node workers in this MVP.
# Mount the same MOLPLATFORM_DATA_DIR at the same path on every worker.
