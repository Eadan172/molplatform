"""Natural-language task API and visual report UI."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from app.agent.schemas import AgentTaskRequest
from app.agent.service import create_task, get_task, get_report, list_reports

router = APIRouter()
_TEMPLATE = Path(__file__).parent / "templates" / "agent_report.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def agent_home():
    try:
        return HTMLResponse(_TEMPLATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(500, "MVP report template is missing")


@router.get("/capabilities", summary="查看 MVP 当前支持的能力")
def capabilities():
    return {
        "supported": [
            "读取已有分子库", "SMILES 基础检查（可选 RDKit）", "按 Ro5/分子量/LogP 筛选",
            "透明启发式排名", "分阶段筛选计数", "HTML 可视化报告", "JSON 导出", "历史任务与报告查询"
        ],
        "not_integrated": ["MolClaw 外部 MCP 调用", "真实分子对接", "真实 MD", "ADMET 计算"],
        "llm_required": False,
        "natural_language_mode": "rule_based_mvp",
        "note": "排名和现有数据库性质仅用于演示软件工作流，不构成科研结论。"
    }


@router.post("/tasks", summary="提交自然语言分子分析任务")
def submit_task(body: AgentTaskRequest):
    return create_task(body.prompt, body.molecule_ids, body.top_n, body.include_invalid)


@router.get("/tasks/{task_id}", summary="查询任务状态")
def task_status(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.get("/reports", summary="报告列表")
def reports(limit: int = Query(20, ge=1, le=100)):
    return {"reports": list_reports(limit)}


@router.get("/reports/{report_id}", summary="获取报告 JSON")
def report_detail(report_id: str):
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


@router.get("/reports/{report_id}/html", response_class=HTMLResponse, summary="查看可视化报告")
def report_html(report_id: str):
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    import json
    payload = json.dumps(report["report"], ensure_ascii=False).replace("</", "<\\/")
    html = _TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("const EMBEDDED_REPORT = null;", f"const EMBEDDED_REPORT = {payload};")
    html = html.replace("const EMBEDDED_REPORT_ID = null;", f"const EMBEDDED_REPORT_ID = {json.dumps(report_id)};")
    return HTMLResponse(html)


@router.get("/reports/{report_id}/export", summary="导出报告 JSON")
def export_report(report_id: str):
    from fastapi.responses import JSONResponse
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return JSONResponse(report["report"], headers={"Content-Disposition": f'attachment; filename="{report_id}.json"'})


@router.post("/dev-fixtures/ro5-violator", summary="开发测试：插入一个违反 Ro5 的测试分子")
def add_ro5_test_fixture():
    """Local-MVP helper to exercise filter exclusion; not intended for production exposure."""
    from app.core.database import get_db, init_db
    init_db()
    conn = get_db()
    name = "MVP-Test-Ro5-Violator"
    existing = conn.execute("SELECT id FROM molecules WHERE name=?", (name,)).fetchone()
    if existing:
        molecule_id = existing["id"]
        created = False
    else:
        cur = conn.execute(
            """INSERT INTO molecules
               (name,smiles,mol_weight,logp,hbd,hba,tpsa,ro5_pass,cluster_id,source)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (name, "C" * 20, 650.0, 6.2, 7, 13, 180.0, 0, None, "mvp_test_fixture")
        )
        molecule_id = cur.lastrowid
        created = True
    conn.commit()
    conn.close()
    return {"created": created, "molecule_id": molecule_id, "name": name,
            "ro5_pass": 0, "message": "已确保存在一个 Ro5 未通过的测试分子；请重新运行 Ro5 筛选。仅用于本地 MVP 测试。"}
