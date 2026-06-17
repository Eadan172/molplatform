"""分子对接 API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
from datetime import datetime

from app.core.database import get_db, init_db, seed_db
from app.services.docking_service import (
    run_docking_job,
    screen_virtual_library,
    get_docking_analysis,
    RECEPTOR_CONFIGS,
)

router = APIRouter()


def ensure_db():
    init_db()
    seed_db()


class DockingJobIn(BaseModel):
    job_name: str
    receptor: str = "1CX2"
    molecule_ids: Optional[List[int]] = None
    exhaustiveness: int = 32
    num_modes: int = 20
    energy_range: float = 3.0
    n_cpu: int = 8


class VirtualScreenIn(BaseModel):
    receptor: str = "1CX2"
    n_molecules: int = 100
    exhaustiveness: int = 32
    top_k: int = 10


@router.get("/jobs", summary="对接任务列表")
def list_jobs():
    ensure_db()
    conn = get_db()
    rows = conn.execute("SELECT * FROM docking_jobs ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/jobs/{job_id}", summary="对接任务详情")
def get_job(job_id: int):
    ensure_db()
    conn = get_db()
    job = conn.execute("SELECT * FROM docking_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "Job not found")
    results = conn.execute(
        """SELECT dr.*, m.name as molecule_name, m.smiles
           FROM docking_results dr JOIN molecules m ON dr.molecule_id=m.id
           WHERE dr.job_id=? ORDER BY dr.rank""",
        (job_id,)
    ).fetchall()
    conn.close()

    result_list = [dict(r) for r in results]
    return {
        **dict(job),
        "results": result_list,
        "analysis": get_docking_analysis(result_list),
    }


@router.post("/jobs", summary="提交对接任务")
def submit_docking(body: DockingJobIn):
    ensure_db()
    conn = get_db()

    # 获取分子数据
    if body.molecule_ids:
        placeholders = ",".join("?" * len(body.molecule_ids))
        mols = conn.execute(
            f"SELECT id,name,smiles FROM molecules WHERE id IN ({placeholders})",
            body.molecule_ids,
        ).fetchall()
    else:
        mols = conn.execute("SELECT id,name,smiles FROM molecules LIMIT 10").fetchall()

    if not mols:
        raise HTTPException(400, "No molecules found")

    mol_list = [dict(m) for m in mols]

    # 创建任务记录
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO docking_jobs (job_name,receptor_pdb,ligand_ids,exhaustiveness,status)
        VALUES (?,?,?,?,'running')
    """, (body.job_name, body.receptor, json.dumps([m["id"] for m in mol_list]), body.exhaustiveness))
    job_id = cur.lastrowid

    # 执行对接
    dock_result = run_docking_job(
        job_id=job_id,
        receptor=body.receptor,
        ligand_smiles_list=mol_list,
        exhaustiveness=body.exhaustiveness,
        num_modes=body.num_modes,
        energy_range=body.energy_range,
        n_cpu=body.n_cpu,
    )

    # 保存结果
    for res in dock_result["results"]:
        cur.execute("""
            INSERT INTO docking_results (job_id,molecule_id,binding_energy,rmsd_lb,rmsd_ub,rank)
            VALUES (?,?,?,?,?,?)
        """, (job_id, res["molecule_id"], res["binding_energy"],
              res["rmsd_lb"], res["rmsd_ub"], res["rank"]))

    cur.execute(
        "UPDATE docking_jobs SET status='done',finished_at=? WHERE id=?",
        (datetime.now().isoformat(), job_id)
    )
    conn.commit()
    conn.close()

    return {
        "job_id": job_id,
        "status": "done",
        **dock_result,
    }


@router.post("/screen", summary="大规模虚拟筛选")
def virtual_screen(body: VirtualScreenIn):
    """
    模拟大规模虚拟筛选
    对应 PPT：日筛选 10⁶+ 分子 (GPU 加速)
    """
    if body.n_molecules > 5000:
        raise HTTPException(400, "Demo 限制最大 5000 分子，生产环境无限制")
    return screen_virtual_library(
        receptor=body.receptor,
        n_molecules=body.n_molecules,
        exhaustiveness=body.exhaustiveness,
        top_k=body.top_k,
    )


@router.get("/receptors", summary="支持的靶标列表")
def list_receptors():
    return RECEPTOR_CONFIGS


@router.get("/results/{job_id}/top", summary="Top 命中化合物")
def top_hits(job_id: int, threshold: float = -8.0, limit: int = 10):
    ensure_db()
    conn = get_db()
    rows = conn.execute("""
        SELECT dr.*, m.name as molecule_name, m.smiles, m.mol_weight, m.logp
        FROM docking_results dr JOIN molecules m ON dr.molecule_id=m.id
        WHERE dr.job_id=? AND dr.binding_energy <= ?
        ORDER BY dr.binding_energy LIMIT ?
    """, (job_id, threshold, limit)).fetchall()
    conn.close()
    return {
        "job_id": job_id,
        "threshold_kcal": threshold,
        "hits": [dict(r) for r in rows],
        "n_hits": len(rows),
    }
