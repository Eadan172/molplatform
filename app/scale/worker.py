"""Distributed task execution. Uses Celery/Redis when installed; local threads are for smoke tests only."""
import csv, hashlib, json, os, re, shutil, subprocess, time, zipfile, heapq
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from sqlalchemy import select, update, case, cast, Integer
from app.scale.config import (DATA_DIR, JOBS_DIR, CHUNK_SIZE, MAX_WORKERS_PER_NODE, DOCKING_CHUNK_SIZE,
    VINA_BINARY, OBABEL_BINARY, GROMACS_BINARY, GROMACS_MDRUN_THREADS, COMMAND_TIMEOUT_SECONDS)
from app.scale.database import SessionLocal, init_scale_db
from app.scale.models import Asset, ScaleJob, JobEvent
from app.scale.molecule_io import split_molecule_file

try:
    from celery import Celery, group, chord
    HAVE_CELERY = True
except ImportError:
    Celery = None; group = chord = None; HAVE_CELERY = False

celery_app = None
if HAVE_CELERY:
    from app.scale.config import REDIS_URL
    celery_app = Celery("molplatform_scale", broker=REDIS_URL, backend=REDIS_URL)
    celery_app.conf.update(
        task_acks_late=True, worker_prefetch_multiplier=1, task_default_queue="control",
        task_track_started=True, result_expires=86400,
        task_routes={
            "app.scale.worker.start_job_task": {"queue": "control"},
            "app.scale.worker.process_properties_chunk_task": {"queue": "chem"},
            "app.scale.worker.process_vina_chunk_task": {"queue": "docking"},
            "app.scale.worker.finalize_job_task": {"queue": "control"},
            "app.scale.worker.run_md_job_task": {"queue": "md"},
        },
    )

_LOCAL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="molplatform-dev")

def now(): return datetime.now(timezone.utc)

def _event(db, job_id: str, message: str, level: str = "info", details: dict | None = None):
    db.add(JobEvent(job_id=job_id, level=level, message=message, details=details or {}))

def _get_job(job_id: str) -> ScaleJob:
    init_scale_db()
    with SessionLocal() as db:
        job = db.get(ScaleJob, job_id)
        if not job: raise ValueError(f"任务不存在：{job_id}")
        # Detach a copy to avoid returning a live ORM object after closing the session.
        return job

def _asset_path(asset_id: str | None) -> Path | None:
    if not asset_id: return None
    with SessionLocal() as db:
        item = db.get(Asset, asset_id)
        if not item: raise ValueError(f"文件资源不存在：{asset_id}")
        p = Path(item.path).resolve()
        if not p.is_file(): raise FileNotFoundError(f"上传文件已不存在：{item.filename}")
        return p

def _update(job_id: str, **fields):
    init_scale_db()
    with SessionLocal() as db:
        j = db.get(ScaleJob, job_id)
        if not j: return
        for k, v in fields.items(): setattr(j, k, v)
        j.updated_at = now()
        db.commit()

def _set_status(job_id: str, status: str, message: str, progress: int | None = None, level="info", details=None):
    init_scale_db()
    with SessionLocal() as db:
        j = db.get(ScaleJob, job_id)
        if not j: return
        j.status = status; j.message = message; j.updated_at = now()
        if progress is not None: j.progress = progress
        db.add(JobEvent(job_id=job_id, level=level, message=message, details=details or {}))
        db.commit()

def _advance(job_id: str, amount: int):
    """Atomically record completed molecules from concurrent chunk workers."""
    if amount <= 0: return
    init_scale_db()
    with SessionLocal() as db:
        next_count = ScaleJob.count_done + int(amount)
        total_expr = case((ScaleJob.count_total > 0, ScaleJob.count_total), else_=1)
        raw_progress = cast(5 + (next_count * 90 / total_expr), Integer)
        progress_expr = case((ScaleJob.count_total > 0, case((raw_progress > 95, 95), else_=raw_progress)), else_=5)
        db.execute(update(ScaleJob).where(ScaleJob.id == job_id).values(
            count_done=next_count, progress=progress_expr, message="分块计算进行中", updated_at=now()))
        db.commit()

def _workdir(job_id: str) -> Path:
    p = (JOBS_DIR / job_id).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def _asset_info(asset_id: str) -> dict:
    with SessionLocal() as db:
        a = db.get(Asset, asset_id)
        if not a: raise ValueError(f"文件资源不存在：{asset_id}")
        return {"id": a.id, "filename": a.filename, "file_type": a.file_type, "path": a.path, "size_bytes": a.size_bytes}

def _make_prepared_ligand_chunks(asset_ids: list[str], out_dir: Path, chunk_size: int) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for aid in asset_ids:
        info = _asset_info(aid)
        if Path(info["filename"]).suffix.lower() != ".pdbqt":
            raise ValueError(f"预制配体只能上传 PDBQT：{info['filename']}")
        rows.append({"name": Path(info["filename"]).stem, "smiles": "", "pdbqt_path": str(Path(info["path"]).resolve())})
    paths = []
    for i in range(0, len(rows), chunk_size):
        p = out_dir / f"ligands_{i // chunk_size:05d}.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["name", "smiles", "pdbqt_path"]); w.writeheader(); w.writerows(rows[i:i+chunk_size])
        paths.append(str(p))
    if not paths: raise ValueError("没有可用的 PDBQT 配体文件。")
    return paths

