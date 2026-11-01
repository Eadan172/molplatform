"""v2 scale API: assets, natural-language job submission, status, reports and downloads."""
import hashlib, hmac, json, os, re, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, Request
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import select
from app.scale.config import API_TOKEN, DATA_DIR, UPLOAD_DIR, MAX_UPLOAD_MB, ENVIRONMENT, VINA_BINARY, OBABEL_BINARY, GROMACS_BINARY
from app.scale.database import SessionLocal, init_scale_db, get_session
from app.scale.models import Asset, ScaleJob, JobEvent
from app.scale.schemas import TaskRequest, TaskResponse
from app.scale.planner import plan_request
from app.scale.worker import enqueue_job

router = APIRouter()
_ALLOWED_EXT = {".csv", ".smi", ".smiles", ".txt", ".sdf", ".pdb", ".pdbqt", ".tpr", ".mdp", ".gro", ".top", ".itp", ".xtc", ".trr", ".edr", ".zip"}

def require_token(authorization: str | None = Header(default=None), x_api_token: str | None = Header(default=None)):
    if not API_TOKEN:
        if ENVIRONMENT in {"production", "prod"}:
            raise HTTPException(503, "生产模式未配置 API_TOKEN，API 已拒绝请求。")
        return True
    token = x_api_token or (authorization[7:] if authorization and authorization.lower().startswith("bearer ") else "")
    if not token or not hmac.compare_digest(token, API_TOKEN):
        raise HTTPException(401, "API Token 缺失或错误。请在网页上方填写服务器 Token。")
    return True

Auth = Annotated[bool, Depends(require_token)]

def _iso(value):
    return value.isoformat() if value else None

def _job_dict(j: ScaleJob):
    return {"task_id": j.id, "prompt": j.prompt, "operation": j.operation, "status": j.status,
        "progress": j.progress, "message": j.message, "error": j.error, "plan": j.plan, "options": j.options,
        "asset_ids": j.asset_ids, "count_total": j.count_total, "count_done": j.count_done,
        "created_at": _iso(j.created_at), "updated_at": _iso(j.updated_at),
        "report_url": f"/api/v2/tasks/{j.id}/report" if j.report_path else None,
        "artifacts_url": f"/api/v2/tasks/{j.id}/artifacts" if j.artifact_zip else None}

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def workbench():
    template = Path(__file__).with_name("workbench.html")
    return HTMLResponse(template.read_text(encoding="utf-8"))

@router.get("/system/capabilities")
def capabilities(_: Auth):
    import shutil, importlib.util
    def find(binary): return shutil.which(binary) or (binary if Path(binary).is_file() else None)
    try:
        import rdkit
        rdkit_info = {"available": True, "version": rdkit.__version__}
    except Exception:
        rdkit_info = {"available": False, "version": None}
    try:
        from app.scale.worker import HAVE_CELERY
    except Exception: HAVE_CELERY = False
    return {"status":"ok", "environment":ENVIRONMENT, "rdkit":rdkit_info,
        "vina":{"available":bool(find(VINA_BINARY)),"binary":find(VINA_BINARY)},
        "openbabel":{"available":bool(find(OBABEL_BINARY)),"binary":find(OBABEL_BINARY)},
        "gromacs":{"available":bool(find(GROMACS_BINARY)),"binary":find(GROMACS_BINARY)},
        "celery":{"available":HAVE_CELERY}, "distributed_queue":HAVE_CELERY,
        "shared_filesystem_required_for_multiple_workers":True,
        "api_token_required":bool(API_TOKEN)}

@router.get("/assets")
def list_assets(_: Auth, limit: int = 100):
    limit = max(1, min(limit, 1000))
    with SessionLocal() as db:
        items = db.scalars(select(Asset).order_by(Asset.created_at.desc()).limit(limit)).all()
        return [{"asset_id":a.id,"filename":a.filename,"file_type":a.file_type,"size_bytes":a.size_bytes,"sha256":a.sha256,"created_at":_iso(a.created_at)} for a in items]

@router.post("/assets", status_code=201)
async def upload_asset(_: Auth, file: UploadFile = File(...)):
    original = Path(file.filename or "upload.bin").name
    ext = Path(original).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型：{ext or '(无扩展名)'}。允许：{', '.join(sorted(_ALLOWED_EXT))}")
    asset_id = str(uuid.uuid4())
    dest = (UPLOAD_DIR / f"{asset_id}{ext}").resolve()
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    sha = hashlib.sha256(); size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                size += len(chunk)
                if size > max_bytes: raise HTTPException(413, f"文件超过 MAX_UPLOAD_MB={MAX_UPLOAD_MB} MB 限制")
                sha.update(chunk); out.write(chunk)
        if size == 0: raise HTTPException(400, "空文件不能上传")
        asset = Asset(id=asset_id, filename=original[:255], file_type=ext, path=str(dest), size_bytes=size, sha256=sha.hexdigest())
        with SessionLocal() as db:
            db.add(asset); db.commit()
        return {"asset_id":asset_id,"filename":original,"file_type":ext,"size_bytes":size,"sha256":sha.hexdigest()}
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

