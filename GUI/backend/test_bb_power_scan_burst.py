"""BB 实际假仪器子进程、全局硬件锁、只读历史与分析互斥。"""
from pathlib import Path
from threading import Event
import sys
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.bb_power_scan_burst.definition import ADAPTER
from lab_workflows.experiment_modules.bb_power_scan_burst.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.bb_power_scan_burst.test_analysis import synthetic_run, frozen_inputs
from .jobs import JobManager
from .bb_power_scan_burst import BBPowerScanBurstSessionService, create_router

ID="bb-power-scan-burst"
BASE="/api/experiments/"+ID
ROOT=Path(__file__).resolve().parents[2]


def wait(predicate):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path,monkeypatch):
    copy_config(tmp_path)
    diagram="lab_workflows/experiment_modules/bb_power_scan_burst/workflow.excalidraw"
    path=tmp_path/diagram; path.parent.mkdir(parents=True); path.write_bytes((ROOT/diagram).read_bytes())
    manager=JobManager(); service=BBPowerScanBurstSessionService(lambda:tmp_path,manager)
    execute=ADAPTER._execute
    def simulated(module,**kwargs):
        if module in (ADAPTER.acquisition_module,service.session_module):
            monkeypatch.setenv("BB_POWER_TEST_FULL","1" if module==ADAPTER.acquisition_module else "0")
            module="lab_workflows.experiment_modules.bb_power_scan_burst.test_workflow"
        return execute(module,**kwargs)
    monkeypatch.setattr(ADAPTER,"_execute",simulated)
    from lab_workflows.experiments import typed
    popen=typed.subprocess.Popen
    def start(command,**kwargs):
        kwargs["cwd"]=ROOT
        return popen([sys.executable,*command[1:]],**kwargs)
    monkeypatch.setattr(typed.subprocess,"Popen",start)
    monkeypatch.setattr(ADAPTER,"root",tmp_path)
    monkeypatch.setattr(ADAPTER,"defaults_path",tmp_path/"params/experiments/bb-power-scan-burst.yaml")
    small_params().save_yaml(ADAPTER.defaults_path)
    monkeypatch.setenv("BB_POWER_TEST_ROOT",str(tmp_path))
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
    state=client.service.get(sid); assert state["error"] is None,state
    return state


def test_dependencies_revision_global_lock_rerun_and_finish(client):
    flow=client.get(BASE+"/workflow").json()
    assert len(flow["acquisition"])==8 and len(flow["analysis"])==3
    assert client.get(BASE+"/workflow/diagram").json()["type"]=="excalidraw"
    sid=create(client); wait(lambda:client.manager.hardware_lock.locked())
    assert client.post(f"{BASE}/sessions/{sid}/steps",json={"revision":0,"step_ids":["A07"]}).status_code==422
    assert client.post(f"{BASE}/sessions/{sid}/steps",json={"revision":0,"step_ids":["A01","A01"]}).status_code==422
    state=steps(client,sid,[f"A{i:02d}" for i in range(1,7)])
    assert client.get(BASE+"/sessions/active").json()["session_id"]==sid
    assert BBPowerScanBurstSessionService(lambda:client.root,client.manager).active() is None
    assert client.post(f"{BASE}/sessions/{sid}/finish",json={"revision":0}).status_code==409
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code==409
    state=steps(client,sid,["A07"])
    first=client.root/"data/bb_rpump_power_scan_burst"/state["run_id"]; before=frozen_inputs(first)
    state=steps(client,sid,["A06","A07"])
    assert state["run_id"]!=first.name and frozen_inputs(first)==before
    response=client.post(f"{BASE}/sessions/{sid}/finish",json={"revision":state["revision"]})
    assert response.status_code==200,response.text
    wait(lambda:client.service.get(sid)["status"]=="closed")
    wait(lambda:not client.manager.hardware_lock.locked())
    state=client.service.get(sid)
    assert state["steps"]["A08"]["status"]=="completed" and state["safety_shutdown"]["completed"]
    assert state["outputs"]["时序"]=="输出/Burst/SYNC 保持开启" and state["outputs"]["Pump"]=="0.12 V DC + ON"


@pytest.mark.parametrize("method",["cancel","eof","shutdown"])
def test_cancel_eof_shutdown_restore_before_unlock(client,method):
    sid=create(client); state=steps(client,sid,[f"A{i:02d}" for i in range(1,7)])
    if method=="cancel": client.manager.get(state["job_id"]).cancellation.cancel()
    elif method=="eof": client.service.close_communication()
    else: client.service.shutdown()
    wait(lambda:client.service.get(sid)["status"]=="closed")
    wait(lambda:not client.manager.hardware_lock.locked())
    state=client.service.get(sid)
    assert state["safety_shutdown"]["completed"] and state["outputs"]["温控"]=="5 V DC + ON"
    assert state["outputs"]["时序"]=="输出/Burst/SYNC 保持开启"