def _ensure_source_chunks(job: ScaleJob) -> list[str]:
    opts = dict(job.options or {})
    workdir = _workdir(job.id)
    if job.operation == "docking" and opts.get("ligand_asset_ids"):
        return _make_prepared_ligand_chunks(opts["ligand_asset_ids"], workdir / "chunks", DOCKING_CHUNK_SIZE)
    aid = opts.get("molecule_asset_id")
    if not aid: raise ValueError("缺少分子库文件。请上传包含 SMILES 的 CSV/SMI/SDF。")
    source = _asset_path(aid)
    chunk_size = DOCKING_CHUNK_SIZE if job.operation == "docking" else CHUNK_SIZE
    return split_molecule_file(source, workdir / "chunks", chunk_size)

def enqueue_job(job_id: str):
    if HAVE_CELERY:
        start_job_task.delay(job_id)
    else:
        # Development fallback only; deployment scripts install Celery and Redis.
        _LOCAL_EXECUTOR.submit(start_job_impl, job_id)

def start_job_impl(job_id: str):
    try:
        job = _get_job(job_id)
        if job.status in {"cancelled", "needs_inputs", "needs_tool"}: return
        _set_status(job_id, "running", "正在准备任务与分块输入", 2)
        if job.operation == "md":
            if HAVE_CELERY:
                run_md_job_task.apply_async(args=[job_id], queue="md")
            else:
                run_md_impl(job_id)
            return
        chunks = _ensure_source_chunks(job)
        _update(job_id, count_total=sum(_count_data_rows(p) for p in chunks))
        _set_status(job_id, "running", f"已切分为 {len(chunks)} 个计算分块，等待 worker 执行", 5,
                    details={"chunk_count": len(chunks), "chunk_size": CHUNK_SIZE})
        if HAVE_CELERY:
            if job.operation == "docking":
                header = group(process_vina_chunk_task.s(job_id, p) for p in chunks)
            else:
                header = group(process_properties_chunk_task.s(job_id, p) for p in chunks)
            chord(header)(finalize_job_task.s(job_id))
        else:
            results = []
            fn = process_vina_chunk_impl if job.operation == "docking" else process_properties_chunk_impl
            for p in chunks: results.append(fn(job_id, p))
            finalize_job_impl(results, job_id)
    except Exception as exc:
        _set_status(job_id, "failed", "任务初始化失败", 100, "error", {"error": str(exc)})
        _update(job_id, error=str(exc))

def _count_data_rows(path: str) -> int:
    try:
        with open(path, encoding="utf-8", newline="") as f: return max(0, sum(1 for _ in f)-1)
    except OSError: return 0

def _rdkit_mol(smiles: str):
    from rdkit import Chem
    return Chem.MolFromSmiles(smiles) if smiles else None

def process_properties_chunk_impl(job_id: str, chunk_path: str) -> dict:
    import csv
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors, QED
    job = _get_job(job_id); opts = dict(job.options or {}); filters = opts.get("filters", {})
    out_dir = _workdir(job_id) / "chunks_out"; out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (Path(chunk_path).stem + "_results.csv")
    columns = ["name","smiles","valid","error","molecular_weight","logp","hbd","hba","tpsa","rotatable_bonds","qed","ro5_violations","ro5_pass","filtered_in","heuristic_score"]
    input_count = valid_count = invalid_count = filtered_count = ro5_pass_count = 0
    with open(chunk_path, encoding="utf-8", newline="") as inp, out_path.open("w", encoding="utf-8", newline="") as out:
        reader = csv.DictReader(inp); writer = csv.DictWriter(out, fieldnames=columns); writer.writeheader()
        for row in reader:
            input_count += 1; smi = (row.get("smiles") or "").strip(); name = row.get("name") or f"compound_{input_count}"
            result = {"name": name, "smiles": smi, "valid": False, "error": "", "molecular_weight": "", "logp": "", "hbd": "", "hba": "", "tpsa": "", "rotatable_bonds": "", "qed": "", "ro5_violations": "", "ro5_pass": False, "filtered_in": False, "heuristic_score": ""}
            try:
                mol = _rdkit_mol(smi)
                if mol is None: raise ValueError("RDKit 无法解析 SMILES")
                mw = float(Descriptors.MolWt(mol)); logp = float(Descriptors.MolLogP(mol))
                hbd = int(Lipinski.NumHDonors(mol)); hba = int(Lipinski.NumHAcceptors(mol)); tpsa = float(rdMolDescriptors.CalcTPSA(mol))
                rot = int(Lipinski.NumRotatableBonds(mol)); qed = float(QED.qed(mol))
                violations = int(mw > 500) + int(logp > 5) + int(hbd > 5) + int(hba > 10)
                ro5 = violations == 0
                filtered = (not filters.get("ro5_only") or ro5) and (filters.get("max_molecular_weight") is None or mw < float(filters["max_molecular_weight"])) and (filters.get("max_logp") is None or logp < float(filters["max_logp"])) and (filters.get("min_qed") is None or qed >= float(filters["min_qed"]))
                score = max(0.0, min(1.0, qed))
                result.update({"valid": True, "molecular_weight": round(mw, 4), "logp": round(logp, 4), "hbd": hbd, "hba": hba, "tpsa": round(tpsa, 4), "rotatable_bonds": rot, "qed": round(qed, 5), "ro5_violations": violations, "ro5_pass": ro5, "filtered_in": filtered, "heuristic_score": round(score, 5)})
                valid_count += 1; ro5_pass_count += int(ro5); filtered_count += int(filtered)
            except Exception as exc:
                result["error"] = str(exc); invalid_count += 1
            writer.writerow(result)
    _advance(job_id, input_count)
    return {"path": str(out_path), "input_count": input_count, "valid_count": valid_count, "invalid_count": invalid_count, "filtered_count": filtered_count, "ro5_pass_count": ro5_pass_count}

