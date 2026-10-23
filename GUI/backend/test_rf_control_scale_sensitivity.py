"""实际 FastAPI 路由、历史快照及分阶段分析；不连接仪器。"""
from pathlib import Path
import json
import shutil
from unittest.mock import Mock
import pytest
import yaml
from fastapi.testclient import TestClient
from GUI.backend import main
from lab_workflows.experiment_modules.rf_control_scale_sensitivity import stages, run_format
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.test_analysis import synthetic_run

BASE = "/api/experiments/" + run_format.EXPERIMENT_ID


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.manager, "has_active_kind", lambda _: False)
    relative = Path("lab_workflows/experiment_modules/rf_control_scale_sensitivity/RF Control Scale.excalidraw")
    (tmp_path / relative).parent.mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[2] / relative, tmp_path / relative)
    return TestClient(main.app)


def test_real_history_files_missing_results_and_paths(client, tmp_path):
    path = synthetic_run(tmp_path / "data" / run_format.DATA_TYPE / "1014_120000_scale")
    before = (path / "experiment_config.yaml").read_bytes()
    response = client.get(BASE + "/runs?limit=50&offset=0")
    assert response.status_code == 200 and response.json()["total"] == 1
    summary = client.get(BASE + f"/runs/{path.name}/summary").json()
    assert summary["parameters"]["CONTROL_SCALES"] == [.9, 1., 1.1]
    assert summary["artifacts"] == {}
    (path / "results/metrics.csv").write_text("scale,gain\n1,2\n", encoding="utf-8")
    url = client.get(BASE + f"/runs/{path.name}/summary").json()["artifacts"]["results/metrics.csv"]
    assert client.get(url).text.startswith("scale,gain")
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/bad%5Cname/summary").status_code in (400, 404, 422)
    assert client.get(BASE + f"/runs/{path.name}/files/../../../outside").status_code in (404, 422)
    assert (path / "experiment_config.yaml").read_bytes() == before


def test_flow_schema_and_all_fields_have_editors(client):
    response = client.get(BASE + "/workflow")
    assert response.status_code == 200
    flow = response.json()
    assert len(flow["acquisition"]) == 10 and len(flow["analysis"]) == 4
    from lab_workflows.experiment_modules.rf_control_scale_sensitivity.definition import ADAPTER
    visible = {f["name"] for f in ADAPTER.schema()["fields"]}
    displayed = {k for s in flow["acquisition"] + flow["analysis"] for k in s["display_parameters"]}
    assert visible <= displayed
    assert "SLOPE_FIT_POINTS" not in visible
    assert "SLOPE_FIT_POINTS" not in flow["parameter_impacts"]["analysis"]
    assert flow["parameter_impacts"]["analysis"]["NOISE_OUTLIER_POLICY"] == ["B03", "B04"]
    assert flow["parameter_impacts"]["analysis"]["SENSITIVITY_BAND_MAX_HZ"] == ["B03", "B04"]
    assert "色散" in client.get(BASE).json()["description"]
    assert {"CONTROL_SCALES", "CONTROL_SCALE_START", "CONTROL_SCALE_STOP", "CONTROL_SCALE_POINTS",
            "CONTROL_SCALE_FACTORS"} <= flow["parameter_impacts"]["acquisition"].keys()
    point = next(stage for stage in flow["acquisition"] if stage["id"] == "A13")
    assert point["dependencies"] == ["A07"]
    assert {"CONTROL_WAVEFORM_FILES", "CONTROL_SCALES", "DEMOD0_TC_CALIB", "XY_CTRL_PHASE", "Z_PHASE_SCAN_STEP_DEG"} <= set(point["display_parameters"])
    assert not {"A08", "A09", "A10", "A11", "A12"} & {stage["id"] for stage in flow["acquisition"]}
    description = client.get(BASE).json()["description"]
    assert "手动相位" in description and "其余校相不跨点继承" in description
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    assert client.get(BASE + "/sessions/active").json() is None
    assert main.rf_control_scale_sessions in main._workflow_session_services()
    fixture = Path(__file__).resolve().parents[1] / "frontend/src/pages/rfControlScaleWorkflow.fixture.json"
    assert json.loads(fixture.read_text(encoding="utf-8")) == flow


