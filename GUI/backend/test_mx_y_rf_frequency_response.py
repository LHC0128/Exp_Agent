"""Mx Y RF 频率响应真实子进程会话、全局互斥、历史与独立分析接口。"""
from dataclasses import replace
from pathlib import Path
from threading import Event
import json
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.models import MxYRFFrequencyResponseParams
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.test_workflow import copy_config
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.test_analysis_stages import build_run
from .jobs import JobManager
from .mx_y_rf_frequency_response import MxYRFFrequencyResponseSessionService, create_router

BASE = "/api/experiments/mx-y-rf-frequency-response"


def wait(predicate):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_y_rf_frequency_response/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] /
        "lab_workflows/experiment_modules/mx_y_rf_frequency_response/workflow.excalidraw").read_bytes())
    manager = JobManager()
    service = MxYRFFrequencyResponseSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_y_rf_frequency_response.test_workflow"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_Y_RF_FREQ_RESP_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service, include_history=True))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.close_communication()
    for job in list(manager.jobs.values()):
        if job.status in ("queued", "running"):
            job.cancellation.cancel()
    wait(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))


def create(client):
    params = replace(MxYRFFrequencyResponseParams(), rf_amplitude_points=2, rf_amplitude_stop_vpp=0.02,
        frequency_points=11, frequency_duration_s=0.016, system_settle_s=0, temp_switch_on_lag_s=0)
    response = client.post(BASE + "/sessions", json={"parameters": params.to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait(lambda: client.service.get(session_id)["status"] in ("waiting", "closed"))
    assert client.service.get(session_id)["status"] == "waiting", client.service.get(session_id)
    return session_id


def steps(client, session_id, ids):
    revision = client.service.get(session_id)["revision"]
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": revision, "step_ids": ids})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    value = client.service.get(session_id)
    assert value["error"] is None, value
    return value


def test_real_child_dependency_revision_lock_refresh_and_finish(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 10 and len(flow["analysis"]) == 3
    from lab_workflows.experiment_modules.mx_y_rf_frequency_response import stages
    declared = stages.definition()
    for column in ("acquisition", "analysis"):
        assert [item["quality_gates"] for item in flow[column]] == [list(item["quality_gates"]) for item in declared[column]]
    assert {item["id"] for item in flow["acquisition"] + flow["analysis"] if item["quality_gates"]} == {
        "A01", "A03", "A08", "B01", "B02"}
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    current = client.get(BASE + "/sessions/active").json()
    assert current["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A08"]})
    assert response.status_code == 422
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(BASE + f"/sessions/{session_id}").json()["revision"] == state["revision"]
    # 新服务不能从磁盘恢复仍带输出的硬件会话。
    assert MxYRFFrequencyResponseSessionService(lambda: client.root, client.manager).active() is None
    state = steps(client, session_id, ["A08", "A09"])
    assert state["demod_mode"] == "response"
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    state = client.service.get(session_id)
    assert state["safety_shutdown"]["completed"]
    assert state["steps"]["A10"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_backend_shutdown_safely_close_real_child(client, method):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    if method == "cancel":
        client.manager.get(state["job_id"]).cancellation.cancel()
    elif method == "eof":
        client.service.close_communication()
    else:
        client.service.shutdown()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(session_id)
    assert final["safety_shutdown"]["completed"]
    assert final["outputs"]["Y RF"] == "OFF"
    config = yaml.safe_load((client.root / "data/Mx_Y_RF_Frequency_Response" / state["run_id"] /
        "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled", "failed")


def test_history_snapshots_missing_results_and_analysis_isolation(client, monkeypatch):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 10)])
    run_id = state["run_id"]
    history = client.get(BASE + "/runs?limit=50&offset=0").json()
    assert history["runs"][0]["run_id"] == run_id
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["FREQUENCY_POINTS"] == 11
    assert summary["analysis_status"] == "not_started"
    assert client.get(BASE + "/runs/not-a-run/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    analysis_state = client.get(f"{BASE}/runs/{run_id}/analysis")
    assert analysis_state.status_code == 200, analysis_state.text
    revision = analysis_state.json()["revision"]
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked_analysis(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30), "测试未释放真实分析子进程"
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked_analysis)
    body = {"revision": revision, "step_ids": ["B01", "B02", "B03"], "analysis_parameters": {"FIT_R_SQUARED_MIN": 0.2}}
    config_path = client.root / "data/Mx_Y_RF_Frequency_Response" / run_id / "experiment_config.yaml"
    snapshot = yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"]
    raw_path = config_path.parent / "raw/frequency_response.npz"
    original_raw = raw_path.read_bytes()
    try:
        job = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert job.status_code == 200, job.text
        job_id = job.json()["id"]
        assert entered.wait(5)
        assert client.manager.hardware_lock.locked(), "独立分析可以在硬件会话等待时运行"
        duplicate = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert duplicate.status_code == 409 and duplicate.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    refreshed = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert refreshed["analysis_status"] == "completed"
    assert refreshed["parameters"]["FIT_R_SQUARED_MIN"] == 0.85
    assert any(name.endswith("frequency_response.png") for name in refreshed["artifacts"])
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"]["FIT_R_SQUARED_MIN"] == 0.2
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == snapshot
    assert raw_path.read_bytes() == original_raw
    stale = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
    assert stale.status_code == 409 and stale.headers["X-Error-Code"] == "revision_conflict"
    comparison_release = Event()
    comparison = client.manager.create("comparison:mx-y-rf-frequency-response", lambda job: comparison_release.wait(30), hardware_required=False)
    try:
        response = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert response.status_code == 409 and response.headers["X-Error-Code"] == "comparison_running"
    finally:
        comparison_release.set()
    wait(lambda: comparison.status == "completed")


