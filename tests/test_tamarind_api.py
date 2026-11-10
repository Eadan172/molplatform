from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.scale import api as scale_api
from app.tamarind import api as tamarind_api


class FakeClient:
    def tools(self):
        return [{"name": "alphafold", "displayName": "AlphaFold"}]

    def tool_schema(self, name):
        return {"name": name, "settings": [{"name": "sequence", "required": True}]}

    def validate(self, tool_type, settings, job_name=None):
        return {"valid": True, "normalized": settings, "mutatedFields": []}

    def submit(self, job_name, tool_type, settings, project_tag=None):
        return {"message": "submitted", "jobName": job_name}

    def ping(self):
        return {"connected": True, "tool_count": 1}


def make_client():
    app = FastAPI()
    app.include_router(tamarind_api.router, prefix="/api/v4")
    return TestClient(app)


def test_tool_discovery_and_schema(monkeypatch):
    monkeypatch.setattr(tamarind_api, "client", FakeClient)
    with make_client() as c:
        assert c.get("/api/v4/tamarind/tools").json()["count"] == 1
        assert c.get("/api/v4/tamarind/tools/alphafold/schema").json()["name"] == "alphafold"


def test_submit_validates_before_explicit_submission(monkeypatch):
    calls = []

    class TrackingClient(FakeClient):
        def validate(self, *args, **kwargs):
            calls.append("validate")
            return super().validate(*args, **kwargs)

        def submit(self, *args, **kwargs):
            calls.append("submit")
            return super().submit(*args, **kwargs)

    monkeypatch.setattr(tamarind_api, "client", TrackingClient)
    with make_client() as c:
        response = c.post("/api/v4/tamarind/jobs", json={
            "tool_type": "alphafold",
            "job_name": "test-run",
            "settings": {"sequence": "ACDE"},
        })
    assert response.status_code == 202, response.text
    assert calls == ["validate", "submit"]


def test_mutated_validation_never_submits(monkeypatch):
    calls = []

    class MutatingClient(FakeClient):
        def validate(self, *args, **kwargs):
            return {"valid": True, "normalized": {"sequence": "CHANGED"}, "mutatedFields": ["sequence"]}

        def submit(self, *args, **kwargs):
            calls.append("submit")
            return super().submit(*args, **kwargs)

    monkeypatch.setattr(tamarind_api, "client", MutatingClient)
    with make_client() as c:
        response = c.post("/api/v4/tamarind/jobs", json={
            "tool_type": "alphafold",
            "job_name": "mutated-run",
            "settings": {"sequence": "ACDE"},
        })
    assert response.status_code == 422
    assert calls == []


def test_console_is_public_but_api_requires_token(monkeypatch):
    monkeypatch.setattr(tamarind_api, "client", FakeClient)
    monkeypatch.setattr(scale_api, "API_TOKEN", "secret-token")
    with make_client() as c:
        assert c.get("/api/v4/tamarind/console").status_code == 200
        assert c.get("/api/v4/tamarind/health").status_code == 401
        assert c.get("/api/v4/tamarind/health", headers={"X-API-Token": "secret-token"}).status_code == 200
