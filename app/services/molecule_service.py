"""
分子数据处理服务
- 并行数据清洗与标准化（对应简历：并行优化 Python 脚本，48h→6h）
- 聚类分析（Tanimoto 相似度）
- 非结构化数据（SDF/SMILES）→ 结构化数据库
"""

import math
import time
import random
import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Optional
from dataclasses import dataclass, asdict


@dataclass
class MoleculeProps:
    name: str
    smiles: str
    mol_weight: float
    logp: float
    hbd: int     # H-bond donors
    hba: int     # H-bond acceptors
    tpsa: float  # Topological polar surface area
    ro5_pass: bool
    cluster_id: Optional[int] = None
    fingerprint: Optional[str] = None


def _estimate_props_from_smiles(smiles: str) -> dict:
    """
    简化版分子性质估算（生产环境使用 RDKit）。
    通过 SMILES 字符统计近似计算理化性质。
    """
    mw = len(smiles) * 6.5 + 80 + random.gauss(0, 15)
    logp = smiles.count("C") * 0.35 - smiles.count("O") * 0.8 - smiles.count("N") * 0.5
    hbd = smiles.count("OH") + smiles.count("NH")
    hba = smiles.count("O") + smiles.count("N")
    tpsa = hbd * 20.2 + hba * 12.8 + random.gauss(0, 5)

    ro5 = (mw <= 500 and logp <= 5 and hbd <= 5 and hba <= 10)

    # 指纹：基于 SMILES 的 MD5 模拟（生产用 Morgan 指纹）
    fp = hashlib.md5(smiles.encode()).hexdigest()

    return {
        "mol_weight": round(max(50, mw), 2),
        "logp": round(logp, 2),
        "hbd": max(0, hbd),
        "hba": max(0, hba),
        "tpsa": round(max(0, tpsa), 1),
        "ro5_pass": int(ro5),
        "fingerprint": fp,
    }


def _tanimoto(fp1: str, fp2: str) -> float:
    """Tanimoto 相似度（生产环境用 RDKit Morgan 指纹位运算）"""
    s1, s2 = set(fp1), set(fp2)
    inter = len(s1 & s2)
    union = len(s1 | s2)
    return inter / union if union else 0.0


# ─── 并行处理核心 ────────────────────────────────────────────────────────────

def process_single_molecule(item: dict) -> dict:
    """单分子处理任务（并行执行单元）"""
    name = item.get("name", f"mol_{random.randint(1000,9999)}")
    smiles = item["smiles"]

    # 模拟计算耗时（生产环境：RDKit 构象生成 + 性质计算）
    time.sleep(0.001)

    props = _estimate_props_from_smiles(smiles)
    return {
        "name": name,
        "smiles": smiles,
        **props,
        "status": "ok",
    }


def parallel_batch_process(molecules: List[dict], n_workers: int = 4) -> dict:
    """
    并行批量处理分子数据
    ─ 对应简历项目：并行计算优化，将处理周期从 48h 降至 6h（提升 8×）
    ─ 生产环境：ProcessPoolExecutor + Celery 分布式任务队列
    """
    t0 = time.time()
    results = []
    failed = []

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(process_single_molecule, mol): mol for mol in molecules}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                failed.append({"mol": futures[future], "error": str(e)})

    elapsed = time.time() - t0

    # 模拟性能报告
    throughput = len(results) / elapsed if elapsed > 0 else 0
    estimated_serial = elapsed * n_workers  # 单进程估计耗时

    return {
        "processed": len(results),
        "failed": len(failed),
        "elapsed_sec": round(elapsed, 3),
        "throughput_per_sec": round(throughput, 1),
        "speedup_vs_serial": f"{n_workers}×",
        "estimated_serial_sec": round(estimated_serial, 3),
        "workers": n_workers,
        "results": results,
        "errors": failed,
    }


# ─── 聚类分析 ────────────────────────────────────────────────────────────────

def butina_cluster(molecules: List[dict], threshold: float = 0.65) -> List[dict]:
    """
    Butina 聚类（简化版）— 生产环境用 RDKit rdMolDescriptors.GetMorganFingerprint
    对应简历：实现对非结构化数据的快速检索与聚类分析
    """
    fps = []
    for mol in molecules:
        fp = mol.get("fingerprint") or hashlib.md5(mol["smiles"].encode()).hexdigest()
        fps.append(fp)

    clusters = []
    assigned = [False] * len(fps)

    for i in range(len(fps)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(fps)):
            if not assigned[j] and _tanimoto(fps[i], fps[j]) >= threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    # 写回 cluster_id
    result = []
    for cid, members in enumerate(clusters, 1):
        for idx in members:
            mol = dict(molecules[idx])
            mol["cluster_id"] = cid
            result.append(mol)

    return result


# ─── 数据标准化 ──────────────────────────────────────────────────────────────

SMILES_ALIASES = {
    # 常见别名 → 标准 SMILES 前缀标记
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
}


def standardize_smiles(smiles: str) -> str:
    """
    SMILES 标准化（生产环境使用 RDKit Chem.MolToSmiles(Chem.MolFromSmiles(...))）
    去除盐、标准化芳香键等
    """
    smiles = smiles.strip()
    # 处理盐（取最大片段）
    if "." in smiles:
        parts = smiles.split(".")
        smiles = max(parts, key=len)
    return smiles


def validate_smiles(smiles: str) -> bool:
    """简单 SMILES 合法性检查（生产用 RDKit）"""
    if not smiles or len(smiles) < 2:
        return False
    brackets = smiles.count("[") == smiles.count("]")
    parens = smiles.count("(") == smiles.count(")")
    return brackets and parens