def test_real_child_finish_uses_executed_parameters_and_holds_lock_through_cleanup(client, monkeypatch):
    gate = client.root / "shutdown-release"
    monkeypatch.setenv("MX_Y_RF_FREQ_RESP_SHUTDOWN_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 9)])
    config_path = client.root / "data/Mx_Y_RF_Frequency_Response" / state["run_id"] / "experiment_config.yaml"
    parameters = yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"]
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15,
            "FREQUENCY_POINTS": 21}})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert state["parameters"]["FREQUENCY_POINTS"] == 21
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == parameters

    probe_started = Event()
    try:
        response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        wait(lambda: gate.with_suffix(".started").is_file())
        assert client.manager.hardware_lock.locked()
        assert client.service.get(session_id)["status"] != "closed"
        probe = client.manager.create("hardware-probe", lambda job: probe_started.set())
        assert not probe_started.is_set() and probe.status == "queued"
        duplicate = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
        assert duplicate.status_code == 409
    finally:
        gate.touch()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: probe.status == "completed")
    assert probe_started.is_set()
    saved = json.loads(gate.with_suffix(".finished").read_text(encoding="utf-8"))
    assert saved == {"pump_frequency_hz": parameters["PUMP_CARRIER_FREQUENCY_HZ"], "completed": True}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == parameters


@pytest.mark.parametrize("damage", ["unknown_raw", "wrong_identity", "wrong_type", "bad_yaml"])
def test_real_main_and_specialized_history_reject_invalid_format_without_job(client, monkeypatch, damage):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    directory = build_run(client.root)
    config_path = directory / "experiment_config.yaml"
    if damage == "bad_yaml":
        config_path.write_text("parameters: [", encoding="utf-8")
    else:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config[{"unknown_raw": "raw_format_version", "wrong_identity": "experiment_id", "wrong_type": "data_type"}[damage]] = "invalid"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    run_id = directory.name
    assert client.get(f"{BASE}/runs/{run_id}/summary").status_code == 422
    assert client.get(f"{BASE}/runs/{run_id}/analysis").status_code == 422
    assert client.get(BASE + "/runs").json()["runs"] == []
    with TestClient(main.app) as http:
        assert http.get(f"/api/runs?experiment_id={ADAPTER.id}").json()["runs"] == []
        response = http.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": ADAPTER.id})
        assert response.status_code == 422, response.text
        assert http.get(f"/api/runs/{ADAPTER.id}/{run_id}/artifacts/analysis.json").status_code == 422
    assert not client.manager.jobs


