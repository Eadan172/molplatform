"""Skill-guided MolClaw integration layer.

This MVP packages the upstream skill/workflow documents and translates their scientific
preconditions into a small allow-listed runtime. It does not pretend Markdown skills are
executable tools and does not launch the full Claude Code benchmark runner per request.
"""
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "integrations" / "molclaw_skills"

SKILLS = {
    "properties": [
        "molclaw-mol-basic-metrics.SKILL.md",
        "molclaw-drug-likeness.SKILL.md",
        "L2_workflows/03-molecular-property-analysis-filtering.md",
    ],
    "docking": [
        "molclaw-docking-screening.SKILL.md",
        "molclaw-quickvina-docking.SKILL.md",
        "L2_workflows/02-molecular-docking-screening.md",
        "L3_methodology/molclaw-drug-discovery-methodology.md",
    ],
    "md": [
        "L2_workflows/07-conformational-sampling-analysis.md",
        "L3_methodology/molclaw-drug-discovery-methodology.md",
    ],
    "admet": ["molclaw-admet.SKILL.md"],
}

def skill_refs(operation: str) -> list[str]:
    existing=[]
    for rel in SKILLS.get(operation, []):
        if (SKILL_ROOT / rel).is_file(): existing.append(rel)
    return existing

def skill_guidance(operation: str) -> dict[str, Any]:
    refs = skill_refs(operation)
    return {
        "integration_mode": "molclaw_skill_guided_allowlist",
        "references": refs,
        "skill_docs_available": bool(refs),
        "runtime_note": "技能文档作为工作流规范和前置条件；实际运算只通过受控的 RDKit、Vina 或 GROMACS 执行器完成。未安装的 MCP/模型工具不会被假定存在。",
    }
