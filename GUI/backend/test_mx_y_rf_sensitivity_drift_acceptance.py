"""独立验收 A10 封存、下一次草稿与真实 EOF 的交叉边界。"""
import json
from pathlib import Path

from .test_mx_y_rf_sensitivity_drift import BASE, client, create, steps, through
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.run_format import DATA_TYPE
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.test_session import wait_for


def test_sealed_run_and_executed_pump_survive_new_parameters_then_real_eof(client):
    session_id = create(client)
    state = steps(client, session_id, through("A10"))
    parent = client.root / "data" / DATA_TYPE / state["run_id"]
    stage_path = parent / "acquisition_state.json"
    sealed = json.loads(stage_path.read_text(encoding="utf-8"))
    child = Path(state["drift_records"][0]["child_run_dir"])
    protected = {path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in [parent / "experiment_config.yaml", *child.rglob("*")] if path.is_file()}
    directories = {path for path in (client.root / "data").glob("*/*") if path.is_dir()}

    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={
        "revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99000000., "DRIFT_INTERVAL_S": 2.},
    })
    assert response.status_code == 200, response.text
    wait_for(lambda: client.service.get(session_id)["pending_request"] is None)
    assert json.loads(stage_path.read_text(encoding="utf-8")) == sealed

    client.service.close_communication()
    wait_for(lambda: not client.manager.hardware_lock.locked())
    final = client.get(f"{BASE}/sessions/{session_id}").json()
    saved = json.loads(stage_path.read_text(encoding="utf-8"))
    assert final["status"] == saved["status"] == "closed"
    assert saved["parameters"] == sealed["parameters"]
    assert saved["drift_records"] == sealed["drift_records"]
    assert saved["steps"]["A10"] == sealed["steps"]["A10"]
    assert saved["steps"]["A10"]["status"] == saved["steps"]["A11"]["status"] == "completed"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected} == protected
    assert {path for path in (client.root / "data").glob("*/*") if path.is_dir()} == directories
    trace = json.loads((client.root / "test-instrument-events.json").read_text(encoding="utf-8"))
    assert trace["safe"]
    assert trace["pump_frequency_hz"] == sealed["parameters"]["PUMP_CARRIER_FREQUENCY_HZ"]
