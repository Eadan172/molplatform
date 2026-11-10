"""
MolPlatform - 百万级分子高通量数据架构与自动化处理平台
计算化学工程师 Demo 项目

技术栈:
- FastAPI: 后端 API 框架
- RDKit: 分子结构处理
- LLM (OpenAI/Claude): AI 辅助分析
- Celery + Redis: 异步任务队列（模拟分布式并行）
- SQLite: 结构化关系数据库（生产替换为 PostgreSQL）
"""

from fastapi import FastAPI
import os
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from app.api import molecules, docking, md_simulation, ai_analysis, pipeline
from app.api import demo as demo_api
from app.api import demo_page
from app.api import agent as agent_api
from app.core.database import init_db, seed_db
from app.scale.api import router as scale_api_router
from app.scale.database import init_scale_db
from app.scale.config import ENVIRONMENT
from app.tamarind.api import router as tamarind_api_router

app = FastAPI(
    title="MolPlatform — Natural Language Molecular Workflow MVP",
    description="""
## MolPlatform Scale MVP (v2)

- `/workbench` and `/api/v2`: natural-language task submission, real RDKit descriptors, real AutoDock Vina CLI and real GROMACS `mdrun` workers.
- PostgreSQL + Redis/Celery distributed tasks; worker nodes must share `MOLPLATFORM_DATA_DIR` and have their own scientific toolchains.
- MolClaw L1/L2/L3 skill documents are used as workflow guidance and precondition checks. Only installed and verified CLIs are executed.
- MoleCode (`structure.molecode` on `/api/v1/agent/tools/molecode`) converts SMILES to Mermaid graphs and RDKit-validates graph edits for the agent/AI path. It does not run docking, MD, or HTVS.
- The original `/api/v1` routes are retained for compatibility, but some legacy docking/MD/pipeline implementations are demonstrations. Do not cite their output as real scientific results.

This MVP does not claim a measured million-molecule/day throughput. Benchmark on the actual server and GPU before making throughput commitments.
    """,
    version="2.0.0-mvp",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(molecules.router, prefix="/api/v1/molecules", tags=["🧬 分子管理"])
app.include_router(docking.router, prefix="/api/v1/docking", tags=["🔬 分子对接"])
app.include_router(md_simulation.router, prefix="/api/v1/md", tags=["⚡ MD 模拟"])
app.include_router(ai_analysis.router, prefix="/api/v1/ai", tags=["🤖 AI 分析"])
app.include_router(pipeline.router, prefix="/api/v1/pipeline", tags=["🚀 全链路管线"])
app.include_router(demo_api.router, prefix="/api/v1/demo", tags=["🎯 演示 Demo · COX-2/1CX2"])
app.include_router(demo_page.router, include_in_schema=False)
app.include_router(agent_api.router, prefix="/api/v1/agent", tags=["🧠 自然语言分析 MVP"])
app.include_router(scale_api_router, prefix="/api/v2", tags=["🚀 分布式科研工作流 v2"])
app.include_router(tamarind_api_router, prefix="/api/v4", tags=["☁️ Tamarind Bio 云端计算"])

@app.get("/workbench", response_class=HTMLResponse, include_in_schema=False)
def scale_workbench_page():
    page = Path(__file__).parent / "scale" / "workbench.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.on_event("startup")
def startup_initialize_database():
    init_db()
    seed_db()
    init_scale_db()


@app.get("/health", tags=["健康检查"])
def health():
    return {"status": "ok"}


@app.get("/api-info", tags=["平台信息"])
def api_info():
    return {
        "platform": "MolPlatform Scale MVP v2 · RDKit/Vina/GROMACS worker + legacy demo APIs",
        "status": "running",
        "description": "v2 工作台使用已安装的 RDKit/Vina/GROMACS 命令；v1 中部分对接/MD/管线端点为原型演示，不代表真实计算。",
        "demo_ui": "/demo",
        "natural_language_mvp": "/api/v1/agent/",
        "scale_workbench": "/api/v2/",
        "scale_api": "/api/v2/tasks",
        "tamarind_api": "/api/v4/tamarind",
        "docs": "/docs",
        "redoc": "/redoc",
        "modules": [
            "demo      — 一键演示：10 配体 + 1CX2 + MD",
            "molecules — 分子数据管理 & 标准化",
            "docking   — AutoDock Vina 风格对接",
            "md        — legacy v1: mock trajectory routes; use v2 with a prepared TPR for real GROMACS",
            "ai        — AI 辅助结果分析",
            "pipeline  — legacy v1 demo; v2 task workflows are queue-backed",
            "tamarind   — optional cloud compute backend; explicit validate-before-submit",
        ],
    }
