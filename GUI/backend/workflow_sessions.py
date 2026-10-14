"""实验会话共用的队列、修订、恢复、取消和独立分析接口。"""
from copy import deepcopy
from pathlib import Path
from queue import Queue
from threading import RLock
from typing import Any, Literal
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field, StrictInt
import yaml
from lab_workflows.experiment_modules.noise_spectrum_xy.state_io import read_state
from .schemas import StrictModel

def conflict(message, code="session_busy"):
    return HTTPException(409, message, headers={"X-Error-Code": code})


class XYStage(StrictModel):
    id: str
    title: str
    group: str
    dependencies: list[str]
    parameters: list[str]
    display_parameters: list[str]
    artifacts: list[str]


class XYFlow(StrictModel):
    acquisition: list[XYStage]
    analysis: list[XYStage]
    parameter_impacts: dict[str, dict[str, list[str]]]
    diagram_url: str


class XYStageState(StrictModel):
    status: str
    error: str = ""
    files: dict[str, list[int] | None] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)


class XYSession(StrictModel):
    session_id: str
    job_id: str
    revision: int
    status: Literal["queued", "starting", "waiting", "running", "closed"]
    parameters: dict[str, Any]
    steps: dict[str, XYStageState]
    current_step: str | None = None
    run_id: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    output_source: str = "最近成功模块的写入结果；未经额外回读"
    demod_mode: str = "unknown"
    rf_phase_calibration: dict[str, Any] | None = None
    xy_phase_calibration: dict[str, Any] | None = None
    safety_shutdown: dict[str, Any] | None = None
    clock_sources: dict[str, Any] = Field(default_factory=dict)
    demod_phase_deg: float | None = None
    daq_settings: dict[str, Any] | None = None
    pending_request: str | None = None
    error: str | None = None


