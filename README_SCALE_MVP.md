# MolPlatform Scale MVP — 服务器端自然语言科研工作流

本版本以 `molplatform-v5.2` 为基础，保留原有 `/api/v1` 演示 API，新增一个应当用于真实工具实验的 `/api/v2` 工作台。它将自然语言请求解析为白名单工作流，上传的分子文件分块后由 Celery worker 执行，计算结果、报告、日志和结构文件保存在服务端。笔记本只需通过浏览器访问服务地址。

> **科学性边界：** `/api/v2` 只在真实命令退出成功并产生对应结果文件时才报告成功。`/api/v1/docking`、`/api/v1/md` 和 `/api/v1/pipeline` 来自原型代码，其中部分服务是演示实现；不要把它们的输出当作文献级真实计算。该项目不会自动推导缺失的实验参数，也不会编造 ADMET 或结合能。
>
> **MoleCode：** `molecode`（MIT）只挂在 agent / AI 路径上，用来做 SMILES ↔ Mermaid 结构图转换，并用 RDKit 校验编辑结果。它不替换本文件描述的 Vina 对接、GROMACS MD 或性质筛选 worker。可选的 `molecode.llm` 复用 `.env` 中的 `LLM_*` 变量；结构转换本身不调用模型。

## 当前 MVP 功能

- 自然语言路由：分子性质筛选、AutoDock Vina 对接、GROMACS MD 运行；可选接 OpenAI-compatible LLM 做计划解析。没有 LLM 密钥时使用确定性解析器。
- RDKit：真实 SMILES 解析与 descriptor 计算，包括 MW、LogP、HBD/HBA、TPSA、可旋转键、QED、Ro5 违例；支持 CSV、SMI、SMILES、TXT、SDF 输入。
- Vina：调用实际 `vina` CLI；可输入含 `name,smiles` 列的 CSV/SMI（需要 `obabel` 生成 PDBQT），或上传多个预制配体 PDBQT。受体必须是已处理好的 PDBQT；中心坐标必须由用户明确提供，或来自已核实的文献/共晶配体位置。Box 的 X/Y/Z 尺寸可以分别设置（每轴大于 0 且不超过 100 Å），应按论文或已验证口袋参数填写。
- GROMACS GPU：调用实际 `gmx mdrun`，支持 `.tpr` 输入、GPU `-nb gpu` 选项、线程设置、真实 stdout/log/轨迹归档。不会自动准备蛋白、配体参数、溶剂盒、离子或 `.tpr`。
- 可视化网页、异步状态、任务/步骤日志、JSON 报告、CSV 结果和 ZIP 工件下载。
- PostgreSQL 持久化、Redis/Celery 队列、批次/chord 分块执行。未安装 Celery 时会回退到仅用于本地冒烟测试的双线程执行器，**不提供分布式并发**。
- MolClaw 的相关 L1/L2/L3 技能文档随包保留；运行时调用通过受控适配实现，不会把任意技能 Markdown 当成已安装的工具。

## 文件格式

### 分子性质分析
CSV 最少需要一个 `smiles` 列，可选 `name`、`compound_id`。SMI/TXT 默认每行 `SMILES [name]`；SDF 需要 RDKit。示例：`examples/mini_library.csv`。示例含无效 SMILES 和一个 Ro5 反例，用于确认失败/过滤统计。

### Vina 对接
- 受体：预处理后的 `.pdbqt`；
- 配体方式 A：CSV/SMI 的 SMILES + Open Babel（`obabel`）可执行文件；
- 配体方式 B：每个配体一个已准备好的 `.pdbqt`，网页多文件上传；
- 需要明确的 `center_x/y/z`（Å）；
- 可设置 exhaustiveness、mode 数、CPU、box size。Box 的 X/Y/Z 尺寸逐轴传给 Vina，允许论文中小于 25 Å 的 box。中心坐标可以包含 0，但必须以文献、共晶配体或经过验证的口袋为依据。

输出包含每个配体的实际运行状态、Vina affinity（`kcal/mol`）、pose PDBQT、日志以及合并的 `all_results.csv`。分数仅代表 Vina 的经验打分，不是实验结合自由能或临床活性。

### GROMACS MD
上传按文献协议预先准备的 `.tpr` 文件。worker 调用近似命令：

```bash
gmx mdrun -s input.tpr -deffnm /shared/data/jobs/<task-id>/gromacs/md \
  -ntmpi 1 -ntomp 8 -pin on -nb gpu
```

命令由 `GROMACS_BINARY`、任务参数和上传路径安全组装，不接受用户提供 shell 字符串。输出 `.xtc/.trr/.edr/.gro/.log/.cpt` 等会归档。若文献方法需要先做蛋白修复、质子化、配体参数化、`grompp`、平衡与 NPT/NVT 阶段，这些阶段必须在上传 `.tpr` 前完成；以后可继续将它们加成单独受控工具。

## 运行方式（推荐：服务器/WSL 原生安装科学工具）

需要 Python 3.11 或 3.12、Docker Compose、RDKit、Vina、GROMACS（GPU build 与 NVIDIA 驱动按需）、如果使用 SMILES 对接则需要 Open Babel。API/worker 都以原生 Linux/WSL 进程运行，避免 Docker 容器找不到主机上已装的 Vina/GROMACS/GPU 库。