def test_readonly_legacy_history_parameters_and_analysis_lock(client,monkeypatch):
    base=client.root/"data/bb_rpump_power_scan_burst"
    run=synthetic_run(base/"legacy",legacy=True); other=synthetic_run(base/"other")
    before=frozen_inputs(run)
    assert client.get(BASE+"/runs").json()["total"]==2
    summary=client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["parameters"]["PUMP_POWER"]==[.08,.12] and summary["parameters"]["ACQ_REPEATS"]==2
    assert not summary["artifacts"] and frozen_inputs(run)==before
    assert client.get(f"{BASE}/runs/missing/summary").status_code==404
    assert client.get(f"{BASE}/runs/..%2Fescape/summary").status_code==404
    state=client.get(f"{BASE}/runs/{run.name}/analysis").json()
    assert state["options"]=={"POST_DARK_TIME_S":.02} and frozen_inputs(run)==before
    execute,entered,release=ADAPTER._execute,Event(),Event()
    def blocked(module,**kwargs):
        if module==ADAPTER.analysis_module:
            entered.set(); assert release.wait(30)
        return execute(module,**kwargs)
    monkeypatch.setattr(ADAPTER,"_execute",blocked)
    body=dict(revision=state["revision"],step_ids=["B01","B02","B03"],analysis_parameters={"POST_DARK_TIME_S":.018})
    try:
        response=client.post(f"{BASE}/runs/{run.name}/analysis",json=body)
        assert response.status_code==200,response.text
        jid=response.json()["id"]; assert entered.wait(5)
        assert client.post(f"{BASE}/runs/{run.name}/analysis",json=body).status_code==409
    finally: release.set()
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    assert client.manager.get(jid).status=="completed",client.manager.get(jid).error
    assert client.get(f"{BASE}/runs/{other.name}/analysis").json()["options"]["POST_DARK_TIME_S"]==.02
    assert client.post(f"{BASE}/runs/{run.name}/analysis",json=body).status_code==409
    summary=client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["analysis_status"]=="completed" and "rpump_eff_vs_pump_power_pub.png" in str(summary["artifacts"])
    assert client.get(next(iter(summary["artifacts"].values()))).status_code==200
    assert frozen_inputs(run)==before


def test_strict_requests_identity_and_format(client):
    run=synthetic_run(client.root/"data/bb_rpump_power_scan_burst/wrong")
    path=run/"experiment_config.yaml"; config=yaml.safe_load(path.read_text(encoding="utf-8")); config["raw_format_version"]=99
    path.write_text(yaml.safe_dump(config),encoding="utf-8")
    assert client.get(f"{BASE}/runs/{run.name}/summary").status_code==422
    assert client.get(BASE+"/runs").json()["total"]==0
    assert client.post(BASE+"/sessions",json={"parameters":{"unknown":1}}).status_code==422
    assert client.post(BASE+"/sessions",json={"parameters":{},"unknown":1}).status_code==422


def test_main_registration_complete_auto_and_shared_analysis_lock(client,monkeypatch):
    from . import main
    from lab_workflows import common
    monkeypatch.setattr(main,"ROOT",client.root); monkeypatch.setattr(main,"manager",client.manager)
    monkeypatch.setattr(main,"bb_power_sessions",client.service)
    monkeypatch.setattr(common,"find_project_root",lambda:client.root)
    http=TestClient(main.app)
    assert ID in main.WORKFLOW_RUN_FORMATS and client.service in main._workflow_session_services()
    assert http.get(BASE).json()["execution_mode"]=="typed_workflow"
    response=http.post(BASE+"/runs",json={"parameters":small_params().to_external()})
    assert response.status_code==200,response.text
    jid=response.json()["id"]
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    job=client.manager.get(jid)
    assert job.status=="completed",job.error
    assert (Path(job.result["run_dir"])/"results/rpump_eff_vs_pump_power_pub.png").is_file()
    run=synthetic_run(client.root/"data/bb_rpump_power_scan_burst/reanalyze")
    before=frozen_inputs(run)
    execute,entered,release=ADAPTER._execute,Event(),Event()
    def blocked(module,**kwargs):
        entered.set(); assert release.wait(30); return execute(module,**kwargs)
    monkeypatch.setattr(ADAPTER,"_execute",blocked)
    try:
        response=http.post(f"/api/runs/{run.name}/analyze",json={"experiment_id":ID})
        assert response.status_code==200,response.text
        jid=response.json()["id"]; assert entered.wait(5)
        assert client.post(f"{BASE}/runs/{run.name}/analysis",json={"revision":0,"step_ids":["B01"]}).status_code==409
    finally: release.set()
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    assert client.manager.get(jid).status=="completed",client.manager.get(jid).error
    assert frozen_inputs(run)==before
