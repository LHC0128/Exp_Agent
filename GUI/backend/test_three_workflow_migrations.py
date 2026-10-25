"""三个真实路由的会话、队列、只读历史与独立分析 API 自检。"""
from hashlib import sha256
import importlib
from pathlib import Path
from threading import Event
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from lab_workflows.experiment_modules.test_direct_aw_migration import copy_configuration, install_fake, small_params
from lab_workflows.experiment_modules.test_direct_aw_migration_analysis import synthetic_run
from .jobs import JobManager

ROOT = Path(__file__).resolve().parents[2]
SPECS = (
    ("t2_calibration", "T2SessionService", "t2_sessions"),
    ("rf_sensitivity_direct_aw_frequency", "RFDirectAWFrequencySessionService", "rf_direct_frequency_sessions"),
    ("xy_direct_aw_dc_calibration", "XYDirectAWDCSessionService", "xy_dc_sessions"),
)


def wait(predicate):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.025)
    raise AssertionError("假仪器任务超时")


@pytest.fixture(params=SPECS)
def client(request,tmp_path,monkeypatch):
    module,classname,main_name=request.param
    copy_configuration(tmp_path)
    install_fake(module,tmp_path,monkeypatch)
    backend=importlib.import_module("GUI.backend."+module)
    adapter=backend.ADAPTER
    monkeypatch.setattr(adapter,"root",tmp_path)
    monkeypatch.setattr(adapter,"defaults_path",tmp_path/f"params/experiments/{adapter.id}.yaml")
    params=small_params(module);params.save_yaml(adapter.defaults_path)
    diagram=f"lab_workflows/experiment_modules/{module}/workflow.excalidraw"
    (tmp_path/diagram).parent.mkdir(parents=True)
    (tmp_path/diagram).write_bytes((ROOT/diagram).read_bytes())
    manager=JobManager()
    service=getattr(backend,classname)(lambda:tmp_path,manager)
    # 子进程只替换仪器工厂，随后进入正式 session.main，不复制业务流程。
    service.session_module="lab_workflows.experiment_modules.test_direct_aw_migration"
    monkeypatch.setenv("MIGRATION_TEST_MODULE",module)
    monkeypatch.setenv("MIGRATION_TEST_ROOT",str(tmp_path))
    from lab_workflows.experiments import typed
    popen=typed.subprocess.Popen
    def start(command,**kwargs):
        kwargs["cwd"]=ROOT
        return popen([sys.executable,*command[1:]],**kwargs)
    monkeypatch.setattr(typed.subprocess,"Popen",start)
    app=FastAPI();app.include_router(backend.create_router(service))
    with TestClient(app) as http:
        http.service,http.manager,http.root,http.module,http.experiment_params=service,manager,tmp_path,module,params
        http.base=f"/api/experiments/{adapter.id}"
        http.main_name=main_name
        yield http
    service.shutdown()
    wait(lambda:all(j.status not in ("queued","running") for j in manager.jobs.values()))


