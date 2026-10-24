"""ConstXY 的真实假仪器子进程、全局硬件锁和只读历史 API。"""
from pathlib import Path
from threading import Event
import sys
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.definition import ADAPTER
from lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.test_analysis import synthetic_run, frozen_inputs
from .jobs import JobManager
from .rf_sensitivity_constxy_frequency import ConstXYSessionService, create_router

ID="rf-sensitivity-constxy-frequency"
BASE="/api/experiments/"+ID
ROOT=Path(__file__).resolve().parents[2]


def wait(predicate):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(0.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram="lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/workflow.excalidraw"
    path=tmp_path/diagram; path.parent.mkdir(parents=True); path.write_bytes((ROOT/diagram).read_bytes())
    manager=JobManager(); service=ConstXYSessionService(lambda:tmp_path,manager)
    original=ADAPTER._execute
    def execute(module, **kwargs):
        if module in (ADAPTER.acquisition_module,service.session_module):
            monkeypatch.setenv("CONSTXY_TEST_FULL","1" if module==ADAPTER.acquisition_module else "0")
            module="lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.test_workflow"
        return original(module,**kwargs)
    monkeypatch.setattr(ADAPTER,"_execute",execute)
    from lab_workflows.experiments import typed
    original_popen=typed.subprocess.Popen
    def popen(command, **kwargs):
        kwargs["cwd"]=ROOT
        return original_popen([sys.executable,*command[1:]],**kwargs)
    monkeypatch.setattr(typed.subprocess,"Popen",popen)
    monkeypatch.setattr(ADAPTER,"root",tmp_path)
    monkeypatch.setattr(ADAPTER,"defaults_path",tmp_path/"params/experiments/rf-sensitivity-constxy-frequency.yaml")
    small_params().save_yaml(ADAPTER.defaults_path)
    monkeypatch.setenv("CONSTXY_TEST_ROOT",str(tmp_path))
    app=FastAPI(); app.include_router(create_router(service))
    with TestClient(app) as http:
        http.service,http.manager,http.root=service,manager,tmp_path
        yield http
    service.shutdown()
    wait(lambda:all(job.status not in ("queued","running") for job in manager.jobs.values()))


def create(client):
    response=client.post(BASE+"/sessions",json={"parameters":small_params().to_external()})
    assert response.status_code==200,response.text
    sid=response.json()["session_id"]
    wait(lambda:client.service.get(sid)["status"] in ("waiting","closed"))
    assert client.service.get(sid)["status"]=="waiting",client.service.get(sid)
    return sid


def steps(client,sid,ids):
    response=client.post(f"{BASE}/sessions/{sid}/steps",json={"revision":client.service.get(sid)["revision"],"step_ids":ids})
    assert response.status_code==200,response.text
    wait(lambda:client.service.get(sid)["pending_request"] is None)
    state=client.service.get(sid)
    assert state["error"] is None,state
    return state


def test_child_dependencies_revision_lock_rerun_and_finish(client):
    flow=client.get(BASE+"/workflow").json()
    assert len(flow["acquisition"])==11 and len(flow["analysis"])==3
    assert client.get(BASE+"/workflow/diagram").json()["type"]=="excalidraw"
    sid=create(client); wait(lambda:client.manager.hardware_lock.locked())
    assert client.post(f"{BASE}/sessions/{sid}/steps",json={"revision":0,"step_ids":["A10"]}).status_code==422
    state=steps(client,sid,[f"A{i:02d}" for i in range(1,10)])
    assert client.get(BASE+"/sessions/active").json()["session_id"]==sid
    assert ConstXYSessionService(lambda:client.root,client.manager).active() is None
    assert client.post(f"{BASE}/sessions/{sid}/finish",json={"revision":0}).status_code==409
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code==409
    state=steps(client,sid,["A10"])
    first=client.root/"data/RF_Field_Sensitivity_ConstXY_FreqSweep"/state["run_id"]
    before=frozen_inputs(first)
    state=steps(client,sid,["A06","A07","A08","A09","A10"])
    assert state["run_id"] != first.name and frozen_inputs(first)==before
    response=client.post(f"{BASE}/sessions/{sid}/finish",json={"revision":state["revision"]})
    assert response.status_code==200,response.text
    wait(lambda:client.service.get(sid)["status"]=="closed")
    wait(lambda:not client.manager.hardware_lock.locked())
    final=client.service.get(sid)
    assert final["steps"]["A11"]["status"]=="completed" and final["safety_shutdown"]["completed"]
    assert final["outputs"]["XY"]=="0 V DC + OFF"


@pytest.mark.parametrize("method",["cancel","eof","shutdown"])
def test_cancel_eof_shutdown_restore_before_unlock(client,method):
    sid=create(client); state=steps(client,sid,[f"A{i:02d}" for i in range(1,10)])
    if method=="cancel": client.manager.get(state["job_id"]).cancellation.cancel()
    elif method=="eof": client.service.close_communication()
    else: client.service.shutdown()
    wait(lambda:client.service.get(sid)["status"]=="closed")
    wait(lambda:not client.manager.hardware_lock.locked())
    final=client.service.get(sid)
    assert final["safety_shutdown"]["completed"] and final["outputs"]["温控"]=="5 V DC + ON"
    assert final["outputs"]["XY"]=="0 V DC + OFF"


def test_readonly_history_options_types_and_real_analysis_lock(client,monkeypatch):
    base=client.root/"data/RF_Field_Sensitivity_ConstXY_FreqSweep"
    run=synthetic_run(base/"legacy",legacy=True); other=synthetic_run(base/"other")
    before=frozen_inputs(run)
    assert client.get(BASE+"/runs").json()["total"]==2
    summary=client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["parameters"]["Z_RF_FREQ_POINTS"]==3 and summary["parameters"]["LARMOR_FREQ_HZ"]==7000
    assert not summary["artifacts"] and frozen_inputs(run)==before
    assert client.get(f"{BASE}/runs/missing/summary").status_code==404
    assert client.get(f"{BASE}/runs/..%2Fescape/summary").status_code==404
    state=client.get(f"{BASE}/runs/{run.name}/analysis").json()
    assert state["options"]["DLZ_INCLUDE_OFFSET"] is True and state["options"]["DLZ_GAMMA_MAX_HZ"] is None
    assert frozen_inputs(run)==before
    execute,entered,release=ADAPTER._execute,Event(),Event()
    def blocked(module,**kwargs):
        if module==ADAPTER.analysis_module:
            entered.set(); assert release.wait(30)
        return execute(module,**kwargs)
    monkeypatch.setattr(ADAPTER,"_execute",blocked)
    body=dict(revision=state["revision"],step_ids=["B01","B02","B03"],analysis_parameters={"ENABLE_LORENTZIAN_FIT":False,"DLZ_INCLUDE_OFFSET":False})
    try:
        response=client.post(f"{BASE}/runs/{run.name}/analysis",json=body)
        assert response.status_code==200,response.text
        jid=response.json()["id"]; assert entered.wait(5)
        assert client.post(f"{BASE}/runs/{run.name}/analysis",json=body).status_code==409
    finally: release.set()
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    assert client.manager.get(jid).status=="completed",client.manager.get(jid).error
    assert client.get(f"{BASE}/runs/{other.name}/analysis").json()["options"]["DLZ_INCLUDE_OFFSET"] is True
    assert client.post(f"{BASE}/runs/{run.name}/analysis",json=body).status_code==409
    summary=client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["analysis_status"]=="completed" and "frequency_response_comparison.png" in str(summary["artifacts"])
    assert client.get(next(iter(summary["artifacts"].values()))).status_code==200
    assert frozen_inputs(run)==before


def test_strict_requests_identity_and_unsupported_format(client):
    run=synthetic_run(client.root/"data/RF_Field_Sensitivity_ConstXY_FreqSweep/wrong")
    config=run/"experiment_config.yaml"; value=yaml.safe_load(config.read_text(encoding="utf-8")); value["raw_format_version"]=99
    config.write_text(yaml.safe_dump(value),encoding="utf-8")
    assert client.get(f"{BASE}/runs/{run.name}/summary").status_code==422
    assert client.get(BASE+"/runs").json()["total"]==0
    assert client.post(BASE+"/sessions",json={"parameters":{"unknown":1}}).status_code==422
    assert client.post(BASE+"/sessions",json={"parameters":{},"unknown":1}).status_code==422


def test_main_registration_full_auto_and_shared_analysis_lock(client,monkeypatch):
    from . import main
    from lab_workflows import common
    monkeypatch.setattr(common,"find_project_root",lambda:client.root)
    monkeypatch.setattr(main,"ROOT",client.root); monkeypatch.setattr(main,"manager",client.manager)
    monkeypatch.setattr(main,"constxy_sessions",client.service)
    http=TestClient(main.app)
    definition=http.get(BASE).json()
    assert definition["execution_mode"]=="typed_workflow" and "XY 保持开启" in definition["description"]
    assert client.service in main._workflow_session_services()
    assert BASE+"/sessions/{session_id}/finish" in main.app.openapi()["paths"]
    run=synthetic_run(client.root/"data/RF_Field_Sensitivity_ConstXY_FreqSweep/generic")
    before=frozen_inputs(run); execute,entered,release=ADAPTER._execute,Event(),Event()
    def blocked(module,**kwargs):
        entered.set(); assert release.wait(30)
        return execute(module,**kwargs)
    monkeypatch.setattr(ADAPTER,"_execute",blocked)
    try:
        response=http.post(f"/api/runs/{run.name}/analyze",json={"experiment_id":ID})
        assert response.status_code==200,response.text
        jid=response.json()["id"]; assert entered.wait(5)
        state=client.get(f"{BASE}/runs/{run.name}/analysis").json()
        assert client.post(f"{BASE}/runs/{run.name}/analysis",json={"revision":state["revision"],"step_ids":["B01"]}).status_code==409
    finally: release.set()
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    assert client.manager.get(jid).status=="completed",client.manager.get(jid).error
    assert frozen_inputs(run)==before
    monkeypatch.setattr(ADAPTER,"_execute",execute)
    response=http.post(BASE+"/runs",json={"parameters":small_params().to_external()})
    assert response.status_code==200,response.text
    jid=response.json()["id"]; wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    job=client.manager.get(jid)
    assert job.status=="completed",job.error
    assert (Path(job.result["run_dir"])/"results/frequency_response_comparison.png").is_file()
