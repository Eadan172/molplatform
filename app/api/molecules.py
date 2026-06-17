"""分子管理 API"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import json

from app.core.database import get_db, init_db, seed_db
from app.services.molecule_service import (
    parallel_batch_process,
    butina_cluster,
    standardize_smiles,
    validate_smiles,
)

router = APIRouter()


def ensure_db():
    init_db()
    seed_db()


# ─── Schemas ─────────────────────────────────────────────────────────────────

class MoleculeIn(BaseModel):
    name: str
    smiles: str
    source: str = "api"


class BatchMoleculeIn(BaseModel):
    molecules: List[MoleculeIn]
    n_workers: int = 4
    cluster: bool = True


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/", summary="获取分子列表")
def list_molecules(
    limit: int = Query(20, le=100),
    offset: int = 0,
    cluster_id: Optional[int] = None,
    ro5_only: bool = False,
):
    ensure_db()
    conn = get_db()
    cur = conn.cursor()

    where = []
    params = []
    if cluster_id is not None:
        where.append("cluster_id = ?")
        params.append(cluster_id)
    if ro5_only:
        where.append("ro5_pass = 1")

    sql = "SELECT * FROM molecules"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {limit} OFFSET {offset}"

    rows = cur.execute(sql, params).fetchall()
    total = cur.execute("SELECT COUNT(*) FROM molecules").fetchone()[0]
    conn.close()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "molecules": [dict(r) for r in rows],
    }


@router.get("/{mol_id}", summary="获取单个分子")
def get_molecule(mol_id: int):
    ensure_db()
    conn = get_db()
    row = conn.execute("SELECT * FROM molecules WHERE id=?", (mol_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Molecule not found")
    return dict(row)


@router.post("/", summary="新增单个分子")
def create_molecule(mol: MoleculeIn):
    ensure_db()
    if not validate_smiles(mol.smiles):
        raise HTTPException(400, f"无效的 SMILES: {mol.smiles}")

    smiles = standardize_smiles(mol.smiles)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO molecules (name,smiles,source) VALUES (?,?,?)",
        (mol.name, smiles, mol.source)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM molecules WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


@router.post("/batch", summary="批量导入分子（并行处理）")
def batch_import(body: BatchMoleculeIn):
    """
    并行批量处理 — 对应简历核心项目：
    通过并行计算优化，将单次处理周期从 48h 降至 6h
    """
    ensure_db()
    molecules = [{"name": m.name, "smiles": m.smiles} for m in body.molecules]

    # 并行处理
    result = parallel_batch_process(molecules, n_workers=body.n_workers)

    # 聚类分析
    if body.cluster and result["results"]:
        result["results"] = butina_cluster(result["results"])

    # 写入数据库
    conn = get_db()
    cur = conn.cursor()
    inserted = 0
    for mol in result["results"]:
        if mol.get("status") == "ok":
            try:
                cur.execute("""
                    INSERT INTO molecules (name,smiles,mol_weight,logp,hbd,hba,tpsa,ro5_pass,cluster_id,source)
                    VALUES (?,?,?,?,?,?,?,?,?,'batch')
                """, (
                    mol["name"], mol["smiles"], mol.get("mol_weight"),
                    mol.get("logp"), mol.get("hbd"), mol.get("hba"),
                    mol.get("tpsa"), mol.get("ro5_pass"), mol.get("cluster_id"),
                ))
                inserted += 1
            except Exception:
                pass
    conn.commit()
    conn.close()

    return {
        "inserted_to_db": inserted,
        "parallel_processing": {
            "workers": body.n_workers,
            "speedup": result["speedup_vs_serial"],
            "throughput_per_sec": result["throughput_per_sec"],
        },
        **result,
    }


@router.get("/stats/summary", summary="分子库统计")
def molecule_stats():
    ensure_db()
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM molecules").fetchone()[0]
    ro5 = conn.execute("SELECT COUNT(*) FROM molecules WHERE ro5_pass=1").fetchone()[0]
    clusters = conn.execute("SELECT COUNT(DISTINCT cluster_id) FROM molecules WHERE cluster_id IS NOT NULL").fetchone()[0]
    avg_mw = conn.execute("SELECT AVG(mol_weight) FROM molecules WHERE mol_weight IS NOT NULL").fetchone()[0]
    avg_logp = conn.execute("SELECT AVG(logp) FROM molecules WHERE logp IS NOT NULL").fetchone()[0]
    conn.close()

    return {
        "total_molecules": total,
        "ro5_pass": ro5,
        "ro5_fail": total - ro5,
        "ro5_pass_rate_pct": round(ro5 / total * 100, 1) if total else 0,
        "clusters": clusters,
        "avg_mol_weight": round(avg_mw, 1) if avg_mw else None,
        "avg_logp": round(avg_logp, 2) if avg_logp else None,
    }


@router.post("/validate", summary="验证 SMILES")
def validate(smiles: str):
    std = standardize_smiles(smiles)
    valid = validate_smiles(std)
    return {"smiles": smiles, "standardized": std, "valid": valid}
