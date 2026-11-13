"""T2 样板实验的注册表驱动历史接线；只读假目录，不连接硬件。"""

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab_workflows.experiment_modules.t2_calibration import run_format
from lab_workflows.experiment_modules.t2_calibration.definition import DEFINITION
from .t2_calibration import T2SessionService, create_router

BASE = "/api/experiments/t2-calibration"


@pytest.fixture
def client(tmp_path):
    service = T2SessionService(lambda: tmp_path, None)
    app = FastAPI()
    app.include_router(create_router(service))
    return TestClient(app), tmp_path / "data" / service.adapter.data_type


def save_run(base, name, **updates):
    path = base / name
    path.mkdir(parents=True)
    config = dict(experiment_id="t2-calibration", timestamp=name, run_tag="原参数",
                  completion_status="completed", parameters={"RUN_TAG": "历史值"})
    config.update(updates)
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_history_identity_comes_from_definition_not_main_import():
    from . import main

    assert DEFINITION.run_history.run_format is run_format
    assert DEFINITION.run_history.historical_parameters is run_format.historical_parameters
    assert main.WORKFLOW_RUN_FORMATS["t2-calibration"] is DEFINITION.run_history.run_format
    assert not hasattr(main, "t2_format"), "样板实验不得再在 main.py 保留 run_format 导入"
    assert "run_history" not in DEFINITION.public(), "实验卡片响应形状不变"


def test_gate_keeps_identity_and_running_rules(client):
    http, base = client
    save_run(base, "1001_done")
    save_run(base, "1002_running", completion_status="running")
    save_run(base, "1003_other", experiment_id="t1-calibration")
    save_run(base, "1004_versioned", raw_format_version=1)

    listed = http.get(BASE + "/runs").json()
    assert [item["run_id"] for item in listed["runs"]] == ["1002_running", "1001_done"]
    assert listed["total"] == 2
    assert http.get(BASE + "/runs/1003_other/summary").status_code == 422
    assert http.get(BASE + "/runs/1004_versioned/summary").status_code == 422
    assert http.get(BASE + "/runs/1002_running/summary").status_code == 200
    assert http.get(BASE + "/runs/1002_running/analysis").status_code == 409


def test_summary_migrates_legacy_snapshot_and_keeps_response_shape(client):
    http, base = client
    run = save_run(base, "1005_legacy", run_tag="旧运行",
                   fixed_params={"laser_power": 1.5}, pump_power_V=2.0, rf_gate_freq_Hz=90000.0,
                   scope={"sample_rate_Hz": 400000.0})
    config_path = run / "experiment_config.yaml"
    legacy = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    legacy.pop("parameters")
    config_path.write_text(yaml.safe_dump(legacy, allow_unicode=True), encoding="utf-8")
    (run / "results").mkdir()
    (run / "results" / "T2_results.yaml").write_text("t2_ms: 2.0\n", encoding="utf-8")

    summary = http.get(BASE + "/runs/1005_legacy/summary").json()
    assert set(summary) == {"run_id", "timestamp", "run_tag", "completion_status",
                            "analysis_status", "analysis_error", "parameters", "artifacts"}
    assert summary["parameters"]["RUN_TAG"] == "旧运行"
    assert summary["parameters"]["PUMP_POWER"] == 2.0
    assert summary["parameters"]["FIXED_PARAMS.laser_power"] == 1.5
    assert summary["parameters"]["RF_GATE_FREQ"] == 90000.0
    assert summary["parameters"]["HF2_OSC_FREQ"] == 90000.0
    assert summary["parameters"]["SCOPE_SAMPLE_RATE"] == 400000.0
    assert set(summary["artifacts"]) == {"results/T2_results.yaml"}
    url = summary["artifacts"]["results/T2_results.yaml"]
    assert url.startswith(f"{BASE}/runs/1005_legacy/files/results/T2_results.yaml?v=")
    assert http.get(url).content == (run / "results" / "T2_results.yaml").read_bytes()


def test_runs_pagination_bounds_and_page_keys(client):
    http, base = client
    save_run(base, "1006_a")
    save_run(base, "1007_b")

    page = http.get(BASE + "/runs", params={"limit": 1, "offset": 1}).json()
    assert set(page) == {"runs", "total"}
    assert page["total"] == 2 and [item["run_id"] for item in page["runs"]] == ["1006_a"]
    assert http.get(BASE + "/runs", params={"limit": 500}).status_code == 200
    assert http.get(BASE + "/runs", params={"limit": 501}).status_code == 422
    assert http.get(BASE + "/runs", params={"limit": 0}).status_code == 422
    assert http.get(BASE + "/runs", params={"offset": -1}).status_code == 422