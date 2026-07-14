"""Bell-Bloom 实验系统本地 FastAPI 入口。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lab_workflows.clock_sync import synchronize_clocks
from lab_workflows.common import find_project_root
from lab_workflows.devices import discover_devices
from lab_workflows.instrument_control import (
    apply_generator_channel,
    apply_scope,
    read_device,
)
from lab_workflows.experiments import get_experiment, list_experiments
from lab_workflows.experiments.catalog import (
    add_tag,
    assign_tag,
    public_catalog,
    rename_tag,
    set_experiment_description,
    set_experiment_metadata,
)
from lab_workflows.phase_calibration import calibrate_demod0_safely

from .jobs import Job, manager


app = FastAPI(title="Bell-Bloom 实验控制台", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
ROOT = find_project_root()


@app.middleware("http")
async def disable_api_cache(request, call_next):
    """API 状态不能被浏览器缓存，避免后端升级后复用旧响应。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


class SettingsBody(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class PhaseBody(BaseModel):
    tolerance_deg: float = 1.0
    max_attempts: int = 5
    settle_time: float = 0.2


class ExperimentBody(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class AnalysisBody(BaseModel):
    experiment_id: str


class TagBody(BaseModel):
    label: str


class TagAssignmentBody(BaseModel):
    tag_id: str


class ExperimentDescriptionBody(BaseModel):
    description: str


class ExperimentMetadataBody(BaseModel):
    title: str
    description: str


def _job_or_404(job_id: str) -> Job:
    try:
        return manager.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _with_short_hardware_lock(action):
    if not manager.hardware_lock.acquire(blocking=False):
        raise HTTPException(409, "其他硬件任务正在运行")
    try:
        return action()
    except (KeyError, ValueError, TypeError, PermissionError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"仪器操作失败: {exc}") from exc
    finally:
        manager.hardware_lock.release()


@app.get("/api/health")
def health():
    return {"status": "ok", "hardware_busy": manager.hardware_lock.locked()}


@app.get("/api/devices")
def devices():
    return [record.to_dict() for record in discover_devices()]


@app.post("/api/devices/{device_id}/refresh")
def refresh_device(device_id: str):
    return _with_short_hardware_lock(lambda: read_device(device_id))


@app.put("/api/devices/{device_id}/channels/{channel}")
def update_channel(device_id: str, channel: int, body: SettingsBody):
    return _with_short_hardware_lock(
        lambda: apply_generator_channel(device_id, channel, body.settings)
    )


@app.put("/api/devices/{device_id}/scope")
def update_scope(device_id: str, body: SettingsBody):
    return _with_short_hardware_lock(lambda: apply_scope(device_id, body.settings))


@app.post("/api/clocks/sync")
def sync_clocks():
    job = manager.create(
        "clock-sync",
        lambda current: [
            result.to_dict()
            for result in synchronize_clocks(progress=lambda event: manager.event(current, event))
        ],
    )
    return job.public()


@app.post("/api/tools/demod0-phase-calibration")
def phase_calibration(body: PhaseBody):
    job = manager.create(
        "phase-calibration",
        lambda current: calibrate_demod0_safely(
            body.tolerance_deg,
            body.max_attempts,
            body.settle_time,
            progress=lambda event: manager.event(current, event),
        ).to_dict(),
    )
    return job.public()


def _experiment_or_404(experiment_id: str):
    try:
        return get_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _default_categories() -> dict[str, str]:
    return {definition.id: definition.category for definition in list_experiments()}


def _experiment_public(definition, catalog: dict[str, Any] | None = None):
    current = catalog or public_catalog(_default_categories())
    tag_id = current["assignments"].get(definition.id, definition.category)
    labels = {item["id"]: item["label"] for item in current["tags"]}
    return {
        **definition.public(),
        "category": tag_id,
        "category_label": labels.get(tag_id, tag_id),
        "title": current["experiment_titles"].get(
            definition.id, definition.title
        ),
        "description": current["experiment_descriptions"].get(
            definition.id, definition.description
        ),
    }


@app.get("/api/experiment-tags")
def experiment_tags():
    return public_catalog(_default_categories())


@app.post("/api/experiment-tags")
def create_experiment_tag(body: TagBody):
    try:
        tag = add_tag(body.label, _default_categories())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "tag": tag, "catalog": public_catalog(_default_categories())}


@app.put("/api/experiment-tags/{tag_id}")
def update_experiment_tag(tag_id: str, body: TagBody):
    try:
        tag = rename_tag(tag_id, body.label, _default_categories())
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "tag": tag, "catalog": public_catalog(_default_categories())}


@app.get("/api/experiments")
def experiments():
    catalog = public_catalog(_default_categories())
    return [_experiment_public(definition, catalog) for definition in list_experiments()]


@app.get("/api/experiments/{experiment_id}")
def experiment(experiment_id: str):
    return _experiment_public(_experiment_or_404(experiment_id))


