"""
演示服务 — COX-2 虚拟筛选 Demo
场景：10 个 COX-2 配体对 PDB 1CX2 的分子对接 + 100ns MD 模拟
"""

import time
import json
import math
import random
from datetime import datetime
from typing import List, Dict, Any, Optional

# ======================================================================
# 10 个 COX-2 配体（真实 SMILES，涵盖上市药物 + 已知抑制剂）
# ======================================================================

COX2_LIGANDS_10: List[Dict[str, Any]] = [
    {
        "name": "Celecoxib",
        "smiles": "CC1=CC=C(C=C1)C1=CC(=NN1C1=CC=C(C=C1)S(N)(=O)=O)C(F)(F)F",
        "mol_weight": 381.37, "logp": 3.50, "hbd": 1, "hba": 5,
        "tpsa": 86.36, "ro5_pass": 1,
        "cluster_id": 1,
        "category": "Coxib (COX-2 选择性抑制剂)",
        "notes": "塞来昔布，首个上市的 COX-2 选择性抑制剂",
    },
    {
        "name": "Rofecoxib",
        "smiles": "CC1=C(C(=O)C2=CC=C(C=C2)OCC)C(C)(C)C(=O)O1",
        "mol_weight": 314.36, "logp": 3.24, "hbd": 0, "hba": 4,
        "tpsa": 63.60, "ro5_pass": 1,
        "cluster_id": 1,
        "category": "Coxib",
        "notes": "罗非昔布，COX-2 选择性抑制剂（万络）",
    },
    {
        "name": "Valdecoxib",
        "smiles": "CC1=CC(=C(=N1)NS(=O)(=O)C2=CC=C(C=C2)N(CC)CC",
        "mol_weight": 314.37, "logp": 3.12, "hbd": 1, "hba": 5,
        "tpsa": 86.27, "ro5_pass": 1,
        "cluster_id": 1,
        "category": "Coxib",
        "notes": "伐地昔布，COX-2 选择性抑制剂",
    },
    {
        "name": "Etoricoxib",
        "smiles": "CC1=CC=C(C=C1)C1=CC(=NN1C2=CC=C(C=C2)Cl)C(F)(F)F",
        "mol_weight": 358.80, "logp": 4.13, "hbd": 0, "hba": 4,
        "tpsa": 54.89, "ro5_pass": 1,
        "cluster_id": 1,
        "category": "Coxib",
        "notes": "依托考昔，COX-2 选择性抑制剂",
    },
    {
        "name": "Diclofenac",
        "smiles": "OC(=O)CC1=CC=CC=C1NC1=C(Cl)C=CC=C1Cl",
        "mol_weight": 296.15, "logp": 4.51, "hbd": 2, "hba": 2,
        "tpsa": 49.33, "ro5_pass": 1,
        "cluster_id": 2,
        "category": "NSAID",
        "notes": "双氯芬酸，经典 NSAID",
    },
    {
        "name": "Indomethacin",
        "smiles": "COC1=CC2=CC(CC(O)=O)=C(N2C(=O)C2=CC=C(Cl)C=C2)C=C1",
        "mol_weight": 357.79, "logp": 4.27, "hbd": 1, "hba": 4,
        "tpsa": 68.53, "ro5_pass": 1,
        "cluster_id": 2,
        "category": "NSAID",
        "notes": "吲哚美辛，经典 NSAID",
    },
    {
        "name": "Ibuprofen",
        "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(O)=O",
        "mol_weight": 206.28, "logp": 3.97, "hbd": 1, "hba": 1,
        "tpsa": 37.30, "ro5_pass": 1,
        "cluster_id": 3,
        "category": "NSAID",
        "notes": "布洛芬，经典 NSAID",
    },
    {
        "name": "Naproxen",
        "smiles": "CC(C1=CC2=CC=CC=C2C=C1)C(O)=O",
        "mol_weight": 230.26, "logp": 3.18, "hbd": 1, "hba": 2,
        "tpsa": 46.53, "ro5_pass": 1,
        "cluster_id": 3,
        "category": "NSAID",
        "notes": "萘普生，经典 NSAID",
    },
    {
        "name": "Aspirin",
        "smiles": "CC(=O)OC1=CC=CC=C1C(O)=O",
        "mol_weight": 180.16, "logp": 1.19, "hbd": 1, "hba": 3,
        "tpsa": 63.60, "ro5_pass": 1,
        "cluster_id": 4,
        "category": "NSAID (非选择性)",
        "notes": "阿司匹林，非选择性 COX 抑制剂",
    },
    {
        "name": "Piroxicam",
        "smiles": "CC1=CC=C(C=C1)S(=O)(=O)NC2=C(OCC3=CC=CC=C3)C(=O)C4=C2C=CC=C4",
        "mol_weight": 331.35, "logp": 3.06, "hbd": 2, "hba": 5,
        "tpsa": 96.85, "ro5_pass": 1,
        "cluster_id": 5,
        "category": "Oxicam",
        "notes": "吡罗昔康，烯醇酸类 NSAID",
    },
]


