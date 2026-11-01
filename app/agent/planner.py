"""Small, deterministic natural-language planner for the first MVP.

This intentionally does not execute model-generated code. It maps common Chinese/English
phrases onto a small allow-list of supported analysis operations.
"""
import re
from typing import Any

SUPPORTED_HINTS = ("分子", "候选物", "筛选", "性质", "排名", "报告", "molecule", "candidate", "property", "rank", "report", "smiles")
UNSUPPORTED_HINTS = {
    "docking": ("对接", "docking", "vina"),
    "md": ("分子动力学", "模拟轨迹", "md simulation", "molecular dynamics"),
    "admet": ("admet", "毒性预测", "药代动力学"),
}


def parse_request(prompt: str, default_top_n: int = 10) -> dict[str, Any]:
    text = prompt.strip()
    lowered = text.lower()
    unsupported = [kind for kind, terms in UNSUPPORTED_HINTS.items() if any(t in lowered for t in terms)]
    top_match = re.search(r"(?:top\s*|前\s*|取\s*|top[- ]?)(\d{1,3})", lowered, re.I)
    top_n = min(100, max(1, int(top_match.group(1)))) if top_match else default_top_n
    ro5_only = any(t in lowered for t in ("类药五原则", "五原则", "ro5", "lipinski", "lipinski rule"))
    mw_match = re.search(r"(?:分子量|molecular weight|mw)\s*(?:小于|低于|<|under|below)\s*(\d+(?:\.\d+)?)", lowered)
    max_mw = float(mw_match.group(1)) if mw_match else None
    logp_match = re.search(r"(?:logp|log\s*p)\s*(?:小于|低于|<|under|below)\s*(-?\d+(?:\.\d+)?)", lowered)
    max_logp = float(logp_match.group(1)) if logp_match else None
    if unsupported:
        supported = False
        reason = {
            "docking": "当前 MVP 不会执行真实分子对接；请先使用现有对接模块，并在后续版本接入已验证的 Vina/MCP 工具。",
            "md": "当前 MVP 不会执行真实 MD 计算；现有演示轨迹不能当作真实模拟结果。",
            "admet": "当前 MVP 尚未接入可验证的 ADMET 计算工具。",
        }[unsupported[0]]
        steps = [{"id": "validate_request", "tool": "request.validate", "status": "planned"}]
    else:
        supported = True
        reason = None
        steps = [
            {"id": "load_molecules", "tool": "molecule.list", "status": "planned"},
            {"id": "filter_molecules", "tool": "molecule.filter", "status": "planned"},
            {"id": "rank_candidates", "tool": "molecule.rank", "status": "planned"},
            {"id": "generate_report", "tool": "report.generate", "status": "planned"},
        ]
    return {
        "goal": "分子性质汇总与候选物筛选报告",
        "supported": supported,
        "unsupported_operations": unsupported,
        "reason": reason,
        "top_n": top_n,
        "filters": {"ro5_only": ro5_only, "max_molecular_weight": max_mw, "max_logp": max_logp},
        "steps": steps,
    }