@router.post("/tasks", response_model=TaskResponse, status_code=202)
def submit_task(body: TaskRequest, _: Auth):
    init_scale_db()
    plan = plan_request(body.prompt)
    operation = plan.get("operation", "unknown")
    # Explicit UI options override parsed text, while numeric constraints stay validated by Pydantic.
    filters = dict(plan.get("filters") or {})
    for key in ("ro5_only", "max_molecular_weight", "max_logp", "min_qed"):
        value = getattr(body, key)
        if value is not None: filters[key] = value
    plan["filters"] = filters
    plan["top_n"] = body.top_n if body.top_n != 100 else plan.get("top_n", 100)
    center = [body.vina_center_x, body.vina_center_y, body.vina_center_z]
    if all(v is not None for v in center): plan["vina_center"] = [float(v) for v in center]
    elif plan.get("vina_center") is None: plan["vina_center"] = None
    options = {"filters":filters,"top_n":plan["top_n"],"molecule_asset_id":body.molecule_asset_id,
       "receptor_asset_id":body.receptor_asset_id,"ligand_asset_ids":body.ligand_asset_ids,
       "md_tpr_asset_id":body.md_tpr_asset_id,"md_bundle_asset_id":body.md_bundle_asset_id,"vina_center":plan.get("vina_center"),
       "vina_box_size":body.vina_box_size,
       "vina_box_size_x":body.vina_box_size_x if body.vina_box_size_x is not None else body.vina_box_size,
       "vina_box_size_y":body.vina_box_size_y if body.vina_box_size_y is not None else body.vina_box_size,
       "vina_box_size_z":body.vina_box_size_z if body.vina_box_size_z is not None else body.vina_box_size,
       "vina_exhaustiveness":body.vina_exhaustiveness,
       "vina_modes":body.vina_modes,"vina_cpu":body.vina_cpu,"use_gpu":body.use_gpu,
       "gromacs_threads":body.gromacs_threads,"timeout_seconds":body.timeout_seconds}
    missing = []
    asset_ids = [x for x in [body.molecule_asset_id, body.receptor_asset_id, body.md_tpr_asset_id, body.md_bundle_asset_id] + body.ligand_asset_ids if x]
    with SessionLocal() as db:
        found = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()} if asset_ids else {}
        missing_assets = [a for a in asset_ids if a not in found]
        if missing_assets: raise HTTPException(400, f"这些 asset_id 不存在：{missing_assets}")
        if operation == "properties":
            if not body.molecule_asset_id: missing.append("包含 name,smiles 列的 CSV，或 SMI/SDF 分子库文件")
            elif Path(found[body.molecule_asset_id].filename).suffix.lower() not in {".csv",".smi",".smiles",".txt",".sdf"}:
                raise HTTPException(400, "分子库文件格式必须是 CSV/SMI/SMILES/TXT/SDF")
        elif operation == "docking":
            if not body.receptor_asset_id: missing.append("预处理好的受体 PDBQT 文件")
            elif Path(found[body.receptor_asset_id].filename).suffix.lower() != ".pdbqt": raise HTTPException(400,"受体文件必须是 PDBQT")
            if not body.molecule_asset_id and not body.ligand_asset_ids: missing.append("CSV/SMI 分子库（需要 Open Babel 转换）或多个预制配体 PDBQT 文件")
            if body.molecule_asset_id and Path(found[body.molecule_asset_id].filename).suffix.lower() not in {".csv",".smi",".smiles",".txt"}:
                raise HTTPException(400, "Vina 批量对接的分子库请使用 SMILES CSV/SMI；也可上传多个已准备好的配体 PDBQT")
            if not options["vina_center"]: missing.append("docking box 中心坐标 x,y,z（需来自文献、共晶配体或已验证口袋）")
        elif operation == "md":
            if body.md_tpr_asset_id and body.md_bundle_asset_id:
                raise HTTPException(400, "MD 输入请在 .tpr 与协议 ZIP 之间选择一种，不能同时上传。")
            if not body.md_tpr_asset_id and not body.md_bundle_asset_id:
                missing.append("已准备好的 GROMACS .tpr，或包含 topol.top、初始 .gro 和阶段 .mdp 的协议 ZIP")
            elif body.md_tpr_asset_id and Path(found[body.md_tpr_asset_id].filename).suffix.lower() != ".tpr": raise HTTPException(400,"GROMACS MD 单文件输入必须是 .tpr")
            elif body.md_bundle_asset_id and Path(found[body.md_bundle_asset_id].filename).suffix.lower() != ".zip": raise HTTPException(400,"GROMACS 协议包必须是 ZIP")
        elif operation == "admet":
            missing.append("当前版本未接入经过验证的 ADMET 预测器；本次不会启动虚假计算")
        else:
            missing.append("明确的分析需求，例如基础性质筛选、Vina 对接或 GROMACS MD")
        tool_missing = []
        import shutil
        if not missing:
            if operation == "properties":
                try:
                    import rdkit  # noqa: F401
                except Exception:
                    tool_missing.append("RDKit（在实际计算 worker 环境安装）")
            elif operation == "docking":
                if not (shutil.which(VINA_BINARY) or Path(VINA_BINARY).is_file()): tool_missing.append(f"AutoDock Vina：{VINA_BINARY}")
                if body.molecule_asset_id and not body.ligand_asset_ids and not (shutil.which(OBABEL_BINARY) or Path(OBABEL_BINARY).is_file()):
                    tool_missing.append(f"Open Babel（SMILES 转 PDBQT）：{OBABEL_BINARY}")
            elif operation == "md":
                if not (shutil.which(GROMACS_BINARY) or Path(GROMACS_BINARY).is_file()): tool_missing.append(f"GROMACS：{GROMACS_BINARY}")
        if tool_missing: missing.extend(tool_missing)
        status = "needs_tool" if operation == "admet" or tool_missing else ("needs_inputs" if missing else "queued")
        message = ("需要安装或配置计算工具，任务尚未执行。" if tool_missing or operation == "admet" else ("请补齐必需输入后重新提交。" if missing else "任务已进入分布式计算队列。"))
        job_id = str(uuid.uuid4())
        job = ScaleJob(id=job_id,prompt=body.prompt,operation=operation,status=status,progress=0,message=message,
                       plan=plan,options=options,asset_ids=asset_ids)
        db.add(job)
        db.add(JobEvent(job_id=job_id,level="info",message=message,details={"missing_inputs":missing,"planner":plan.get("planner")}))
        db.commit()
    if status == "queued":
        try:
            enqueue_job(job_id)
        except Exception as exc:
            # Do not leave a job stuck in queued if Redis/Celery is unavailable.
            queue_message = f"任务队列不可用，尚未开始计算：{type(exc).__name__}: {exc}"
            with SessionLocal() as db:
                failed_job = db.get(ScaleJob, job_id)
                if failed_job:
                    failed_job.status = "failed"
                    failed_job.progress = 100
                    failed_job.message = queue_message
                    failed_job.error = queue_message
                    failed_job.updated_at = datetime.now(timezone.utc)
                    db.add(JobEvent(job_id=job_id, level="error", message=queue_message,
                                    details={"stage": "enqueue", "exception": type(exc).__name__}))
                    db.commit()
            status = "failed"
            message = queue_message
    return TaskResponse(task_id=job_id,operation=operation,status=status,progress=100 if status == "failed" else 0,message=message,missing_inputs=missing,
                        status_url=f"/api/v2/tasks/{job_id}", report_url=None)

