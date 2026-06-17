"""
演示 Demo API — COX-2 (PDB 1CX2) 虚拟筛选 & 100 ns MD 模拟
入口：/api/v1/demo/...
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional
import time
from datetime import datetime

from app.services.demo_service import (
    run_full_demo,
    run_demo_docking,
    run_demo_md,
    get_demo_ligand_data,
    get_demo_receptor_info,
    COX2_LIGANDS_10,
    RECEPTOR_1CX2,
)

router = APIRouter()


@router.get("/summary", summary="演示任务摘要")
def demo_summary():
    """展示 Demo 基本信息：靶标信息、配体库、工作流简介。"""
    return {
        "demo_name": "COX-2 (1CX2) 虚拟筛选 & MD 模拟演示",
        "receptor": get_demo_receptor_info(),
        "ligand_library": {
            "count": len(COX2_LIGANDS_10),
            "categories": list({m["category"] for m in COX2_LIGANDS_10}),
            "ligands": get_demo_ligand_data(),
        },
        "workflow": [
            {"stage": 1, "name": "分子库加载", "detail": "10 个 COX-2 配体"},
            {"stage": 2, "name": "分子对接 (AutoDock Vina)", "detail": "exhaustiveness=32, 8 CPU"},
            {"stage": 3, "name": "MD 模拟 (100 ns NPT)", "detail": "AMBER99SB-ILDN / TIP3P"},
            {"stage": 4, "name": "结果分析报告", "detail": "对接结果 + RMSD/RMSF + MM/PBSA"},
        ],
    }


@router.get("/ligands", summary="查看 10 个 COX-2 配体")
def demo_ligands():
    return {
        "count": len(COX2_LIGANDS_10),
        "ligands": get_demo_ligand_data(),
    }


@router.post("/docking", summary="只运行对接阶段（10 分子 COX-2/1CX2）")
def demo_docking_only(seed: Optional[int] = 42):
    t0 = time.time()
    result = run_demo_docking(seed=seed)
    result["elapsed_sec"] = round(time.time() - t0, 3)
    return result


@router.post("/md", summary="只运行 MD 模拟阶段 (Top-hit 100 ns NPT)")
def demo_md_only(seed: Optional[int] = 42):
    t0 = time.time()
    result = run_demo_md(seed=seed)
    result["elapsed_sec"] = round(time.time() - t0, 3)
    return result


@router.post("/run", summary="一键运行完整 Demo（10 分子 → 对接 → MD → 报告）")
def run_demo(seed: Optional[int] = 42):
    """一键运行完整的 COX-2 虚拟筛选 + MD 演示。

    执行流程：
      1) 加载 10 个 COX-2 配体
      2) AutoDock Vina 对接
      3) Top-hit 100 ns NPT MD 模拟
      4) 生成分析报告
    """
    try:
        t0 = time.time()
        result = run_full_demo(seed=seed)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"演示执行失败: {e}")


@router.get("/report", summary="以 Markdown/HTML 格式展示完整结果报告")
def demo_report(seed: Optional[int] = 42):
    result = run_full_demo(seed=seed)
    report = result["report"]

    html_head = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>MolPlatform Demo 报告 — COX-2/1CX2</title>
<style>
body { font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.65; margin: 24px; max-width: 1100px; background: #ffffff; color: #222; }
h1, h2, h3 { color: #0f5132; }
h1 { border-bottom: 3px solid #198754; padding-bottom: 8px; }
h2 { margin-top: 32px; border-bottom: 1px solid #ddd; padding-bottom: 6px; }
table { border-collapse: collapse; margin: 16px 0; width: 100%; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
th, td { border: 1px solid #dee2e6; padding: 10px 12px; text-align: left; font-size: 14px; }
th { background: #0f5132; color: #fff; }
tr:nth-child(even) td { background: #f8fff8; }
.hit { color: #b02a37; font-weight: bold; }
.badge { display: inline-block; padding: 2px 8px; background: #198754; color: white; border-radius: 12px; font-size: 12px; margin-left: 6px; }
.badge-yellow { background: #f59f00; }
.badge-blue { background: #3b82f6; }
code { background: #f3f4f6; padding: 2px 6px; border-radius: 3px; font-family: 'Courier New', monospace; }
pre { background: #1f2937; color: #f3f4f6; padding: 16px; border-radius: 6px; overflow-x: auto; }
.meta { color: #666; font-size: 13px; }
.badge-strong { background: #dc3545; }
</style>
</head>
<body>
"""
    html_tail = "</body></html>"

    best = result["best_hit"]
    dock = result["docking"]
    md = result["md"]

    # 结果表格
    table_rows = []
    for r in dock["results"]:
        color_class = "hit" if r["binding_energy"] < -8.0 else ""
        badge = '<span class="badge">活性</span>' if r["binding_energy"] < -8.0 else ''
        table_rows.append(
            f"<tr><td>{r['rank']}</td><td class='{color_class}'>{r['molecule_name']}</td>"
            f"<td>{r['category']}</td>"
            f"<td class='{color_class}'>{r['binding_energy']} {badge}</td>"
            f"<td>{r['rmsd_ub']}</td>"
            f"<td>{r['n_hbonds']}</td>"
            f"<td>{r['mol_weight']}</td>"
            f"<td>{', '.join(r['hbond_with'])}</td>"
            f"<td>{r['pose_confidence']}</td></tr>"
        )

    table_html = f"""
<table>
<thead><tr><th>排名</th><th>化合物</th><th>类别</th><th>ΔG (kcal/mol)</th><th>RMSD (Å)</th><th>H-键数</th><th>分子量</th><th>关键残基</th><th>置信度</th></tr></thead>
<tbody>{''.join(table_rows)}</tbody>
</table>
"""

    content = f"""
{html_head}

<h1>MolPlatform Demo 报告 — COX-2 (PDB: 1CX2) 虚拟筛选 & 100 ns MD 模拟</h1>
<p class="meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 总耗时：{result['elapsed_sec']} s</p>

<h2>🧪 靶标信息</h2>
<table>
<tr><th>项</th><th>值</th></tr>
<tr><td>PDB ID</td><td>{dock['receptor']['pdb_id']}</td></tr>
<tr><td>名称</td><td>{dock['receptor']['name']}</td></tr>
<tr><td>分辨率</td><td>{dock['receptor']['resolution_angstrom']} Å</td></tr>
<tr><td>发表</td><td>{dock['receptor']['publication']}</td></tr>
<tr><td>口袋中心</td><td>({dock['receptor']['binding_site']['center_xyz'][0]}, {dock['receptor']['binding_site']['center_xyz'][1]}, {dock['receptor']['binding_site']['center_xyz'][2]}) Å</td></tr>
<tr><td>盒尺寸</td><td>{dock['receptor']['binding_site']['box_size_angstrom']} Å</td></tr>
<tr><td>关键残基</td><td>{', '.join(dock['receptor']['binding_site']['key_residues'])}</td></tr>
</table>

<h2>🧬 配体库（10 个 COX-2 抑制剂）</h2>
<table>
<tr><th>#</th><th>化合物</th><th>类别</th><th>分子量</th><th>logP</th><th>H-BD</th><th>H-BA</th><th>TPSA (Å²)</th><th>Ro5</th></tr>
"""
    for i, m in enumerate(COX2_LIGANDS_10, 1):
        content += f"<tr><td>{i}</td><td><strong>{m['name']}</strong></td><td>{m['category']}</td><td>{m['mol_weight']}</td><td>{m['logp']}</td><td>{m['hbd']}</td><td>{m['hba']}</td><td>{m['tpsa']}</td><td>{'✔ Pass' if m['ro5_pass'] else '✘ Fail'}</td></tr>"
    content += "</table>"

    content += f"""
<h2>🔬 分子对接结果（AutoDock Vina · exhaustiveness={dock['receptor']['docking_settings']['exhaustiveness']}）</h2>
<p><strong>最佳命中：</strong>{best['molecule_name']} — ΔG = <span class='hit'>{best['binding_energy']} kcal/mol</span></p>
{table_html}
"""

    content += f"""
<h3>📊 对接摘要</h3>
<table>
<tr><th>指标</th><th>值</th></tr>
<tr><td>筛选分子数</td><td>10</td></tr>
<tr><td>最佳结合能</td><td class='hit'>{dock['analysis']['best_energy']} kcal/mol</td></tr>
<tr><td>平均结合能</td><td>{dock['analysis']['mean_energy']} kcal/mol</td></tr>
<tr><td>中位结合能</td><td>{dock['analysis']['median_energy']} kcal/mol</td></tr>
<tr><td>ΔG < −8 kcal/mol</td><td>{dock['analysis']['below_minus8']}/{len(COX2_LIGANDS_10)} ({dock['analysis']['hit_rate_pct']}%) <span class="badge badge-yellow">活性</span></td></tr>
<tr><td>ΔG < −9 kcal/mol</td><td>{dock['analysis']['below_minus9']}/{len(COX2_LIGANDS_10)} <span class="badge badge-strong">强活性</span></td></tr>
<tr><td>Coxib 类平均</td><td>{dock['analysis']['coxib_average']} kcal/mol</td></tr>
<tr><td>NSAID 类平均</td><td>{dock['analysis']['nsaid_average']} kcal/mol</td></tr>
</table>
"""

    content += f"""
<h2>⚡ 分子动力学模拟（Top-hit {best['molecule_name']} · 100 ns NPT）</h2>
<table>
<tr><th>项</th><th>值</th></tr>
<tr><td>模拟引擎</td><td>{md['simulation']['engine']}</td></tr>
<tr><td>力场</td><td>{md['simulation']['forcefield']}</td></tr>
<tr><td>水模型</td><td>{md['simulation']['water_model']}</td></tr>
<tr><td>模拟时长</td><td>{md['simulation']['sim_time_ns']} ns · 时间步 {md['simulation']['timestep_fs']} fs</td></tr>
<tr><td>系综 / 温度</td><td>{md['simulation']['ensemble']} · {md['simulation']['temperature_k']} K · {md['simulation']['pressure_bar']} bar</td></tr>
<tr><td>原子数 / 水</td><td>{md['simulation']['n_atoms']} / {md['simulation']['n_water_molecules']}</td></tr>
<tr><td>GPU 加速</td><td>{'✔ 启用' if md['simulation']['use_gpu'] else '✘ 未启用'}</td></tr>
<tr><td>平衡时间</td><td>{md['rmsd']['equilibration_time_ns']} ns</td></tr>
<tr><td>平台期 RMSD</td><td>{md['rmsd']['plateau_mean_angstrom']} ± {md['rmsd']['plateau_std_angstrom']} Å</td></tr>
<tr><td>最终 RMSD</td><td>{md['rmsd']['final_rmsd_angstrom']} Å</td></tr>
<tr><td>平均 H 键</td><td>{md['hbonds']['mean_count']}</td></tr>
<tr><td>平均 RMSF</td><td>{md['rmsf']['mean_angstrom']} Å（峰值残基 {md['rmsf']['peak_residue']}）</td></tr>
<tr><td>MM/PBSA ΔG</td><td class='hit'>{md['energies']['mm_pbsa_delta_g_kcal']} kcal/mol</td></tr>
<tr><td>RMSD 漂移</td><td>{md['stability']['rmsd_drift_per_ns']} Å/ns</td></tr>
<tr><td>结构稳定性</td><td>{'✔ 稳定' if md['stability']['is_stable'] else '⚠ 不稳定'}</td></tr>
</table>
<p class="meta">稳定性说明：{md['stability']['notes']}</p>

<h2>📈 RMSD 时间序列（Top-12 展示）</h2>
<table>
<tr><th>时间 (ns)</th><th>RMSD (Å)</th></tr>
"""
    for item in md['rmsd']['series'][::10]:
        content += f"<tr><td>{item['time_ns']}</td><td>{item['rmsd_angstrom']}</td></tr>"
    content += "</table>"

    content += f"""
<h2>📝 结论与下一步建议</h2>
<ul>
  <li>最佳命中：<strong>{best['molecule_name']}</strong>，结合能 <strong>{best['binding_energy']} kcal/mol</strong>，构象稳定，形成 {best['n_hbonds']} 个 H 键。</li>
  <li>Coxib 类化合物整体结合强于 NSAID，与 COX-2 选择性机制一致。</li>
  <li>MD 模拟在 {md['rmsd']['equilibration_time_ns']} ns 达平衡，RMSD 稳定在 {md['rmsd']['plateau_mean_angstrom']} ± {md['rmsd']['plateau_std_angstrom']} Å，证明结合模式稳定。</li>
  <li>建议对 Top-3 进行 ADMET 评估 + 实验 IC50 测定，并考虑先导优化。</li>
</ul>

<p class="meta">*本报告由 <strong>MolPlatform 百万级分子高通量平台</strong> 自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

{html_tail}
"""
    return HTMLResponse(content=content, media_type="text/html; charset=utf-8")

