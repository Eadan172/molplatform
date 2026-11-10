"""Small, explicit REST client. No job is submitted implicitly."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import quote
import httpx
from app.tamarind.config import settings

class TamarindError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload=None):
        super().__init__(message)
        self.status_code, self.payload = status_code, payload

class TamarindClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, transport=None):
        self.api_key = (api_key if api_key is not None else settings.tamarind_api_key).strip()
        self.base_url = (base_url or settings.tamarind_api_base).rstrip('/') + '/'
        self.transport = transport

    def _client(self):
        if not self.api_key:
            raise TamarindError('未配置 TAMARIND_API_KEY。请在本地 .env 中设置；不要把密钥发到聊天或前端。')
        return httpx.Client(base_url=self.base_url, headers={'x-api-key': self.api_key},
                            timeout=settings.tamarind_request_timeout, transport=self.transport,
                            follow_redirects=True)

    @staticmethod
    def _decode(resp):
        try: data = resp.json()
        except Exception: data = {'message': resp.text[:2000]}
        if resp.status_code >= 400:
            detail = data.get('detail') or data.get('error') or data.get('message') or resp.text[:1000]
            raise TamarindError(f'Tamarind API HTTP {resp.status_code}: {detail}', resp.status_code, data)
        return data

    def request(self, method: str, path: str, **kwargs):
        try:
            with self._client() as c:
                resp = c.request(method, path.lstrip('/'), **kwargs)
            return self._decode(resp)
        except httpx.HTTPError as exc:
            raise TamarindError(f'无法连接 Tamarind：{exc}') from exc

    def ping(self):
        data = self.request('GET', 'tools')
        return {'connected': True, 'tool_count': len(data) if isinstance(data, list) else len(data.get('tools', [])),
                'base_url': self.base_url}

    def tools(self):
        data = self.request('GET', 'tools')
        if isinstance(data, dict) and 'tools' in data: return data['tools']
        return data if isinstance(data, list) else []

    def tool_schema(self, tool_name: str):
        for item in self.tools():
            if item.get('name') == tool_name:
                return item
        raise TamarindError(f'当前账户工具目录中没有 {tool_name!r}；请先刷新工具目录。')

    def validate(self, tool_type: str, settings_payload: dict, job_name: str | None = None):
        body = {'type': tool_type, 'settings': settings_payload}
        if job_name: body['jobName'] = job_name
        result = self.request('POST', 'validate-job', json=body)
        if not isinstance(result, dict) or 'valid' not in result:
            raise TamarindError(f'无法识别参数验证响应：{result}')
        return result

    def upload_file(self, file_path: str | Path, remote_name: str | None = None):
        path = Path(file_path).expanduser().resolve()
        if not path.is_file(): raise TamarindError(f'本地文件不存在：{path}')
        if path.stat().st_size == 0: raise TamarindError('空文件不能上传。')
        if path.stat().st_size > settings.tamarind_max_upload_mb * 1024 * 1024:
            raise TamarindError(f'文件超过 TAMARIND_MAX_UPLOAD_MB={settings.tamarind_max_upload_mb} MB。大文件请使用官方 CLI/分块上传。')
        name = Path(remote_name or path.name).name
        if name in {'', '.', '..'}: raise TamarindError('非法远端文件名。')
        try:
            with self._client() as c, path.open('rb') as f:
                resp = c.put('upload/' + quote(name), content=f,
                             headers={'Content-Type': 'application/octet-stream'})
            return {'filename': name, 'size_bytes': path.stat().st_size, 'response': self._decode(resp)}
        except httpx.HTTPError as exc: raise TamarindError(f'文件上传失败：{exc}') from exc

    def submit(self, job_name: str, tool_type: str, settings_payload: dict, project_tag: str | None = None):
        body = {'jobName': job_name, 'type': tool_type, 'settings': settings_payload}
        if project_tag: body['projectTag'] = project_tag
        return self.request('POST', 'submit-job', json=body)

    def job(self, job_name: str):
        return self.request('GET', 'jobs', params={'jobName': job_name})

    def result(self, job_name: str):
        return self.request('POST', 'result', json={'jobName': job_name})

    def download_result(self, job_name: str, destination: str | Path):
        result = self.result(job_name)
        # API commonly returns a presigned URL (string) or a JSON object containing one.
        url = result if isinstance(result, str) and result.startswith('http') else None
        if isinstance(result, dict):
            for key in ('url', 'downloadUrl', 'resultUrl', 'presignedUrl'):
                if isinstance(result.get(key), str): url = result[key]; break
        if not url:
            return {'job_name': job_name, 'response': result, 'downloaded': False,
                    'message': 'Tamarind 返回了结果元数据而非可识别的下载 URL；请检查 response。'}
        dest = Path(destination).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.stream('GET', url, timeout=settings.tamarind_request_timeout, follow_redirects=True) as r:
                r.raise_for_status()
                with dest.open('wb') as f:
                    for chunk in r.iter_bytes(): f.write(chunk)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise TamarindError(f'结果下载失败：{exc}') from exc
        return {'job_name': job_name, 'downloaded': True, 'path': str(dest), 'size_bytes': dest.stat().st_size}
