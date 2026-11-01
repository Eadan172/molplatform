"""Constrained natural-language planner. LLM output is advisory and never executable code."""
import json, re
from typing import Any
import httpx
from app.scale.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER
from app.scale.molclaw_adapter import skill_guidance

_ALLOWED = {"properties", "docking", "md", "admet", "unknown"}

def _heuristic(prompt: str) -> dict[str, Any]:
    t = prompt.lower()
    if any(x in t for x in ("gromacs", "gromacs-gpu", "分子动力学", "跑md", "md模拟", "md simulation", "md run")):
        op = "md"
    elif any(x in t for x in ("对接", "vina", "docking", "虚拟筛选")):
        op = "docking"
    elif any(x in t for x in ("admet", "毒性预测", "药代动力学", "药代性质")):
        op = "admet"
    elif any(x in t for x in ("分子", "smiles", "筛选", "性质", "候选物", "报告", "分子量", "logp", "qed")):
        op = "properties"
    else:
        op = "unknown"
    top = re.search(r"(?:top\s*|前\s*|展示\s*前\s*)(\d{1,5})", t, re.I)
    mw = re.search(r"(?:分子量|molecular weight|mw)\s*(?:小于|低于|<|under|below)\s*(\d+(?:\.\d+)?)", t, re.I)
    logp = re.search(r"(?:log\s*p|logp)\s*(?:小于|低于|<|under|below)\s*(-?\d+(?:\.\d+)?)", t, re.I)
    qed = re.search(r"(?:qed)\s*(?:大于|高于|>|above|over)\s*(0(?:\.\d+)?|1(?:\.0+)?)", t, re.I)
    center = re.search(r"(?:center|中心)\s*[:=]?\s*\(?\s*(-?\d+(?:\.\d+)?)\s*[,， ]\s*(-?\d+(?:\.\d+)?)\s*[,， ]\s*(-?\d+(?:\.\d+)?)", t, re.I)
    return {
        "operation": op,
        "top_n": min(10000, max(1, int(top.group(1)))) if top else 100,
        "filters": {
            "ro5_only": any(x in t for x in ("ro5", "类药五原则", "五原则", "lipinski")),
            "max_molecular_weight": float(mw.group(1)) if mw else None,
            "max_logp": float(logp.group(1)) if logp else None,
            "min_qed": float(qed.group(1)) if qed else None,
        },
        "vina_center": [float(center.group(i)) for i in (1, 2, 3)] if center else None,
        "planner": "deterministic_rules",
    }

def _llm_plan(prompt: str) -> dict[str, Any] | None:
    if not (LLM_PROVIDER != "none" and LLM_API_KEY and LLM_BASE_URL and LLM_MODEL):
        return None
    system = """You are a scientific workflow request parser. Return one JSON object only. Allowed operation: properties, docking, md, admet, unknown. Schema: {operation, top_n, filters:{ro5_only,max_molecular_weight,max_logp,min_qed}, vina_center:[x,y,z] or null, planner:'llm'}. Do not create shell commands, tool names, paths, or claim a calculation has run. If intent ambiguous use unknown. Do not infer target preparation or missing input files."""
    url = LLM_BASE_URL.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    try:
        r = httpx.post(url, headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                       json={"model": LLM_MODEL, "temperature": 0, "response_format": {"type": "json_object"},
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}, timeout=30)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        obj = json.loads(content)
        if obj.get("operation") not in _ALLOWED:
            return None
        obj["top_n"] = min(10000, max(1, int(obj.get("top_n", 100))))
        filt = obj.get("filters") or {}
        obj["filters"] = {
            "ro5_only": bool(filt.get("ro5_only", False)),
            "max_molecular_weight": _bounded_float(filt.get("max_molecular_weight"), 0.1, 100000),
            "max_logp": _bounded_float(filt.get("max_logp"), -20, 50),
            "min_qed": _bounded_float(filt.get("min_qed"), 0, 1),
        }
        center = obj.get("vina_center")
        if not (isinstance(center, list) and len(center) == 3 and all(_is_num(v) and abs(float(v)) <= 1000 for v in center)):
            obj["vina_center"] = None
        else:
            obj["vina_center"] = [float(v) for v in center]
        obj["planner"] = "llm_validated"
        return obj
    except Exception:
        # LLM is optional; deterministic parsing remains usable and is recorded in provenance.
        return None

def _is_num(v: Any) -> bool:
    try: float(v); return True
    except (TypeError, ValueError): return False

def _bounded_float(v: Any, low: float, high: float):
    if v is None or not _is_num(v): return None
    x = float(v)
    return x if low <= x <= high else None

def plan_request(prompt: str) -> dict[str, Any]:
    plan = _llm_plan(prompt) or _heuristic(prompt)
    if plan.get("operation") not in _ALLOWED:
        plan["operation"] = "unknown"
    op = plan["operation"]
    if op == "properties":
        steps = ["validate_smiles", "calculate_rdkit_descriptors", "apply_filters", "rank_candidates", "generate_report"]
    elif op == "docking":
        steps = ["validate_inputs", "convert_ligand_if_needed", "run_vina", "aggregate_scores", "generate_report"]
    elif op == "md":
        steps = ["validate_tpr_and_gromacs", "run_gromacs_mdrun", "collect_artifacts", "generate_report"]
    elif op == "admet":
        steps = ["validate_request"]
    else:
        steps = ["clarify_request"]
    plan["steps"] = [{"step": s, "status": "planned"} for s in steps]
    plan["requires_confirmation"] = op in {"docking", "md"}
    plan["molclaw"] = skill_guidance(op)
    return plan