### 1. 环境准备

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：必须把 `POSTGRES_PASSWORD` 与 `DATABASE_URL` 中的密码改成同一个随机值；生成长随机 `API_TOKEN`。若 Vina/GROMACS 不在 PATH，设为 WSL/Linux 内能执行的绝对路径。不要填 Windows `C:\...` 形式的路径；WSL 应使用 `/mnt/c/...`（更推荐把工作目录放在 Linux 文件系统）。

### 2. 启动基础设施

```bash
./scripts/start_infra.sh
```

该脚本启动 PostgreSQL 和 Redis，端口只绑定本机回环地址。若 Docker 不能运行，也可以自行提供兼容的 PostgreSQL/Redis 服务并修改 `.env`。

### 3. 工具检查

```bash
./scripts/toolcheck.sh
```

检查 RDKit 导入、Vina/Open Babel/GROMACS PATH、GPU `nvidia-smi`、Redis、Postgres。**这是 API 主机的检查；若使用独立 worker，还要在每台实际计算 worker 上执行同样的检查。**

### 4. 分别启动 API 和 worker

终端 A：

```bash
source .venv/bin/activate
./scripts/run_api.sh
```

终端 B（CPU/RDKit/Vina）：

```bash
source .venv/bin/activate
./scripts/run_worker.sh
```

终端 C（GPU MD；建议一个 GPU 一个并发 worker）：

```bash
source .venv/bin/activate
./scripts/run_md_worker.sh
```

浏览器打开 `http://<服务器IP>:8000/workbench` 或 `http://<服务器IP>:8000/api/v2/`。API 文档：`http://<服务器IP>:8000/docs`。在网页输入 `.env` 中的 `API_TOKEN`。同一台 WSL 电脑上可用 `http://127.0.0.1:8000/workbench`。

## API 速览

- `GET /api/v2/system/capabilities`：工具可用性检查；
- `POST /api/v2/assets`：上传文件，返回 asset ID；
- `POST /api/v2/tasks`：自然语言提交；
- `GET /api/v2/tasks`、`GET /api/v2/tasks/{id}`：任务/日志；
- `GET /api/v2/tasks/{id}/report`：结构化 JSON 报告；
- `GET /api/v2/tasks/{id}/artifacts`：工件列表；
- `GET /api/v2/tasks/{id}/artifacts/<path>`：下载输出；
- `GET /api/v2/examples/molecules.csv`：示例库。

API 上传和任务提交使用 `Authorization: Bearer <API_TOKEN>` 或 `X-API-Token`。`API_TOKEN` 是共享服务访问令牌，不是多租户用户系统；真实公网部署请使用 HTTPS 反向代理、强 token、网络 ACL/防火墙和备份策略，不能直接将开发端口暴露互联网。

## 分布式/百万量级设计与限制

- 分子库先流式解析，然后按 `CHUNK_SIZE` 分块（默认 5000 个分子/块），经 Celery group/chord 分发；返回结果是文件路径和计数，避免把全部结构塞进 Redis result backend。
- 多 worker 节点必须对同一个 `MOLPLATFORM_DATA_DIR` 使用相同路径挂载共享文件系统（NFS/并行文件系统）。本包尚未接 MinIO/S3；跨节点不共享目录时，分块路径/产物访问会失败。
- PostgreSQL 保存任务元数据和事件，Redis 负责队列。大输出保存在文件系统中，而非数据库字段。
- 调整 `CHUNK_SIZE`、CPU worker 数、Vina `--cpu`、GPU worker 并发和资源队列时要按实际节点压测。单个 GROMACS 长模拟不是按分子块并行拆分，GPU worker 默认并发 1；大量独立模拟应提交成独立任务。
- 本项目不是“百万并发用户”已经验证过的互联网服务；百万级**输入分子/计算记录**是以分块/队列/共享存储为目标的架构设计。吞吐和并发上限必须在你的真实服务器、GPU、网络和文献协议下测试后才能承诺。
- 该 MVP 暂不提供自动蛋白质准备、口袋自动检测、参数化、ADMET 预测器、SLURM 调度、S3/MinIO、多租户 RBAC、任务级配额或高可用 Redis；它不应被描述为全自动药物发现系统。

## 文献实验的建议测试顺序

1. 先跑 `examples/mini_library.csv`，确认 RDKit 报告的有效数、无效数、Ro5 排除数和 CSV 结果；
2. 用文献公开、许可允许的受体 PDBQT + 一个已知配体 PDBQT 测 Vina，先核对 center/box、工具日志与 pose；
3. 用已按文献协议生成的短测试 `.tpr` 验证 GPU build，再逐步延长模拟；
4. 将报告结果与原论文的系统、力场、构象、质子化、对接盒和模拟参数逐项核对。仅运行出文件不代表复现文献成功。

## 自动化测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

测试覆盖自然语言规划、分块读取、RDKit 描述符、Ro5 排除、报告生成、GROMACS ZIP 阶段解析与 ZIP 路径安全检查。Vina/GROMACS 真实运行测试需要你环境中的合法受体/配体文件、`.tpr` 与二进制，因此不会用 mock 分数代替真实集成测试。
