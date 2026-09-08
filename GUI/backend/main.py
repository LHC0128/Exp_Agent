"""Bell-Bloom 实验系统本地 FastAPI 入口。"""

from __future__ import annotations

import asyncio
import json
import shutil
import stat
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lab_workflows.clock_sync import synchronize_clocks
from lab_workflows.common import find_project_root
from lab_workflows.devices import discover_devices
from lab_workflows.instrument_control import (
    ControlRevisionConflict,
    apply_control_target_current_source,
    apply_control_target_emission,
    apply_control_target_generator,
    apply_control_target_laser,
    apply_control_target_scope,
    apply_control_target_tec,
    apply_current_source,
    apply_generator_channel,
    apply_laser_emission,
    apply_laser_settings,
    apply_scope,
    list_control_targets,
    read_control_target,
    read_control_targets,
    read_device,
)
from lab_workflows.instrument_config import (
    RevisionConflict,
    discover_visa_devices,
    public_device_library,
    public_physical_mappings,
    save_device_library,
    save_physical_mappings,
)
from lab_workflows.keithley_arb import (
    convert_frequency_source,
    parse_arbitrary_text,
    parse_calibration_text,
)
from lab_workflows.experiments import get_experiment, list_experiments
from lab_workflows.experiments.catalog import (
    add_tag,
    apply_parameter_layout,
    assign_tag,
    public_catalog,
    rename_tag,
    set_experiment_description,
    set_experiment_metadata,
    set_parameter_layout,
    validate_parameter_layout,
)
from lab_workflows.phase_calibration import calibrate_demod0_safely
from lab_workflows.z_arbitrary_control import (
    ZArbitraryControlService, ZArbitrarySettings, list_sources, preview_source,
)

from .jobs import Job, manager
from .schemas import (
    ControlCurrentSourceBody,
    ControlEmissionBody,
    ControlGeneratorBody,
    ControlLaserBody,
    ControlRevisionBody,
    ControlScopeBody,
    ControlTargetCatalog,
    ControlTargetBulkResponse,
    ControlTargetResponse,
    ControlTecBody,
    CurrentSourceSettingsBody,
    DeviceSnapshot,
    DeviceSummary,
    GeneratorChannelSettingsBody,
    LaserEmissionSettingsBody,
    LaserSettingsBody,
    ScopeSettingsBody,
    ZArbitraryActionBody,
    ZArbitraryPreview,
    ZArbitrarySourceItem,
    ZArbitraryStatus,
)


