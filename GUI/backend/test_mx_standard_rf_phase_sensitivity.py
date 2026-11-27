"""在主应用真路由上验证标准 Mx 射频相位灵敏度接线；仅子进程执行用替身。"""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from lab_workflows.common import DATA_ROOT_ENV, data_root
from lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity import run_format
from lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity.definition import ADAPTER, DEFINITION
from lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity.models import (
    MxStandardRfPhaseSensitivityParams,
)
from lab_workflows.setups import SETUP_ENV_VAR

from . import main
from .jobs import JobManager

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "GUI/frontend/src/pages/MxStandardRfPhaseSensitivityPage.fixture.json"
ID = "mx-standard-rf-phase-sensitivity"
BASE = f"/api/experiments/{ID}"
PEER = "/api/experiments/rf-phase-sensitivity"
ACQUISITION = ["A01", "A02", "A03", "A04", "A05", "A06", "A07"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_dir = tmp_path / "params"
    config_dir.mkdir()
    for filename in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copy2(ROOT / "params" / filename, config_dir / filename)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(SETUP_ENV_VAR, raising=False)
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path / "fake_runs"))
    monkeypatch.setattr(ADAPTER, "root", tmp_path)
    monkeypatch.setattr(main, "ROOT", tmp_path)

    manager = JobManager()
    service = main.mx_standard_rf_phase_sensitivity_sessions
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setattr(service, "manager", manager)
    monkeypatch.setattr(service, "sessions", {})

    def unexpected_execution(*args, **kwargs):
        raise AssertionError("API 测试不能启动真实采集或分析子进程")

    monkeypatch.setattr(ADAPTER, "_execute", unexpected_execution)
    http = TestClient(main.app)
    try:
        yield http
    finally:
        service.close_communication()
        manager.wait_for_shutdown()
        http.close()


def test_discovery_schema_workflow_and_real_preflight(client):
    service = main.mx_standard_rf_phase_sensitivity_sessions
    assert service in main._workflow_session_services()
    assert service.run_history is DEFINITION.run_history
    assert service.run_history.historical_parameters is run_format.recorded_parameters
    # 历史资格由定义声明，WORKFLOW_RUN_FORMATS 由注册表按 run_history 自动派生。
    assert main.WORKFLOW_RUN_FORMATS[ID] is run_format

    catalog = client.get("/api/experiments")
    assert catalog.status_code == 200, catalog.text
    entries = {item["id"]: item for item in catalog.json()}
    definition = entries[ID]
    assert definition["title"] == "Mx 标准射频相位灵敏度测量"
    assert definition["execution_mode"] == "typed_workflow" and definition["can_analyze"]
    assert definition["data_type"] == run_format.DATA_TYPE == "mx_standard_rf_phase_sensitivity"
    assert definition["data_type"] != entries["rf-phase-sensitivity"]["data_type"]

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = client.get(f"{BASE}/schema")
    flow = client.get(f"{BASE}/workflow")
    assert schema.status_code == flow.status_code == 200
    assert schema.json()["fields"] == fixture["fields"]
    manifest = flow.json()
    assert manifest == fixture["flow"]
    assert [stage["id"] for stage in manifest["acquisition"]] == ACQUISITION
    assert [stage["id"] for stage in manifest["analysis"]] == ["B01"]
    assert (manifest["run_step_id"], manifest["finish_step_id"]) == ("A06", "A07")
    assert manifest["diagram_url"] == f"{BASE}/workflow/diagram"

    parameters = {"Y_RF_VPP": 0.2, "PM_FREQUENCIES_HZ": [10.0, 20.0, 40.0]}
    response = client.post(f"{BASE}/preflight", json={"parameters": parameters})
    assert response.status_code == 200 and response.json()["ok"], response.text
    for bad in ({"Y_RF_VPP": 21.0}, {"PHASE_GRID_STEP_DEG": 7.0}, {"DEMOD_IDX": 0},
                {"PM_FREQUENCIES_HZ": [40.0, 20.0]}, {"DEMOD_DYNAMIC_RATE": 300.0}):
        invalid = parameters | bad
        response = client.post(f"{BASE}/preflight", json={"parameters": invalid})
        assert response.status_code == 200 and not response.json()["ok"], response.text
        assert response.json()["errors"]
        assert client.post(f"{BASE}/runs", json={"parameters": invalid}).status_code == 422
    assert client.get(f"{BASE}/sessions/active").json() is None
    assert client.get("/api/jobs").json() == []
    assert not data_root(main.ROOT).exists()