def _parse_vina_score(pose_path: Path, log_path: Path) -> float | None:
    pattern = re.compile(r"REMARK VINA RESULT:\s*(-?\d+(?:\.\d+)?)")
    for p in (pose_path, log_path):
        try:
            for line in p.read_text(errors="ignore").splitlines():
                m = pattern.search(line)
                if m: return float(m.group(1))
        except OSError: pass
    return None

def process_vina_chunk_impl(job_id: str, chunk_path: str) -> dict:
    job = _get_job(job_id); opts = dict(job.options or {})
    receptor = _asset_path(opts.get("receptor_asset_id"))
    if not receptor: raise ValueError("缺少受体 PDBQT 文件。")
    if shutil.which(VINA_BINARY) is None and not Path(VINA_BINARY).is_file():
        raise RuntimeError(f"找不到 Vina 可执行文件：{VINA_BINARY}。请设置 VINA_BINARY 的完整路径并确保 worker 可访问。")
    rows_out = []; out_dir = _workdir(job_id) / "docking" / Path(chunk_path).stem; out_dir.mkdir(parents=True, exist_ok=True)
    with open(chunk_path, encoding="utf-8", newline="") as f:
        for idx, row in enumerate(csv.DictReader(f), start=1):
            name = row.get("name") or f"ligand_{idx}"; smi = (row.get("smiles") or "").strip()
            ligand = Path((row.get("pdbqt_path") or "").strip()) if row.get("pdbqt_path") else None
            pose_path = out_dir / f"ligand_{idx:06d}_pose.pdbqt"; log_path = out_dir / f"ligand_{idx:06d}.log"
            try:
                if ligand is None:
                    if not smi: raise ValueError("配体没有 SMILES 或预制 PDBQT 路径")
                    obabel = shutil.which(OBABEL_BINARY) or (OBABEL_BINARY if Path(OBABEL_BINARY).is_file() else None)
                    if not obabel: raise RuntimeError(f"未找到 Open Babel ({OBABEL_BINARY})。请安装 Open Babel/Meeko 转换工具，或直接上传预制配体 PDBQT。")
                    ligand = out_dir / f"ligand_{idx:06d}.pdbqt"
                    conv = subprocess.run([str(obabel), f"-:{smi}", "-O", str(ligand), "--gen3d"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=min(600, int(opts.get("timeout_seconds", COMMAND_TIMEOUT_SECONDS))))
                    if conv.returncode != 0 or not ligand.exists() or ligand.stat().st_size == 0:
                        raise RuntimeError("Open Babel 配体转换失败：" + (conv.stdout or "")[-800:])
                center = opts.get("vina_center")
                if not center: raise ValueError("缺少 docking box 中心坐标。请在网页表单填写 center_x/y/z 或在提示词中写 center (x,y,z)。")
                fallback_box = float(opts.get("vina_box_size", 25.0))
                box_x = float(opts.get("vina_box_size_x", fallback_box))
                box_y = float(opts.get("vina_box_size_y", fallback_box))
                box_z = float(opts.get("vina_box_size_z", fallback_box))
                if not all(0 < v <= 100 for v in (box_x, box_y, box_z)):
                    raise ValueError("Vina box 每个轴的长度必须在 (0, 100] Å 内，请按文献参数设置。")
                vina = shutil.which(VINA_BINARY) or VINA_BINARY
                command = [str(vina), "--receptor", str(receptor), "--ligand", str(ligand),
                    "--center_x", str(center[0]), "--center_y", str(center[1]), "--center_z", str(center[2]),
                    "--size_x", str(box_x), "--size_y", str(box_y), "--size_z", str(box_z),
                    "--exhaustiveness", str(int(opts.get("vina_exhaustiveness", 8))), "--num_modes", str(int(opts.get("vina_modes", 9))),
                    "--cpu", str(int(opts.get("vina_cpu", 1))), "--out", str(pose_path), "--log", str(log_path)]
                completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                           timeout=int(opts.get("timeout_seconds", COMMAND_TIMEOUT_SECONDS)))
                (out_dir / f"ligand_{idx:06d}_stdout.txt").write_text(completed.stdout or "", encoding="utf-8")
                score = _parse_vina_score(pose_path, log_path)
                if completed.returncode != 0 or score is None:
                    raise RuntimeError(f"Vina 失败或没有可解析的真实结合能，returncode={completed.returncode}。日志：{(completed.stdout or '')[-800:]}")
                rows_out.append({"name": name, "smiles": smi, "status": "completed", "binding_energy_kcal_mol": score, "pose_file": str(pose_path), "error": ""})
            except Exception as exc:
                rows_out.append({"name": name, "smiles": smi, "status": "failed", "binding_energy_kcal_mol": "", "pose_file": "", "error": str(exc)})
    out_csv = out_dir / "results.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        cols = ["name","smiles","status","binding_energy_kcal_mol","pose_file","error"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows_out)
    successes = sum(r["status"] == "completed" for r in rows_out)
    _advance(job_id, len(rows_out))
    return {"path": str(out_csv), "input_count": len(rows_out), "success_count": successes, "failed_count": len(rows_out)-successes}