def test_derive_normalizes_legacy_and_range_without_hardware(client):
    legacy = client.post(BASE + "/derive", json={"parameters": {"CONTROL_SCALES": [3., 0., -2.]}})
    assert legacy.status_code == 200
    parameters = legacy.json()["values"]["parameters"]
    assert parameters["CONTROL_SCALES"] == [3., 0., -2.]
    assert parameters["CONTROL_SCALE_START"] is None
    assert parameters["CONTROL_SCALE_FACTORS"] == {}
    response = client.post(BASE + "/derive", json={"parameters": parameters | {
        "CONTROL_SCALE_START": 2., "CONTROL_SCALE_STOP": -2., "CONTROL_SCALE_POINTS": 3,
        "CONTROL_SCALE_FACTORS": {"ControlA": 1.25}}})
    assert response.status_code == 200
    resolved = response.json()["values"]["parameters"]
    assert resolved["CONTROL_SCALES"] == [2., 0., -2.]
    assert resolved["CONTROL_SCALE_FACTORS"] == {"ControlA": 1.25}


def test_reanalysis_and_directory_overrides(client, tmp_path, monkeypatch):
    path = synthetic_run(tmp_path / "data" / run_format.DATA_TYPE / "1014_120000_scale")
    jobs = []
    def create(kind, target, **kwargs):
        jobs.append(kind)
        job = Mock()
        job.public.return_value = dict(id="analysis", kind=kind, status="queued")
        return job
    monkeypatch.setattr(main.manager, "create", create)
    response = client.post(f"/api/runs/{path.name}/analyze", json={"experiment_id": run_format.EXPERIMENT_ID})
    assert response.status_code == 200 and run_format.EXPERIMENT_ID in jobs[-1]
    state = client.get(BASE + f"/runs/{path.name}/analysis").json()
    response = client.post(BASE + f"/runs/{path.name}/analysis", json={
        "revision": state["revision"], "step_ids": ["B01", "B02"],
        "analysis_parameters": {"SENSITIVITY_BAND_MAX_HZ": 120, "NOISE_OUTLIER_POLICY": "none"}})
    assert response.status_code == 200
    assert yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]["SLOPE_FIT_POINTS"] == 13
    # 这里只验证入队；任务执行前不提前提交目录覆盖值。
    assert client.get(BASE + f"/runs/{path.name}/analysis").json()["options"]["noise_outlier_policy"] == "mad_high"
    invalid = client.post(BASE + f"/runs/{path.name}/analysis", json={"revision": state["revision"], "step_ids": ["B04"]})
    assert invalid.status_code == 422


def test_session_revision_dependencies_finish_and_hardware_queue(client, monkeypatch):
    service = main.rf_control_scale_sessions
    job = Mock(status="running")
    job.cancellation.cancelled = False
    monkeypatch.setattr(main.manager, "get", lambda _: job)
    from queue import Queue
    state = dict(session_id="fake", job_id="fake-job", revision=2, status="waiting",
                 pending_request=None, parameters={}, steps=stages.initial_states(stages.ACQUISITION))
    service.sessions["fake"] = {"state": state, "queue": Queue()}
    try:
        assert client.post(BASE + "/sessions/fake/steps", json={"revision": 1, "step_ids": ["A01"]}).status_code == 409
        assert client.post(BASE + "/sessions/fake/steps", json={"revision": 2, "step_ids": ["A13"]}).status_code == 422
        assert client.post(BASE + "/sessions/fake/finish", json={"revision": 2}).status_code == 200
        assert service.sessions["fake"]["queue"].get_nowait()["action"] == "finish"
        assert client.post(BASE + "/sessions/fake/finish", json={"revision": 2}).status_code == 409
    finally:
        service.sessions.pop("fake", None)
