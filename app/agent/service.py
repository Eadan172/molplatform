"""Task persistence, safe execution and report generation for the MVP."""
import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.database import get_db, init_db, seed_db
from app.agent.planner import parse_request

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="molplatform-agent")
_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_tables() -> None:
    init_db()
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS agent_tasks (
      task_id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL,
      plan_json TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
      message TEXT NOT NULL DEFAULT '', error TEXT, report_id TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS agent_reports (
      report_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, title TEXT NOT NULL,
      status TEXT NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id)
    );
    """)
    conn.commit()
    conn.close()
    seed_db()


def create_task(prompt: str, molecule_ids: list[int] | None = None, top_n: int = 10, include_invalid: bool = False) -> dict[str, Any]:
    _ensure_tables()
    plan = parse_request(prompt, top_n)
    task_id = "task_" + uuid.uuid4().hex[:12]
    now = utc_now()
    status = "queued" if plan["supported"] else "needs_tool"
    message = "任务已排队" if plan["supported"] else plan["reason"]
    plan["molecule_ids"] = molecule_ids or []
    plan["include_invalid"] = include_invalid
    conn = get_db()
    conn.execute("INSERT INTO agent_tasks(task_id,prompt,status,plan_json,progress,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                 (task_id, prompt, status, json.dumps(plan, ensure_ascii=False), 0, message, now, now))
    conn.commit()
    conn.close()
    if plan["supported"]:
        _EXECUTOR.submit(run_task, task_id)
    return get_task(task_id)


def _update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    assignments = ",".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [task_id]
    conn = get_db()
    conn.execute(f"UPDATE agent_tasks SET {assignments} WHERE task_id=?", values)
    conn.commit()
    conn.close()


def get_task(task_id: str) -> dict[str, Any] | None:
    _ensure_tables()
    conn = get_db()
    row = conn.execute("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["plan"] = json.loads(data.pop("plan_json"))
    return data


def get_report(report_id: str) -> dict[str, Any] | None:
    _ensure_tables()
    conn = get_db()
    row = conn.execute("SELECT * FROM agent_reports WHERE report_id=?", (report_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["report"] = json.loads(data.pop("report_json"))
    return data


def list_reports(limit: int = 20) -> list[dict[str, Any]]:
    _ensure_tables()
    conn = get_db()
    rows = conn.execute("SELECT report_id,task_id,title,status,created_at FROM agent_reports ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_molecules(ids: list[int]) -> list[dict[str, Any]]:
    conn = get_db()
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT id,name,smiles,mol_weight,logp,hbd,hba,tpsa,ro5_pass,cluster_id,source FROM molecules WHERE id IN ({placeholders})", ids).fetchall()
    else:
        rows = conn.execute("SELECT id,name,smiles,mol_weight,logp,hbd,hba,tpsa,ro5_pass,cluster_id,source FROM molecules ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _valid_smiles(smiles: str) -> tuple[bool, str]:
    """Use RDKit when installed; fallback is explicitly labelled syntax-only."""
    try:
        from rdkit import Chem  # type: ignore
        valid = Chem.MolFromSmiles(smiles) is not None
        return valid, "rdkit" if valid else "rdkit_invalid"
    except ImportError:
        if not smiles or len(smiles) < 2:
            return False, "basic_syntax"
        pairs = [("(", ")"), ("[", "]")]
        for left, right in pairs:
            depth = 0
            for ch in smiles:
                if ch == left: depth += 1
                if ch == right:
                    depth -= 1
                    if depth < 0: return False, "basic_syntax"
            if depth != 0: return False, "basic_syntax"
        return True, "basic_syntax_only"


def run_task(task_id: str) -> None:
    try:
        task = get_task(task_id)
        if not task or task["status"] in ("cancelled", "needs_tool"):
            return
        plan = task["plan"]
        _update_task(task_id, status="running", progress=10, message="正在读取分子库")
        molecules = _load_molecules(plan.get("molecule_ids", []))
        if plan.get("molecule_ids"):
            requested = set(plan["molecule_ids"])
            found = {m["id"] for m in molecules}
            missing = sorted(requested - found)
            if missing:
                raise ValueError(f"找不到分子 ID：{missing}")
        if not molecules:
            raise ValueError("分子库为空，请先通过分子管理 API 导入分子。")

        _update_task(task_id, progress=30, message="正在检查结构并应用筛选条件")
        valid_molecules, invalid_molecules = [], []
        validation_modes: set[str] = set()
        for mol in molecules:
            valid, mode = _valid_smiles(mol.get("smiles") or "")
            validation_modes.add(mode)
            enriched = {**mol, "smiles_valid": valid, "validation_mode": mode}
            if valid: valid_molecules.append(enriched)
            else: invalid_molecules.append(enriched)

        filters = plan["filters"]
        # Record counts at each stage so users can verify that filters actually exclude rows.
        filter_diagnostics = {"input_valid_count": len(valid_molecules), "after_ro5_count": None,
                              "after_molecular_weight_count": None, "after_logp_count": None,
                              "excluded_by_ro5": 0, "excluded_by_molecular_weight": 0,
                              "excluded_by_logp": 0}
        candidates = valid_molecules[:]
        if filters.get("ro5_only"):
            before = len(candidates)
            candidates = [m for m in candidates if m.get("ro5_pass") == 1]
            filter_diagnostics["excluded_by_ro5"] = before - len(candidates)
        filter_diagnostics["after_ro5_count"] = len(candidates)
        if filters.get("max_molecular_weight") is not None:
            before = len(candidates)
            candidates = [m for m in candidates if m.get("mol_weight") is not None and m["mol_weight"] < filters["max_molecular_weight"]]
            filter_diagnostics["excluded_by_molecular_weight"] = before - len(candidates)
        filter_diagnostics["after_molecular_weight_count"] = len(candidates)
        if filters.get("max_logp") is not None:
            before = len(candidates)
            candidates = [m for m in candidates if m.get("logp") is not None and m["logp"] < filters["max_logp"]]
            filter_diagnostics["excluded_by_logp"] = before - len(candidates)
        filter_diagnostics["after_logp_count"] = len(candidates)
        # Score is a transparent heuristic based on stored properties, not a predicted binding score.
        def score(m: dict[str, Any]) -> float:
            mw = m.get("mol_weight")
            logp = m.get("logp")
            hbd = m.get("hbd")
            hba = m.get("hba")
            ro5 = m.get("ro5_pass")
            score_value = 0.0
            score_value += 1.0 if ro5 == 1 else 0.0
            score_value += 0.25 if mw is not None and 150 <= mw <= 500 else 0.0
            score_value += 0.25 if logp is not None and 0 <= logp <= 5 else 0.0
            score_value += 0.25 if hbd is not None and hbd <= 5 else 0.0
            score_value += 0.25 if hba is not None and hba <= 10 else 0.0
            return round(score_value, 2)
        for mol in candidates:
            mol["heuristic_score"] = score(mol)
        candidates.sort(key=lambda m: (-m["heuristic_score"], m.get("mol_weight") is None, m.get("mol_weight") or 0, m["id"]))
        top_n = plan["top_n"]
        ranked = candidates[:top_n]
        _update_task(task_id, progress=70, message="正在整理图表数据与报告")

        report_id = "report_" + uuid.uuid4().hex[:12]
        title = "自然语言分子分析报告"
        report = {
            "schema_version": "1.0",
            "title": title,
            "summary": {
                "input_count": len(molecules), "valid_count": len(valid_molecules),
                "invalid_count": len(invalid_molecules), "filtered_count": len(candidates),
                "ranked_count": len(ranked),
                "text": f"共读取 {len(molecules)} 个分子，结构检查通过 {len(valid_molecules)} 个；筛选后 {len(candidates)} 个，展示前 {len(ranked)} 个。"
            },
            "filters": filters,
            "filter_diagnostics": filter_diagnostics,
            "ranking_method": "透明启发式规则：Ro5 通过 + 基础性质范围加分；不是结合亲和力或成药性预测。",
            "data_quality": {
                "property_source": "molplatform SQLite molecules 表中已有字段；未在本任务中重新计算这些数值。",
                "validation_modes": sorted(validation_modes),
                "validation_note": "若安装 RDKit，使用 RDKit 解析 SMILES；否则仅做括号等基础语法检查，不代表化学结构已被验证。",
                "calculation_mode": "database_properties_and_heuristic_ranking",
                "real_docking_performed": False,
                "real_md_performed": False,
                "warnings": ["排名仅用于演示筛选流程，不构成药物研发结论。", "分子性质字段可能来自原有演示种子数据或先前导入数据。"],
                "filter_note": "筛选分阶段计数可在报告中查看；Ro5 分布按结构检查通过的输入分子统计，而不是筛选后的候选物。"
            },
            "charts": {
                "molecular_weight": [{"name": m["name"], "value": m["mol_weight"]} for m in ranked if m.get("mol_weight") is not None],
                "logp": [{"name": m["name"], "value": m["logp"]} for m in ranked if m.get("logp") is not None],
                # Distribution is measured on valid input molecules BEFORE filters,
                # otherwise ro5_only makes the chart misleadingly show 100% pass.
                "ro5_distribution": [
                    {"label": "通过", "value": sum(1 for m in valid_molecules if m.get("ro5_pass") == 1)},
                    {"label": "未通过/未知", "value": sum(1 for m in valid_molecules if m.get("ro5_pass") != 1)},
                ]
            },
            "candidates": [{k: m.get(k) for k in ("id", "name", "smiles", "mol_weight", "logp", "hbd", "hba", "tpsa", "ro5_pass", "cluster_id", "source", "smiles_valid", "validation_mode", "heuristic_score")} for m in ranked],
            "invalid_molecules": [{"id": m["id"], "name": m["name"], "smiles": m["smiles"], "validation_mode": m["validation_mode"]} for m in invalid_molecules] if plan.get("include_invalid") else [],
            "provenance": {"task_id": task_id, "created_at": utc_now(), "tools": ["molecule.list", "molecule.validate", "molecule.filter", "molecule.rank", "report.generate"]}
        }
        conn = get_db()
        conn.execute("INSERT INTO agent_reports(report_id,task_id,title,status,report_json,created_at) VALUES(?,?,?,?,?,?)",
                     (report_id, task_id, title, "completed", json.dumps(report, ensure_ascii=False), utc_now()))
        conn.commit()
        conn.close()
        _update_task(task_id, status="completed", progress=100, message="报告已生成", report_id=report_id)
    except Exception as exc:
        _update_task(task_id, status="failed", progress=100, message="任务执行失败", error=str(exc))