class XYCreateBody(StrictModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class XYRevisionBody(StrictModel):
    revision: StrictInt = Field(ge=0)


class XYParametersBody(XYRevisionBody):
    parameters: dict[str, Any]


class XYStepsBody(XYRevisionBody):
    step_ids: list[str] = Field(min_length=1)


class XYAnalysisBody(XYStepsBody):
    analysis_parameters: dict[str, Any] = Field(default_factory=dict)


class XYAnalysisState(StrictModel):
    run_id: str
    revision: int
    options: dict[str, float | str]
    steps: dict[str, XYStageState]
    fingerprint: dict[str, Any]
    summary: dict[str, Any]
    source_signature: dict[str, Any] | None = None


class WorkflowSessionService:
    def __init__(self, root, manager, *, adapter, preflight, stages, analysis_workflow, session_module, diagram_file):
        self.adapter, self.preflight, self.stages = adapter, preflight, stages
        self.analysis_workflow, self.session_module, self.diagram_file = analysis_workflow, session_module, diagram_file
        self.root = root
        self.manager = manager
        self.lock = RLock()
        self.sessions = {}

    def active(self):
        with self.lock:
            for session_id in reversed(self.sessions):
                value = self.get(session_id)
                if value["status"] != "closed":
                    return value
        return None

    def get(self, session_id):
        with self.lock:
            item = self.sessions.get(session_id)
            if item is None:
                raise HTTPException(404, "会话不存在；后台重启后请新建会话")
            value = deepcopy(item["state"])
            try:
                job = self.manager.get(value["job_id"])
            except KeyError:
                value.update(status="closed", pending_request=None)
                return value
            if job.status in ("completed", "cancelled", "failed"):
                value.update(status="closed", pending_request=None)
                value["error"] = job.error or value.get("error")
            return value

    def create(self, parameters):
        unknown = parameters.keys() - self.adapter.defaults().to_external().keys()
        if unknown:
            raise HTTPException(422, "未知参数：" + "、".join(sorted(unknown)))
        try:
            params = self.preflight(self.adapter._resolved(parameters), self.root())[0]
        except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
            raise HTTPException(422, str(exc)) from exc
        with self.lock:
            if self.active():
                raise conflict("已有存活的实验会话，请继续或结束该会话")
            session_id = uuid4().hex
            queue = Queue()
            state = dict(session_id=session_id, job_id="", revision=0, status="queued",
                parameters=params.to_external(), steps=self.stages.initial_states(self.stages.ACQUISITION),
                pending_request=None, error=None)
            item = self.sessions[session_id] = {"state": state, "queue": queue}

            def progress(job, event):
                with self.lock:
                    if snapshot := event.data.get("workflow_session"):
                        item["state"].update(snapshot)
                    if result := event.data.get("command_result"):
                        if result["request_id"] == item["state"].get("pending_request"):
                            item["state"].update(pending_request=None, error=result.get("error"))
                    self.manager.event(job, event)

            def run(job):
                with self.lock:
                    state["status"] = "starting"
                try:
                    self.adapter._execute(self.session_module,
                        parameters=params.to_external() | {"session_id": session_id},
                        commands=queue, cancellation=job.cancellation,
                        progress=lambda event: progress(job, event))
                    return self.get(session_id)
                finally:
                    with self.lock:
                        item["state"].update(status="closed", pending_request=None)

            job = self.manager.create(f"session:{self.adapter.id}", run)
            state["job_id"] = job.id
            return self.get(session_id)

    def command(self, session_id, revision, action, **payload):
        with self.lock:
            item = self.sessions.get(session_id)
            state = self.get(session_id)
            if revision != state["revision"]:
                raise conflict("会话修订已改变，请刷新后重试", "revision_conflict")
            if state["status"] != "waiting" or state.get("pending_request"):
                raise conflict("会话正在执行、排队或已结束")
            if self.manager.get(state["job_id"]).cancellation.cancelled:
                raise conflict("会话已请求安全停止，请等待收尾")
            if action == "steps":
                try:
                    payload["step_ids"] = self.stages.validate_selection(self.stages.ACQUISITION, state["steps"], payload["step_ids"])
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            elif action == "parameters":
                unknown = payload["parameters"].keys() - state["parameters"].keys()
                try:
                    if unknown:
                        raise ValueError("未知参数：" + "、".join(sorted(unknown)))
                    self.preflight(self.adapter.params_type.from_external(state["parameters"] | payload["parameters"]), self.root())
                except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
                    raise HTTPException(422, str(exc)) from exc
            request_id = uuid4().hex
            item["state"].update(pending_request=request_id, error=None)
            item["queue"].put(dict(request_id=request_id, revision=revision, action=action, **payload))
            return self.get(session_id)

    def close_communication(self):
        with self.lock:
            for item in self.sessions.values():
                item["queue"].put(None)

    def shutdown(self):
        from time import sleep
        self.close_communication()
        for item in list(self.sessions.values()):
            try:
                job = self.manager.get(item["state"]["job_id"])
            except KeyError:
                continue
            if job.status == "queued":
                job.cancellation.cancel()
            while job.status in ("queued", "running"):
                sleep(0.1)

    def run_directory(self, run_id, *, allow_running=False):
        base = (self.root() / "data" / self.adapter.data_type).resolve()
        path = (base / run_id).resolve()
        if path.parent != base or not path.is_dir():
            raise HTTPException(404, "运行目录不存在")
        config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
        if config.get("experiment_id") not in (None, self.adapter.id):
            raise HTTPException(422, "运行目录与实验不匹配")
        if not allow_running and config.get("completion_status") == "running":
            raise conflict("本轮扫描仍在写入数据", "run_busy")
        return path

    def analysis_state(self, run_id):
        try:
            path = self.run_directory(run_id)
            if self.manager.has_active_kind(f"analysis:{self.adapter.id}:{run_id}"):
                if snapshot := read_state(path / "analysis_state.json"):
                    return {**snapshot, "run_id": path.name}
            return self.analysis_workflow(path).snapshot()
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def analyze(self, run_id, body):
        with self.manager.jobs_lock:
            kind = f"analysis:{self.adapter.id}:{run_id}"
            if self.manager.has_active_kind(f"comparison:{self.adapter.id}"):
                raise conflict("对比任务正在执行", "comparison_running")
            if self.manager.has_active_kind(kind):
                raise conflict("该运行目录的分析正在执行", "analysis_running")
            path = self.run_directory(run_id)
            try:
                original = self.analysis_workflow(path)
                if original.revision != body.revision:
                    raise conflict("分析修订已改变，请刷新后重试", "revision_conflict")
                ctx = self.analysis_workflow(path, body.analysis_parameters)
                selected = self.stages.validate_selection(self.stages.ANALYSIS, ctx.states, body.step_ids)
            except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
                raise HTTPException(422, str(exc)) from exc

            def run(job):
                self.adapter._execute(self.adapter.analysis_module, run_dir=path,
                    parameters=dict(step_ids=selected, analysis_parameters=body.analysis_parameters, revision=body.revision),
                    progress=lambda event: self.manager.event(job, event), cancellation=job.cancellation)
                return {"run_dir": str(path), "analysis_state": self.analysis_state(run_id)}

            return self.manager.create(kind, run, hardware_required=False).public()

def create_router(service):
    router = APIRouter(prefix=f"/api/experiments/{service.adapter.id}")

    @router.get("/workflow", response_model=XYFlow)
    def workflow_definition():
        return service.stages.definition()

    @router.get("/workflow/diagram")
    def diagram():
        path = service.root() / service.diagram_file
        return FileResponse(path, media_type="application/json", filename=path.name)

    @router.get("/sessions/active", response_model=XYSession | None)
    def active():
        return service.active()

    @router.post("/sessions", response_model=XYSession)
    def create(body: XYCreateBody):
        return service.create(body.parameters)

    @router.get("/sessions/{session_id}", response_model=XYSession)
    def get(session_id: str):
        return service.get(session_id)

    @router.patch("/sessions/{session_id}/parameters", response_model=XYSession)
    def parameters(session_id: str, body: XYParametersBody):
        return service.command(session_id, body.revision, "parameters", parameters=body.parameters)

    @router.post("/sessions/{session_id}/steps", response_model=XYSession)
    def steps(session_id: str, body: XYStepsBody):
        return service.command(session_id, body.revision, "steps", step_ids=body.step_ids)

    @router.post("/sessions/{session_id}/finish", response_model=XYSession)
    def finish(session_id: str, body: XYRevisionBody):
        return service.command(session_id, body.revision, "finish")

    @router.get("/runs/{run_id}/analysis", response_model=XYAnalysisState)
    def analysis_state(run_id: str):
        return service.analysis_state(run_id)

    @router.get("/runs/{run_id}/files/{name:path}")
    def run_file(run_id: str, name: str):
        directory = service.run_directory(run_id, allow_running=True)
        path = (directory / name).resolve()
        if directory not in path.parents or not path.is_file():
            raise HTTPException(404, "产物不存在")
        return FileResponse(path, filename=path.name)

    @router.post("/runs/{run_id}/analysis")
    def analyze(run_id: str, body: XYAnalysisBody):
        return service.analyze(run_id, body)

    return router
