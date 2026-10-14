"""静磁场历史、分阶段 API 和真实子进程假仪器会话回归。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from . import main
from .jobs import JobManager
from .static_sensitivity import StaticSessionService, create_router
from lab_workflows.experiment_modules.static_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.static_sensitivity.test_dc_scan import make_run
from lab_workflows.experiment_modules.static_sensitivity.test_support import fake_rig
from lab_workflows.experiment_modules.static_sensitivity.stages import definition
from lab_workflows.experiment_modules.static_sensitivity.workflow import DATA_TYPE

BASE="/api/experiments/static-sensitivity"


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"ROOT",tmp_path)
    monkeypatch.setattr(main.manager,"has_active_kind",lambda kind:False)
    return TestClient(main.app)


def create_run(root,name="v3",**changes):
    path=root / "data" / DATA_TYPE / name
    path.mkdir(parents=True)
    make_run(path)
    if changes:
        config=yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
        config.update(changes)
        (path / "experiment_config.yaml").write_text(yaml.safe_dump(config),encoding="utf-8")
    return path


def test_history_summary_snapshot_missing_results_and_paths(client,tmp_path,monkeypatch):
    run=create_run(tmp_path)
    old=create_run(tmp_path,"old",schema_version=1)
    before=(run / "experiment_config.yaml").read_bytes()
    assert client.get(BASE+"/runs").json()["total"]==2
    data=client.get(BASE+"/runs/v3/summary").json()
    assert data["parameters"]["z_v_to_nt"]==1950
    assert data["analysis"]=={} and data["artifacts"]=={}
    assert (run / "experiment_config.yaml").read_bytes()==before
    assert client.get(BASE+"/runs/missing/summary").status_code==404
    assert client.get(BASE+"/runs/..%5Coutside/summary").status_code==404
    create=Mock(return_value=SimpleNamespace(public=lambda:{"id":"analysis","status":"queued"}))
    monkeypatch.setattr(main.manager,"create",create)
    assert client.post("/api/runs/old/analyze",json={"experiment_id":"static-sensitivity"}).status_code==400
    assert not create.called
    assert client.post("/api/runs/v3/analyze",json={"experiment_id":"static-sensitivity"}).status_code==200
    assert create.call_args.args[0]=="analysis:static-sensitivity:v3"
    assert create.call_args.kwargs["hardware_required"] is False


def test_running_directories_and_same_directory_analysis_lock(client,tmp_path,monkeypatch):
    run=create_run(tmp_path,completion_status="running")
    create=Mock()
    monkeypatch.setattr(main.manager,"create",create)
    assert client.get(BASE+"/runs/v3/analysis").status_code==409
    assert client.post("/api/runs/v3/analyze",json={"experiment_id":"static-sensitivity"}).status_code==409
    assert not create.called
    config=yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
    config["completion_status"]="completed"
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config),encoding="utf-8")
    monkeypatch.setattr(main.manager,"has_active_kind",lambda kind:kind=="analysis:static-sensitivity:v3")
    assert client.post("/api/runs/v3/analyze",json={"experiment_id":"static-sensitivity"}).status_code==409
    assert client.post(BASE+"/runs/v3/analysis",json={"revision":0,"step_ids":["B01"]}).status_code==409


@pytest.fixture
def workflow_client(tmp_path,monkeypatch):
    with fake_rig(tmp_path) as rig:
        manager=JobManager()
        service=StaticSessionService(lambda:tmp_path,manager)
        app=FastAPI()
        app.include_router(create_router(service))
        original=ADAPTER._execute
        monkeypatch.setattr(ADAPTER,"_execute",lambda module,**kwargs:original(
            "lab_workflows.experiment_modules.static_sensitivity.test_stages" if module.endswith(".session") else module,**kwargs))
        monkeypatch.setenv("STATIC_TEST_ROOT",str(tmp_path))
        diagram=Path(__file__).resolve().parents[2] / service.diagram_file
        dest=tmp_path / service.diagram_file
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(diagram.read_bytes())
        client=TestClient(app)
        yield client,service,manager,rig
        service.close_communication()
        for job in manager.jobs.values():
            job.cancellation.cancel()
        service.shutdown()
        client.close()


def wait_for(predicate):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("假仪器子进程未及时完成")


def create_session(client,service,rig):
    response=client.post(BASE+"/sessions",json={"parameters":rig.params.to_external()})
    assert response.status_code==200,response.text
    identifier=response.json()["session_id"]
    wait_for(lambda:service.get(identifier)["status"] in ("waiting","closed"))
    assert service.get(identifier)["status"]=="waiting",manager_error(service,identifier)
    return identifier


def manager_error(service,identifier):
    return service.manager.get(service.get(identifier)["job_id"]).error


def submit_steps(client,service,identifier,steps):
    state=service.get(identifier)
    response=client.post(BASE+f"/sessions/{identifier}/steps",json={"revision":state["revision"],"step_ids":steps})
    assert response.status_code==200,response.text
    wait_for(lambda:not service.get(identifier).get("pending_request") or service.get(identifier)["status"]=="closed")
    assert service.get(identifier)["status"]=="waiting",manager_error(service,identifier)
    return service.get(identifier)


def test_workflow_diagram_and_strict_requests(workflow_client):
    client,service,manager,rig=workflow_client
    assert client.get(BASE+"/workflow").json()==json.loads(json.dumps(definition()))
    diagram=client.get(BASE+"/workflow/diagram")
    assert diagram.status_code==200 and diagram.json()["type"]=="excalidraw"
    assert any("静磁场" in item.get("text","") for item in diagram.json()["elements"])
    assert client.post(BASE+"/sessions",json={"parameters":{"dispersion_steps":2}}).status_code==422
    assert client.post(BASE+"/sessions",json={"parameters":{},"unknown":1}).status_code==422
    assert client.post(BASE+"/runs/missing/analysis",json={"revision":0,"step_ids":["B01"]}).status_code==404


def test_real_session_holds_lock_refreshes_applies_and_ends(workflow_client):
    client,service,manager,rig=workflow_client
    identifier=create_session(client,service,rig)
    path=BASE+f"/sessions/{identifier}"
    assert client.get(BASE+"/sessions/active").json()["session_id"]==identifier
    probe=manager.create("probe",lambda job:"done")
    assert probe.status=="queued"
    assert client.post(path+"/steps",json={"revision":0,"step_ids":["A09"]}).status_code==422
    state=submit_steps(client,service,identifier,[f"A{i:02d}" for i in range(1,9) if i != 5])
    assert client.get(path).json()["steps"]["A08"]["status"]=="completed"
    assert client.patch(path+"/parameters",json={"revision":0,"parameters":{"noise_n_avg":2}}).status_code==409
    response=client.patch(path+"/parameters",json={"revision":state["revision"],"parameters":{"dispersion_temp_switch_on_settle_s":.8}})
    assert response.status_code==200,response.text
    wait_for(lambda:not service.get(identifier).get("pending_request"))
    assert service.get(identifier)["steps"]["A08"]["status"]=="stale"
    state=submit_steps(client,service,identifier,["A08","A09","A10","A11","A12"])
    assert state["steps"]["A12"]["status"]=="completed" and state["demod_mode"]=="dispersion"
    assert probe.status=="queued"
    run_id=state["run_id"]
    original=(rig.root / "data" / DATA_TYPE / run_id / "experiment_config.yaml").read_bytes()
    snapshot=client.get(BASE+f"/runs/{run_id}/analysis").json()
    response=client.post(BASE+f"/runs/{run_id}/analysis",json={"revision":snapshot["revision"],"step_ids":["B01","B03"]})
    assert response.status_code==200,response.text
    job=manager.get(response.json()["id"])
    wait_for(lambda:job.status in ("completed","failed"))
    assert job.status=="completed",job.error
    assert probe.status=="queued"
    assert client.post(path+"/finish",json={"revision":state["revision"]}).status_code==200
    wait_for(lambda:service.get(identifier)["status"]=="closed" and probe.status=="completed")
    assert service.get(identifier)["safety_shutdown"]["completed"] is True
    assert original==(rig.root / "data" / DATA_TYPE / run_id / "experiment_config.yaml").read_bytes()
    assert service.active() is None
    assert StaticSessionService(lambda:rig.root,manager).active() is None


@pytest.mark.parametrize("action",["eof","cancel","shutdown"])
def test_eof_cancel_and_backend_shutdown_safely_restore(workflow_client,action):
    client,service,manager,rig=workflow_client
    identifier=create_session(client,service,rig)
    state=submit_steps(client,service,identifier,[f"A{i:02d}" for i in range(1,11) if i != 5])
    assert state["demod_mode"]=="noise"
    run_id=state["run_id"]
    assert client.get(BASE+f"/runs/{run_id}/analysis").status_code==409
    if action=="eof":
        service.close_communication()
    elif action=="cancel":
        manager.get(state["job_id"]).cancellation.cancel()
    else:
        service.shutdown()
    wait_for(lambda:service.get(identifier)["status"]=="closed")
    state=service.get(identifier)
    assert state["demod_mode"]=="dispersion"
    assert state["safety_shutdown"]["completed"] is True
    config=yaml.safe_load((rig.root / "data" / DATA_TYPE / run_id / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled","failed")
    assert config["safety_shutdown"]["completed"] is True


@pytest.mark.parametrize("action",["cancel","eof","shutdown"])
def test_stop_during_daq_restores_before_releasing_lock(workflow_client,monkeypatch,action):
    client,service,manager,rig=workflow_client
    monkeypatch.setenv("STATIC_TEST_BLOCK_NOISE","1")
    identifier=create_session(client,service,rig)
    state=submit_steps(client,service,identifier,[f"A{i:02d}" for i in range(1,11) if i != 5])
    probe=manager.create("probe",lambda job:"done")
    assert probe.status=="queued"
    response=client.post(BASE+f"/sessions/{identifier}/steps",json={"revision":state["revision"],"step_ids":["A11"]})
    assert response.status_code==200,response.text
    wait_for(lambda:(rig.root / "noise_waiting").exists())
    assert service.get(identifier)["current_step"]=="A11"
    revision=service.get(identifier)["revision"]
    assert client.post(BASE+f"/sessions/{identifier}/steps",json={"revision":revision,"step_ids":["A11"]}).status_code==409
    assert client.patch(BASE+f"/sessions/{identifier}/parameters",json={"revision":revision,"parameters":{"noise_n_avg":2}}).status_code==409
    if action=="cancel":
        manager.get(state["job_id"]).cancellation.cancel()
    elif action=="eof":
        service.close_communication()
    else:
        service.shutdown()
    wait_for(lambda:service.get(identifier)["status"]=="closed" and probe.status=="completed")
    closed=service.get(identifier)
    assert closed["safety_shutdown"]["completed"] is True
    assert closed["demod_mode"]=="dispersion"
    assert closed["steps"]["A11"]["error"]=="收到 GUI 安全停止请求"
    config=yaml.safe_load((rig.root / "data" / DATA_TYPE / closed["run_id"] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"]=="cancelled"
    assert config["safety_shutdown"]["completed"] is True
