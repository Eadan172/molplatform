"""MD 模拟 API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.core.database import get_db, init_db, seed_db
from app.services.md_service import (
    submit_md_job,
    generate_mock_trajectory,
    estimate_compute_time,
    FORCEFIELDS,
    WATER_MODELS,
)

router = APIRouter()


def ensure_db():
    init_db()
    seed_db()


class MDJobIn(BaseModel):
    job_name: str
    receptor_pdb: str = "1CX2"
    ligand_id: Optional[int] = 1
    forcefield: str = "AMBER99SB-ILDN"
    water_model: str = "TIP3P"
    sim_time_ns: float = 100.0
    timestep_fs: float = 2.0
    temperature_k: int = 300
    pressure_bar: float = 1.0
    use_gpu: bool = True


@router.get("/jobs", summary="MD 任务列表")
def list_jobs():
    ensure_db()
    conn = get_db()
    rows = conn.execute("SELECT * FROM md_jobs ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/jobs/{job_id}", summary="MD 任务详情")
def get_job(job_id: int):
    ensure_db()
    conn = get_db()
    job = conn.execute("SELECT * FROM md_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "Job not found")
    conn.close()
    return dict(job)


@router.post("/jobs", summary="提交 MD 模拟任务")
def submit_job(body: MDJobIn):
    ensure_db()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO md_jobs (job_name,receptor_pdb,ligand_id,forcefield,water_model,
                              sim_time_ns,timestep_fs,temperature_k,pressure_bar,use_gpu,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,'submitted')
    """, (body.job_name, body.receptor_pdb, body.ligand_id,
          body.forcefield, body.water_model,
          body.sim_time_ns, body.timestep_fs, body.temperature_k,
          body.pressure_bar, int(body.use_gpu)))
    job_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 生成任务配置
    job_config = submit_md_job(body.dict())
    return {
        "job_id": job_id,
        **job_config,
    }


@router.get("/jobs/{job_id}/trajectory", summary="MD 轨迹分析数据")
def get_trajectory(
    job_id: int,
    dt_ns: float = 1.0,
):
    ensure_db()
    conn = get_db()
    job = conn.execute("SELECT * FROM md_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "Job not found")
    job = dict(job)

    if job["status"] != "done":
        return {"job_id": job_id, "status": job["status"], "message": "任务尚未完成"}

    # 尝试从数据库读取 RMSD 序列
    rows = conn.execute(
        "SELECT time_ns, value FROM md_results WHERE job_id=? AND metric='rmsd' ORDER BY time_ns",
        (job_id,)
    ).fetchall()
    conn.close()

    if rows:
        rmsd_series = [{"time_ns": r[0], "rmsd_angstrom": r[1]} for r in rows]
        return {
            "job_id": job_id,
            "job_name": job["job_name"],
            "sim_time_ns": job["sim_time_ns"],
            "forcefield": job["forcefield"],
            "rmsd_series": rmsd_series,
            "rmsd_final": job.get("rmsd_final"),
            "status": "done",
        }
    else:
        # 实时生成（用于新提交的任务）
        traj = generate_mock_trajectory(job["sim_time_ns"], dt_ns)
        return {"job_id": job_id, **traj}


@router.get("/jobs/{job_id}/rmsf", summary="RMSF 残基柔性")
def get_rmsf(job_id: int):
    ensure_db()
    conn = get_db()
    job = conn.execute("SELECT * FROM md_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not job:
        raise HTTPException(404, "Job not found")
    traj = generate_mock_trajectory(dict(job)["sim_time_ns"], seed=job_id * 7)
    return {
        "job_id": job_id,
        "rmsf_per_residue": traj["rmsf_per_residue"],
        "mean_hbonds": traj["mean_hbonds"],
        "hbond_series": traj["hbond_counts"],
    }


@router.get("/estimate", summary="计算时间估算")
def estimate(
    n_atoms: int = 45000,
    sim_time_ns: float = 100,
    use_gpu: bool = True,
    n_gpu: int = 1,
):
    return estimate_compute_time(n_atoms, sim_time_ns, use_gpu, n_gpu)


@router.get("/forcefields", summary="支持的力场列表")
def list_forcefields():
    return FORCEFIELDS


@router.get("/water-models", summary="支持的水模型")
def list_water_models():
    return WATER_MODELS
