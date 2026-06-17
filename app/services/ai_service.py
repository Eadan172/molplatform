"""
AI 分析服务 — 调用 Claude API 进行结果解读与报告生成
对应简历：全链路自动化（结构获取 → 报告生成）
"""

import json
import httpx
from typing import Optional


CLAUDE_MODEL = "claude-sonnet-4-6"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


async def call_claude(
    prompt: str,
    system: str = "",
    max_tokens: int = 1500,
) -> dict:
    """
    调用 Anthropic Claude API
    （API Key 由服务端环境变量注入，前端无需传递）
    """
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            ANTHROPIC_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()

    if "error" in data:
        return {"success": False, "error": data["error"], "content": ""}

    text = "".join(
        block.get("text", "") for block in data.get("content", [])
        if block.get("type") == "text"
    )
    return {
        "success": True,
        "content": text,
        "model": CLAUDE_MODEL,
        "usage": data.get("usage", {}),
    }


# ─── 专项分析 prompt ─────────────────────────────────────────────────────────

SYSTEM_COMP_CHEM = """你是一位资深计算化学工程师，专精分子动力学模拟（Gromacs/Amber）、
分子对接（AutoDock Vina）和计算机辅助药物设计（CADD/AIDD）。
请用专业、简洁的中文给出分析，适当使用术语，并给出可操作的建议。"""


async def analyze_docking_results(results: list, receptor: str) -> dict:
    """对接结果 AI 解读"""
    top5 = sorted(results, key=lambda x: x.get("binding_energy", 0))[:5]
    results_summary = "\n".join(
        f"- {r['molecule_name']}: {r['binding_energy']} kcal/mol (RMSD_lb={r['rmsd_lb']})"
        for r in top5
    )

    prompt = f"""
对接靶标：{receptor}
Top-5 对接结果：
{results_summary}

请分析：
1. 哪些分子最有前景？结合能阈值建议（-8 kcal/mol 通常认为有显著结合力）
2. RMSD 值对构象质量的影响
3. 下一步建议（MD 验证？共价对接？ADMET 过滤？）
4. 若用于 COX-2 靶标，与已知 NSAIDs 相比如何？

回答控制在 300 字以内，分点作答。
"""
    result = await call_claude(prompt, system=SYSTEM_COMP_CHEM)
    return {
        "type": "docking_analysis",
        "receptor": receptor,
        "n_results": len(results),
        "ai_analysis": result.get("content", ""),
        "model": CLAUDE_MODEL,
        "usage": result.get("usage", {}),
    }


async def analyze_md_results(trajectory_data: dict, job_name: str) -> dict:
    """MD 轨迹结果 AI 解读"""
    notes = "\n".join(trajectory_data.get("analysis_notes", []))
    rmsd_mean = trajectory_data.get("rmsd_plateau_mean", "N/A")
    rmsd_std = trajectory_data.get("rmsd_plateau_std", "N/A")
    eq_ns = trajectory_data.get("equilibration_ns", "N/A")
    hbonds = trajectory_data.get("mean_hbonds", "N/A")

    prompt = f"""
MD 模拟任务：{job_name}
关键指标：
- 平衡时间：{eq_ns} ns
- 平台期 RMSD：{rmsd_mean} ± {rmsd_std} Å
- 平均氢键数：{hbonds}
- 自动分析：{notes}

请分析：
1. 体系是否稳定收敛？RMSD 是否在合理范围（<3Å 通常认为稳定）？
2. 氢键数量对结合稳定性的意义
3. 是否建议进行 MM/PBSA 自由能计算？
4. 有哪些需要警惕的信号？

回答控制在 300 字以内，分点作答。
"""
    result = await call_claude(prompt, system=SYSTEM_COMP_CHEM)
    return {
        "type": "md_analysis",
        "job_name": job_name,
        "ai_analysis": result.get("content", ""),
        "model": CLAUDE_MODEL,
        "usage": result.get("usage", {}),
    }


async def generate_lead_optimization_report(
    molecules: list,
    docking_results: list,
    target: str = "COX-2",
) -> dict:
    """先导化合物优化报告"""
    top_mols = sorted(docking_results, key=lambda x: x.get("binding_energy", 0))[:3]
    mol_info = "\n".join(
        f"- {m['molecule_name']}: SMILES={m['smiles'][:40]}..., 结合能={m['binding_energy']} kcal/mol"
        for m in top_mols
    )

    prompt = f"""
靶标：{target}
候选先导化合物：
{mol_info}

请生成一份简洁的先导化合物优化建议报告，包含：
1. 构效关系（SAR）初步分析
2. 建议优化的基团（增强结合力 / 改善 ADMET）
3. 下一步实验验证方案（体外 IC50、选择性测试等）
4. 计算层面建议（FEP、共价对接、AIDD 模型）

报告格式：结构化，约 400 字。
"""
    result = await call_claude(prompt, system=SYSTEM_COMP_CHEM)
    return {
        "type": "lead_optimization_report",
        "target": target,
        "n_candidates": len(top_mols),
        "report": result.get("content", ""),
        "model": CLAUDE_MODEL,
        "usage": result.get("usage", {}),
    }


async def chat_with_expert(
    user_question: str,
    context: Optional[dict] = None,
) -> dict:
    """
    计算化学专家对话
    可携带当前项目上下文（对接结果、MD 数据等）
    """
    ctx_str = ""
    if context:
        ctx_str = f"\n当前项目上下文：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n"

    prompt = f"{ctx_str}\n用户问题：{user_question}"

    result = await call_claude(prompt, system=SYSTEM_COMP_CHEM, max_tokens=800)
    return {
        "question": user_question,
        "answer": result.get("content", ""),
        "model": CLAUDE_MODEL,
        "has_context": bool(context),
    }