def test_real_main_keeps_verified_history_and_running_conflict(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    directory = build_run(client.root, status="running")
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["schema_version"] = 1
    config["experiment_type"] = config.pop("data_type")
    config.pop("experiment_id")
    config.pop("raw_format_version")
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(f"{BASE}/runs/{directory.name}/summary").status_code == 200
    response = client.get(f"{BASE}/runs/{directory.name}/analysis")
    assert response.status_code == 409 and response.headers["X-Error-Code"] == "run_busy"
    with TestClient(main.app) as http:
        assert http.get(f"/api/runs?experiment_id={ADAPTER.id}").json()["runs"][0]["can_analyze"] is False
        response = http.post(f"/api/runs/{directory.name}/analyze", json={"experiment_id": ADAPTER.id})
        assert response.status_code == 409 and response.headers["X-Error-Code"] == "run_busy"
    assert not client.manager.jobs
    config["completion_status"] = "completed"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(f"{BASE}/runs/{directory.name}/analysis").status_code == 200


def test_legacy_nan_report_is_readonly_strict_json_with_all_curves_and_plots(client):
    directory = build_run(client.root, amplitudes=(0.01, 0.02, 0.03, 0.04, 0.05))
    config_path = directory / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["schema_version"] = 1
    config["experiment_type"] = config.pop("data_type")
    config.pop("experiment_id")
    config.pop("raw_format_version")
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    report = {
        "success": True,
        "model": "NaN is an unavailable uncertainty, not a zero",
        "per_amplitude": [dict(amplitude_vpp=amplitude, success=True, split=index >= 2,
            parameters=dict(gamma_hz=373.4749916128946 + index, center_hz=10984.7253789837,
                saturation=0.0 if index < 2 else float(index)), r_squared=0.9966502818113792,
            uncertainties={"gamma_hz": float("nan") if index < 2 else 17.499703036153363},
            relative_gamma_uncertainty=float("nan") if index < 2 else 0.054262563589183584,
            rejection_reasons=[]) for index, amplitude in enumerate((0.01, 0.02, 0.03, 0.04, 0.05))],
        "saturation_scaling": dict(success=True, slope_per_vpp2=2000.0, r_squared=0.98, n_points=5),
    }
    results = directory / "results"
    results.mkdir()
    legacy_path = results / "analysis.json"
    legacy_path.write_text(json.dumps(report), encoding="utf-8")
    for name in ("frequency_response.png", "s_vs_amplitude.png"):
        (results / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    before = {path.relative_to(directory).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*") if path.is_file()}
    legacy_bytes = legacy_path.read_bytes()
    assert b"NaN" in legacy_bytes

    summary = client.get(f"{BASE}/runs/{directory.name}/summary").json()
    raw_report = client.get(summary["artifacts"]["results/analysis.json"])
    assert raw_report.content == legacy_bytes, "下载旧科学产物必须保留原始字节"
    response = client.get(f"{BASE}/runs/{directory.name}/analysis")
    assert response.status_code == 200, response.text
    def reject_constant(value):
        raise AssertionError(f"正规 API 不能返回非标准 JSON 常量：{value}")
    state = json.loads(response.text, parse_constant=reject_constant)
    expected = json.loads(legacy_bytes, parse_constant=lambda value: None)
    assert state["summary"] == expected
    assert len(state["summary"]["per_amplitude"]) == 5
    assert state["summary"]["per_amplitude"][0]["uncertainties"]["gamma_hz"] is None
    assert all(stage["status"] == "pending" for stage in state["steps"].values()), "查看旧结果不能伪造已完成阶段"
    for name in ("frequency_response.png", "s_vs_amplitude.png"):
        image = client.get(summary["artifacts"]["results/" + name])
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        assert image.content == (results / name).read_bytes()
    assert {path.relative_to(directory).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*") if path.is_file()} == before
    assert not client.manager.jobs


@pytest.mark.parametrize("content", ["[]", "null", "{broken json"])
def test_legacy_report_invalid_mapping_or_json_is_explicit_read_error(client, content):
    directory = build_run(client.root)
    results = directory / "results"
    results.mkdir()
    path = results / "analysis.json"
    path.write_text(content, encoding="utf-8")
    response = client.get(f"{BASE}/runs/{directory.name}/analysis")
    assert response.status_code == 422, response.text
    assert "拟合诊断" in response.json()["detail"]
    assert path.read_text(encoding="utf-8") == content
    assert not (directory / "analysis_state.json").exists()
    assert not client.manager.jobs


def test_new_run_without_report_stays_empty_and_readonly(client):
    directory = build_run(client.root)
    response = client.get(f"{BASE}/runs/{directory.name}/analysis")
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == {}
    assert not (directory / "analysis_state.json").exists()
    assert not (directory / "results").exists()
    assert not client.manager.jobs


def test_current_quality_failure_diagnostics_take_precedence_over_legacy_report(client):
    from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis import AnalysisWorkflow
    from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis_steps import AnalysisQualityError
    directory = build_run(client.root, noise=0.02, seed=11)
    workflow = AnalysisWorkflow(directory)
    with pytest.raises(AnalysisQualityError, match="没有任何幅度曲线"):
        workflow.execute(["B01", "B02"])
    expected = workflow.snapshot()["summary"]
    report = directory / "results/analysis.json"
    report.write_text('{"success": true, "per_amplitude": [{"success": true}]}', encoding="utf-8")
    response = client.get(f"{BASE}/runs/{directory.name}/analysis")
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["summary"] == expected
    assert state["summary"]["status"] == "quality_failed"
    assert state["steps"]["B03"]["status"] == "pending"
    assert all(not curve["success"] for curve in state["summary"]["per_amplitude"])
    assert not client.manager.jobs
