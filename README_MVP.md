# MolPlatform 自然语言分析 MVP（本地测试版）

本压缩包是在原始 `molplatform-main` 源码上增加 MVP 功能后的完整工程。原项目已有的分子管理、对接、MD、AI 分析和演示接口均保留。

## MVP 当前能做什么

- 在网页输入中文/英文自然语言分析需求。
- 将常见需求解析为一个经过白名单约束的任务计划。
- 从已有 SQLite 分子库读取分子，可选指定 `molecule_ids`。
- 检查 SMILES：若安装 RDKit 则使用 RDKit 解析；否则只做括号等基础语法检查，并在报告中明确标注。
- 使用数据库里已有的分子性质字段，支持 Ro5、分子量上限、LogP 上限等基础筛选。
- 按透明的启发式规则排序，生成图表和可视化 HTML 报告。
- 报告显示每个筛选阶段的输入/输出数量及排除数量，便于验证过滤条件是否实际生效。
- 网页提供“添加 Ro5 反例（测试）”按钮，可插入一个明确标记的 Ro5 未通过测试分子，验证筛选逻辑。该开发测试接口不应暴露到生产环境。
- 显示筛选前的 Ro5 分布，避免启用 Ro5 筛选后图表只显示 100% 通过而造成误解。
- 保存任务状态、任务计划和报告；支持历史报告和 JSON 导出。
- 不要求配置 LLM API Key，也能测试整个端到端流程。
- MoleCode 结构层：`POST /api/v1/agent/tools/molecode` 在 SMILES 与 Mermaid 图之间转换，并用 RDKit 校验。请求里出现 MoleCode、Mermaid 或「结构图」时，自然语言任务会调用同一工具。它不执行对接、MD 或 HTVS。转换不需要 LLM；可选的 `molecode.llm` 复用 `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。

## 当前明确不包含的功能

- 当前 MVP 不会调用 MolClaw 的外部 MCP 工具；三个技能说明副本放在 `integrations/molclaw_skills/` 供后续接入。
- 不会执行真实分子对接、MD 或 ADMET 计算。对于此类请求，任务会提示相应能力尚未接入，不会把原项目中的模拟轨迹或演示打分冒充真实结果。
- 当前自然语言理解采用规则解析，不是完整的 LLM agent。支持范围见下文。
- 排名是演示用启发式规则，不是亲和力预测、临床结论或药物开发建议。
- 分子性质数值取自数据库既有字段；本次任务不会重新计算这些字段。

## 环境要求

- Python 3.10 或更高版本（推荐 Python 3.11）
- 可选：RDKit。若要进行更可靠的 SMILES 结构解析，推荐通过 conda 安装：

```bash
conda create -n molplatform python=3.11 -y
conda activate molplatform
conda install -c conda-forge rdkit -y
pip install -r requirements.txt
```

如果暂时不安装 RDKit，也可以启动 MVP，但报告会标注 `basic_syntax_only`，这不等于真实化学结构验证。

## Windows 本地启动

在解压后的 `molplatform-main` 目录中打开 PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

若 PowerShell 阻止虚拟环境脚本，可在当前窗口执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## macOS / Linux 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 手动测试

1. 打开 `http://127.0.0.1:8000/api/v1/agent/`。
2. 在输入框使用默认示例，点击“生成可视化报告”。
3. 等待状态变为“报告生成成功”，查看候选物表格、分子量/LogP 图表及数据质量说明。
4. 如需验证 Ro5 排除逻辑，先点击“添加 Ro5 反例（测试）”，再运行“筛选类药五原则通过的候选物”。报告应显示 Ro5 排除数量至少为 1，且 Ro5 分布图出现“未通过/未知”。
5. 点击“查看历史报告”确认报告已保存。
5. 点击“导出 JSON”下载机器可读的报告。
6. 打开 `http://127.0.0.1:8000/docs` 检查 API。

### 推荐提示词

- `分析平台中的分子，筛选类药五原则通过的候选物，展示 Top 5 并生成报告。`
- `分析分子库，筛选分子量小于 350 的分子，展示前 8 个。`
- `筛选 LogP 小于 3 的候选物，生成可视化报告。`
- `请执行 COX-2 分子对接并给出结合能排名。`（预期行为：提示 MVP 尚未接入真实对接工具，不会伪造结果）

### API 快速测试

创建任务：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt":"筛选类药五原则通过的分子，展示 Top 5 并生成报告","top_n":5}'
```

查询任务（将 `task_xxx` 替换成返回的 task_id）：

```bash
curl http://127.0.0.1:8000/api/v1/agent/tasks/task_xxx
```

报告列表：

```bash
curl http://127.0.0.1:8000/api/v1/agent/reports
```

## 数据库和测试数据

首次启动时会初始化 SQLite 数据库并写入原项目自带的 10 条演示分子数据。数据库路径为 `app/data/molplatform.db`。不要把真实用户数据直接放进演示环境。

## 新增代码位置

- `app/agent/planner.py`：自然语言规则解析与计划生成。
- `app/agent/service.py`：任务存储、后台执行、筛选、排名与报告 JSON 生成。
- `app/agent/schemas.py`：任务 Schema。
- `app/api/agent.py`：任务、报告、网页和导出接口。
- `app/api/templates/agent_report.html`：自包含的可视化报告页面，显示 Ro5 分布和筛选阶段计数。
- `POST /api/v1/agent/dev-fixtures/ro5-violator`：仅供本地开发测试，幂等插入一个 Ro5 未通过的反例分子。
- `integrations/molclaw_skills/`：精选 MolClaw 技能说明参考文件；尚未调用远程 MCP 工具。

## 自动化测试

```bash
python tests/test_api.py
python tests/test_agent_mvp.py
```

MVP 测试不需要 Anthropic/OpenAI API Key，也不需要外部 MolClaw MCP 服务。