def finalize_job_impl(chunk_results: list[dict], job_id: str):
    job = _get_job(job_id); workdir = _workdir(job_id); op = job.operation
    fatal = [r.get("fatal_error") for r in chunk_results if r.get("fatal_error")]
    if fatal:
        report = {"schema_version":"2.0", "task_id":job_id, "operation":op, "title":"任务执行失败报告", "status":"failed",
                  "summary":{"text":"至少一个计算分块发生致命错误；没有把该分块标记为科学计算成功。"},
                  "errors":fatal, "provenance":{"created_at":now().isoformat(),"calculation_mode":"failed_before_valid_result"},
                  "warnings":["请检查 worker 环境、共享文件系统、RDKit/Vina 安装和分块日志后重试。"]}
        report_path = workdir / "report.json"; report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _set_status(job_id,"failed","计算分块发生致命错误",100,"error",{"errors":fatal[:20]})
        _update(job_id,error="; ".join(fatal[:10]),report_path=str(report_path))
        return
    try:
        if op == "properties":
            all_path = workdir / "all_results.csv"
            columns = ["name","smiles","valid","error","molecular_weight","logp","hbd","hba","tpsa","rotatable_bonds","qed","ro5_violations","ro5_pass","filtered_in","heuristic_score"]
            stats = {k: 0 for k in ("input_count","valid_count","invalid_count","filtered_count","ro5_pass_count")}
            top_heap = []
            top_limit = int(job.options.get("top_n", 100))
            tie_counter = 0
            with all_path.open("w", encoding="utf-8", newline="") as outf:
                writer = csv.DictWriter(outf, fieldnames=columns); writer.writeheader()
                for res in chunk_results:
                    for k in stats: stats[k] += int(res.get(k, 0))
                    with open(res["path"], encoding="utf-8", newline="") as inf:
                        for row in csv.DictReader(inf):
                            writer.writerow(row)
                            if row.get("filtered_in") == "True":
                                row["molecular_weight"] = _float_or_none(row.get("molecular_weight")); row["logp"] = _float_or_none(row.get("logp")); row["qed"] = _float_or_none(row.get("qed")); row["heuristic_score"] = _float_or_none(row.get("heuristic_score"))
                                tie_counter += 1
                                name = str(row.get("name") or "")
                                inv_name = "".join(chr(0x10FFFF - ord(c)) for c in name)
                                quality = (row.get("heuristic_score") or 0.0, -(row.get("molecular_weight") or 1e9), inv_name, tie_counter, row)
                                if len(top_heap) < top_limit:
                                    heapq.heappush(top_heap, quality)
                                elif quality[:4] > top_heap[0][:4]:
                                    heapq.heapreplace(top_heap, quality)
            preview = [item[4] for item in top_heap]
            preview.sort(key=lambda r: (-(r.get("heuristic_score") or 0), r.get("molecular_weight") or 1e9, r.get("name") or ""))
            top_n = top_limit
            report = {
                "schema_version": "2.0", "task_id": job_id, "operation": "properties", "title": "RDKit 分子性质与筛选报告",
                "status": "completed", "summary": {**stats, "ranked_count": len(preview), "text": f"输入 {stats['input_count']} 个；RDKit 有效 {stats['valid_count']} 个；无效 {stats['invalid_count']} 个；按条件保留 {stats['filtered_count']} 个；展示 Top {len(preview)}。"},
                "filters": job.options.get("filters", {}), "ranking_method": "RDKit QED 排序（仅作性质排序，不是结合能或临床成药性结论）",
                "charts": {"molecular_weight": [{"name": r["name"], "value": r["molecular_weight"]} for r in preview if r.get("molecular_weight") is not None],
                           "logp": [{"name": r["name"], "value": r["logp"]} for r in preview if r.get("logp") is not None],
                           "qed": [{"name": r["name"], "value": r["qed"]} for r in preview if r.get("qed") is not None],
                           "ro5_distribution": [{"label":"通过","value":stats["ro5_pass_count"]},{"label":"未通过/无效","value":stats["input_count"]-stats["ro5_pass_count"]}]},
                "candidates": preview, "artifacts": [{"name":"all_results.csv","url":f"/api/v2/tasks/{job_id}/artifacts/all_results.csv"}],
                "provenance": {"calculation_mode":"real_rdkit_descriptors", "tool":"RDKit", "version": _rdkit_version(), "created_at": now().isoformat(), "shared_filesystem_required_for_distributed_workers": True},
                "warnings": ["QED 和 RDKit 描述符是计算性质，不等同于药效、结合亲和力或临床成功率。", "Ro5 仅根据分子量、LogP、氢键供体/受体计算。"]}
            artifact_zip = _zip_artifacts(workdir, "artifacts.zip")
            report["artifacts"].append({"name":"artifacts.zip","url":f"/api/v2/tasks/{job_id}/artifacts/artifacts.zip"})
            _write_report(job_id, report, all_path, artifact_zip, "completed", f"分析完成：{stats['filtered_count']} 个分子满足筛选条件。")
        elif op == "docking":
            all_path = workdir / "all_results.csv"; columns = ["name","smiles","status","binding_energy_kcal_mol","pose_file","error"]
            total = success = failed = 0; scored_heap = []; tie_counter = 0
            top_limit = int(job.options.get("top_n", 100))
            with all_path.open("w", encoding="utf-8", newline="") as outf:
                w = csv.DictWriter(outf, fieldnames=columns); w.writeheader()
                for res in chunk_results:
                    with open(res["path"], encoding="utf-8", newline="") as inf:
                        for row in csv.DictReader(inf):
                            w.writerow(row); total += 1
                            if row["status"] == "completed":
                                success += 1; row["binding_energy_kcal_mol"] = float(row["binding_energy_kcal_mol"])
                                tie_counter += 1
                                inv_name = "".join(chr(0x10FFFF - ord(c)) for c in str(row.get("name") or ""))
                                quality = (-row["binding_energy_kcal_mol"], inv_name, tie_counter, row)
                                if len(scored_heap) < top_limit: heapq.heappush(scored_heap, quality)
                                elif quality[:3] > scored_heap[0][:3]: heapq.heapreplace(scored_heap, quality)
                            else: failed += 1
            scored = [x[3] for x in scored_heap]
            scored.sort(key=lambda r: (r["binding_energy_kcal_mol"], str(r.get("name") or "")))
            state = "completed" if total and failed == 0 else ("partial_success" if success else "failed")
            report = {"schema_version":"2.0", "task_id":job_id, "operation":"docking", "title":"AutoDock Vina 对接报告", "status":state,
              "summary":{"input_count":total,"completed_count":success,"failed_count":failed,"ranked_count":min(success,int(job.options.get("top_n",100))),"text":f"输入 {total} 个配体；Vina 成功 {success} 个，失败 {failed} 个。"},
              "ranking_method":"真实 AutoDock Vina 输出的 mode 1 binding affinity (kcal/mol)，数值越负代表 Vina 打分更优，不是实测亲和力。",
              "charts":{"binding_energy":[{"name":r["name"],"value":r["binding_energy_kcal_mol"]} for r in scored[:int(job.options.get("top_n",100))]]},
              "candidates":scored[:int(job.options.get("top_n",100))],
              "artifacts":[{"name":"all_results.csv","url":f"/api/v2/tasks/{job_id}/artifacts/all_results.csv"}],
              "provenance":{"calculation_mode":"real_vina_cli","vina_binary":VINA_BINARY,"receptor_asset_id":job.options.get("receptor_asset_id"),"vina_center":job.options.get("vina_center"),"vina_box_size_angstrom":{"x":float(job.options.get("vina_box_size_x",job.options.get("vina_box_size",25))),"y":float(job.options.get("vina_box_size_y",job.options.get("vina_box_size",25))),"z":float(job.options.get("vina_box_size_z",job.options.get("vina_box_size",25)))},"created_at":now().isoformat()},
              "warnings":["Vina affinity 是经验评分，不应当表述为实验结合自由能。","该 MVP 要求预处理好的受体 PDBQT；SMILES 转 PDBQT 需要 Open Babel。","报告中的成功数和排名来自实际命令返回和 pose 文件。"]}
            artifact_zip = _zip_artifacts(workdir, "artifacts.zip")
            report["artifacts"].append({"name":"artifacts.zip","url":f"/api/v2/tasks/{job_id}/artifacts/artifacts.zip"})
            _write_report(job_id, report, all_path, artifact_zip, state, f"对接任务结束：成功 {success}，失败 {failed}。", error=None if success else "所有 Vina 子任务均未成功，请检查报告日志。")
        else:
            raise ValueError(f"未知操作：{op}")
    except Exception as exc:
        _set_status(job_id, "failed", "报告汇总失败", 100, "error", {"error": str(exc)}); _update(job_id, error=str(exc))

