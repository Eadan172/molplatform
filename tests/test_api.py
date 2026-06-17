"""
自动化测试脚本
运行方式：python tests/test_api.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import init_db, seed_db, get_db
from app.services.molecule_service import (
    parallel_batch_process, butina_cluster,
    standardize_smiles, validate_smiles,
)
from app.services.docking_service import (
    run_docking_job, screen_virtual_library, get_docking_analysis,
)
from app.services.md_service import (
    generate_mock_trajectory, estimate_compute_time,
)

PASS = "✅"
FAIL = "❌"
results = []


def test(name, fn):
    try:
        fn()
        print(f"  {PASS} {name}")
        results.append((name, True))
    except Exception as e:
        print(f"  {FAIL} {name}: {e}")
        results.append((name, False))


# ─── Database ────────────────────────────────────────────────────────────────
print("\n【数据库 & 测试数据】")

def t_db_init():
    init_db()
    seed_db()
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM molecules").fetchone()[0]
    conn.close()
    assert count >= 10, f"Expected ≥10 molecules, got {count}"

def t_db_docking_seed():
    conn = get_db()
    jobs = conn.execute("SELECT COUNT(*) FROM docking_jobs").fetchone()[0]
    results = conn.execute("SELECT COUNT(*) FROM docking_results").fetchone()[0]
    conn.close()
    assert jobs >= 1 and results >= 6, f"jobs={jobs}, results={results}"

def t_db_md_seed():
    conn = get_db()
    md = conn.execute("SELECT COUNT(*) FROM md_jobs").fetchone()[0]
    rmsd_pts = conn.execute("SELECT COUNT(*) FROM md_results WHERE metric='rmsd'").fetchone()[0]
    conn.close()
    assert md >= 1 and rmsd_pts >= 50, f"md={md}, rmsd_pts={rmsd_pts}"

test("数据库初始化", t_db_init)
test("对接任务种子数据", t_db_docking_seed)
test("MD 任务 RMSD 时间序列", t_db_md_seed)


# ─── Molecule Service ─────────────────────────────────────────────────────────
print("\n【分子数据处理服务】")

def t_smiles_validate():
    assert validate_smiles("CC(=O)O") == True
    assert validate_smiles("") == False
    assert validate_smiles("X") == False

def t_smiles_standardize():
    # 盐处理
    result = standardize_smiles("CC(=O)O.Cl")
    assert "." not in result, f"Salt not removed: {result}"

def t_parallel_process():
    mols = [
        {"name": f"mol_{i}", "smiles": "CC(=O)O" * (i % 3 + 1)}
        for i in range(8)
    ]
    result = parallel_batch_process(mols, n_workers=2)
    assert result["processed"] == 8
    assert result["elapsed_sec"] > 0
    assert result["throughput_per_sec"] > 0

def t_cluster():
    mols = [
        {"name": f"mol_{i}", "smiles": "CC(=O)O", "fingerprint": "abc" * i}
        for i in range(5)
    ]
    clustered = butina_cluster(mols)
    assert all("cluster_id" in m for m in clustered)
    assert all(m["cluster_id"] >= 1 for m in clustered)

test("SMILES 合法性验证", t_smiles_validate)
test("SMILES 标准化（盐处理）", t_smiles_standardize)
test("并行批处理（8 分子 × 2 worker）", t_parallel_process)
test("Butina 聚类分析", t_cluster)


# ─── Docking Service ──────────────────────────────────────────────────────────
print("\n【分子对接服务】")

def t_docking_run():
    mols = [
        {"id": i, "name": f"mol_{i}", "smiles": "CC(=O)Oc1ccccc1C(=O)O"}
        for i in range(1, 6)
    ]
    result = run_docking_job(
        job_id=999, receptor="1CX2",
        ligand_smiles_list=mols, exhaustiveness=8
    )
    assert result["n_ligands"] == 5
    assert result["top_hit"] is not None
    assert result["top_hit"]["binding_energy"] < 0
    # 结果按能量排序
    energies = [r["binding_energy"] for r in result["results"]]
    assert energies == sorted(energies)

def t_docking_analysis():
    fake_results = [
        {"binding_energy": -9.5}, {"binding_energy": -8.1},
        {"binding_energy": -7.2}, {"binding_energy": -6.0},
    ]
    analysis = get_docking_analysis(fake_results)
    assert analysis["best_energy"] == -9.5
    assert analysis["hits_below_minus8"] == 2
    assert analysis["hits_below_minus9"] == 1

def t_virtual_screen():
    result = screen_virtual_library("1CX2", n_molecules=50, top_k=5)
    assert result["library_size"] == 50
    assert result["hits_found"] >= 0
    assert "estimated_daily_capacity_gpu" in result

test("单次对接任务运行", t_docking_run)
test("对接结果统计分析", t_docking_analysis)
test("虚拟筛选（50 分子库）", t_virtual_screen)


# ─── MD Service ───────────────────────────────────────────────────────────────
print("\n【MD 模拟服务】")

def t_trajectory():
    traj = generate_mock_trajectory(sim_time_ns=50, dt_ns=2.0)
    assert len(traj["rmsd_series"]) == 26  # 0,2,4,...,50
    assert traj["equilibration_ns"] is not None
    assert traj["rmsd_plateau_mean"] > 0
    assert len(traj["rmsf_per_residue"]) == 200

def t_compute_estimate():
    est = estimate_compute_time(n_atoms=45000, sim_time_ns=100, use_gpu=True)
    assert est["gpu_estimate_days"] < est["cpu_estimate_days"]
    assert est["speedup_gpu_vs_cpu"] != "N/A"

def t_md_hbonds():
    traj = generate_mock_trajectory(sim_time_ns=10)
    assert traj["mean_hbonds"] > 0
    assert len(traj["hbond_counts"]) > 0

test("MD 轨迹生成（50 ns）", t_trajectory)
test("计算时间估算（CPU vs GPU）", t_compute_estimate)
test("氢键分析", t_md_hbonds)


# ─── Pipeline ─────────────────────────────────────────────────────────────────
print("\n【FastAPI 路由注册】")

def t_app_routes():
    from app.main import app
    routes = [r.path for r in app.routes]
    required = [
        "/api/v1/molecules/",
        "/api/v1/docking/jobs",
        "/api/v1/md/jobs",
        "/api/v1/ai/chat",
        "/api/v1/pipeline/submit",
    ]
    for r in required:
        assert r in routes, f"Missing route: {r}"

test("所有 API 路由注册正常", t_app_routes)


# ─── Summary ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"测试结果：{passed}/{total} 通过")
if passed == total:
    print("🎉 全部测试通过！")
else:
    failed = [n for n, ok in results if not ok]
    print(f"失败项：{failed}")
    sys.exit(1)