app = FastAPI(title="Bell-Bloom 实验控制台", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
ROOT = find_project_root()
z_arbitrary_service = ZArbitraryControlService(ROOT)


@app.middleware("http")
async def disable_api_cache(request, call_next):
    """API 状态不能被浏览器缓存，避免后端升级后复用旧响应。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


class PhaseBody(BaseModel):
    tolerance_deg: float = 1.0
    max_attempts: int = 5
    settle_time: float = 0.2


class KeithleyWaveformConvertBody(BaseModel):
    arbitrary_text: str = ""
    calibration_text: str | None = None


class ExperimentBody(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class ParameterLayoutBody(BaseModel):
    basic: list[str]
    advanced: list[str]


class ExperimentDefaultsBody(ExperimentBody):
    parameter_layout: ParameterLayoutBody | None = None


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


class DeviceLibraryBody(BaseModel):
    base_revision: str
    devices: dict[str, dict[str, Any]]


class PhysicalMappingsBody(BaseModel):
    base_revision: str
    mapping: dict[str, dict[str, Any]]
    constraints: dict[str, dict[str, list[str]]]


def _job_or_404(job_id: str) -> Job:
    try:
        return manager.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _conflict(detail: str, code: str) -> HTTPException:
    """409 响应附加结构化错误码，前端按 code 分支处理。"""
    return HTTPException(409, detail, headers={"X-Error-Code": code})


def _with_short_hardware_lock(action):
    # 短时仪器操作与长任务共享同一把硬件锁和同一排队条件：
    # 存在排队任务时拒绝插队，返回 hardware_busy。
    if not manager.try_acquire_short_hardware():
        raise _conflict("其他硬件任务正在运行", "hardware_busy")
    try:
        return action()
    except ControlRevisionConflict as exc:
        raise _conflict(str(exc), "revision_conflict") from exc
    except (KeyError, ValueError, TypeError, PermissionError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"仪器操作失败: {exc}") from exc
    finally:
        manager.hardware_lock.release()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "hardware_busy": manager.hardware_lock.locked(),
        "queued_hardware_jobs": manager.queued_hardware_count(),
    }


@app.get("/api/devices", response_model=list[DeviceSummary])
def devices():
    return [record.to_dict() for record in discover_devices()]


@app.get("/api/control-targets", response_model=ControlTargetCatalog)
def control_targets():
    return list_control_targets()


@app.post(
    "/api/control-targets/{mapping_key}/refresh",
    response_model=ControlTargetResponse,
)
def refresh_control_target(mapping_key: str, body: ControlRevisionBody):
    return _with_short_hardware_lock(lambda: read_control_target(
        mapping_key,
        body.device_library_revision,
        body.physical_mapping_revision,
    ))


@app.post("/api/control-targets/refresh-all", response_model=ControlTargetBulkResponse)
def refresh_all_control_targets(body: ControlRevisionBody):
    results = _with_short_hardware_lock(lambda: read_control_targets(
        body.device_library_revision,
        body.physical_mapping_revision,
    ))
    return {"results": results}


@app.put("/api/control-targets/{mapping_key}/generator", response_model=ControlTargetResponse)
def update_control_generator(mapping_key: str, body: ControlGeneratorBody):
    return _with_short_hardware_lock(lambda: apply_control_target_generator(
        mapping_key, body.device_library_revision, body.physical_mapping_revision,
        body.settings.model_dump(exclude_unset=True),
    ))


@app.put("/api/control-targets/{mapping_key}/current-source", response_model=ControlTargetResponse)
def update_control_current_source(mapping_key: str, body: ControlCurrentSourceBody):
    return _with_short_hardware_lock(lambda: apply_control_target_current_source(
        mapping_key, body.device_library_revision, body.physical_mapping_revision,
        body.settings.model_dump(exclude_unset=True),
    ))


@app.put("/api/control-targets/{mapping_key}/laser", response_model=ControlTargetResponse)
def update_control_laser(mapping_key: str, body: ControlLaserBody):
    return _with_short_hardware_lock(lambda: apply_control_target_laser(
        mapping_key, body.device_library_revision, body.physical_mapping_revision,
        body.settings.model_dump(exclude_unset=True),
    ))


@app.put("/api/control-targets/{mapping_key}/emission", response_model=ControlTargetResponse)
def update_control_emission(mapping_key: str, body: ControlEmissionBody):
    return _with_short_hardware_lock(lambda: apply_control_target_emission(
        mapping_key, body.device_library_revision, body.physical_mapping_revision,
        body.settings.model_dump(exclude_unset=True),
    ))


@app.put("/api/control-targets/{mapping_key}/scope", response_model=ControlTargetResponse)
def update_control_scope(mapping_key: str, body: ControlScopeBody):
    return _with_short_hardware_lock(lambda: apply_control_target_scope(
        mapping_key, body.device_library_revision, body.physical_mapping_revision,
        body.settings.model_dump(exclude_unset=True),
    ))


@app.put("/api/control-targets/{mapping_key}/tec", response_model=ControlTargetResponse)
def update_control_tec(mapping_key: str, body: ControlTecBody):
    return _with_short_hardware_lock(lambda: apply_control_target_tec(
        mapping_key, body.device_library_revision, body.physical_mapping_revision,
        body.settings.model_dump(exclude_unset=True),
    ))


@app.get("/api/device-library")
def device_library():
    return public_device_library(ROOT)


def _with_configuration_lock(action):
    """设备库/物理映射写入：检查与写入在硬件锁内原子完成。

    仅做一次 lock 检查后直接写入会留下 TOCTOU 竞态（检查后排队
    任务可能立刻获得硬件锁并开始配置硬件），因此这里与硬件任务
    调度同步：存在排队任务或运行中任务时拒绝，通过后持锁完成写入。
    """
    if not manager.try_acquire_short_hardware():
        raise _conflict("硬件任务运行期间不能修改设备配置", "hardware_busy")
    try:
        return action()
    except RevisionConflict as exc:
        raise _conflict(str(exc), "revision_conflict") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, {"errors": [str(exc)]}) from exc
    finally:
        manager.hardware_lock.release()


@app.put("/api/device-library")
def update_device_library(body: DeviceLibraryBody):
    return _with_configuration_lock(
        lambda: save_device_library(
            body.devices,
            base_revision=body.base_revision,
            root=ROOT,
        )
    )


@app.post("/api/device-library/discover-visa")
def discover_device_library():
    return _with_short_hardware_lock(discover_visa_devices)


@app.get("/api/physical-mappings")
def physical_mappings():
    return public_physical_mappings(ROOT)


@app.put("/api/physical-mappings")
def update_physical_mappings(body: PhysicalMappingsBody):
    return _with_configuration_lock(
        lambda: save_physical_mappings(
            body.mapping,
            body.constraints,
            base_revision=body.base_revision,
            root=ROOT,
        )
    )


@app.post("/api/devices/{device_id}/refresh", response_model=DeviceSnapshot)
def refresh_device(device_id: str):
    return _with_short_hardware_lock(lambda: read_device(device_id))


@app.put(
    "/api/devices/{device_id}/channels/{channel}",
    response_model=DeviceSnapshot,
)
def update_channel(
    device_id: str,
    channel: int,
    body: GeneratorChannelSettingsBody,
):
    return _with_short_hardware_lock(
        lambda: apply_generator_channel(
            device_id,
            channel,
            body.settings.model_dump(exclude_unset=True),
        )
    )


@app.put("/api/devices/{device_id}/scope", response_model=DeviceSnapshot)
def update_scope(device_id: str, body: ScopeSettingsBody):
    return _with_short_hardware_lock(
        lambda: apply_scope(
            device_id,
            body.settings.model_dump(exclude_unset=True),
        )
    )


@app.put(
    "/api/devices/{device_id}/current-source",
    response_model=DeviceSnapshot,
)
def update_current_source(
    device_id: str,
    body: CurrentSourceSettingsBody,
):
    return _with_short_hardware_lock(
        lambda: apply_current_source(
            device_id,
            body.settings.model_dump(exclude_unset=True),
        )
    )


@app.put("/api/devices/{device_id}/laser", response_model=DeviceSnapshot)
def update_laser(device_id: str, body: LaserSettingsBody):
    return _with_short_hardware_lock(
        lambda: apply_laser_settings(
            device_id,
            body.settings.model_dump(exclude_unset=True),
        )
    )


@app.put(
    "/api/devices/{device_id}/emission",
    response_model=DeviceSnapshot,
)
def update_laser_emission(
    device_id: str,
    body: LaserEmissionSettingsBody,
):
    return _with_short_hardware_lock(
        lambda: apply_laser_emission(
            device_id,
            body.settings.model_dump(exclude_unset=True),
        )
    )


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


@app.get("/api/tools/z-arbitrary-control/sources", response_model=list[ZArbitrarySourceItem])
def z_arbitrary_sources():
    return list_sources(ROOT)


@app.get("/api/tools/z-arbitrary-control/preview/{run_name}", response_model=ZArbitraryPreview)
def z_arbitrary_preview(run_name: str):
    try:
        return preview_source(ROOT, run_name)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/tools/z-arbitrary-control/state", response_model=ZArbitraryStatus)
def z_arbitrary_state():
    return z_arbitrary_service.status()


@app.post("/api/tools/z-arbitrary-control/actions")
def z_arbitrary_action(body: ZArbitraryActionBody):
    request = body.model_dump(exclude={"settings"})
    request["settings"] = ZArbitrarySettings(**body.settings.model_dump())
    try:
        # 入队前提供即时错误反馈；获得硬件锁后 execute 会再次完整预检。
        z_arbitrary_service.preflight(**request)
    except ControlRevisionConflict as exc:
        raise _conflict(str(exc), "revision_conflict") from exc
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return manager.create(
        "z-arbitrary-control",
        lambda current: z_arbitrary_service.execute(
            **request, cancellation=current.cancellation,
            progress=lambda event: manager.event(current, event),
        ),
    ).public()


@app.post("/api/tools/keithley-waveform-convert")
def keithley_waveform_convert(body: KeithleyWaveformConvertBody):
    """纯计算接口：解析 6221 任意波与标定文本并换算电流包络，不连接硬件。"""
    try:
        calibration = (
            parse_calibration_text(body.calibration_text)
            if body.calibration_text else None
        )
        source = (
            parse_arbitrary_text(body.arbitrary_text)
            if body.arbitrary_text.strip() else None
        )
        converted = (
            convert_frequency_source(source, calibration)
            if source is not None else None
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "source": source.to_dict() if source else None,
        "calibration": calibration.to_dict() if calibration else None,
        "waveform": converted.to_dict() if converted else None,
    }


def _experiment_or_404(experiment_id: str):
    try:
        return get_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _default_categories() -> dict[str, str]:
    return {definition.id: definition.category for definition in list_experiments()}


def _experiment_schema(definition) -> dict[str, Any]:
    return apply_parameter_layout(
        definition.id,
        definition.schema(),
        _default_categories(),
    )


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
    return _experiment_schema(_experiment_or_404(experiment_id))


@app.put("/api/experiments/{experiment_id}/defaults")
def save_experiment_defaults(experiment_id: str, body: ExperimentDefaultsBody):
    definition = _experiment_or_404(experiment_id)
    schema = definition.schema()
    layout = None
    saved_schema = None
    try:
        if body.parameter_layout is not None:
            layout = validate_parameter_layout(
                {
                    "basic": body.parameter_layout.basic,
                    "advanced": body.parameter_layout.advanced,
                },
                schema.get("fields", []),
            )
        definition.save_defaults(body.parameters)
        if layout is not None:
            set_parameter_layout(
                experiment_id,
                layout,
                schema.get("fields", []),
                _default_categories(),
            )
        saved_schema = _experiment_schema(definition)
        if layout is not None and saved_schema.get("parameter_layout") != layout:
            raise RuntimeError("参数分类写入后回读不一致")
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(422, {"errors": [str(exc)]}) from exc
    return {
        "ok": True,
        "message": "当前参数及分类已保存为默认值" if layout is not None else "当前参数已保存为默认值",
        "schema": saved_schema,
    }


@app.post("/api/experiments/{experiment_id}/preflight")
def experiment_preflight(experiment_id: str, body: ExperimentBody):
    errors = _experiment_or_404(experiment_id).preflight(body.parameters)
    return {"ok": not errors, "errors": errors}


@app.post("/api/experiments/{experiment_id}/derive")
def experiment_derive(experiment_id: str, body: ExperimentBody):
    try:
        values = _experiment_or_404(experiment_id).derive(body.parameters)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, {"errors": [str(exc)]}) from exc
    return {"ok": True, "values": values}


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
    return manager.list_summaries()


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
async def job_events(job_id: str, request: Request):
    current = _job_or_404(job_id)
    try:
        index = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        index = 0

    async def stream():
        nonlocal index
        while True:
            events, status = manager.stream_state(current, index)
            for event in events:
                index = int(event["index"]) + 1
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {index}\ndata: {payload}\n\n"
            if status in {"completed", "failed", "cancelled"}:
                payload = json.dumps(
                    {"done": True, "status": status},
                    ensure_ascii=False,
                )
                yield f"id: {index + 1}\ndata: {payload}\n\n"
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _is_existing_dir(path: Path) -> bool:
    """目录存在性判断，并发删除时返回 False 而不是抛异常。"""
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_existing_file(path: Path) -> bool:
    """文件存在性判断，并发删除时返回 False 而不是抛异常。"""
    try:
        return path.is_file()
    except OSError:
        return False


@app.get("/api/runs")
def runs(experiment_id: str | None = None, limit: int = 50, offset: int = 0):
    definitions = [
        definition for definition in list_experiments()
        if experiment_id is None or definition.id == experiment_id
    ]
    result = []
    for definition in definitions:
        base = ROOT / "data" / definition.data_type
        try:
            if not base.exists():
                continue
            candidates = [
                item for item in base.iterdir()
                if _is_existing_dir(item)
            ]
        except OSError:
            # 数据基目录被并发清理时跳过，不让整个列表请求失败。
            continue
        for path in candidates:
            try:
                modified_at = path.stat().st_mtime
            except OSError:
                # 运行目录被并发删除时跳过该目录，不让整个接口返回 500。
                continue
            results_dir = path / "results"
            try:
                artifacts = (
                    [
                        item.name for item in results_dir.iterdir()
                        if _is_existing_file(item)
                    ]
                    if _is_existing_dir(results_dir)
                    else []
                )
            except OSError:
                artifacts = []
            result.append({
                "id": path.name,
                "experiment_id": definition.id,
                "experiment_title": definition.title,
                "path": str(path),
                "artifacts": artifacts,
                "can_analyze": definition.analysis_runner is not None,
                "modified_at": modified_at,
            })
    result.sort(key=lambda item: item["modified_at"], reverse=True)
    total = len(result)
    page_size = max(1, min(limit, 200))
    page_start = max(0, offset)
    return {
        "total": total,
        "offset": page_start,
        "limit": page_size,
        "runs": result[page_start:page_start + page_size],
    }


def _run_dir(experiment_id: str, run_id: str) -> Path:
    definition = _experiment_or_404(experiment_id)
    base = (ROOT / "data" / definition.data_type).resolve()
    path = (base / run_id).resolve()
    if base not in path.parents or not path.is_dir():
        raise HTTPException(404, "运行目录不存在")
    return path


@app.delete("/api/runs/{experiment_id}/{run_id}")
def delete_run(experiment_id: str, run_id: str):
    """选定运行目录的永久删除，与 GUI 采集/分析任务注册原子互斥。"""
    definition = _experiment_or_404(experiment_id)
    if (not run_id or run_id.endswith((".", " "))
            or any(c in run_id for c in "/\\:")):
        raise HTTPException(400, "运行 ID 必须是单个目录名")
    with manager.jobs_lock:
        # 采集任务结束前还可能自动分析或扫描同类目录，因此整类运行暂缓删除。
        ids = {item.id for item in list_experiments() if item.data_type == definition.data_type}
        if any(
            item.status in {"queued", "running"} and (
                item.kind in {f"experiment:{value}" for value in ids}
                or item.kind in {f"analysis:{value}:{run_id}" for value in ids}
            ) for item in manager.jobs.values()
        ):
            raise _conflict("该数据正在采集或分析，暂时不能删除", "run_busy")
        data_root = (ROOT / "data").resolve()
        base = data_root / definition.data_type
        candidate = base / run_id
        path = _run_dir(experiment_id, run_id)
        if base.resolve().parent != data_root or path.parent != base.resolve():
            raise HTTPException(400, "运行目录超出允许范围")
        # Windows junction 与符号链接均不得作为删除目标或中间目录。
        pending = [base, candidate]
        while pending:
            entry = pending.pop()
            info = entry.lstat()
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise HTTPException(400, "含链接或联接点的运行目录不能删除")
            if entry != base and entry.is_dir():
                pending.extend(entry.iterdir())
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise HTTPException(409, f"删除失败，请刷新历史后重试: {exc}") from exc
    return {"ok": True, "experiment_id": experiment_id, "run_id": run_id}


@app.post("/api/runs/{run_id}/analyze")
def analyze_run(run_id: str, body: AnalysisBody):
    with manager.jobs_lock:
        return _create_analysis_job(run_id, body)


def _create_analysis_job(run_id: str, body: AnalysisBody):
    definition = _experiment_or_404(body.experiment_id)
    if any(manager.has_active_kind(f"experiment:{item.id}") for item in list_experiments()
           if item.data_type == definition.data_type):
        raise _conflict("同类数据仍在采集或自动分析，请稍后重新分析", "run_busy")
    run_dir = _run_dir(body.experiment_id, run_id)
    if not definition.analysis_runner:
        raise _conflict("该实验没有独立离线分析器", "no_analyzer")
    kind = f"analysis:{definition.id}:{run_id}"
    if manager.has_active_kind(kind):
        raise _conflict("该运行目录的分析任务正在执行", "analysis_running")
    job = manager.create(
        kind,
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
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, f"接口不存在: /{path}")
        candidate = (FRONTEND / path).resolve()
        if FRONTEND.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND / "index.html")