def test_gs200_current_follows_ui_calibration_parameters(client):
    fields = {field["name"]: field for field in client.get(f"{BASE}/schema").json()["fields"]}
    for name in ("Y_RF_FREQ_HZ", "CAL_K_HZ_PER_MA", "CAL_B_HZ"):
        assert fields[name]["read_only"] is False
    cases = ((5000.0, 11089.0, 219.7), (4000.0, 11089.0, 219.7), (5000.0, 12000.0, 0.0))
    for freq, k, b in cases:
        parameters = {"Y_RF_FREQ_HZ": freq, "CAL_K_HZ_PER_MA": k, "CAL_B_HZ": b}
        expected = (freq - b) / k
        response = client.post(f"{BASE}/derive", json={"parameters": parameters})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"]
        assert payload["values"]["main_field_current_ma"] == pytest.approx(expected)
        params = MxStandardRfPhaseSensitivityParams(
            y_rf_freq_hz=freq, cal_k_hz_per_ma=k, cal_b_hz=b)
        assert params.main_field_current_ma == pytest.approx(expected)
        assert params.phase_grid_deg[1] - params.phase_grid_deg[0] == pytest.approx(
            params.phase_grid_step_deg)
    assert fields["CAL_K_HZ_PER_MA"]["default"] == pytest.approx(11089.0)
    assert fields["CAL_B_HZ"]["default"] == pytest.approx(219.7)


def _history_run(name, *, patch=None):
    directory = data_root(main.ROOT) / run_format.DATA_TYPE / name
    directory.mkdir(parents=True)
    params = MxStandardRfPhaseSensitivityParams(run_tag=name)
    config = run_format.initial_metadata(params) | {
        "timestamp": "2026-11-27T15:00:00", "run_tag": name, "completion_status": "completed",
        "schema_version": params.schema_version, "parameters": params.to_external(),
    } | (patch or {})
    (directory / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return directory / "experiment_config.yaml"


def test_history_lists_only_own_format_and_serves_artifacts(client):
    archived = _history_run("history")
    _history_run("wrong_version", patch={"raw_format_version": run_format.RAW_FORMAT_VERSION + 1})
    _history_run("other_experiment",
                 patch={"experiment_id": "rf-phase-sensitivity", "data_type": "rf_phase_sensitivity"})
    _history_run("running", patch={"completion_status": "running"})
    summary_name = f"results/{run_format.SUMMARY}"
    artifact = archived.parent / summary_name
    artifact.parent.mkdir()
    artifact.write_bytes(b'{"phase_sensitivity": "synthetic"}')
    before = archived.read_bytes(), artifact.read_bytes()

    history = client.get(f"{BASE}/runs?limit=20&offset=0")
    assert history.status_code == 200, history.text
    assert {item["run_id"] for item in history.json()["runs"]} == {"history", "running"}

    response = client.get(f"{BASE}/runs/history/summary")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["parameters"] == MxStandardRfPhaseSensitivityParams(run_tag="history").to_external()
    assert summary_name in summary["artifacts"]
    served = client.get(summary["artifacts"][summary_name])
    assert served.status_code == 200 and served.content == b'{"phase_sensitivity": "synthetic"}'
    assert client.get(f"{BASE}/runs/wrong_version/summary").status_code == 422
    assert client.get(f"{BASE}/runs/other_experiment/summary").status_code == 422
    assert client.get(f"{BASE}/runs/running/analysis").status_code == 409
    assert (archived.read_bytes(), artifact.read_bytes()) == before