@app.put("/api/experiments/{experiment_id}/tag")
def update_experiment_assignment(experiment_id: str, body: TagAssignmentBody):
    try:
        assign_tag(experiment_id, body.tag_id, _default_categories())
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "ok": True,
        "experiment": _experiment_public(_experiment_or_404(experiment_id)),
        "catalog": public_catalog(_default_categories()),
    }


@app.put("/api/experiments/{experiment_id}/description")
def update_experiment_description(
    experiment_id: str,
    body: ExperimentDescriptionBody,
):
    try:
        set_experiment_description(
            experiment_id, body.description, _default_categories()
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "ok": True,
        "experiment": _experiment_public(_experiment_or_404(experiment_id)),
    }


@app.put("/api/experiments/{experiment_id}/metadata")
def update_experiment_metadata(
    experiment_id: str,
    body: ExperimentMetadataBody,
):
    try:
        set_experiment_metadata(
            experiment_id,
            body.title,
            body.description,
            _default_categories(),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "ok": True,
        "experiment": _experiment_public(_experiment_or_404(experiment_id)),
    }


@app.get("/api/experiments/{experiment_id}/schema")
def experiment_schema(experiment_id: str):
    return _experiment_or_404(experiment_id).schema()


@app.put("/api/experiments/{experiment_id}/defaults")
def save_experiment_defaults(experiment_id: str, body: ExperimentBody):
    definition = _experiment_or_404(experiment_id)
    try:
        definition.save_defaults(body.parameters)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(422, {"errors": [str(exc)]}) from exc
    return {
        "ok": True,
        "message": "当前参数已保存为默认值",
        "schema": definition.schema(),
    }


@app.post("/api/experiments/{experiment_id}/preflight")
def experiment_preflight(experiment_id: str, body: ExperimentBody):
    errors = _experiment_or_404(experiment_id).preflight(body.parameters)
    return {"ok": not errors, "errors": errors}


@app.post("/api/experiments/{experiment_id}/runs")
def experiment_run(experiment_id: str, body: ExperimentBody):
    definition = _experiment_or_404(experiment_id)
    errors = definition.preflight(body.parameters)
    if errors:
        raise HTTPException(422, {"errors": errors})
    job = manager.create(
        f"experiment:{definition.id}",
        lambda current: definition.run(
            body.parameters,
            progress=lambda event: manager.event(current, event),
            cancellation=current.cancellation,
        ),
    )
    return job.public()


@app.get("/api/jobs")
def jobs():
    return [job.public() for job in reversed(list(manager.jobs.values()))]


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    return _job_or_404(job_id).public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        return manager.cancel(job_id).public()
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    current = _job_or_404(job_id)

    async def stream():
        index = 0
        while True:
            while index < len(current.events):
                yield f"data: {json.dumps(current.events[index], ensure_ascii=False)}\n\n"
                index += 1
            if current.status in {"completed", "failed", "cancelled"}:
                yield f"data: {json.dumps({'done': True, 'status': current.status}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/runs")
def runs():
    result = []
    for definition in list_experiments():
        base = ROOT / "data" / definition.data_type
        if not base.exists():
            continue
        for path in (item for item in base.iterdir() if item.is_dir()):
            results_dir = path / "results"
            artifacts = [item.name for item in results_dir.iterdir() if item.is_file()] if results_dir.exists() else []
            result.append({
                "id": path.name,
                "experiment_id": definition.id,
                "experiment_title": definition.title,
                "path": str(path),
                "artifacts": artifacts,
                "can_analyze": definition.analysis_runner is not None,
                "modified_at": path.stat().st_mtime,
            })
    return sorted(result, key=lambda item: item["modified_at"], reverse=True)


def _run_dir(experiment_id: str, run_id: str) -> Path:
    definition = _experiment_or_404(experiment_id)
    base = (ROOT / "data" / definition.data_type).resolve()
    path = (base / run_id).resolve()
    if base not in path.parents or not path.is_dir():
        raise HTTPException(404, "运行目录不存在")
    return path


@app.post("/api/runs/{run_id}/analyze")
def analyze_run(run_id: str, body: AnalysisBody):
    definition = _experiment_or_404(body.experiment_id)
    run_dir = _run_dir(body.experiment_id, run_id)
    if not definition.analysis_runner:
        raise HTTPException(409, "该实验没有独立离线分析器")
    job = manager.create(
        f"analysis:{definition.id}",
        lambda current: definition.analyze(
            run_dir, progress=lambda event: manager.event(current, event)
        ),
        hardware_required=False,
    )
    return job.public()


@app.get("/api/runs/{experiment_id}/{run_id}/artifacts/{name}")
def artifact(experiment_id: str, run_id: str, name: str):
    run_dir = _run_dir(experiment_id, run_id)
    results = (run_dir / "results").resolve()
    path = (results / name).resolve()
    if results not in path.parents or not path.is_file():
        raise HTTPException(404, "结果文件不存在")
    return FileResponse(path)


FRONTEND = ROOT / "GUI" / "frontend" / "dist"
if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        candidate = (FRONTEND / path).resolve()
        if FRONTEND.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND / "index.html")
