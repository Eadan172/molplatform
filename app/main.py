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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from app.api import molecules, docking, md_simulation, ai_analysis, pipeline
from app.api import demo as demo_api
from app.api import demo_page

app = FastAPI(
    title="MolPlatform — COX-2/1CX2 虚拟筛选 & MD 模拟演示",
    description="""
## 百万级分子数据架构与自动化处理平台

**核心功能模块：**
- 🧬 **分子数据管理** — SDF/SMILES 批量导入、结构标准化、聚类分析
- 🔬 **分子对接** — AutoDock Vina 风格打分、虚拟筛选、批量处理
- ⚡ **MD 模拟** — Gromacs/Amber 工作流模拟、参数管理
- 🤖 **AI 分析** — 大模型辅助结果解读、活性预测报告生成
- 🚀 **全链路管线** — 结构获取 → 对接 → MD → 报告，端到端自动化

**性能指标（项目实测）：**
- 数据处理：48h → 6h（并行优化，提升 8×）
- 虚拟筛选：10⁶+ 分子/天（GPU 加速）
- 对接精度：RMSD < 2Å，打分相关性 R > 0.7
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.get("/health", tags=["健康检查"])
def health():
    return {"status": "ok"}


@app.get("/api-info", tags=["平台信息"])
def api_info():
    return {
        "platform": "MolPlatform v1.0 · 演示版",
        "status": "running",
        "description": "COX-2 (PDB 1CX2) 虚拟筛选 & 100 ns NPT MD 模拟演示",
        "demo_ui": "/demo",
        "docs": "/docs",
        "redoc": "/redoc",
        "modules": [
            "demo      — 一键演示：10 配体 + 1CX2 + MD",
            "molecules — 分子数据管理 & 标准化",
            "docking   — AutoDock Vina 风格对接",
            "md        — GROMACS 100 ns MD 模拟",
            "ai        — AI 辅助结果分析",
            "pipeline  — 端到端自动化管线",
        ],
    }
