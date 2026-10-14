"""已知噪声实验历史、流程与重分析入口无硬件验证。"""
from unittest.mock import Mock
from pathlib import Path
import shutil
from fastapi.testclient import TestClient
import pytest
import yaml
from GUI.backend import main
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise import run_format
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.definition import ADAPTER

BASE = "/api/experiments/" + run_format.EXPERIMENT_ID


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.manager, "has_active_kind", lambda _: False)
    relative = Path("lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/RF 已知噪声灵敏度.excalidraw")
    (tmp_path / relative).parent.mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[2] / relative, tmp_path / relative)
    return TestClient(main.app)


def create_run(root):
    path = root / "data" / run_format.DATA_TYPE / "1014_120000_known"
    (path / "raw").mkdir(parents=True)
    (path / "results").mkdir()
    config = dict(experiment_id=run_format.EXPERIMENT_ID, schema_version=2,
        execution_mode="typed_workflow", acquisition_method=run_format.ACQUISITION_METHOD,
        parameters={"RUN_TAG": "saved", "CONTROL_WAVEFORM_FILES": ["v9/waveforms/optimal_control_waveform.csv"]},
        completion_status="completed", phase_calibration_status="completed",
        xy_phase_calibration={"converged": True}, analysis_status="not_started")
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_history_summary_missing_results_and_invalid_ids(client, tmp_path):
    path = create_run(tmp_path)
    before = (path / "experiment_config.yaml").read_bytes()
    page = client.get(BASE + "/runs").json()
    assert page["total"] == 1
    assert page["runs"][0]["run_id"] == path.name
    summary = client.get(BASE + f"/runs/{path.name}/summary").json()
    assert summary["parameters"]["RUN_TAG"] == "saved"
    assert summary["artifacts"] == {}
    assert summary["can_analyze"] is True
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/bad%5Cname/summary").status_code in (400, 404, 422)
    assert before == (path / "experiment_config.yaml").read_bytes()
    generic = client.get("/api/runs", params={"experiment_id": run_format.EXPERIMENT_ID}).json()
    assert generic["total"] == 1


def test_workflow_download_and_schema_are_hardware_free(client):
    flow = client.get(BASE + "/workflow")
    assert flow.status_code == 200
    assert len(flow.json()["acquisition"]) == 17
    assert "CONTROL_WAVEFORM_FILES" in flow.json()["parameter_impacts"]["acquisition"]
    diagram = client.get(BASE + "/workflow/diagram")
    assert diagram.status_code == 200
    assert diagram.json()["type"] == "excalidraw"
    assert client.get(BASE + "/sessions/active").json() is None


def test_reanalysis_route_uses_own_adapter(client, tmp_path, monkeypatch):
    path = create_run(tmp_path)
    job = Mock(id="analysis", status="queued", kind=f"analyze:{run_format.EXPERIMENT_ID}")
    def create(kind, target, **kwargs):
        job.kind = kind
        job.public.return_value = {"id": "analysis", "kind": kind, "status": "queued"}
        return job
    monkeypatch.setattr(main.manager, "create", create)
    response = client.post(f"/api/runs/{path.name}/analyze", json={"experiment_id": run_format.EXPERIMENT_ID})
    assert response.status_code == 200
    assert response.json()["id"] == "analysis"
    assert run_format.EXPERIMENT_ID in job.kind