@router.get("/tasks")
def list_tasks(_: Auth, limit: int = 50):
    limit=max(1,min(limit,500))
    with SessionLocal() as db:
        jobs=db.scalars(select(ScaleJob).order_by(ScaleJob.created_at.desc()).limit(limit)).all()
        return [_job_dict(j) for j in jobs]

@router.get("/tasks/{task_id}")
def get_task(task_id: str, _: Auth):
    with SessionLocal() as db:
        j=db.get(ScaleJob,task_id)
        if not j: raise HTTPException(404,"任务不存在")
        result=_job_dict(j)
        events=db.scalars(select(JobEvent).where(JobEvent.job_id==task_id).order_by(JobEvent.id.desc()).limit(100)).all()
        result["events"]=[{"level":e.level,"message":e.message,"details":e.details,"created_at":_iso(e.created_at)} for e in reversed(events)]
        return result

@router.get("/tasks/{task_id}/report")
def get_report(task_id: str, _: Auth):
    with SessionLocal() as db:
        j=db.get(ScaleJob,task_id)
        if not j: raise HTTPException(404,"任务不存在")
        path=Path(j.report_path).resolve() if j.report_path else None
        if not path or not path.is_file(): raise HTTPException(404,"报告尚未生成")
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc: raise HTTPException(500,f"报告读取失败：{exc}")

@router.get("/tasks/{task_id}/artifacts")
def list_artifacts(task_id: str, _: Auth):
    root=(DATA_DIR / "jobs" / task_id).resolve()
    if not root.is_dir(): raise HTTPException(404,"任务输出目录不存在")
    results=[]
    for p in root.rglob("*"):
        if p.is_file() and "chunks" not in p.parts and "chunks_out" not in p.parts:
            rel=p.relative_to(root).as_posix()
            results.append({"name":rel,"size_bytes":p.stat().st_size,"url":f"/api/v2/tasks/{task_id}/artifacts/{rel}"})
    return {"task_id":task_id,"artifacts":results}

@router.get("/tasks/{task_id}/artifacts/{artifact_name:path}")
def download_artifact(task_id: str, artifact_name: str, _: Auth):
    if ".." in Path(artifact_name).parts or artifact_name.startswith(("/", "\\")): raise HTTPException(400,"非法文件路径")
    root=(DATA_DIR / "jobs" / task_id).resolve(); target=(root / artifact_name).resolve()
    if root not in target.parents or not target.is_file(): raise HTTPException(404,"文件不存在")
    return FileResponse(target, filename=target.name)

@router.get("/examples/molecules.csv")
def example_library():
    p=Path(__file__).resolve().parents[2] / "examples" / "mini_library.csv"
    return FileResponse(p,filename="mini_library.csv",media_type="text/csv")
