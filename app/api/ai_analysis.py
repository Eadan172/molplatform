"""AI 分析 API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Any, Dict
import json

from app.core.database import get_db, init_db, seed_db
from app.services.ai_service import (
    analyze_docking_results,
    analyze_md_results,
    generate_lead_optimization_report,
    chat_with_expert,
)
from app.services.md_service import generate_mock_trajectory

router = APIRouter()


def ensure_db():
    init_db()
    seed_db()


class ChatIn(BaseModel):
    question: str
    context: Optional[dict] = None


class LeadOptIn(BaseModel):
    docking_job_id: int
    target: str = "COX-2"


@router.post("/analyze/docking/{job_id}", summary="AI 解读对接结果")
async def ai_analyze_docking(job_id: int):
    ensure_db()
    conn = get_db()
    job = conn.execute("SELECT * FROM docking_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "Docking job not found")
    results = conn.execute("""
        SELECT dr.*, m.name as molecule_name, m.smiles
        FROM docking_results dr JOIN molecules m ON dr.molecule_id=m.id
        WHERE dr.job_id=? ORDER BY dr.rank
    """, (job_id,)).fetchall()
    conn.close()

    result_list = [dict(r) for r in results]
    return await analyze_docking_results(result_list, dict(job)["receptor_pdb"])


@router.post("/analyze/md/{job_id}", summary="AI 解读 MD 模拟结果")
async def ai_analyze_md(job_id: int):
    ensure_db()
    conn = get_db()
    job = conn.execute("SELECT * FROM md_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not job:
        raise HTTPException(404, "MD job not found")

    traj = generate_mock_trajectory(dict(job)["sim_time_ns"])
    return await analyze_md_results(traj, dict(job)["job_name"])


@router.post("/report/lead-opt", summary="AI 先导化合物优化报告")
async def lead_opt_report(body: LeadOptIn):
    ensure_db()
    conn = get_db()
    results = conn.execute("""
        SELECT dr.*, m.name as molecule_name, m.smiles
        FROM docking_results dr JOIN molecules m ON dr.molecule_id=m.id
        WHERE dr.job_id=? ORDER BY dr.binding_energy LIMIT 5
    """, (body.docking_job_id,)).fetchall()
    mols = conn.execute("SELECT * FROM molecules").fetchall()
    conn.close()

    return await generate_lead_optimization_report(
        [dict(m) for m in mols],
        [dict(r) for r in results],
        target=body.target,
    )


@router.post("/chat", summary="计算化学专家对话")
async def expert_chat(body: ChatIn):
    """
    与计算化学 AI 专家对话
    可附带项目上下文（对接结果、MD 数据等）进行专业问答
    """
    return await chat_with_expert(body.question, body.context)


@router.get("/prompts/examples", summary="示例提示词")
def example_prompts():
    return {
        "docking": [
            "Celecoxib 在 COX-2 活性口袋中的关键相互作用有哪些？",
            "结合能 -9.2 kcal/mol 对应的 Ki 约为多少？",
            "如何判断对接结果是否可靠？",
        ],
        "md": [
            "RMSD 超过 3Å 是否意味着模拟出了问题？",
            "如何从 MD 轨迹提取代表性构象用于对接？",
            "MM/PBSA 和 FEP 各适用于什么场景？",
        ],
        "drug_design": [
            "如何设计针对 COX-2 的选择性抑制剂而不影响 COX-1？",
            "共价抑制剂和非共价抑制剂在设计上有什么区别？",
            "AIDD 中 MPNN 模型的优势是什么？",
        ],
    }
