# MolPlatform

基于 FastAPI 的分子工作流平台：RDKit 性质分析、AutoDock Vina 对接、GROMACS MD、可选 Tamarind 云端计算，以及网页工作台。

## 1. 环境要求

- Linux / WSL（推荐）或 Windows
- Python 3.11 / 3.12
- Docker Compose（PostgreSQL + Redis）
- RDKit、AutoDock Vina、GROMACS
- 使用 SMILES 对接时需要 Open Babel
- 可选：NVIDIA GPU + GROMACS GPU build

## 2. 安装

```bash
git clone <repository-url>
cd molplatform
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

- `POSTGRES_PASSWORD` 与 `DATABASE_URL` 中的密码保持一致；
- `API_TOKEN` 设置为足够长的随机字符串；
- 如果 Vina / Open Babel / GROMACS 不在 PATH，设置 `VINA_BINARY`、`OBABEL_BINARY`、`GROMACS_BINARY`；
- 使用 Tamarind 时填写 `TAMARIND_API_KEY`，密钥只保存在服务端。

不要提交 `.env`。

## 3. 启动

```bash
./scripts/start_infra.sh   # 启动 PostgreSQL 和 Redis
./scripts/toolcheck.sh     # 检查 RDKit / Vina / GROMACS / Redis / Postgres
```

分别启动 API 和 worker：

```bash
./scripts/run_api.sh        # API
./scripts/run_worker.sh     # CPU / RDKit / Vina worker
./scripts/run_md_worker.sh  # GPU / GROMACS worker
```

访问 `http://localhost:8000/workbench`，API 文档位于 `http://localhost:8000/docs`。远程部署时将 `localhost` 替换为服务器地址。

## 4. 使用流程

### 4.1 网页工作台

1. 打开 `/workbench`，输入 `.env` 中的 `API_TOKEN`。
2. 上传分子库（CSV / SMI / TXT / SDF）或结构文件（PDBQT / TPR / ZIP）。
3. 输入自然语言需求，例如：
   - `计算分子库的 RDKit 性质，筛选 Ro5 通过且分子量小于 500 的候选物。`
   - `使用给定的受体和对接盒运行 Vina 对接，输出排名。`
   - `运行准备好的 GROMACS TPR，并归档轨迹和日志。`
4. 提交后查看任务状态、步骤日志、JSON 报告、CSV 结果和 ZIP 工件。

### 4.2 API

```bash
# 上传文件
curl -X POST http://localhost:8000/api/v2/assets \
  -H "X-API-Token: $API_TOKEN" \
  -F "file=@examples/mini_library.csv"

# 提交任务
curl -X POST http://localhost:8000/api/v2/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Token: $API_TOKEN" \
  -d '{"prompt":"计算 RDKit 性质并筛选 Ro5 通过的分子","asset_ids":["<asset-id>"]}'

# 查询任务、报告和工件
curl -H "X-API-Token: $API_TOKEN" http://localhost:8000/api/v2/tasks/<task-id>
curl -H "X-API-Token: $API_TOKEN" http://localhost:8000/api/v2/tasks/<task-id>/report
curl -H "X-API-Token: $API_TOKEN" http://localhost:8000/api/v2/tasks/<task-id>/artifacts
```

### 4.3 输入格式

- 性质分析：CSV 至少包含 `smiles` 列，可选 `name`；也支持 SMI / TXT / SDF。
- Vina 对接：预处理后的受体 `.pdbqt`；配体使用 CSV / SMI（SMILES）或预制 `.pdbqt`；必须提供 `center_x/y/z` 和 box 尺寸。
- GROMACS MD：上传已准备好的 `.tpr`，或包含拓扑、坐标、`.mdp` 和 `protocol.json` 的 ZIP。`protocol.json` 示例见 `examples/protocol.json.example`。

### 4.4 Tamarind 云端计算（可选）

1. 在 `.env` 中设置 `TAMARIND_API_BASE` 和 `TAMARIND_API_KEY`。
2. 打开 `/api/v4/tamarind/console`。
3. 依次执行：检查连接 → 刷新工具目录 → 选择工具 → 读取 Schema → 填写参数 → 仅验证 → 确认后提交 → 查询状态 → 下载结果。
4. 未配置 API Key 时不影响本地功能；提交真实云端任务可能产生费用。

## 5. 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 6. 注意事项

- `/api/v2` 仅在外部命令成功且生成结果文件时报告成功。
- `/api/v1` 中的 docking、MD、pipeline 包含演示实现，不应作为真实科研结果引用。
- 多 worker 部署时，所有节点必须挂载相同的 `MOLPLATFORM_DATA_DIR`。
- 公网部署请使用 HTTPS、强 `API_TOKEN`、防火墙和访问控制，不要直接暴露开发端口。
- 不要提交 `.env`、数据库、轨迹、结构文件或其他运行产物。
