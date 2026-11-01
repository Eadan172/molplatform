"""Offline test suite for scale MVP; real Vina/GROMACS tests require local toolchains and scientific inputs."""
import csv, json, tempfile, uuid, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.scale.database import init_scale_db, SessionLocal
from app.scale.models import ScaleJob
from app.scale.planner import plan_request
from app.scale.schemas import TaskRequest
from app.scale.molecule_io import iter_smiles, split_molecule_file
from app.scale.worker import (process_properties_chunk_impl, finalize_job_impl,
    _safe_extract_md_bundle, _pick_md_inputs, _make_prepared_ligand_chunks)
from app.scale.config import JOBS_DIR


def test_planner_routes_operations_and_filters():
    p = plan_request("分析分子库，筛选类药五原则通过且分子量小于 350 的分子，展示 Top 8")
    assert p["operation"] == "properties"
    assert p["filters"]["ro5_only"] is True
    assert p["filters"]["max_molecular_weight"] == 350
    assert p["top_n"] == 8
    assert plan_request("用 Vina 进行 COX-2 对接")["operation"] == "docking"
    assert plan_request("用 GROMACS GPU 运行 MD simulation")["operation"] == "md"
    assert plan_request("预测 ADMET 毒性")["operation"] == "admet"


def test_capability_endpoint_and_workbench():
    with TestClient(app) as client:
        page = client.get("/workbench")
        assert page.status_code == 200 and "自然语言科研工作台" in page.text
        cap = client.get("/api/v2/system/capabilities")
        assert cap.status_code == 200, cap.text
        assert "rdkit" in cap.json() and "gromacs" in cap.json()
        docs = client.get("/docs")
        assert docs.status_code == 200


def test_rdkit_chunk_filter_and_report():
    init_scale_db()
    test_id = "test-" + uuid.uuid4().hex
    wd = JOBS_DIR / test_id
    in_dir = wd / "chunks"; in_dir.mkdir(parents=True, exist_ok=True)
    source = in_dir / "sample.csv"
    with source.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "smiles"]); writer.writeheader()
        writer.writerows([
            {"name":"Aspirin", "smiles":"CC(=O)OC1=CC=CC=C1C(O)=O"},
            {"name":"Caffeine", "smiles":"Cn1c(=O)c2c(ncn2C)n(C)c1=O"},
            {"name":"LongAlkane_Control", "smiles":"CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"},
            {"name":"Invalid_Control", "smiles":"not_a_smiles"},
        ])
    assert len(list(iter_smiles(source))) == 4
    with SessionLocal() as db:
        db.add(ScaleJob(id=test_id, prompt="test RDKit", operation="properties", status="running", progress=5,
            message="test", plan={"operation":"properties"}, options={"top_n":10,"filters":{"ro5_only":True}}, asset_ids=[], count_total=4, count_done=0))
        db.commit()
    chunk = process_properties_chunk_impl(test_id, str(source))
    assert chunk["input_count"] == 4
    assert chunk["valid_count"] == 3
    assert chunk["invalid_count"] == 1
    assert chunk["filtered_count"] < chunk["valid_count"]
    finalize_job_impl([chunk], test_id)
    with SessionLocal() as db:
        job = db.get(ScaleJob, test_id)
        assert job.status == "completed", job.error
        report = json.loads(Path(job.report_path).read_text(encoding="utf-8"))
        assert report["summary"]["input_count"] == 4
        assert report["summary"]["invalid_count"] == 1
        assert report["summary"]["filtered_count"] == chunk["filtered_count"]
        assert report["provenance"]["calculation_mode"] == "real_rdkit_descriptors"
        assert (wd / "all_results.csv").is_file()
        assert Path(job.artifact_zip).is_file()
    # Clean test artifacts but keep actual app database/table schema untouched.
    import shutil
    shutil.rmtree(wd, ignore_errors=True)


def test_missing_docking_inputs_are_not_queued():
    with TestClient(app) as client:
        r = client.post("/api/v2/tasks", json={"prompt":"请用 Vina 做 COX-2 分子对接并输出报告"})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "needs_inputs"
        assert any("PDBQT" in text for text in body["missing_inputs"])

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_gromacs_protocol_bundle_validation_and_stage_order():
    import zipfile, json, shutil
    base = Path(tempfile.mkdtemp(prefix="gmx-bundle-test-"))
    bundle = base / "protocol.zip"
    files = {
        "example/topol.top": "; topology placeholder\n",
        "example/conf.gro": "coordinate placeholder\n",
        "example/em.mdp": "integrator = steep\n",
        "example/nvt.mdp": "integrator = md\n",
        "example/npt.mdp": "integrator = md\n",
        "example/md.mdp": "integrator = md\n",
        "example/protocol.json": json.dumps({"topology":"topol.top", "coordinates":"conf.gro",
            "stages":[{"name":"minimization", "mdp":"em.mdp"}, {"name":"nvt", "mdp":"nvt.mdp"},
                      {"name":"npt", "mdp":"npt.mdp"}, {"name":"production", "mdp":"md.mdp"}]})
    }
    try:
        with zipfile.ZipFile(bundle, "w") as zf:
            for name, content in files.items(): zf.writestr(name, content)
        root = _safe_extract_md_bundle(bundle, base / "extracted")
        topology, coordinates, index_file, stages, protocol = _pick_md_inputs(root)
        assert topology.name == "topol.top"
        assert coordinates.name == "conf.gro"
        assert index_file is None
        assert [s["name"] for s in stages] == ["minimization", "nvt", "npt", "production"]
        assert all(s["mdp"].is_file() for s in stages)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_gromacs_protocol_bundle_rejects_zip_slip():
    import zipfile, shutil
    base = Path(tempfile.mkdtemp(prefix="gmx-zipslip-test-"))
    bundle = base / "bad.zip"
    try:
        with zipfile.ZipFile(bundle, "w") as zf:
            zf.writestr("../escaped.txt", "not allowed")
        try:
            _safe_extract_md_bundle(bundle, base / "out")
        except ValueError as exc:
            assert "非法路径" in str(exc) or "路径越界" in str(exc)
        else:
            raise AssertionError("zip-slip archive was not rejected")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_vina_literature_box_dimensions_can_be_non_cubic():
    req = TaskRequest(prompt="用 Vina 对接", vina_box_size_x=18.0, vina_box_size_y=22.5, vina_box_size_z=20.0)
    assert req.vina_box_size_x == 18.0
    assert req.vina_box_size_y == 22.5
    assert req.vina_box_size_z == 20.0


def test_prepared_pdbqt_ligands_create_chunk_directory(tmp_path, monkeypatch):
    import app.scale.worker as worker
    source = tmp_path / "ligand.pdbqt"
    source.write_text("REMARK test ligand\n", encoding="utf-8")
    monkeypatch.setattr(worker, "_asset_info", lambda aid: {
        "id": aid, "filename": "ligand.pdbqt", "file_type": ".pdbqt", "path": str(source), "size_bytes": source.stat().st_size
    })
    out_dir = tmp_path / "new" / "chunks"
    chunks = _make_prepared_ligand_chunks(["asset-1"], out_dir, 100)
    assert len(chunks) == 1 and Path(chunks[0]).is_file()
    assert "ligand.pdbqt" in Path(chunks[0]).read_text(encoding="utf-8")