def _float_or_none(v):
    try: return float(v) if v not in (None, "") else None
    except (TypeError, ValueError): return None

def _rdkit_version():
    try:
        import rdkit
        return rdkit.__version__
    except Exception: return "unavailable"

def _zip_artifacts(workdir: Path, zip_name: str) -> str:
    target = workdir / zip_name
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
        for p in workdir.rglob("*"):
            rel = p.relative_to(workdir)
            if p.is_file() and p != target and "chunks" not in rel.parts and "chunks_out" not in rel.parts and "input_bundle" not in rel.parts:
                try: zf.write(p, rel)
                except OSError: pass
    return str(target)

def _write_report(job_id, report, result_path, zip_path, status, message, error=None):
    report_path = _workdir(job_id) / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _update(job_id, status=status, progress=100, message=message, error=error, report_path=str(report_path), artifact_zip=zip_path)
    _set_status(job_id, status, message, 100, "error" if status == "failed" else "info", {"report_path": str(report_path), "result_path": str(result_path)})

def _safe_extract_md_bundle(zip_path: Path, target: Path) -> Path:
    """Extract a data-only GROMACS input bundle while preventing zip-slip/symlink attacks."""
    import stat
    if target.exists(): shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    max_unpacked = int(os.getenv("MAX_MD_BUNDLE_UNPACKED_GB", "10")) * 1024**3
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > 20000: raise ValueError("GROMACS ZIP 文件数超过 20000，拒绝解压。")
        total = 0
        root = target.resolve()
        for info in infos:
            name = info.filename.replace("\\", "/")
            path = Path(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if path.is_absolute() or ".." in path.parts or not name or (mode and stat.S_ISLNK(mode)):
                raise ValueError(f"GROMACS ZIP 含非法路径或符号链接：{name}")
            total += int(info.file_size)
            if total > max_unpacked: raise ValueError("GROMACS ZIP 解压后大小超过限制。")
            destination = (target / name).resolve()
            if root != destination and root not in destination.parents:
                raise ValueError(f"GROMACS ZIP 路径越界：{name}")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True); continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024*1024)
    # Typical archive has a single top-level folder; use it as working root when appropriate.
    entries = [p for p in target.iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir(): return entries[0]
    return target

def _path_inside(root: Path, value: str, expected_suffix: str | None = None) -> Path:
    p = (root / value).resolve()
    if root.resolve() not in p.parents: raise ValueError(f"protocol.json 路径必须位于 ZIP 内：{value}")
    if not p.is_file(): raise FileNotFoundError(f"protocol.json 引用的文件不存在：{value}")
    if expected_suffix and p.suffix.lower() != expected_suffix: raise ValueError(f"文件类型不正确：{value}")
    return p

def _pick_md_inputs(root: Path):
    protocol_path = next(iter(root.rglob("protocol.json")), None)
    protocol = {}
    if protocol_path:
        try: protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        except Exception as exc: raise ValueError(f"protocol.json 解析失败：{exc}")
        if not isinstance(protocol, dict): raise ValueError("protocol.json 顶层必须是 JSON 对象")
        # Manifest paths are interpreted relative to the manifest's directory.
        root = protocol_path.parent.resolve()
    topology_value = protocol.get("topology")
    topology = _path_inside(root, topology_value, ".top") if topology_value else None
    if topology is None:
        topologies = list(root.rglob("topol.top")) or list(root.rglob("*.top"))
        if not topologies: raise FileNotFoundError("ZIP 中找不到 topol.top 或其他 .top 拓扑文件")
        topology = topologies[0]
    coords_value = protocol.get("coordinates")
    coordinates = _path_inside(root, coords_value, ".gro") if coords_value else None
    if coordinates is None:
        gro_files = list(root.rglob("*.gro"))
        preferred = [p for p in gro_files if p.name.lower() in {"conf.gro","solvated.gro","ions.gro","system.gro","start.gro"}]
        avoid = {"nvt.gro","npt.gro","md.gro","prod.gro","em.gro","minim.gro"}
        candidates = preferred or [p for p in gro_files if p.name.lower() not in avoid]
        if not candidates: raise FileNotFoundError("ZIP 中找不到初始 .gro 坐标文件")
        coordinates = candidates[0]
    index_value = protocol.get("index_file")
    index_file = _path_inside(root, index_value, ".ndx") if index_value else None
    raw_stages = protocol.get("stages")
    stages=[]
    if raw_stages:
        if not isinstance(raw_stages, list) or not raw_stages: raise ValueError("protocol.json stages 必须是非空列表")
        for i, stage in enumerate(raw_stages):
            if not isinstance(stage, dict) or not stage.get("mdp"): raise ValueError(f"第 {i+1} 个 stage 缺少 mdp")
            name = re.sub(r"[^A-Za-z0-9_-]", "_", str(stage.get("name") or f"stage{i+1}"))[:40]
            stages.append({"name":name,"mdp":_path_inside(root,str(stage["mdp"]),".mdp")})
    else:
        mdps = list(root.rglob("*.mdp"))
        def order(p):
            n=p.stem.lower()
            if any(k in n for k in ("em", "minim", "energy")): return (0,n)
            if "nvt" in n: return (1,n)
            if "npt" in n: return (2,n)
            if any(k in n for k in ("md", "prod", "production")): return (3,n)
            return (4,n)
        mdps.sort(key=order)
        if not mdps: raise FileNotFoundError("ZIP 中找不到 .tpr 或 .mdp；阶段式运行至少需要一个 .mdp")
        stages=[{"name":p.stem,"mdp":p} for p in mdps]
    return topology, coordinates, index_file, stages, protocol

def run_md_impl(job_id: str):
    job = _get_job(job_id); opts = dict(job.options or {})
    try:
        tpr = _asset_path(opts.get("md_tpr_asset_id"))
        bundle = _asset_path(opts.get("md_bundle_asset_id"))
        if bool(tpr) == bool(bundle): raise ValueError("请且只请提供一个 GROMACS .tpr 或协议 ZIP 文件。")
        gmx = shutil.which(GROMACS_BINARY) or (GROMACS_BINARY if Path(GROMACS_BINARY).is_file() else None)
        if not gmx: raise RuntimeError(f"未找到 GROMACS：{GROMACS_BINARY}。请设置 GROMACS_BINARY。")
        workdir = _workdir(job_id) / "gromacs"; workdir.mkdir(parents=True, exist_ok=True)
        ver = subprocess.run([str(gmx), "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
        (workdir / "gromacs_version.txt").write_text(ver.stdout or "", encoding="utf-8")
        if ver.returncode != 0: raise RuntimeError("GROMACS 版本检查失败：" + (ver.stdout or "")[-1000:])
        timeout = int(opts.get("timeout_seconds", COMMAND_TIMEOUT_SECONDS))
        threads = int(opts.get("gromacs_threads", GROMACS_MDRUN_THREADS))
        use_gpu = bool(opts.get("use_gpu", True))
        stages_run=[]; direct_tpr=tpr
        if bundle:
            input_root = _safe_extract_md_bundle(bundle, _workdir(job_id) / "input_bundle")
            bundled_tprs = sorted(input_root.rglob("*.tpr"))
            if len(bundled_tprs) == 1:
                direct_tpr = bundled_tprs[0]
            else:
                if len(bundled_tprs) > 1 and not list(input_root.rglob("*.mdp")):
                    raise ValueError("ZIP 中有多个 .tpr 但没有 .mdp 阶段定义；请一次上传一个 TPR，或提供 topol.top/.gro/.mdp 阶段包。")
                topology, current_gro, index_file, stages, protocol = _pick_md_inputs(input_root)
                stage_dir = workdir / "stages"; stage_dir.mkdir(parents=True, exist_ok=True)
                previous_cpt = None
                previous_checkpoint_compatible = False
                for idx, stage in enumerate(stages):
                    name = stage["name"]
                    prefix = stage_dir / name
                    tpr_path = stage_dir / f"{name}.tpr"
                    grompp_log = stage_dir / f"{name}.grompp.log"
                    grompp_cmd = [str(gmx), "grompp", "-f", str(stage["mdp"]), "-c", str(current_gro), "-p", str(topology), "-o", str(tpr_path)]
                    if index_file: grompp_cmd += ["-n", str(index_file)]
                    if previous_cpt and previous_cpt.is_file() and previous_checkpoint_compatible:
                        grompp_cmd += ["-t", str(previous_cpt)]
                    _set_status(job_id, "running", f"GROMACS 阶段 {idx+1}/{len(stages)}：grompp {name}", min(85, 10 + idx*70//max(1,len(stages))))
                    with grompp_log.open("w", encoding="utf-8") as log:
                        gp = subprocess.run(grompp_cmd, cwd=str(topology.parent), stdout=log, stderr=subprocess.STDOUT, text=True, timeout=min(timeout, 1800))
                    if gp.returncode != 0 or not tpr_path.is_file():
                        raise RuntimeError(f"GROMACS grompp 阶段 {name} 失败（exit={gp.returncode}），日志：{grompp_log}")
                    mdrun_cmd = [str(gmx), "mdrun", "-s", str(tpr_path), "-deffnm", str(prefix), "-ntmpi", "1", "-ntomp", str(threads), "-pin", "on"]
                    if use_gpu: mdrun_cmd += ["-nb", "gpu"]
                    (stage_dir / f"{name}.command.json").write_text(json.dumps({"grompp_argv":grompp_cmd,"mdrun_argv":mdrun_cmd},indent=2),encoding="utf-8")
                    with (stage_dir / f"{name}.stdout.log").open("w", encoding="utf-8") as log:
                        mr = subprocess.run(mdrun_cmd, cwd=str(topology.parent), stdout=log, stderr=subprocess.STDOUT, text=True, timeout=timeout)
                    if mr.returncode != 0:
                        raise RuntimeError(f"GROMACS mdrun 阶段 {name} 失败（exit={mr.returncode}），日志：{stage_dir / f'{name}.stdout.log'}")
                    next_gro = Path(str(prefix) + ".gro")
                    if not next_gro.is_file(): raise RuntimeError(f"GROMACS 阶段 {name} 没有生成预期坐标文件：{next_gro}")
                    current_gro = next_gro
                    cpt = Path(str(prefix) + ".cpt"); previous_cpt = cpt if cpt.is_file() else None
                    stage_lower = name.lower()
                    previous_checkpoint_compatible = not any(k in stage_lower for k in ("em", "minim", "energy"))
                    stages_run.append({"name":name,"status":"completed","tpr":str(tpr_path),"coordinates_out":str(next_gro),"grompp_log":str(grompp_log),"mdrun_log":str(stage_dir / f"{name}.stdout.log")})
                direct_tpr = None
        if direct_tpr:
            deffnm = workdir / "md"
            cmd = [str(gmx), "mdrun", "-s", str(direct_tpr), "-deffnm", str(deffnm), "-ntmpi", "1", "-ntomp", str(threads), "-pin", "on"]
            if use_gpu: cmd += ["-nb", "gpu"]
            _set_status(job_id, "running", "正在执行 GROMACS mdrun；真实计算日志会写入任务目录", 10, details={"command": cmd, "gpu_requested": use_gpu})
            (workdir / "command.json").write_text(json.dumps({"argv": cmd, "tpr": str(direct_tpr), "submitted_at": now().isoformat()}, indent=2), encoding="utf-8")
            with (workdir / "mdrun.stdout.log").open("w", encoding="utf-8") as log:
                proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=timeout)
            (workdir / "exit_code.txt").write_text(str(proc.returncode), encoding="utf-8")
            outputs = [p for p in workdir.rglob("*") if p.is_file()]
            scientific_outputs = [p for p in outputs if p.suffix.lower() in {".xtc", ".trr", ".edr", ".gro", ".log", ".cpt"}]
            state = "completed" if proc.returncode == 0 and any(p.suffix.lower() in {".xtc", ".trr", ".gro"} for p in scientific_outputs) else "failed"
            return_code = proc.returncode
            cmd_record = cmd
        else:
            # All stages ran in sequence. The final stage's files establish run success.
            return_code = 0 if stages_run and stages_run[-1]["status"] == "completed" else 1
            outputs = [p for p in workdir.rglob("*") if p.is_file()]
            scientific_outputs = [p for p in outputs if p.suffix.lower() in {".xtc", ".trr", ".edr", ".gro", ".log", ".cpt"}]
            state = "completed" if return_code == 0 and any(p.suffix.lower() in {".xtc", ".trr", ".gro"} for p in scientific_outputs) else "failed"
            cmd_record = "staged_grompp_mdrun"
        artifact_zip = _zip_artifacts(_workdir(job_id), "artifacts.zip")
        report = {"schema_version":"2.0", "task_id":job_id, "operation":"md", "title":"GROMACS MD 运行报告", "status":state,
          "summary":{"text":"GROMACS 真实计算完成。" if state=="completed" else "GROMACS 计算未成功；请检查阶段日志和输入文件。", "return_code":return_code, "output_file_count":len(scientific_outputs), "completed_stages":len(stages_run)},
          "stages":stages_run,
          "provenance":{"calculation_mode":"real_gromacs_mdrun", "gromacs_binary":str(gmx), "tpr_asset_id":opts.get("md_tpr_asset_id"), "bundle_asset_id":opts.get("md_bundle_asset_id"), "gpu_requested":use_gpu, "threads":threads, "gromacs_version_file":"gromacs_version.txt", "command":cmd_record, "created_at":now().isoformat()},
          "artifacts":[{"name":"artifacts.zip","url":f"/api/v2/tasks/{job_id}/artifacts/artifacts.zip"}],
          "warnings":["该任务运行预制 .tpr，或对 ZIP 中已完成蛋白/配体准备的结构按 .mdp 执行 grompp+mdrun。","不会自动质子化蛋白、生成配体力场参数、加水或加离子；这些输入必须与论文协议一致。","GPU 模拟需要服务器的 GROMACS GPU build、驱动和可见 GPU。"]}
        report_path = _workdir(job_id) / "report.json"; report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _update(job_id, status=state, progress=100, message=report["summary"]["text"], error=None if state=="completed" else f"GROMACS exit code {return_code}", report_path=str(report_path), artifact_zip=artifact_zip)
        _set_status(job_id, state, report["summary"]["text"], 100, "info" if state=="completed" else "error", {"return_code":return_code})
    except subprocess.TimeoutExpired as exc:
        reason = f"GROMACS 超过配置的超时时间（{exc.timeout} 秒）"
        _write_md_failure(job_id, reason)
    except Exception as exc:
        _write_md_failure(job_id, str(exc))

def _write_md_failure(job_id: str, reason: str):
    workdir = _workdir(job_id)
    report = {"schema_version":"2.0", "task_id":job_id, "operation":"md", "title":"GROMACS 执行失败报告", "status":"failed",
        "summary":{"text":"GROMACS 任务失败；请查看错误和归档日志。", "error":reason},
        "provenance":{"calculation_mode":"failed_or_incomplete_gromacs_run", "created_at":now().isoformat()},
        "warnings":["失败任务不会被当成已完成科学计算。请检查 GROMACS stderr/stdout、输入文件、拓扑 include 和 GPU build。"]}
    archive = None
    try:
        archive = _zip_artifacts(workdir, "artifacts.zip")
        report["artifacts"] = [{"name":"artifacts.zip","url":f"/api/v2/tasks/{job_id}/artifacts/artifacts.zip"}]
    except Exception: pass
    report_path = workdir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_status(job_id, "failed", "GROMACS 任务失败：" + reason[:400], 100, "error", {"error":reason})
    _update(job_id, error=reason, report_path=str(report_path), artifact_zip=archive)

if HAVE_CELERY:
    @celery_app.task(name="app.scale.worker.start_job_task", bind=True, max_retries=0)
    def start_job_task(self, job_id: str): return start_job_impl(job_id)
    def _safe_chunk(job_id: str, path: str, operation: str):
        try:
            return process_vina_chunk_impl(job_id, path) if operation == "docking" else process_properties_chunk_impl(job_id, path)
        except Exception as exc:
            out_dir = _workdir(job_id) / "chunks_out"; out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / (Path(path).stem + "_fatal.csv")
            if operation == "docking": columns = ["name","smiles","status","binding_energy_kcal_mol","pose_file","error"]
            else: columns = ["name","smiles","valid","error","molecular_weight","logp","hbd","hba","tpsa","rotatable_bonds","qed","ro5_violations","ro5_pass","filtered_in","heuristic_score"]
            with out_path.open("w",encoding="utf-8",newline="") as f:
                w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
            return {"path":str(out_path),"input_count":_count_data_rows(path),"fatal_error":f"{Path(path).name}: {type(exc).__name__}: {exc}"}
    @celery_app.task(name="app.scale.worker.process_properties_chunk_task", acks_late=True)
    def process_properties_chunk_task(job_id: str, path: str): return _safe_chunk(job_id, path, "properties")
    @celery_app.task(name="app.scale.worker.process_vina_chunk_task", acks_late=True)
    def process_vina_chunk_task(job_id: str, path: str): return _safe_chunk(job_id, path, "docking")
    @celery_app.task(name="app.scale.worker.finalize_job_task")
    def finalize_job_task(results, job_id: str): return finalize_job_impl(results, job_id)
    @celery_app.task(name="app.scale.worker.run_md_job_task", acks_late=True, reject_on_worker_lost=True)
    def run_md_job_task(job_id: str): return run_md_impl(job_id)
else:
    class _LocalTask:
        def __init__(self, fn): self.fn = fn
        def delay(self, *args, **kwargs): return _LOCAL_EXECUTOR.submit(self.fn, *args, **kwargs)
        def apply_async(self, args=None, kwargs=None, queue=None): return self.delay(*(args or []), **(kwargs or {}))
    start_job_task = _LocalTask(start_job_impl)
    run_md_job_task = _LocalTask(run_md_impl)