def test_real_session_dependencies_revision_and_lock(client):
    base=client.base
    flow=client.get(base+"/workflow").json()
    assert len(flow["acquisition"])==7 and len(flow["analysis"])==2
    page = {"t2_calibration": "T2CalibrationPage", "rf_sensitivity_direct_aw_frequency": "RFDirectAWFrequencyPage", "xy_direct_aw_dc_calibration": "XYDirectAWDCCalibrationPage"}[client.module]
    import json
    fixture = json.loads((ROOT / f"GUI/frontend/src/pages/{page}.fixture.json").read_text(encoding="utf-8"))
    assert flow == fixture["flow"], "前端 fixture 必须与真实后端清单一致"
    for column in ("acquisition", "analysis"):
        seen = set()
        for stage in flow[column]:
            assert stage["id"] not in seen and set(stage["dependencies"]) <= seen
            seen.add(stage["id"])
    assert not flow["acquisition"][-1]["dependencies"]
    assert client.get(base+"/workflow/diagram").json()["type"]=="excalidraw"
    response=client.post(base+"/sessions",json={"parameters":client.experiment_params.to_external()})
    assert response.status_code==200,response.text
    sid=response.json()["session_id"]
    wait(lambda:client.service.get(sid)["status"] in ("waiting","closed"))
    assert client.service.get(sid)["status"]=="waiting",client.service.get(sid)
    assert client.manager.hardware_lock.locked()
    prefix=f"{base}/sessions/{sid}"
    assert client.post(prefix+"/steps",json={"revision":0,"step_ids":["A06"]}).status_code==422
    assert client.post(prefix+"/steps",json={"revision":0,"step_ids":["A01","A01"]}).status_code==422
    response=client.post(prefix+"/steps",json={"revision":0,"step_ids":["A01","A02","A03","A04","A05","A06"]})
    assert response.status_code==200,response.text
    wait(lambda:client.service.get(sid)["pending_request"] is None)
    state=client.service.get(sid)
    assert state["error"] is None and state["steps"]["A06"]["status"]=="completed",client.manager.get(state["job_id"]).public()
    assert client.get(base+"/sessions/active").json()["session_id"]==sid
    assert client.get(f"{base}/runs/{state['run_id']}/analysis").status_code==409
    assert client.post(prefix+"/finish",json={"revision":0}).status_code==409
    response=client.post(prefix+"/finish",json={"revision":state["revision"]})
    assert response.status_code==200,response.text
    wait(lambda:client.service.get(sid)["status"]=="closed")
    wait(lambda:not client.manager.hardware_lock.locked())
    assert client.service.get(sid)["safety_shutdown"]["completed"]
    run=client.root/"data"/client.service.adapter.data_type/state["run_id"]
    before={p.relative_to(run).as_posix():sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
    summary=client.get(f"{base}/runs/{state['run_id']}/summary").json()
    assert summary["parameters"]["RUN_TAG"]==client.experiment_params.run_tag and summary["completion_status"]=="completed"
    assert not summary["artifacts"]
    after={p.relative_to(run).as_posix():sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
    assert before==after
    assert client.get(base+"/runs/missing/summary").status_code==404


def test_empty_session_finish_keeps_unconnected_outputs_unknown(client):
    response=client.post(client.base+"/sessions",json={"parameters":client.experiment_params.to_external()})
    assert response.status_code==200,response.text
    sid=response.json()["session_id"]
    wait(lambda:client.service.get(sid)["status"] in ("waiting","closed"))
    state=client.service.get(sid)
    assert state["status"]=="waiting" and state["run_id"] is None
    response=client.post(f"{client.base}/sessions/{sid}/finish",json={"revision":state["revision"]})
    assert response.status_code==200,response.text
    wait(lambda:client.service.get(sid)["status"]=="closed")
    assert set(client.service.get(sid)["outputs"].values())=={"未知"}, "未连接仪器不能宣称已实际关闭或回读"
    wait(lambda:not client.manager.hardware_lock.locked())


def test_analysis_binding_mutex_strict_requests_and_main_wiring(client,monkeypatch):
    base=client.base
    run=client.root/"data"/client.service.adapter.data_type/"historical"
    if client.module=="t2_calibration":
        import numpy as np
        (run/"raw").mkdir(parents=True)
        (run/"experiment_config.yaml").write_text("experiment_id: t2-calibration\n",encoding="utf-8")
        t=np.arange(4000)/400000;y=.2*np.exp(-t/.002)*np.cos(2*np.pi*90000*t)
        np.savez(run/"raw/fid_waveforms.npz",time=t,avg_waveform=y,waveforms=[y],rf_gate_freq=90000.,trigger_slope="FALLing")
    else: synthetic_run(run,client.module)
    assert client.get(base+"/runs").json()["total"]==1
    summary=client.get(base+"/runs/historical/summary").json()
    assert not summary["artifacts"]
    assert client.post(base+"/sessions",json={"parameters":{"unknown":1}}).status_code==422
    assert client.post(base+"/sessions",json={"parameters":{},"unknown":1}).status_code==422
    state=client.get(base+"/runs/historical/analysis").json()
    entered,release=Event(),Event()
    adapter=client.service.adapter;execute=adapter._execute
    def blocked(module,**kwargs):
        entered.set();assert release.wait(30)
        return execute(module,**kwargs)
    monkeypatch.setattr(adapter,"_execute",blocked)
    body=dict(revision=state["revision"],step_ids=["B01"],analysis_parameters={})
    try:
        response=client.post(base+"/runs/historical/analysis",json=body)
        assert response.status_code==200,response.text
        jid=response.json()["id"];assert entered.wait(5)
        assert client.post(base+"/runs/historical/analysis",json=body).status_code==409
        assert not client.manager.hardware_lock.locked(),"离线分析不能占用硬件锁"
    finally: release.set()
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    assert client.manager.get(jid).status=="completed",client.manager.get(jid).error
    assert client.get(base+"/runs/historical/analysis").json()["steps"]["B01"]["status"]=="completed"
    assert client.post(base+"/runs/historical/analysis",json=body).status_code==409
    from . import main
    monkeypatch.setattr(main,client.main_name,client.service)
    assert client.service in main._workflow_session_services()
    assert adapter.id in main.WORKFLOW_RUN_FORMATS
    assert TestClient(main.app).get(base+"/workflow").status_code==200