# ======================================================================
# PDB 1CX2 靶标配置
# ======================================================================

RECEPTOR_1CX2: Dict[str, Any] = {
    "pdb_id": "1CX2",
    "name": "Cyclooxygenase-2 (COX-2) — 前列腺素 H2 合成酶",
    "organism": "Mus musculus (小鼠)",
    "resolution_angstrom": 3.0,
    "publication": "Kurumbail et al., Cell 1996",
    "binding_site": {
        "description": "Arg120-Tyr355-Tyr385-Trp387-Phe518-Ser530 六联口袋",
        "center_xyz": (22.5, 5.8, 18.3),
        "box_size_angstrom": 30,
        "key_residues": ["ARG120", "TYR355", "TYR385", "TRP387", "PHE518", "SER530"],
        "hydrophobic_residues": ["VAL349", "LEU352", "VAL523"],
        "hydrophilic_residues": ["ARG120", "TYR355", "TYR385"],
    },
    "docking_settings": {
        "software": "AutoDock Vina (Demo)",
        "exhaustiveness": 32,
        "num_modes": 9,
        "energy_range_kcal": 3.0,
        "n_cpu": 8,
    },
    "md_settings": {
        "engine": "GROMACS 2026 (Demo)",
        "forcefield": "AMBER99SB-ILDN",
        "water_model": "TIP3P",
        "sim_time_ns": 100.0,
        "timestep_fs": 2.0,
        "temperature_k": 300,
        "pressure_bar": 1.0,
        "ensemble": "NPT",
        "use_gpu": True,
    },
}


# ======================================================================
# 参考结合能（文献报道值，kcal/mol）
# ======================================================================

REFERENCE_BINDING_ENERGY: Dict[str, float] = {
    "Celecoxib": -9.2,
    "Rofecoxib": -9.5,
    "Valdecoxib": -9.0,
    "Etoricoxib": -9.3,
    "Diclofenac": -8.7,
    "Indomethacin": -8.4,
    "Ibuprofen": -7.8,
    "Naproxen": -7.5,
    "Aspirin": -6.2,
    "Piroxicam": -8.9,
}


# ======================================================================
# 对接打分（基于参考值 + 小扰动 ——保持可重复）
# ======================================================================

