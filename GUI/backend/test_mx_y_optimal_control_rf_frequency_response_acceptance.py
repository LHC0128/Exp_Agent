"""真实main路由的双向分析互斥与专属摘要兼容验收。"""
from threading import Event
import pytest
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.test_analysis import build_run
from .test_mx_y_optimal_control_rf_frequency_response import client, wait, BASE, ADAPTER


@pytest.mark.parametrize("first", ["stages", "legacy"])
def test_real_main_analysis_mutex_both_directions_and_original_summary_keys(client, monkeypatch, first):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    directory = build_run(client.root)
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked(module, **kwargs):
        assert kwargs.get("run_dir") == directory
        entered.set()
        assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    with TestClient(main.app) as http:
        revision = client.get(f"{BASE}/runs/{directory.name}/analysis").json()["revision"]
        staged = f"{BASE}/runs/{directory.name}/analysis"
        legacy = f"/api/runs/{directory.name}/analyze"
        staged_body = {"revision": revision, "step_ids": ["B01", "B02", "B03"], "analysis_parameters": {}}
        legacy_body = {"experiment_id": ADAPTER.id}
        try:
            response = (client.post(staged, json=staged_body) if first == "stages"
                else http.post(legacy, json=legacy_body))
            assert response.status_code == 200, response.text
            job = client.manager.get(response.json()["id"])
            assert entered.wait(5)
            assert not client.manager.hardware_lock.locked()
            other = (http.post(legacy, json=legacy_body) if first == "stages"
                else client.post(staged, json=staged_body))
            assert other.status_code == 409 and other.headers["X-Error-Code"] == "analysis_running"
        finally:
            release.set()
        wait(lambda: job.status in ("completed", "failed"))
        assert job.status == "completed", job.error
        summary = http.get(f"{BASE}/runs/{directory.name}/summary")
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["control_source"] == {"run": "frozen", "amplitude_vpp": .2, "offset_v": 0.}
        assert {"r_phase_frequency.png", "rf_frequency_response_comparison.png"} <= body["artifacts"].keys()
        assert body["optimal_response_metadata"]["response_median_v"] == [2., 5., 8.]
        assert body["constant_response_metadata"]["response_v"] == [.1, .2, .3]
        assert client.get(f"{BASE}/runs/{directory.name}/analysis").json()["options"] == {}
