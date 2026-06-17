"""
交互式演示 HTML 前端 — 提供一键运行 Demo 和结果可视化
入口：/（根路径）或 /demo

HTML 模板存放在 templates/demo_page.html，每次请求时动态读取，
修改前端代码后刷新浏览器即可生效，无需重启服务器。
"""

import os
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_PATH = _TEMPLATE_DIR / "demo_page.html"


def _load_demo_html() -> str:
    """每次请求动态读取 HTML 模板，便于前端热更新。"""
    try:
        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"""
        <html><body style="font-family:sans-serif;padding:40px;">
        <h2>演示页面模板文件缺失</h2>
        <p>找不到：<code>{_TEMPLATE_PATH}</code></p>
        <p>请确认 templates/demo_page.html 存在后刷新页面。</p>
        </body></html>
        """


@router.get("/", response_class=HTMLResponse, summary="交互式演示主页")
@router.get("/demo", response_class=HTMLResponse, summary="交互式演示主页")
def demo_home():
    return HTMLResponse(content=_load_demo_html(), media_type="text/html; charset=utf-8")
