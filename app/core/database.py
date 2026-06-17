"""
数据库模型 — SQLite（生产环境替换为 PostgreSQL）
涵盖：分子、对接结果、MD 任务、分析报告
"""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "molplatform.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
    -- 分子库（支持 SDF/SMILES 非结构化数据转结构化）
    CREATE TABLE IF NOT EXISTS molecules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        smiles      TEXT NOT NULL,
        mol_weight  REAL,
        logp        REAL,
        hbd         INTEGER,  -- 氢键供体
        hba         INTEGER,  -- 氢键受体
        tpsa        REAL,     -- 拓扑极性面积
        ro5_pass    INTEGER,  -- 类药五原则
        cluster_id  INTEGER,
        source      TEXT DEFAULT 'manual',
        created_at  TEXT DEFAULT (datetime('now'))
    );

    -- 对接任务
    CREATE TABLE IF NOT EXISTS docking_jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        job_name        TEXT NOT NULL,
        receptor_pdb    TEXT NOT NULL,
        ligand_ids      TEXT NOT NULL,   -- JSON array
        center_x        REAL,
        center_y        REAL,
        center_z        REAL,
        box_size        INTEGER DEFAULT 60,
        exhaustiveness  INTEGER DEFAULT 32,
        status          TEXT DEFAULT 'pending',  -- pending/running/done/failed
        created_at      TEXT DEFAULT (datetime('now')),
        finished_at     TEXT
    );

    -- 对接结果
    CREATE TABLE IF NOT EXISTS docking_results (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id          INTEGER REFERENCES docking_jobs(id),
        molecule_id     INTEGER REFERENCES molecules(id),
        binding_energy  REAL,    -- kcal/mol
        rmsd_lb         REAL,
        rmsd_ub         REAL,
        rank            INTEGER,
        pose_pdbqt      TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    );

    -- MD 模拟任务
    CREATE TABLE IF NOT EXISTS md_jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        job_name        TEXT NOT NULL,
        receptor_pdb    TEXT NOT NULL,
        ligand_id       INTEGER REFERENCES molecules(id),
        forcefield      TEXT DEFAULT 'AMBER99SB-ILDN',
        water_model     TEXT DEFAULT 'TIP3P',
        sim_time_ns     REAL DEFAULT 100,
        timestep_fs     REAL DEFAULT 2.0,
        temperature_k   INTEGER DEFAULT 300,
        pressure_bar    REAL DEFAULT 1.0,
        use_gpu         INTEGER DEFAULT 1,
        status          TEXT DEFAULT 'pending',
        progress        REAL DEFAULT 0.0,
        rmsd_final      REAL,
        rg_final        REAL,
        fes_data        TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        finished_at     TEXT
    );

    -- MD 分析结果（RMSD/RMSF/氢键）
    CREATE TABLE IF NOT EXISTS md_results (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id      INTEGER REFERENCES md_jobs(id),
        metric      TEXT NOT NULL,   -- rmsd/rmsf/hbond/mm_pbsa
        time_ns     REAL,
        value       REAL,
        extra       TEXT             -- JSON
    );

    -- AI 分析报告
    CREATE TABLE IF NOT EXISTS ai_reports (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        report_type     TEXT NOT NULL,  -- docking_summary/md_analysis/lead_opt
        source_ids      TEXT,           -- JSON
        model_used      TEXT DEFAULT 'claude-sonnet-4-6',
        prompt_tokens   INTEGER,
        content         TEXT NOT NULL,
        created_at      TEXT DEFAULT (datetime('now'))
    );

    -- 全链路管线任务
    CREATE TABLE IF NOT EXISTS pipeline_jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        pipeline_name   TEXT NOT NULL,
        config          TEXT NOT NULL,  -- JSON
        status          TEXT DEFAULT 'pending',
        current_stage   TEXT,
        stages_done     TEXT DEFAULT '[]',
        docking_job_id  INTEGER,
        md_job_id       INTEGER,
        ai_report_id    INTEGER,
        created_at      TEXT DEFAULT (datetime('now')),
        finished_at     TEXT
    );
    """)

    # ─── Schema 迁移：为已存在的表添加新列（幂等，兼容旧数据库） ───
    def _add_column_if_missing(table_name: str, col_def: str):
        try:
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # 列已存在

    _add_column_if_missing("md_jobs", "rg_final REAL")
    _add_column_if_missing("md_jobs", "fes_data TEXT")

    conn.commit()
    conn.close()


# ─── 测试数据 ───────────────────────────────────────────────────────────────

SEED_MOLECULES = [
    # COX-2 抑制剂 (NSAIDs)  — 对应 PPT 中的 COX-2/1CX2 案例
    {
        "name": "Celecoxib",
        "smiles": "CC1=CC=C(C=C1)C1=CC(=NN1C1=CC=C(C=C1)S(N)(=O)=O)C(F)(F)F",
        "mol_weight": 381.37, "logp": 3.50, "hbd": 1, "hba": 4, "tpsa": 100.0, "ro5_pass": 1, "cluster_id": 1
    },
    {
        "name": "Ibuprofen",
        "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(O)=O",
        "mol_weight": 206.28, "logp": 3.97, "hbd": 1, "hba": 1, "tpsa": 37.3, "ro5_pass": 1, "cluster_id": 2
    },
    {
        "name": "Naproxen",
        "smiles": "CC(C1=CC2=CC=CC=C2C=C1)C(O)=O",
        "mol_weight": 230.26, "logp": 3.18, "hbd": 1, "hba": 2, "tpsa": 46.5, "ro5_pass": 1, "cluster_id": 2
    },
    {
        "name": "Aspirin",
        "smiles": "CC(=O)OC1=CC=CC=C1C(O)=O",
        "mol_weight": 180.16, "logp": 1.19, "hbd": 1, "hba": 3, "tpsa": 63.6, "ro5_pass": 1, "cluster_id": 3
    },
    {
        "name": "Diclofenac",
        "smiles": "OC(=O)CC1=CC=CC=C1NC1=C(Cl)C=CC=C1Cl",
        "mol_weight": 296.15, "logp": 4.51, "hbd": 2, "hba": 2, "tpsa": 49.3, "ro5_pass": 1, "cluster_id": 1
    },
    {
        "name": "Indomethacin",
        "smiles": "COC1=CC2=CC(CC(O)=O)=C(N2C(=O)C2=CC=C(Cl)C=C2)C=C1",
        "mol_weight": 357.79, "logp": 4.27, "hbd": 1, "hba": 4, "tpsa": 68.5, "ro5_pass": 1, "cluster_id": 1
    },
    # DJ-1 共价抑制剂候选 — PPT 中的 DJ-1 项目
    {
        "name": "DJ1-Cov-001",
        "smiles": "O=C(NC1=CC=C(F)C=C1)C1=CC=C(NC(=O)/C=C/C(=O)O)C=C1",
        "mol_weight": 356.33, "logp": 2.10, "hbd": 3, "hba": 5, "tpsa": 95.2, "ro5_pass": 1, "cluster_id": 4
    },
    {
        "name": "DJ1-Cov-002",
        "smiles": "O=C(/C=C/CN1CCCC1)NC1=CC=C(OC)C(OC)=C1",
        "mol_weight": 302.37, "logp": 1.85, "hbd": 1, "hba": 5, "tpsa": 61.4, "ro5_pass": 1, "cluster_id": 4
    },
    # 激酶选择性优化候选
    {
        "name": "Kinase-Lead-A",
        "smiles": "CC1=CN=CN1CC(=O)NC1=CC(=C(C=C1)OC)OC",
        "mol_weight": 290.32, "logp": 1.60, "hbd": 2, "hba": 6, "tpsa": 79.1, "ro5_pass": 1, "cluster_id": 5
    },
    {
        "name": "Kinase-Lead-B",
        "smiles": "O=C(NC1=CC=CC=C1F)C1=CN=C(N)N=C1",
        "mol_weight": 232.23, "logp": 1.22, "hbd": 3, "hba": 5, "tpsa": 92.4, "ro5_pass": 1, "cluster_id": 5
    },
]

SEED_DOCKING_JOB = {
    "job_name": "COX-2 NSAIDs 虚拟筛选（1CX2）",
    "receptor_pdb": "1CX2",
    "ligand_ids": json.dumps(list(range(1, 7))),
    "center_x": 22.5, "center_y": 5.8, "center_z": 18.3,
    "box_size": 60, "exhaustiveness": 32,
    "status": "done",
    "finished_at": "2026-06-10 14:30:00",
}

SEED_DOCKING_RESULTS = [
    # binding_energy (kcal/mol)，数据基于文献典型值
    {"molecule_id": 1, "rank": 1, "binding_energy": -9.2, "rmsd_lb": 0.0, "rmsd_ub": 1.8},
    {"molecule_id": 5, "rank": 2, "binding_energy": -8.7, "rmsd_lb": 0.0, "rmsd_ub": 2.1},
    {"molecule_id": 6, "rank": 3, "binding_energy": -8.4, "rmsd_lb": 0.0, "rmsd_ub": 1.9},
    {"molecule_id": 2, "rank": 4, "binding_energy": -7.8, "rmsd_lb": 0.0, "rmsd_ub": 2.4},
    {"molecule_id": 3, "rank": 5, "binding_energy": -7.5, "rmsd_lb": 0.0, "rmsd_ub": 2.2},
    {"molecule_id": 4, "rank": 6, "binding_energy": -6.2, "rmsd_lb": 0.0, "rmsd_ub": 3.1},
]

SEED_MD_JOB = {
    "job_name": "Celecoxib-COX2 100ns NPT",
    "receptor_pdb": "1CX2",
    "ligand_id": 1,
    "forcefield": "AMBER99SB-ILDN",
    "water_model": "TIP3P",
    "sim_time_ns": 100,
    "timestep_fs": 2.0,
    "temperature_k": 300,
    "pressure_bar": 1.0,
    "use_gpu": 1,
    "status": "done",
    "rmsd_final": 2.31,
    "progress": 100.0,
    "finished_at": "2026-06-12 09:45:00",
}


def seed_db():
    conn = get_db()
    cur = conn.cursor()

    # 检查是否已有数据
    count = cur.execute("SELECT COUNT(*) FROM molecules").fetchone()[0]
    if count > 0:
        conn.close()
        return

    # 插入分子
    for mol in SEED_MOLECULES:
        cur.execute("""
            INSERT INTO molecules (name, smiles, mol_weight, logp, hbd, hba, tpsa, ro5_pass, cluster_id, source)
            VALUES (:name,:smiles,:mol_weight,:logp,:hbd,:hba,:tpsa,:ro5_pass,:cluster_id,'seed')
        """, mol)

    # 对接任务
    cur.execute("""
        INSERT INTO docking_jobs (job_name,receptor_pdb,ligand_ids,center_x,center_y,center_z,
                                   box_size,exhaustiveness,status,finished_at)
        VALUES (:job_name,:receptor_pdb,:ligand_ids,:center_x,:center_y,:center_z,
                :box_size,:exhaustiveness,:status,:finished_at)
    """, SEED_DOCKING_JOB)
    job_id = cur.lastrowid

    # 对接结果
    for res in SEED_DOCKING_RESULTS:
        cur.execute("""
            INSERT INTO docking_results (job_id,molecule_id,rank,binding_energy,rmsd_lb,rmsd_ub)
            VALUES (?,?,?,?,?,?)
        """, (job_id, res["molecule_id"], res["rank"],
              res["binding_energy"], res["rmsd_lb"], res["rmsd_ub"]))

    # MD 任务
    cur.execute("""
        INSERT INTO md_jobs (job_name,receptor_pdb,ligand_id,forcefield,water_model,
                              sim_time_ns,timestep_fs,temperature_k,pressure_bar,
                              use_gpu,status,rmsd_final,progress,finished_at)
        VALUES (:job_name,:receptor_pdb,:ligand_id,:forcefield,:water_model,
                :sim_time_ns,:timestep_fs,:temperature_k,:pressure_bar,
                :use_gpu,:status,:rmsd_final,:progress,:finished_at)
    """, SEED_MD_JOB)
    md_id = cur.lastrowid

    # MD RMSD 时间序列（100ns，每 1ns 一个点）
    import random, math
    random.seed(42)
    for t in range(0, 101, 2):
        rmsd = 0.5 + 1.8 * (1 - math.exp(-t / 20)) + random.gauss(0, 0.15)
        rmsd = max(0.3, rmsd)
        cur.execute(
            "INSERT INTO md_results (job_id,metric,time_ns,value) VALUES (?,?,?,?)",
            (md_id, "rmsd", t, round(rmsd, 3))
        )

    conn.commit()
    conn.close()
    print("[DB] 测试数据初始化完成")
