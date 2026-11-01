# MolPlatform — 百万级分子高通量数据架构与自动化处理平台

> 计算化学工程师 Demo 项目  
> 技术覆盖：FastAPI · 分子对接 · MD 模拟 · 并行计算 · LLM 集成

---

## 项目背景

针对药物研发过程中产生的海量分子结构数据，设计并实现一套高吞吐量的处理流水线：

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 单次处理周期 | 48 小时 | 6 小时 | **8×** |
| 虚拟筛选通量 | 60 分子/h | 10⁶+ 分子/天 (GPU) | **~700×** |
| 对接精度 | - | RMSD < 2Å, R > 0.7 | - |

---

## 技术架构

```
MolPlatform/
├── app/
│   ├── main.py                   # FastAPI 应用入口
│   ├── api/
│   │   ├── molecules.py          # 分子管理 API（批量导入/聚类/标准化）
│   │   ├── docking.py            # 分子对接 API（AutoDock Vina 风格）
│   │   ├── md_simulation.py      # MD 模拟 API（Gromacs/Amber 工作流）
│   │   ├── ai_analysis.py        # AI 分析 API（Claude LLM 集成）
│   │   └── pipeline.py           # 全链路管线 API（端到端自动化）
│   ├── core/
│   │   └── database.py           # SQLite 模型 + 测试数据种子
│   └── services/
│       ├── molecule_service.py   # 并行处理 / Butina 聚类
│       ├── docking_service.py    # 对接打分 / 虚拟筛选
│       ├── md_service.py         # MD 参数 / 轨迹分析
│       └── ai_service.py         # Anthropic Claude API 调用
├── tests/
│   └── test_api.py               # 自动化测试
├── requirements.txt
└── README.md
```

---

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
uvicorn app.main:app --reload --port 8000

# 3. 打开文档
open http://localhost:8000/docs
```

---

## API 模块说明

### 🧬 分子管理 `/api/v1/molecules`

| 接口 | 说明 |
|------|------|
| `GET /` | 列表查询（支持聚类过滤、Ro5 过滤） |
| `POST /` | 新增单个分子（SMILES 验证 + 标准化） |
| `POST /batch` | **并行批量导入**（对应简历：48h→6h 优化） |
| `POST /validate` | SMILES 合法性验证 |
| `GET /stats/summary` | 分子库统计（Ro5 通过率、聚类数） |

批量导入示例：
```json
POST /api/v1/molecules/batch
{
  "molecules": [
    {"name": "Compound-1", "smiles": "CC(=O)Oc1ccccc1C(=O)O"},
    {"name": "Compound-2", "smiles": "CC(C)Cc1ccc(cc1)C(C)C(=O)O"}
  ],
  "n_workers": 4,
  "cluster": true
}
```

### 🔬 分子对接 `/api/v1/docking`

对应 PPT：AutoDock Vina + COX-2 (1CX2) 虚拟筛选案例

| 接口 | 说明 |
|------|------|
| `POST /jobs` | 提交对接任务（含 exhaustiveness、CPU 核数） |
| `GET /jobs/{id}` | 任务详情 + 结果分析 |
| `POST /screen` | **大规模虚拟筛选**（模拟 10⁶ 分子/天） |
| `GET /results/{id}/top` | Top 命中（可设活性阈值） |
| `GET /receptors` | 支持靶标列表（COX-2、DJ-1） |

### ⚡ MD 模拟 `/api/v1/md`

对应 PPT：Gromacs/Amber 工作流，100ns NPT 模拟

| 接口 | 说明 |
|------|------|
| `POST /jobs` | 提交 MD 任务（力场/水模型/GPU 配置） |
| `GET /jobs/{id}/trajectory` | RMSD 时间序列 |
| `GET /jobs/{id}/rmsf` | RMSF 残基柔性 + 氢键分析 |
| `GET /estimate` | 计算时间估算（CPU vs GPU） |
| `GET /forcefields` | 支持的力场列表 |

### 🤖 AI 分析 `/api/v1/ai`

集成 Claude (claude-sonnet-4-6) 进行专业分析：

| 接口 | 说明 |
|------|------|
| `POST /analyze/docking/{id}` | AI 解读对接结果 |
| `POST /analyze/md/{id}` | AI 解读 MD 轨迹 |
| `POST /report/lead-opt` | 先导化合物优化报告 |
| `POST /chat` | 计算化学专家对话（支持携带项目上下文） |

### 🚀 全链路管线 `/api/v1/pipeline`

端到端自动化，对应简历：「从结构获取到报告生成」全链路闭环

```
Stage 1: 分子数据加载 & 标准化
Stage 2: AutoDock Vina 虚拟筛选
Stage 3: Top-hit MD 模拟验证（100ns）
Stage 4: AI 分析报告生成
```

提交全链路任务：
```json
POST /api/v1/pipeline/submit
{
  "name": "COX2-Campaign-001",
  "receptor": "1CX2",
  "docking_exhaustiveness": 32,
  "energy_threshold": -7.5,
  "run_md": true,
  "md_sim_time_ns": 100,
  "generate_ai_report": true
}
```

---

## 测试数据

内置 10 个分子（自动种子）：

| 分子 | 用途 | 聚类 |
|------|------|------|
| Celecoxib | COX-2 抑制剂（已上市） | 1 |
| Ibuprofen / Naproxen | NSAIDs | 2 |
| Aspirin | 非选择性 COX | 3 |
| Diclofenac / Indomethacin | COX-2 抑制剂 | 1 |
| DJ1-Cov-001/002 | DJ-1 共价抑制剂候选 | 4 |
| Kinase-Lead-A/B | 激酶选择性优化候选 | 5 |

---

## 生产环境扩展

| 模块 | Demo | 生产替换 |
|------|------|----------|
| 分子处理 | 自实现估算 | **RDKit** |
| 分子对接 | 模拟打分 | **AutoDock Vina / Smina / GNINA** |
| MD 模拟 | 轨迹生成 | **Gromacs / Amber + SLURM** |
| 任务队列 | 同步执行 | **Celery + Redis** |
| 数据库 | SQLite | **PostgreSQL** |
| 存储 | 本地 | **分布式对象存储（MinIO/OSS）** |
| LLM | Claude API | Claude / GPT-4 / 本地 LLM |
