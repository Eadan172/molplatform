"""MoleCode structure helpers: aspirin round-trip and invalid-graph rejection."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.agent.planner import parse_request
from app.main import app
from app.services.molecode_service import (
    MoleCodeStructureError,
    llm_client_from_env,
    llm_configured,
    mermaid_to_mol,
    mermaid_to_smiles,
    smiles_to_mermaid,
)

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"

UNKNOWN_ATOM = """graph TB
    subgraph Bad["Bad"]
        Bad_C_1[CH3]
        Bad_Q_1[Unobtainium]
        Bad_C_1 --- Bad_Q_1
    end"""

BAD_VALENCE = """graph TB
    subgraph Bad["Bad"]
        Bad_C_1[CH4]
        Bad_C_2[CH4]
        Bad_C_1 --- Bad_C_2
    end"""

DANGLING_BOND = """graph TB
    subgraph Bad["Bad"]
        Bad_C_1[CH3]
        Bad_C_1 --- Bad_C_2
    end"""


def test_aspirin_round_trip():
    graph = smiles_to_mermaid(ASPIRIN, name="Aspirin")
    assert "graph TB" in graph
    assert "Aspirin" in graph
    mol = mermaid_to_mol(graph)
    assert mol.GetNumAtoms() == 13
    assert mermaid_to_smiles(graph) == ASPIRIN


def test_invalid_graphs_are_rejected():
    for graph in (UNKNOWN_ATOM, BAD_VALENCE, DANGLING_BOND, "this is not a graph", ""):
        try:
            mermaid_to_mol(graph)
        except MoleCodeStructureError as exc:
            assert exc.code in {"invalid_atom", "invalid_valence", "invalid_graph"}
            assert str(exc)
            continue
        raise AssertionError("invalid MoleCode graph was accepted")
    unknown = _expect_error(UNKNOWN_ATOM)
    assert unknown.code == "invalid_atom"
    assert "Unobtainium" in str(unknown)
    valence = _expect_error(BAD_VALENCE)
    assert valence.code == "invalid_valence"
    dangling = _expect_error(DANGLING_BOND)
    assert dangling.code == "invalid_graph"
    assert "Bad_C_2" in str(dangling)


def test_invalid_smiles_is_rejected():
    try:
        smiles_to_mermaid("not-a-smiles", name="Bad")
    except MoleCodeStructureError as exc:
        assert exc.code == "invalid_smiles"
        return
    raise AssertionError("invalid SMILES was accepted")


def test_property_and_docking_plans_stay_on_their_paths():
    analysis = parse_request("分析分子库，筛选类药五原则通过的候选物，生成 Top 5 报告")
    assert analysis["supported"] is True
    assert analysis.get("operation") != "structure"
    assert any(step["tool"] == "molecule.filter" for step in analysis["steps"])
    docking = parse_request("请用 MoleCode 做真实 COX-2 分子对接")
    assert docking["supported"] is False
    assert docking.get("operation") != "structure"
    assert "不会执行真实分子对接" in docking["reason"]


def test_llm_client_uses_existing_env_and_stays_off_by_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert llm_configured() is False
    assert llm_client_from_env() is None
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    assert llm_client_from_env() is None
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    client = llm_client_from_env()
    assert client is not None
    assert client.base_url == "https://example.invalid/v1"
    assert client.model == "test-model"
    assert client.api_key == "test-key-not-a-secret"


def test_tool_endpoint_round_trip_and_rejection():
    with TestClient(app) as client:
        created = client.post("/api/v1/agent/tools/molecode", json={
            "action": "smiles_to_mermaid",
            "smiles": ASPIRIN,
            "name": "Aspirin",
        })
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["tool"] == "structure.molecode"
        assert body["rdkit_valid"] is True
        assert body["canonical_smiles"] == ASPIRIN
        assert body["atom_count"] == 13
        restored = client.post("/api/v1/agent/tools/molecode", json={
            "action": "mermaid_to_smiles",
            "mermaid": body["mermaid"],
        })
        assert restored.status_code == 200, restored.text
        assert restored.json()["canonical_smiles"] == ASPIRIN
        rejected = client.post("/api/v1/agent/tools/molecode", json={
            "action": "validate",
            "mermaid": UNKNOWN_ATOM,
        })
        assert rejected.status_code == 400
        detail = rejected.json()["detail"]
        assert detail["code"] == "invalid_atom"
        assert "Unobtainium" in detail["message"]
        home = client.get("/api/v1/agent/")
        assert home.status_code == 200
        assert "MoleCode" in home.text
        capabilities = client.get("/api/v1/agent/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["structure_tool"]["name"] == "structure.molecode"
        assert capabilities.json()["structure_tool"]["llm_required"] is False


def test_agent_task_converts_aspirin_and_rejects_invalid_graph():
    with TestClient(app) as client:
        ok = client.post("/api/v1/agent/tasks", json={
            "prompt": "用 MoleCode 把阿司匹林转成结构图：CC(=O)Oc1ccccc1C(=O)O",
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()["plan"]["operation"] == "structure"
        assert ok.json()["plan"]["steps"][0]["tool"] == "structure.molecode"
        done = _wait(client, ok.json()["task_id"])
        assert done["status"] == "completed", done
        report = client.get(f"/api/v1/agent/reports/{done['report_id']}").json()["report"]
        assert report["structure"]["canonical_smiles"] == ASPIRIN
        assert report["data_quality"]["real_docking_performed"] is False
        assert report["data_quality"]["real_md_performed"] is False
        assert report["provenance"]["tools"] == ["structure.molecode"]

        bad = client.post("/api/v1/agent/tasks", json={
            "prompt": "请校验这段 MoleCode 图：\n```mermaid\n" + UNKNOWN_ATOM + "\n```",
        })
        assert bad.status_code == 200, bad.text
        failed = _wait(client, bad.json()["task_id"])
        assert failed["status"] == "failed", failed
        assert "invalid_atom" in (failed.get("error") or "")
        assert "Unobtainium" in (failed.get("error") or "")


def test_chat_attaches_validated_structure_without_calling_a_model_first(monkeypatch):
    captured = {}

    async def fake_call(prompt, system="", max_tokens=1500):
        captured["prompt"] = prompt
        captured["system"] = system
        return {"success": True, "content": "结构已核对", "usage": {}}

    monkeypatch.setattr("app.services.ai_service.call_claude", fake_call)
    from app.services.ai_service import chat_with_expert

    result = asyncio.run(chat_with_expert("看看这个分子", {"smiles": ASPIRIN, "name": "Aspirin"}))
    assert result["structure"]["ok"] is True
    assert result["structure"]["canonical_smiles"] == ASPIRIN
    assert "graph TB" in result["structure"]["mermaid"]
    assert ASPIRIN in captured["prompt"]
    assert "MoleCode" in captured["system"]

    bad = asyncio.run(chat_with_expert("编辑这个结构", {"mermaid": UNKNOWN_ATOM}))
    assert bad["structure"]["ok"] is False
    assert bad["structure"]["code"] == "invalid_atom"
    plain = asyncio.run(chat_with_expert("如何判断对接结果是否可靠？", None))
    assert plain["structure"] is None


def _expect_error(graph: str) -> MoleCodeStructureError:
    try:
        mermaid_to_mol(graph)
    except MoleCodeStructureError as exc:
        return exc
    raise AssertionError("invalid MoleCode graph was accepted")


def _wait(client: TestClient, task_id: str) -> dict:
    status = None
    for _ in range(60):
        status = client.get(f"/api/v1/agent/tasks/{task_id}").json()
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.05)
    return status or {}
