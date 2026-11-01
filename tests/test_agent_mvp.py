"""Focused regression tests for the natural-language analysis MVP."""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app


def main():
    with TestClient(app) as client:
        r = client.post("/api/v1/agent/tasks", json={
            "prompt": "分析分子库，筛选类药五原则通过的候选物，生成 Top 5 报告",
            "top_n": 5,
        })
        assert r.status_code == 200, r.text
        task_id = r.json()["task_id"]
        status = None
        for _ in range(60):
            status_resp = client.get(f"/api/v1/agent/tasks/{task_id}")
            assert status_resp.status_code == 200, status_resp.text
            status = status_resp.json()
            if status["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert status and status["status"] == "completed", status
        report_id = status["report_id"]
        report_resp = client.get(f"/api/v1/agent/reports/{report_id}")
        assert report_resp.status_code == 200, report_resp.text
        report = report_resp.json()["report"]
        assert report["summary"]["input_count"] >= 1
        assert report["summary"]["ranked_count"] <= 5
        assert report["data_quality"]["real_docking_performed"] is False
        assert report["filter_diagnostics"]["input_valid_count"] >= report["filter_diagnostics"]["after_ro5_count"]
        assert len(report["charts"]["ro5_distribution"]) == 2
        html = client.get(f"/api/v1/agent/reports/{report_id}/html")
        assert html.status_code == 200 and "候选物排名" in html.text

        unsupported = client.post("/api/v1/agent/tasks", json={
            "prompt": "请执行真实 COX-2 分子对接并返回结合能",
        })
        assert unsupported.status_code == 200, unsupported.text
        assert unsupported.json()["status"] == "needs_tool"
        assert "不会执行真实分子对接" in unsupported.json()["message"]

        # Add an explicit Ro5-negative fixture and prove that the filter excludes it.
        fixture = client.post("/api/v1/agent/dev-fixtures/ro5-violator")
        assert fixture.status_code == 200, fixture.text
        assert fixture.json()["ro5_pass"] == 0
        filtered = client.post("/api/v1/agent/tasks", json={
            "prompt": "筛选类药五原则通过的分子，生成 Top 20 报告", "top_n": 20,
        })
        task_id = filtered.json()["task_id"]
        for _ in range(60):
            state = client.get(f"/api/v1/agent/tasks/{task_id}").json()
            if state["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert state["status"] == "completed", state
        filtered_report = client.get(f"/api/v1/agent/reports/{state['report_id']}").json()["report"]
        diagnostics = filtered_report["filter_diagnostics"]
        assert diagnostics["excluded_by_ro5"] >= 1, diagnostics
        assert diagnostics["after_ro5_count"] < diagnostics["input_valid_count"], diagnostics
        assert any(x["label"] == "未通过/未知" and x["value"] >= 1 for x in filtered_report["charts"]["ro5_distribution"])

    print("MVP 测试通过：任务提交、报告查询、HTML 页面、能力边界、Ro5 反例排除与阶段计数。")


if __name__ == "__main__":
    main()
