"""REST endpoints for explicit Tamarind discovery, validation and execution."""
import re, time, uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
from app.tamarind.client import TamarindClient, TamarindError
from app.scale.config import DATA_DIR
from app.scale.api import require_token

router = APIRouter(prefix='/tamarind', tags=['☁️ Tamarind 云端计算'])
STATE_DIR = DATA_DIR / 'tamarind_jobs'
STATE_DIR.mkdir(parents=True, exist_ok=True)

class ValidateRequest(BaseModel):
    tool_type: str = Field(min_length=1)
    settings: dict
    job_name: str | None = None

class SubmitRequest(ValidateRequest):
    project_tag: str | None = None
    require_validation: bool = True

class DownloadRequest(BaseModel):
    destination_name: str | None = None

def client(): return TamarindClient()
def call(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except TamarindError as e: raise HTTPException(e.status_code or 502, str(e))

def _safe_job_name(name: str):
    name = re.sub(r'[^A-Za-z0-9_-]+', '-', name).strip('-')[:80]
    return name or f'molplatform-{uuid.uuid4().hex[:12]}'

def _save_state(job_name, payload):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f'{job_name}.json').write_text(__import__('json').dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

@router.get('/console', response_class=HTMLResponse, include_in_schema=False)
def console():
    return HTMLResponse((Path(__file__).with_name('console.html')).read_text(encoding='utf-8'))

@router.get('/health', dependencies=[Depends(require_token)])
def health():
    return call(client().ping)

@router.get('/tools', dependencies=[Depends(require_token)])
def tools(search: str | None = None, modality: str | None = None, function: str | None = None):
    items = call(client().tools)
    def match(x):
        if search and search.lower() not in (x.get('name','') + ' ' + x.get('displayName','') + ' ' + x.get('description','')).lower(): return False
        if modality and modality not in x.get('categories', []): return False
        if function and function not in x.get('tags', []): return False
        return True
    return {'count': sum(1 for x in items if match(x)), 'tools': [x for x in items if match(x)]}

@router.get('/tools/{tool_name}/schema', dependencies=[Depends(require_token)])
def schema(tool_name: str):
    return call(client().tool_schema, tool_name)

@router.post('/validate', dependencies=[Depends(require_token)])
def validate(body: ValidateRequest):
    if body.job_name and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', body.job_name):
        raise HTTPException(422, 'job_name 只能包含英文字母、数字、下划线和短横线，最多 100 字符。')
    return call(client().validate, body.tool_type, body.settings, body.job_name)

@router.post('/upload', dependencies=[Depends(require_token)])
async def upload(file: UploadFile = File(...)):
    # Stream to a temporary local file, then stream it to Tamarind; never load multi-GB files into RAM.
    import tempfile, os
    name = Path(file.filename or 'upload.bin').name
    tmpdir = DATA_DIR / 'tamarind_upload_staging'; tmpdir.mkdir(parents=True, exist_ok=True)
    tmp = tmpdir / f'{uuid.uuid4().hex}_{name}'
    size = 0
    try:
        with tmp.open('wb') as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                size += len(chunk)
                if size > 5 * 1024**3: raise HTTPException(413, 'MVP 单文件上限为 5 GiB；更大文件请使用官方 CLI。')
                out.write(chunk)
        if not size: raise HTTPException(400, '空文件不能上传。')
        result = call(client().upload_file, tmp, name)
        return result
    finally:
        tmp.unlink(missing_ok=True)
        await file.close()

@router.post('/jobs', status_code=202, dependencies=[Depends(require_token)])
def submit(body: SubmitRequest):
    if body.job_name and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', body.job_name):
        raise HTTPException(422, 'job_name 只能包含英文字母、数字、下划线和短横线，最多 100 字符。')
    name = body.job_name or _safe_job_name(f'molplatform-{uuid.uuid4().hex[:12]}')
    cli = client()
    if body.require_validation:
        checked = call(cli.validate, body.tool_type, body.settings, name)
        if not checked.get('valid'):
            raise HTTPException(422, {'message':'Tamarind 参数验证未通过；未提交计算任务。','validation':checked})
        if checked.get('mutatedFields'):
            raise HTTPException(422, {'message':'验证器修改了输入字段；请检查原始输入并重新验证，未提交任务。','validation':checked})
        settings_payload = checked.get('normalized', body.settings)
    else:
        checked, settings_payload = None, body.settings
    submitted = call(cli.submit, name, body.tool_type, settings_payload, body.project_tag)
    state = {'job_name':name,'tool_type':body.tool_type,'settings':settings_payload,'validation':checked,'submission':submitted,'submitted_at':time.time()}
    _save_state(name, state)
    return {'job_name':name,'provider':'tamarind','status':'submitted','validation':checked,'submission':submitted,
            'status_url':f'/api/v4/tamarind/jobs/{name}','result_url':f'/api/v4/tamarind/jobs/{name}/result'}

@router.get('/jobs/{job_name}', dependencies=[Depends(require_token)])
def status(job_name: str):
    return call(client().job, job_name)

@router.get('/jobs/{job_name}/wait', dependencies=[Depends(require_token)])
def wait(job_name: str, timeout_seconds: int = Query(3600, ge=1, le=86400), interval_seconds: int = Query(15, ge=2, le=300)):
    """Bounded polling endpoint; avoid keeping this HTTP request open for very long in production."""
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        last = call(client().job, job_name)
        statuses = []
        if isinstance(last, dict):
            rows = last.get('jobs') or [last]
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict):
                    statuses.append(str(row.get('JobStatus') or row.get('status') or row.get('jobStatus') or '').lower())
        if statuses and all(s in {'complete','completed','failed','stopped','error','cancelled','canceled'} for s in statuses):
            return {'finished':True,'job':last}
        time.sleep(interval_seconds)
    return {'finished':False,'job':last,'message':'等待超时；云端任务可能仍在运行，可再次查询状态。'}

@router.post('/jobs/{job_name}/result', dependencies=[Depends(require_token)])
def result(job_name: str):
    return call(client().result, job_name)

@router.post('/jobs/{job_name}/download', dependencies=[Depends(require_token)])
def download(job_name: str, body: DownloadRequest = DownloadRequest()):
    filename = Path(body.destination_name or f'{job_name}_results.zip').name
    target = (DATA_DIR / 'tamarind_results' / filename).resolve()
    outcome = call(client().download_result, job_name, target)
    if isinstance(outcome, dict) and outcome.get('downloaded') and target.is_file():
        return FileResponse(target, filename=target.name, media_type='application/zip')
    return outcome