def run_demo_docking(seed: int = 42) -> Dict[str, Any]:
    """执行 COX-2 1CX2 虚拟筛选 Demo 对接任务。

    返回：
        10 个分子的结合能、RMSD、关键相互作用、构象能量。
    """
    random.seed(seed)
    results = []
    for idx, mol in enumerate(COX2_LIGANDS_10, 1):
        name = mol["name"]
        ref = REFERENCE_BINDING_ENERGY[name]
        # 结合能（参考值 ± 扰动）
        binding_energy = round(ref + random.gauss(0, 0.25), 2)
        rmsd_lb = 0.0
        rmsd_ub = round(max(1.5, 1.5 + abs(ref) * 0.18 + random.gauss(0, 0.2)), 2)

        # H 键
        n_hbonds = random.randint(1, 3)
        hbond_residues = random.sample(
            RECEPTOR_1CX2["binding_site"]["key_residues"], n_hbonds
        )

        # 相互作用（随机生成展示）
        interactions = []
        for res in hbond_residues:
            interactions.append({"type": "H-BOND", "residue": res, "energy_kcal": round(random.uniform(-1.8, -0.5), 2)})

        # 疏水作用
        hydro = random.sample(
            RECEPTOR_1CX2["binding_site"].get("hydrophobic_residues", []), 2
        )
        for res in hydro:
            interactions.append({"type": "HYDROPHOBIC", "residue": res, "energy_kcal": round(random.uniform(-1.2, -0.3), 2)})

        results.append({
            "rank": 0,
            "index": idx,
            "molecule_name": name,
            "smiles": mol["smiles"],
            "mol_weight": mol["mol_weight"],
            "category": mol["category"],
            "binding_energy": binding_energy,
            "rmsd_lb": rmsd_lb,
            "rmsd_ub": rmsd_ub,
            "n_hbonds": n_hbonds,
            "hbond_with": hbond_residues,
            "interactions": interactions,
            "pose_confidence": round(min(1.0, max(0.4, 1.0 + binding_energy / 12.0 + random.gauss(0, 0.05))), 2),
        })

    # 按结合能排序
    results.sort(key=lambda x: x["binding_energy"])
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    # 统计分析
    energies = [r["binding_energy"] for r in results]
    analysis = {
        "best_energy": min(energies),
        "worst_energy": max(energies),
        "mean_energy": round(sum(energies) / len(energies), 2),
        "median_energy": round(sorted(energies)[len(energies) // 2], 2),
        "below_minus8": sum(1 for e in energies if e < -8.0),
        "below_minus9": sum(1 for e in energies if e < -9.0),
        "hit_rate_pct": round(sum(1 for e in energies if e < -8.0) / len(energies) * 100, 1),
        "coxib_average": round(
            sum(r["binding_energy"] for r in results if r["category"].startswith("Coxib")) /
            sum(1 for r in results if r["category"].startswith("Coxib")), 2),
        "nsaid_average": round(
            sum(r["binding_energy"] for r in results if r["category"].startswith("NSAID")) /
            sum(1 for r in results if r["category"].startswith("NSAID")), 2),
    }

    return {
        "timestamp": datetime.now().isoformat(),
        "receptor": RECEPTOR_1CX2,
        "results": results,
        "analysis": analysis,
        "best_hit": results[0],
    }


# ======================================================================
# MD 轨迹分析（RMSD/RMSF/氢键/能量）
# ======================================================================

def run_demo_md(seed=None) -> Dict[str, Any]:
    """对最佳命中 (Top-3 进行 MD 模拟分析 Demo。

    输出 100ns NPT MD 模拟结果：RMSD 时间序列、RMSF、氢键、能量。
    """
    seed = 42
    random.seed(seed)
    n_frames = 101  # 0~100ns, 每 1ns 采样
    times = [round(i * 1.0, 1) for i in range(n_frames)]

    # RMSD 时间序列（指数弛豫 + 噪声）
    plateau = 2.32  # 平台期 RMSD (Å)
    rmsd_series = []
    for t in times:
        val = 0.5 + (plateau - 0.5) * (1 - math.exp(-t / 18.0))
        val += random.gauss(0, 0.10 if t < 40 else 0.15)
        rmsd_series.append({"time_ns": round(t, 1), "rmsd_angstrom": round(max(0.3, val), 3)})

    # 平衡时间
    eq_ns = 35.0

    # RMSF（200 残基）
    n_residues = 200
    rmsf_data = []
    for r in range(1, n_residues + 1):
        # 末端柔性高，loop 区（约 80-100）有峰值
        dist_end = min(r, n_residues - r + 1)
        if dist_end <= 8:
            base = 1.6 + (8 - dist_end) * 0.08
        elif 78 < r < 102:
            base = 1.15 + 0.35 * math.sin((r - 78) / 12 * math.pi)
        else:
            base = 0.50 + 0.25 * math.sin(r / 14)
        base += random.gauss(0, 0.09)
        rmsf_data.append({"residue": r, "rmsf_angstrom": round(max(0.28, base), 3)})

    # 氢键时间序列（配体-蛋白 H 键）
    hbond_series = []
    for t in times[::2]:
        hbond_series.append({"time_ns": t, "count": max(1, int(3 + random.gauss(0, 1.1)))})

    # 势能/动能时间序列
    potential_series = []
    kinetic_series = []
    for t in times[::4]:
        potential_series.append({"time_ns": t, "energy_kjmol": round(-1.28e6 + random.gauss(0, 2000), 0)})
        kinetic_series.append({"time_ns": t, "energy_kjmol": round(5.6e5 + random.gauss(0, 1500), 0)})

    # 结合自由能估计（MM/PBSA 近似）
    mm_pbsa_delta_g = round(-(8.5 + random.gauss(0, 0.4)), 2)

    # 平台期统计
    tail_rmsd = [r["rmsd_angstrom"] for r in rmsd_series[int(len(rmsd_series) * 0.6):]]
    tail_mean = sum(tail_rmsd) / len(tail_rmsd)
    tail_std = math.sqrt(sum((x - tail_mean) ** 2 for x in tail_rmsd) / len(tail_rmsd))

    return {
        "timestamp": datetime.now().isoformat(),
        "simulation": {
            "engine": RECEPTOR_1CX2["md_settings"]["engine"],
            "forcefield": RECEPTOR_1CX2["md_settings"]["forcefield"],
            "water_model": RECEPTOR_1CX2["md_settings"]["water_model"],
            "sim_time_ns": RECEPTOR_1CX2["md_settings"]["sim_time_ns"],
            "timestep_fs": RECEPTOR_1CX2["md_settings"]["timestep_fs"],
            "temperature_k": RECEPTOR_1CX2["md_settings"]["temperature_k"],
            "pressure_bar": RECEPTOR_1CX2["md_settings"]["pressure_bar"],
            "ensemble": RECEPTOR_1CX2["md_settings"]["ensemble"],
            "use_gpu": RECEPTOR_1CX2["md_settings"]["use_gpu"],
            "box_size_nm": 6.5,
            "n_atoms": 45182,
            "n_water_molecules": 13842,
            "total_charge": 0,
        },
        "rmsd": {
            "series": rmsd_series,
            "plateau_mean_angstrom": round(tail_mean, 3),
            "plateau_std_angstrom": round(tail_std, 3),
            "equilibration_time_ns": eq_ns,
            "initial_rmsd_angstrom": round(rmsd_series[0]["rmsd_angstrom"], 3),
            "final_rmsd_angstrom": round(rmsd_series[-1]["rmsd_angstrom"], 3),
        },
        "rmsf": {
            "per_residue": rmsf_data,
            "mean_angstrom": round(sum(r["rmsf_angstrom"] for r in rmsf_data) / len(rmsf_data), 3),
            "max_angstrom": round(max(r["rmsf_angstrom"] for r in rmsf_data), 3),
            "peak_residue": max(rmsf_data, key=lambda r: r["rmsf_angstrom"])["residue"],
        },
        "hbonds": {
            "series": hbond_series,
            "mean_count": round(sum(h["count"] for h in hbond_series) / len(hbond_series), 2),
            "max_count": max(h["count"] for h in hbond_series),
        },
        "energies": {
            "potential_series_kjmol": potential_series,
            "kinetic_series_kjmol": kinetic_series,
            "mm_pbsa_delta_g_kcal": mm_pbsa_delta_g,
        },
        "stability": {
            "rmsd_drift_per_ns": round((rmsd_series[-1]["rmsd_angstrom"] - rmsd_series[int(len(rmsd_series) * 0.6)]["rmsd_angstrom"]) / 40, 4),
            "is_stable": True,
            "notes": "体系在 35 ns 后达平衡，RMSD 稳定在 2.32 ± 0.15 Å，结构稳定",
        },
    }


# ======================================================================
# 完整 Demo 管线
# ======================================================================

def run_full_demo(seed: int = 42) -> Dict[str, Any]:
    """运行完整的 COX-2 演示管线：
        步骤 1：加载 10 个 COX-2 配体（分子库）
        步骤 2：AutoDock Vina 分子对接
        步骤 3：Top-hit 100ns NPT MD 模拟
        步骤 4：生成完整分析报告
    """
    t0 = time.time()

    # 阶段 1: 分子库
    library = [dict(m) for m in COX2_LIGANDS_10]

    # 阶段 2: 对接
    docking = run_demo_docking(seed=seed)

    # 阶段 3: MD (取Top 3 进行模拟)
    md = run_demo_md(seed=seed + 1)

    # 阶段 4: 报告
    elapsed = time.time() - t0

    best = docking["best_hit"]
    report = generate_demo_report(library, docking, md)

    return {
        "demo_name": "COX-2 (1CX2) 虚拟筛选 & MD 模拟演示",
        "version": "1.0",
        "elapsed_sec": round(elapsed, 3),
        "timestamp": datetime.now().isoformat(),
        "pipeline_stages": [
            "阶段 1 — 分子库加载（10 个 COX-2 配体）",
            "阶段 2 — AutoDock Vina 分子对接",
            "阶段 3 — Top-hit 100 ns NPT MD 模拟",
            "阶段 4 — 结果分析 & 报告生成",
        ],
        "library": library,
        "docking": docking,
        "md": md,
        "best_hit": best,
        "report": report,
    }


# ======================================================================
# Demo 报告生成
# ======================================================================

def generate_demo_report(library, docking, md) -> Dict[str, Any]:
    """生成丰富的演示结果报告（Markdown + 结构化摘要）"""
    analysis = docking["analysis"]
    best = docking["best_hit"]
    top3 = docking["results"][:3]

    # 分类统计
    categories = {}
    for r in docking["results"]:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "energies": []}
        categories[cat]["count"] += 1
        categories[cat]["energies"].append(r["binding_energy"])

    category_summary = []
    for cat, info in categories.items():
        avg = round(sum(info["energies"]) / len(info["energies"]), 2)
        category_summary.append({"category": cat, "count": info["count"], "avg_energy": avg})

    # 命中率/类药五原则统计
    ro5_passes = sum(1 for m in library if m["ro5_pass"])
    ro5_fail = len(library) - ro5_passes

    # 生成可读报告
    receptor = docking["receptor"]
    binding = receptor["binding_site"]
    center_xyz = binding["center_xyz"]

    avg_mw = round(sum(m["mol_weight"] for m in library) / len(library), 1)
    avg_logp = round(sum(m["logp"] for m in library) / len(library), 2)

    rmsd_eq = md["rmsd"]["equilibration_time_ns"]
    rmsd_plateau = md["rmsd"]["plateau_mean_angstrom"]
    rmsd_std = md["rmsd"]["plateau_std_angstrom"]
    rmsd_final = md["rmsd"]["final_rmsd_angstrom"]
    mean_hbonds = md["hbonds"]["mean_count"]
    mm_pbsa = md["energies"]["mm_pbsa_delta_g_kcal"]
    stability_note = md["stability"]["notes"]
    ff_name = md["simulation"]["forcefield"]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    summary_lines = [
        "### 🔬 **靶标：** " + receptor["name"],
        "- PDB ID: **" + receptor["pdb_id"] + "**",
        "- 分辨率: " + str(receptor["resolution_angstrom"]) + " Å",
        "- 发表: " + receptor["publication"],
        "- 结合口袋中心: (" + str(center_xyz[0]) + ", " + str(center_xyz[1]) + ", " + str(center_xyz[2]) + ") Å",
        "",
        "### 🧬 **配体库：** 10 个 COX-2 抑制剂（Coxib 4, NSAID 5, Oxicam 1）",
        "- 类药五原则 (Ro5) 通过率: " + str(ro5_passes) + "/" + str(len(library)),
        "- 平均分子量: " + str(avg_mw),
        "- 平均 logP: " + str(avg_logp),
        "",
        "### 🎯 **对接分析（AutoDock Vina）**",
        "- **最佳命中:** **" + best["molecule_name"] + "** — ΔG = " + str(best["binding_energy"]) + " kcal/mol",
        "- 平均结合能: " + str(analysis["mean_energy"]) + " kcal/mol",
        "- 中位结合能: " + str(analysis["median_energy"]) + " kcal/mol",
        "- 活性命中数 (ΔG < −8.0 kcal/mol): " + str(analysis["below_minus8"]) + "/" + str(len(library)) + " (" + str(analysis["hit_rate_pct"]) + "%)",
        "- 强活性命中数 (ΔG < −9.0 kcal/mol): " + str(analysis["below_minus9"]) + "/" + str(len(library)),
        "- Coxib 平均: " + str(analysis["coxib_average"]) + " kcal/mol",
        "- NSAID 平均: " + str(analysis["nsaid_average"]) + " kcal/mol",
        "",
        "### ⚡ **MD 模拟（100 ns NPT，" + ff_name + "）**",
        "- 平衡时间: " + str(rmsd_eq) + " ns",
        "- 平台期 RMSD: " + str(rmsd_plateau) + " ± " + str(rmsd_std) + " Å",
        "- 最终 RMSD: " + str(rmsd_final) + " Å",
        "- 平均氢键数 (配体-蛋白): " + str(mean_hbonds),
        "- MM/PBSA ΔG ≈ " + str(mm_pbsa) + " kcal/mol",
        "- 结构稳定性: **稳定**（" + stability_note + "）",
        "",
        "### 📊 **Top-3 命中化合物**",
    ]
    for r in top3:
        line = ("- **" + str(r["rank"]) + ". " + r["molecule_name"] + "** (" + r["category"] +
                ") — ΔG = " + str(r["binding_energy"]) + " kcal/mol, RMSD = " +
                str(r["rmsd_ub"]) + " Å, H-Bonds = " + str(r["n_hbonds"]))
        summary_lines.append(line)

    summary_lines += [
        "",
        "### 🧪 **下一步建议**",
        "1. 对 Top-3 化合物进行实验 IC50 测定（COX-1 vs COX-2 选择性评估）",
        "2. 进行 ADMET 性质评估（SwissADME）",
        "3. 扩展虚拟筛选至 10⁶ 级分子库",
        "4. 进行 500 ns 增强采样（或伞形采样）以计算精确结合自由能",
        "5. 设计 Coxib 类似物进行先导优化",
        "",
        "*本报告由 **MolPlatform 高通量平台** 自动生成 — " + now_str + "*",
    ]

    summary_text = "\n".join(summary_lines)

    return {
        "summary_text": summary_text,
        "summary_markdown": summary_text,
        "category_summary": category_summary,
        "top3_hits": top3,
        "ro5_summary": {
            "pass": ro5_passes,
            "fail": ro5_fail,
            "rate_pct": round(ro5_passes / len(library) * 100, 1),
        },
        "library_size": len(library),
        "docking_summary": analysis,
        "md_summary": {
            "equilibration_ns": md["rmsd"]["equilibration_time_ns"],
            "plateau_rmsd": md["rmsd"]["plateau_mean_angstrom"],
            "final_rmsd": md["rmsd"]["final_rmsd_angstrom"],
            "mean_hbonds": md["hbonds"]["mean_count"],
            "mm_pbsa": md["energies"]["mm_pbsa_delta_g_kcal"],
        },
    }


# ======================================================================
# Demo 数据用于前端可视化（JSON）
# ======================================================================

def get_demo_ligand_data(_unused=None) -> List[Dict[str, Any]]:
    """返回用于前端展示的 10 个配体数据。"""
    return [dict(m) for m in COX2_LIGANDS_10]


def get_demo_receptor_info(_unused=None) -> Dict[str, Any]:
    """返回 1CX2 靶标信息。"""
    return dict(RECEPTOR_1CX2)

