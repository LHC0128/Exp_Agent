"""RF DirectAW 新格式历史列表、摘要、路径与重新分析接口。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import yaml

from GUI.backend import main
from lab_workflows import control_sources
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.models import RFSensitivityDirectAWParams
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.definition import ADAPTER
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.run_format import DATA_TYPE, EXPERIMENT_ID
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_workflow import rig, control_source

BASE = f"/api/experiments/{EXPERIMENT_ID}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.manager, "has_active_kind", lambda kind: False)
    return TestClient(main.app)


def create_run(root, name="1009_120000_test", **changes):
    directory = root / "data" / DATA_TYPE / name
    directory.mkdir(parents=True)
    config = {
        "experiment_id": EXPERIMENT_ID, "schema_version": 2, "execution_mode": "typed_workflow", "acquisition_method": "direct_aw_x",
        "started_at": name, "run_tag": "snapshot",
        "parameters": replace(RFSensitivityDirectAWParams(), run_tag="snapshot", xy_trigger_freq=200, z_trigger_freq=200).to_external(),
        "completion_status": "completed", "phase_calibration_status": "completed", "xy_phase_calibration": {"converged": True},
        "analysis_status": "not_started", "safety_shutdown": {"completed": True}, "actual_rates": {"demod3_noise_Sa_s": 90000},
    }
    config.update(changes)
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return directory


def test_history_filters_before_pagination(client, tmp_path):
    create_run(tmp_path, "1009_110000_older")
    create_run(tmp_path, "1009_120000_failed", completion_status="failed", failure_reason="XY 校相失败", phase_calibration_status="failed", xy_phase_calibration={"converged": False})
    create_run(tmp_path, "1009_130000_legacy", schema_version=1)
    create_run(tmp_path, "1009_140000_other", experiment_id="other-experiment")
    page = client.get(f"{BASE}/runs?limit=1&offset=1").json()
    assert page["total"] == 2
    assert page["runs"][0]["run_id"] == "1009_110000_older"
    generic = client.get(f"/api/runs?experiment_id={EXPERIMENT_ID}").json()
    assert generic["total"] == 2
    assert next(item for item in generic["runs"] if "failed" in item["id"])["can_analyze"] is False


def test_snapshot_and_missing_results_are_read_only(client, tmp_path, monkeypatch):
    directory = create_run(tmp_path)
    before = (directory / "experiment_config.yaml").read_bytes()
    monkeypatch.setattr(main._experiment_or_404(EXPERIMENT_ID), "schema_provider", lambda: (_ for _ in ()).throw(AssertionError("不得读取当前默认值")))
    response = client.get(f"{BASE}/runs/{directory.name}/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["parameters"]["RUN_TAG"] == "snapshot"
    assert data["parameters"]["XY_TRIGGER_FREQ"] == data["parameters"]["Z_TRIGGER_FREQ"] == 200
    assert data["analysis"] is None
    assert data["artifacts"] == {}
    assert data["can_analyze"] is True
    assert data["actual_rates"]["demod3_noise_Sa_s"] == 90000
    assert before == (directory / "experiment_config.yaml").read_bytes()


def test_summary_reads_result_artifacts(client, tmp_path):
    directory = create_run(tmp_path, analysis_status="completed")
    results = directory / "results"
    results.mkdir()
    (results / "analysis.yaml").write_text(yaml.safe_dump({"response_fit": {"slope_V_per_nT": 0.01, "success": False, "method": "numeric_gradient"}}), encoding="utf-8")
    (results / "full_analysis.png").write_bytes(b"test image")
    data = client.get(f"{BASE}/runs/{directory.name}/summary").json()
    assert data["analysis"]["response_fit"]["method"] == "numeric_gradient"
    url = data["artifacts"]["full_analysis.png"]
    assert "?v=" in url
    assert client.get(url).content == b"test image"


def test_summary_uses_recorded_rf_route_and_index(client, tmp_path):
    chain = "Demod0 Y -> AuxOut4 -> internal -> RF demodulator (index 2) X"
    params = replace(RFSensitivityDirectAWParams(), demod3_idx=2, rf_input_route="internal", rf_aux_output_idx=3).to_external()
    directory = create_run(tmp_path, parameters=params, signal_chain=chain)
    before = (directory / "experiment_config.yaml").read_bytes()
    data = client.get(f"{BASE}/runs/{directory.name}/summary").json()
    assert data["signal_chain"] == chain
    assert data["parameters"]["DEMOD3_IDX"] == 2
    assert data["parameters"]["RF_AUX_OUTPUT_IDX"] == 3
    assert before == (directory / "experiment_config.yaml").read_bytes()


@pytest.mark.parametrize("changes", [{"schema_version": 1}, {"completion_status": "running"}, {"completion_status": "cancelled"}, {"phase_calibration_status": "failed", "xy_phase_calibration": {"converged": False}}])
def test_ineligible_runs_do_not_start_analysis(client, tmp_path, monkeypatch, changes):
    directory = create_run(tmp_path, **changes)
    create = Mock()
    monkeypatch.setattr(main.manager, "create", create)
    response = client.post(f"/api/runs/{directory.name}/analyze", json={"experiment_id": EXPERIMENT_ID})
    assert response.status_code == 400
    assert create.call_count == 0
    summary = client.get(f"{BASE}/runs/{directory.name}/summary")
    if changes.get("schema_version") == 1:
        assert summary.status_code == 404
    else:
        assert summary.json()["can_analyze"] is False
        assert summary.json()["analysis_block_reason"]


def test_reanalysis_uses_existing_nonhardware_job(client, tmp_path, monkeypatch):
    directory = create_run(tmp_path)
    create = Mock(return_value=SimpleNamespace(public=lambda: {"id": "analysis-job", "status": "queued"}))
    monkeypatch.setattr(main.manager, "create", create)
    response = client.post(f"/api/runs/{directory.name}/analyze", json={"experiment_id": EXPERIMENT_ID})
    assert response.status_code == 200
    assert response.json()["id"] == "analysis-job"
    assert create.call_args.args[0] == f"analysis:{EXPERIMENT_ID}:{directory.name}"
    assert create.call_args.kwargs["hardware_required"] is False


def test_paths_and_unknown_runs_are_rejected(client, tmp_path):
    directory = create_run(tmp_path)
    (directory / "results").mkdir()
    assert client.get(f"{BASE}/runs/missing/summary").status_code == 404
    assert client.get(f"{BASE}/runs/..%5Coutside/summary").status_code == 400
    assert client.get(f"/api/runs/{EXPERIMENT_ID}/{directory.name}/artifacts/..%5Cexperiment_config.yaml").status_code == 404
    assert client.post("/api/runs/missing/analyze", json={"experiment_id": EXPERIMENT_ID}).status_code == 404


def test_gui_description_matches_signal_chain(client):
    response = client.get(BASE)
    assert response.status_code == 200
    data = response.json()
    assert "时序信号2" in data["description"]
    assert "RF 解调器 X" in data["description"]
    assert "dg_am" not in data["description"]


def test_schema_lists_oc_sens_versions_without_choosing_a_default(client, tmp_path, monkeypatch, rig):
    # 隔离用户通过 GUI 保存的默认版本，只验证空默认值时不会自动选择。
    monkeypatch.setattr(ADAPTER, "defaults", lambda: RFSensitivityDirectAWParams())
    monkeypatch.setattr(ADAPTER, "root", rig.tmp_path)
    directory = tmp_path / "theory_agent/simulate/results/oc_sens"
    waveform = directory / "v8/waveforms/optimal_control_waveform.csv"
    waveform.parent.mkdir(parents=True)
    waveform.write_text("# time_s,Omega_ctrl_Hz\n0,1\n0.00005,2\n", encoding="utf-8")
    monkeypatch.setitem(control_sources.CONTROL_SOURCE_SET_ROOTS, "oc_sens", directory)
    response = client.get(f"{BASE}/schema")
    assert response.status_code == 200
    field = next(item for item in response.json()["fields"] if item["name"] == "CONTROL_WAVEFORM_FILES")
    assert field["default"] is None and field["type"] == "array"
    assert field["options"] == [{"value": "v8/waveforms/optimal_control_waveform.csv", "label": "v8"}]
    assert not any(item["name"] == "ARB_WAVEFORM_FILE" for item in response.json()["fields"])
    response = client.post(f"{BASE}/preflight", json={"parameters": {"ARB_WAVEFORM_FILE": ""}})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "请选择" in "；".join(response.json()["errors"])


def comparison_sources(root):
    from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_comparison import result_run
    return [result_run(root / "data" / DATA_TYPE / name) for name in ("first", "second")]


def test_comparison_availability_is_read_only(client, tmp_path):
    comparison_sources(tmp_path)
    data = client.get(f"{BASE}/runs").json()
    assert all(item["can_compare"] and not item["comparison_block_reason"] for item in data["runs"])
    assert client.get(f"{BASE}/runs/first/summary").json()["can_compare"]
    (tmp_path / "data" / DATA_TYPE / "second/results/sensitivity.npz").unlink()
    data = client.get(f"{BASE}/runs/second/summary").json()
    assert not data["can_compare"] and "缺少" in data["comparison_block_reason"]


def test_comparison_uses_nonhardware_job_and_normalized_legends(client, tmp_path, monkeypatch):
    comparison_sources(tmp_path)
    create = Mock(return_value=SimpleNamespace(public=lambda: {"id": "compare-job", "status": "queued"}))
    monkeypatch.setattr(main.manager, "create", create)
    runner = Mock(return_value={"runs": [], "warnings": []})
    monkeypatch.setattr(main, "rf_direct_aw_comparison_runner", runner)
    response = client.post(f"{BASE}/compare", json={"runs": [{"run_id": "first", "legend": " Control A "}, {"run_id": "second"}]})
    assert response.status_code == 200
    assert create.call_args.args[0] == f"comparison:{EXPERIMENT_ID}"
    assert create.call_args.kwargs["hardware_required"] is False
    current = SimpleNamespace(id="a" * 32, cancellation=None)
    result = create.call_args.args[1](current)
    assert [item["legend"] for item in runner.call_args.args[0]] == ["Control A", "second"]
    assert result["comparison_id"] == current.id
    assert "full_analysis_comparison.pdf" in result["artifacts"]


@pytest.mark.parametrize("runs,code", [
    ([{"run_id": "first"}], 422),
    ([{"run_id": "first"}, {"run_id": "first"}], 400),
    ([{"run_id": "first"}, {"run_id": "..\\outside"}], 400),
    ([{"run_id": "first"}, {"run_id": "missing"}], 404),
    ([{"run_id": "first", "legend": "中文"}, {"run_id": "second"}], 400),
    ([{"run_id": "first", "run_dir": "outside"}, {"run_id": "second"}], 422),
])
def test_invalid_comparisons_do_not_create_jobs(client, tmp_path, monkeypatch, runs, code):
    comparison_sources(tmp_path)
    create = Mock()
    monkeypatch.setattr(main.manager, "create", create)
    assert client.post(f"{BASE}/compare", json={"runs": runs}).status_code == code
    assert not create.called


def test_comparison_guards_reanalysis_deletion_and_duplicate_jobs(client, tmp_path, monkeypatch):
    comparison_sources(tmp_path)
    kind = f"comparison:{EXPERIMENT_ID}"
    monkeypatch.setattr(main.manager, "has_active_kind", lambda value: value == kind)
    monkeypatch.setattr(main.manager, "jobs", {"compare": SimpleNamespace(kind=kind, status="running")})
    assert client.post(f"{BASE}/compare", json={"runs": [{"run_id": "first"}, {"run_id": "second"}]}).status_code == 409
    assert client.post("/api/runs/first/analyze", json={"experiment_id": EXPERIMENT_ID}).status_code == 409
    assert client.delete(f"/api/runs/{EXPERIMENT_ID}/first").status_code == 409
    assert (tmp_path / "data" / DATA_TYPE / "first/experiment_config.yaml").exists()
    monkeypatch.setattr(main.manager, "has_active_kind", lambda value: value == f"analysis:{EXPERIMENT_ID}:first")
    assert client.post(f"{BASE}/compare", json={"runs": [{"run_id": "first"}, {"run_id": "second"}]}).status_code == 409


def test_active_session_guards_run_deletion(client, tmp_path, monkeypatch):
    directory = create_run(tmp_path)
    monkeypatch.setattr(main.manager, "jobs", {"session": SimpleNamespace(
        kind=f"session:{EXPERIMENT_ID}", status="running")})
    response = client.delete(f"/api/runs/{EXPERIMENT_ID}/{directory.name}")
    assert response.status_code == 409
    assert directory.is_dir()


def test_downloads_require_complete_manifest_and_safe_names(client, tmp_path):
    identifier = "a" * 32
    directory = main._rf_direct_aw_comparison_dir(identifier)
    directory.mkdir(parents=True)
    (directory / "full_analysis_comparison.png").write_bytes(b"test png")
    url = f"{BASE}/comparisons/{identifier}/artifacts/"
    assert client.get(url + "full_analysis_comparison.png").status_code == 404
    (directory / "comparison.json").write_text("{}", encoding="utf-8")
    assert client.get(url + "full_analysis_comparison.png").content == b"test png"
    assert client.get(url + "comparison.json").status_code == 404
    assert client.get(url + "..%5Ccomparison.json").status_code == 404
    assert client.get(f"{BASE}/comparisons/..%5Coutside/artifacts/full_analysis_comparison.png").status_code == 400


def test_real_comparison_job_downloads_without_changing_sources(client, tmp_path, monkeypatch):
    import time
    from GUI.backend.jobs import JobManager
    from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_comparison import snapshot
    comparison_sources(tmp_path)
    before = snapshot(tmp_path / "data")
    monkeypatch.setattr(main, "manager", JobManager())
    response = client.post(f"{BASE}/compare", json={"runs": [{"run_id": "first"}, {"run_id": "second"}]})
    assert response.status_code == 200
    identifier = response.json()["id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{identifier}").json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job
    result = job["result"]
    assert [item["legend"] for item in result["runs"]] == ["first", "second"]
    assert client.get(result["artifacts"]["full_analysis_comparison.png"]).content.startswith(b"\x89PNG")
    assert client.get(result["artifacts"]["full_analysis_comparison.pdf"]).content.startswith(b"%PDF")
    assert snapshot(tmp_path / "data") == before


@pytest.fixture
def workflow_client(rig, monkeypatch):
    from fastapi import FastAPI
    from .jobs import JobManager
    from .rf_sensitivity_direct_aw import RFSessionService, create_router
    manager = JobManager()
    service = RFSessionService(lambda: rig.tmp_path, manager)
    app = FastAPI()
    app.include_router(create_router(service))
    original = ADAPTER._execute
    monkeypatch.setattr(ADAPTER, "_execute", lambda module, **kwargs: original(
        "lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_stages"
        if module.endswith(".session") else module, **kwargs))
    monkeypatch.setenv("RF_TEST_ROOT", str(rig.tmp_path))
    client = TestClient(app)
    yield client, service, manager
    service.close_communication()
    for job in manager.jobs.values():
        job.cancellation.cancel()
    service.shutdown()
    client.close()


def wait_for(predicate):
    import time
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("假仪器子进程没有及时完成")


def create_session(client, service, rig):
    response = client.post(f"{BASE}/sessions", json={"parameters": rig.params.to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait_for(lambda: service.get(session_id)["status"] in ("waiting", "closed"))
    assert service.get(session_id)["status"] == "waiting", service.manager.get(response.json()["job_id"]).error
    return session_id


def test_workflow_manifest_diagram_and_strict_requests(workflow_client, rig):
    client, service, manager = workflow_client
    flow = client.get(f"{BASE}/workflow").json()
    assert len(flow["acquisition"]) == 17 and len(flow["analysis"]) == 4
    assert flow["acquisition"][-1]["dependencies"] == []
    assert flow["parameter_impacts"]["analysis"]["NOISE_NPERSEG"] == ["B03", "B04"]
    # 下载使用真实仓库中的可编辑源图，临时根目录只负责硬件和数据隔离。
    from lab_workflows.common import find_project_root
    source = find_project_root() / service.diagram_file
    target = rig.tmp_path / service.diagram_file
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    diagram = client.get(flow["diagram_url"])
    assert diagram.status_code == 200 and diagram.json()["type"] == "excalidraw"
    assert not manager.jobs
    session_id = create_session(client, service, rig)
    path = f"{BASE}/sessions/{session_id}/steps"
    assert client.post(path, json={"revision": 0, "step_ids": ["A13"]}).status_code == 422
    assert client.post(path, json={"revision": "0", "step_ids": ["A01"]}).status_code == 422
    assert client.post(path, json={"revision": 0, "step_ids": ["A01"], "extra": 1}).status_code == 422
    assert client.post(path, json={"revision": 9, "step_ids": ["A01"]}).headers["X-Error-Code"] == "revision_conflict"
    assert client.get(f"{BASE}/sessions/active").json()["session_id"] == session_id
    client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0})
    wait_for(lambda: service.get(session_id)["status"] == "closed")


def test_real_session_holds_hardware_lock_seals_run_and_analyzes_independently(workflow_client, rig):
    client, service, manager = workflow_client
    session_id = create_session(client, service, rig)
    probe = manager.create("hardware-probe", lambda job: {"ok": True})
    assert probe.status == "queued"
    path = f"{BASE}/sessions/{session_id}"
    response = client.post(path + "/steps", json={"revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 16)]})
    assert response.status_code == 200
    # 提交完成前拒绝第二条命令；完成后旧修订也不能复用。
    assert client.post(path + "/steps", json={"revision": 0, "step_ids": ["A01"]}).status_code == 409
    wait_for(lambda: service.get(session_id)["steps"]["A15"]["status"] == "completed" or service.get(session_id)["status"] == "closed")
    snapshot = service.get(session_id)
    assert snapshot["status"] == "waiting", snapshot
    assert snapshot["outputs"]["X"] == "OFF"
    assert probe.status == "queued"
    run_id = snapshot["run_id"]
    state = client.get(f"{BASE}/runs/{run_id}/analysis")
    assert state.status_code == 200, state.text
    response = client.post(f"{BASE}/runs/{run_id}/analysis", json={"revision": state.json()["revision"], "step_ids": ["B01"]})
    assert response.status_code == 200, response.text
    job = manager.get(response.json()["id"])
    wait_for(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", job.error
    assert probe.status == "queued"
    client.post(path + "/finish", json={"revision": snapshot["revision"]})
    wait_for(lambda: service.get(session_id)["status"] == "closed" and probe.status == "completed")
    assert service.active() is None
    from .rf_sensitivity_direct_aw import RFSessionService
    assert RFSessionService(lambda: rig.tmp_path, manager).active() is None


def test_eof_and_cancel_both_finish_with_safety_report(workflow_client, rig):
    client, service, manager = workflow_client
    session_id = create_session(client, service, rig)
    client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 11)]})
    wait_for(lambda: service.get(session_id)["steps"]["A10"]["status"] == "completed" or service.get(session_id)["status"] == "closed")
    assert service.get(session_id)["status"] == "waiting"
    run_id = service.get(session_id)["run_id"]
    waveform = client.get(f"{BASE}/runs/{run_id}/files/raw/xy_aw_waveforms.npz")
    assert waveform.status_code == 200
    assert client.get(f"{BASE}/runs/{run_id}/analysis").status_code == 409
    assert client.get(f"{BASE}/runs/{run_id}/files/..%5Coutside").status_code == 404
    service.close_communication()
    wait_for(lambda: service.get(session_id)["status"] == "closed")
    assert service.get(session_id)["safety_shutdown"]["completed"] is True
    second = create_session(client, service, rig)
    manager.get(service.get(second)["job_id"]).cancellation.cancel()
    wait_for(lambda: service.get(second)["status"] == "closed")
    assert service.active() is None
