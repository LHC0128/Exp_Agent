"""Bell-Bloom 实验系统本地 FastAPI 入口。"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lab_workflows.clock_sync import initialize_output_loads, synchronize_clocks
from lab_workflows.common import find_project_root, load_mapping
from lab_workflows.devices import discover_devices
from lab_workflows.instrument_control import (
    ControlRevisionConflict,
    apply_control_target_current_source,
    apply_control_target_emission,
    apply_control_target_generator,
    apply_control_target_laser,
    apply_control_target_scope,
    apply_control_target_tec,
    apply_control_target_lockin,
    capture_control_target_lockin,
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
    select_lockin_device,
)
from lab_workflows.keithley_arb import (
    convert_frequency_source,
    parse_arbitrary_text,
    parse_calibration_text,
)
from lab_workflows.experiments import get_experiment, list_experiments
from lab_workflows.experiments.archived import ARCHIVED_EXPERIMENTS, ARCHIVE_PATH
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.run_format import (
    COMPARISON_FILES as RF_DIRECT_AW_COMPARISON_FILES,
    COMPARISON_JOB_KIND as RF_DIRECT_AW_COMPARISON_KIND,
    EXPERIMENT_ID as RF_DIRECT_AW_ID,
    analysis_block_reason as rf_direct_aw_analysis_block_reason,
    comparison_block_reason as rf_direct_aw_comparison_block_reason,
    is_new_run as is_rf_direct_aw_run,
    normalize_comparison_runs,
)
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.definition import comparison_runner as rf_direct_aw_comparison_runner
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw import run_format as rf_aw_format
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise import run_format as rf_known_noise_format
from lab_workflows.experiment_modules.rf_control_scale_sensitivity import run_format as rf_control_scale_format
from lab_workflows.experiment_modules.static_sensitivity.analysis import analysis_block_reason as static_analysis_block_reason
from lab_workflows.experiment_modules.static_sensitivity.run_format import format_block_reason as static_format_block_reason
from lab_workflows.experiment_modules.static_sensitivity_optimize_local import run_format as static_local_format
from lab_workflows.experiment_modules.noise_spectrum_xy import run_format as xy_format
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.analysis import analysis_block_reason as known_noise_analysis_block_reason
from lab_workflows.experiment_modules.scope_capture import run_format as scope_capture_format
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction import run_format as z_closed_loop_format
from lab_workflows.experiment_modules.mx_y_rf_frequency_response import run_format as mx_y_rf_frequency_format
from lab_workflows.experiment_modules.mx_y_rf_power_optimization import run_format as mx_y_rf_power_format
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response import run_format as mx_y_optimal_frequency_format
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift import run_format as mx_y_rf_drift_format
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity import run_format as mx_z_optimal_rf_format
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity import run_format as mx_z_optimal_xy_rf_format
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum import run_format as mx_z_optimal_xy_noise_format
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response import run_format as mx_z_optimal_xy_leakage_format
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response import run_format as mx_z_optimal_xy_phase_format
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance import run_format as mx_z_optimal_xyz_balance_format
from lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance import run_format as mx_z_optimal_dg_bias_format
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum import run_format as mx_main_field_scope_format
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration import run_format as mx_xy_residual_format
from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r import run_format as demod3_r_format
from lab_workflows.experiment_modules.projection_noise import run_format as projection_noise_format
from lab_workflows.experiment_modules.photon_shot_noise import run_format as photon_format
from lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency import run_format as constxy_format
from lab_workflows.experiment_modules.t1_calibration import run_format as t1_format
from lab_workflows.experiment_modules.bb_power_scan_burst import run_format as bb_power_format
from lab_workflows.experiment_modules.bb_duty_scan_burst import run_format as bb_duty_format
from lab_workflows.experiment_modules.z_field_bandwidth import run_format as z_bandwidth_format
RF_RUN_FORMATS = {rf_aw_format.EXPERIMENT_ID: rf_aw_format, rf_known_noise_format.EXPERIMENT_ID: rf_known_noise_format}
WORKFLOW_RUN_FORMATS = {item.EXPERIMENT_ID: item for item in (
    t1_format,
    static_local_format,
    bb_power_format,
    bb_duty_format,
    z_bandwidth_format, constxy_format, projection_noise_format, photon_format, demod3_r_format, mx_xy_residual_format, mx_main_field_scope_format, rf_control_scale_format, scope_capture_format, z_closed_loop_format, mx_y_rf_power_format, mx_y_rf_frequency_format, mx_y_optimal_frequency_format, mx_y_rf_drift_format, mx_z_optimal_rf_format, mx_z_optimal_xy_rf_format, mx_z_optimal_xy_noise_format, mx_z_optimal_xy_leakage_format, mx_z_optimal_xy_phase_format, mx_z_optimal_xyz_balance_format, mx_z_optimal_dg_bias_format,
)}
from lab_workflows.experiments.catalog import (
    add_tag,
    apply_parameter_layout,
    assign_tag,
    public_catalog,
    rename_tag,
    set_experiment_archived,
    set_experiment_description,
    set_experiment_metadata,
    set_parameter_layout,
    validate_parameter_layout,
)
from lab_workflows.phase_calibration import calibrate_demod0_safely
from lab_workflows.z_arbitrary_control import (
    ZArbitraryControlService, ZArbitrarySettings, list_sources, preview_source,
)

from .jobs import Job, RestartInProgressError, manager
from .run_management import checked_run_path, read_starred, rename_directory, save_starred, split_run_name
from .noise_spectrum_xy import XYSessionService, create_router as xy_router
from .updates import UpdatesService, create_router as updates_router
from .rf_sensitivity_direct_aw import RFSessionService, create_router as rf_workflow_router
from .rf_sensitivity_aw_known_noise import RFKnownNoiseSessionService
from .rf_control_scale_sensitivity import RFControlScaleSessionService
from .noise_spectrum_xy_known_noise import KnownNoiseSessionService
from .noise_spectrum_xy_probe_am import ProbeAMSessionService
from .static_sensitivity import StaticSessionService
from .static_sensitivity_optimize_local import StaticLocalSessionService, create_router as static_local_router
from .detection_chain_frequency_response import DetectionChainFrequencyResponseSessionService
from .mx_y_rf_sensitivity import MxYRFSessionService
from .mx_y_rf_probe_detuning_optimization import MxYRFProbeDetuningSessionService
from .mx_y_rf_power_optimization import MxYRFPowerOptimizationSessionService
from .scope_capture import ScopeCaptureSessionService
from .mx_y_rf_frequency_response import MxYRFFrequencyResponseSessionService
from .mx_y_optimal_control_rf_frequency_response import MxYOptimalControlFrequencySessionService
from .mx_z_optimal_control_rf_sensitivity import MxZOptimalControlRFSessionService, create_router as mx_z_optimal_rf_router
from .mx_z_optimal_control_xy_rf_sensitivity import MxZOptimalControlXYRFSessionService, create_router as mx_z_optimal_xy_rf_router
from .mx_z_optimal_control_xy_noise_spectrum import MxZOptimalControlXYNoiseSessionService
from .mx_z_optimal_control_xy_leakage_response import MxZOptimalControlXYLeakageSessionService
from .mx_z_optimal_control_xy_rf_phase_response import MxZOptimalControlXYRFPhaseSessionService
from .mx_z_optimal_control_xyz_balance import MxZOptimalControlXYZBalanceSessionService
from .mx_z_optimal_control_dg4000_bias_xyz_balance import MxZOptimalControlDG4000BiasXYZBalanceSessionService
from .mx_main_field_calibration import MxMainFieldCalibrationSessionService
from .mx_main_field_scope_noise_spectrum import MxMainFieldScopeNoiseSessionService, create_router as mx_main_field_scope_router
from .mx_xy_residual_field_calibration import MxXYResidualFieldCalibrationSessionService, create_router as mx_xy_residual_router
from .noise_spectrum_xy_demod3_r import NoiseSpectrumXYDemod3RSessionService, create_router as demod3_r_router
from .projection_noise import ProjectionNoiseSessionService, create_router as projection_noise_router
from .photon_shot_noise import PhotonShotNoiseSessionService, create_router as photon_router
from .rf_sensitivity_constxy_frequency import ConstXYSessionService, create_router as constxy_router
from .t1_calibration import T1SessionService, create_router as t1_router
from .bb_power_scan_burst import BBPowerScanBurstSessionService, create_router as bb_power_router
from .bb_duty_scan_burst import BBDutySessionService, create_router as bb_duty_router
from .z_field_bandwidth import ZFieldBandwidthSessionService, create_router as z_bandwidth_router
from .mx_z_field_calibration import MxZFieldCalibrationSessionService
from .z_aw_waveform_scope_check import ZAWWaveformScopeCheckSessionService
from .z_aw_current_waveform_scope_check import ZAWCurrentWaveformScopeCheckSessionService
from .z_aw_closed_loop_waveform_correction import (ZAWClosedLoopSessionService,
    ZAWClosedLoopRunSummary, create_router as z_closed_loop_router)
from .z_coil_inductance_frequency_response import ZCoilInductanceFrequencyResponseSessionService
from .z_coil_current_frequency_response import ZCoilCurrentFrequencyResponseSessionService
from .bell_bloom_z_field_calibration import BellBloomZFieldCalibrationSessionService
from .mx_z_current_coupling_calibration import (
    MxZCurrentCouplingCalibrationSessionService,
    create_router as mx_z_current_coupling_router,
)
from .mx_y_rf_sensitivity_drift import (
    MxYRFDriftSessionService,
    create_router as mx_y_rf_drift_router,
)
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
    ControlLockinBody,
    ControlLockinCaptureBody,
    LockinSelectionBody,
    CurrentSourceSettingsBody,
    DeviceSnapshot,
    DeviceSummary,
    ExperimentArchiveBody,
    RFDirectAWCompareBody,
    RFDirectAWRunItem,
    RFDirectAWRunRenameBody,
    RFDirectAWRunStarBody,
    RFDirectAWRunsResponse,
    RFDirectAWRunSummary,
    GeneratorChannelSettingsBody,
    LaserEmissionSettingsBody,
    LaserSettingsBody,
    ScopeSettingsBody,
    ZArbitraryActionBody,
    ZArbitraryPreview,
    ZArbitrarySourceItem,
    ZArbitraryStatus,
)


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(application):
    try:
        yield
    finally:
        services = _workflow_session_services()
        for service in services:
            service.close_communication()
        for service in services:
            await asyncio.to_thread(service.shutdown)


def _workflow_session_services():
    """集中列出已接线的会话，由回归检查后台收尾是否遗漏。"""
    return (
        xy_sessions, known_noise_sessions, probe_am_sessions, rf_sessions, bb_power_sessions, t1_sessions,
        rf_known_noise_sessions, rf_control_scale_sessions, static_sessions, static_local_sessions, detection_chain_sessions,
        mx_y_rf_sessions, mx_y_rf_probe_detuning_sessions, mx_y_rf_power_sessions, z_aw_scope_sessions, z_aw_current_scope_sessions, z_closed_loop_sessions,
        mx_main_field_sessions, mx_main_field_scope_sessions, mx_xy_residual_sessions, demod3_r_sessions, photon_sessions, projection_noise_sessions, constxy_sessions, z_bandwidth_sessions,
        bb_duty_sessions, z_coil_inductance_sessions,
        mx_z_field_sessions,
        z_coil_current_sessions,
        bell_bloom_z_sessions,
        mx_z_current_coupling_sessions,
        scope_capture_sessions, mx_y_rf_frequency_response_sessions,
        mx_y_optimal_control_frequency_sessions,
        mx_y_rf_drift_sessions,
        mx_z_optimal_rf_sessions, mx_z_optimal_xy_rf_sessions, mx_z_optimal_xy_noise_sessions,
        mx_z_optimal_xy_leakage_sessions, mx_z_optimal_xy_phase_sessions, mx_z_optimal_xyz_balance_sessions,
        mx_z_optimal_dg_bias_sessions,
    )


app = FastAPI(title="Bell-Bloom 实验控制台", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def archived_experiment_card_only(request: Request, call_next):
    """归档代码没有运行入口；旧书签/API 同样不能绕过卡片限制。"""
    parts = request.url.path.strip("/").split("/")
    experiment_id = None
    metadata = False
    if parts[:2] == ["api", "experiments"] and len(parts) >= 3:
        experiment_id = parts[2]
        metadata = len(parts) == 3 or (len(parts) == 4 and parts[3] in {
            "archive", "tag", "description", "metadata",
        })
    elif parts[:2] == ["api", "runs"]:
        experiment_id = parts[2] if len(parts) >= 3 else request.query_params.get("experiment_id")
    if experiment_id in ARCHIVED_EXPERIMENTS and not metadata:
        return JSONResponse(status_code=410, content={
            "detail": f"实验已归档，GUI 仅保留卡片；代码位于 {ARCHIVE_PATH}，参数和历史数据仍保留。",
        })
    return await call_next(request)


@app.exception_handler(RestartInProgressError)
async def handle_restart_in_progress(request: Request, exc: RestartInProgressError):
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc)},
        headers={"X-Error-Code": "restarting"},
    )


ROOT = find_project_root()
INSTANCE_ID = uuid4().hex
z_arbitrary_service = ZArbitraryControlService(ROOT)
xy_sessions = XYSessionService(lambda: ROOT, manager)
app.include_router(xy_router(xy_sessions))
known_noise_sessions = KnownNoiseSessionService(lambda: ROOT, manager)
app.include_router(xy_router(known_noise_sessions))
probe_am_sessions = ProbeAMSessionService(lambda: ROOT, manager)
app.include_router(xy_router(probe_am_sessions))
rf_sessions = RFSessionService(lambda: ROOT, manager)
app.include_router(rf_workflow_router(rf_sessions))
rf_known_noise_sessions = RFKnownNoiseSessionService(lambda: ROOT, manager)
app.include_router(rf_workflow_router(rf_known_noise_sessions))
rf_control_scale_sessions = RFControlScaleSessionService(lambda: ROOT, manager)
app.include_router(rf_workflow_router(rf_control_scale_sessions, include_history=True))
static_sessions = StaticSessionService(lambda: ROOT, manager)
app.include_router(rf_workflow_router(static_sessions))
static_local_sessions = StaticLocalSessionService(lambda: ROOT, manager)
app.include_router(static_local_router(static_local_sessions))
detection_chain_sessions = DetectionChainFrequencyResponseSessionService(lambda: ROOT, manager)
app.include_router(xy_router(detection_chain_sessions))
mx_y_rf_sessions = MxYRFSessionService(lambda: ROOT, manager)
app.include_router(xy_router(mx_y_rf_sessions, include_history=True))
mx_y_rf_probe_detuning_sessions = MxYRFProbeDetuningSessionService(lambda: ROOT, manager)
app.include_router(xy_router(mx_y_rf_probe_detuning_sessions, include_history=True))
mx_y_rf_power_sessions = MxYRFPowerOptimizationSessionService(lambda: ROOT, manager)
app.include_router(xy_router(mx_y_rf_power_sessions, include_history=True))
z_aw_scope_sessions = ZAWWaveformScopeCheckSessionService(lambda: ROOT, manager)
app.include_router(xy_router(z_aw_scope_sessions, include_history=True))
z_aw_current_scope_sessions = ZAWCurrentWaveformScopeCheckSessionService(lambda: ROOT, manager)
app.include_router(xy_router(z_aw_current_scope_sessions, include_history=True))
z_closed_loop_sessions = ZAWClosedLoopSessionService(lambda: ROOT, manager)
app.include_router(z_closed_loop_router(z_closed_loop_sessions))
mx_main_field_sessions = MxMainFieldCalibrationSessionService(lambda: ROOT, manager)
app.include_router(xy_router(mx_main_field_sessions, include_history=True))
mx_main_field_scope_sessions = MxMainFieldScopeNoiseSessionService(lambda: ROOT, manager)
app.include_router(mx_main_field_scope_router(mx_main_field_scope_sessions))
mx_xy_residual_sessions = MxXYResidualFieldCalibrationSessionService(lambda: ROOT, manager)
app.include_router(mx_xy_residual_router(mx_xy_residual_sessions))
demod3_r_sessions = NoiseSpectrumXYDemod3RSessionService(lambda: ROOT, manager)
app.include_router(demod3_r_router(demod3_r_sessions))
projection_noise_sessions = ProjectionNoiseSessionService(lambda: ROOT, manager)
app.include_router(projection_noise_router(projection_noise_sessions))
photon_sessions = PhotonShotNoiseSessionService(lambda: ROOT, manager)
app.include_router(photon_router(photon_sessions))
constxy_sessions = ConstXYSessionService(lambda: ROOT, manager)
app.include_router(constxy_router(constxy_sessions))
t1_sessions = T1SessionService(lambda: ROOT, manager)
app.include_router(t1_router(t1_sessions))
bb_power_sessions = BBPowerScanBurstSessionService(lambda: ROOT, manager)
app.include_router(bb_power_router(bb_power_sessions))
bb_duty_sessions = BBDutySessionService(lambda: ROOT, manager)
app.include_router(bb_duty_router(bb_duty_sessions))
z_bandwidth_sessions = ZFieldBandwidthSessionService(lambda: ROOT, manager)
app.include_router(z_bandwidth_router(z_bandwidth_sessions))
z_coil_inductance_sessions = ZCoilInductanceFrequencyResponseSessionService(lambda: ROOT, manager)
app.include_router(xy_router(z_coil_inductance_sessions, include_history=True))
mx_z_field_sessions = MxZFieldCalibrationSessionService(lambda: ROOT, manager)
app.include_router(xy_router(mx_z_field_sessions, include_history=True))
z_coil_current_sessions = ZCoilCurrentFrequencyResponseSessionService(lambda: ROOT, manager)
app.include_router(xy_router(z_coil_current_sessions, include_history=True))
bell_bloom_z_sessions = BellBloomZFieldCalibrationSessionService(lambda: ROOT, manager)
app.include_router(xy_router(bell_bloom_z_sessions))
mx_z_current_coupling_sessions = MxZCurrentCouplingCalibrationSessionService(lambda: ROOT, manager)
app.include_router(mx_z_current_coupling_router(mx_z_current_coupling_sessions, include_history=True))
scope_capture_sessions = ScopeCaptureSessionService(lambda: ROOT, manager)
app.include_router(xy_router(scope_capture_sessions, include_history=True))
mx_y_rf_frequency_response_sessions = MxYRFFrequencyResponseSessionService(lambda: ROOT, manager)
app.include_router(xy_router(mx_y_rf_frequency_response_sessions, include_history=True))
mx_y_optimal_control_frequency_sessions = MxYOptimalControlFrequencySessionService(lambda: ROOT, manager)
app.include_router(xy_router(mx_y_optimal_control_frequency_sessions))
mx_z_optimal_rf_sessions = MxZOptimalControlRFSessionService(lambda: ROOT, manager)
app.include_router(mx_z_optimal_rf_router(mx_z_optimal_rf_sessions))
mx_z_optimal_xy_rf_sessions = MxZOptimalControlXYRFSessionService(lambda: ROOT, manager)
app.include_router(mx_z_optimal_xy_rf_router(mx_z_optimal_xy_rf_sessions))
mx_z_optimal_xy_noise_sessions = MxZOptimalControlXYNoiseSessionService(lambda: ROOT, manager)
app.include_router(mx_z_optimal_xy_rf_router(mx_z_optimal_xy_noise_sessions))
mx_z_optimal_xy_leakage_sessions = MxZOptimalControlXYLeakageSessionService(lambda: ROOT, manager)
app.include_router(mx_z_optimal_xy_rf_router(mx_z_optimal_xy_leakage_sessions))
mx_z_optimal_xy_phase_sessions = MxZOptimalControlXYRFPhaseSessionService(lambda: ROOT, manager)
app.include_router(mx_z_optimal_xy_rf_router(mx_z_optimal_xy_phase_sessions))
mx_z_optimal_xyz_balance_sessions = MxZOptimalControlXYZBalanceSessionService(lambda: ROOT, manager)
app.include_router(mx_z_optimal_xy_rf_router(mx_z_optimal_xyz_balance_sessions))
mx_z_optimal_dg_bias_sessions = MxZOptimalControlDG4000BiasXYZBalanceSessionService(lambda: ROOT, manager)
app.include_router(mx_z_optimal_xy_rf_router(mx_z_optimal_dg_bias_sessions))
mx_y_rf_drift_sessions = MxYRFDriftSessionService(lambda: ROOT, manager)
app.include_router(mx_y_rf_drift_router(mx_y_rf_drift_sessions, include_history=True))
updates_service = UpdatesService(lambda: ROOT, manager)
app.include_router(updates_router(updates_service))


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


class MxYOptimalControlFrequencyRunSummary(BaseModel):
    """Mx Y 最优控制 RF 频率响应单次运行摘要，供专属 GUI 历史 Tab 渲染。"""

    run_id: str
    timestamp: str
    run_tag: str
    completion_status: str
    comparison_enabled: bool
    parameters: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    control_source: dict[str, Any] = Field(default_factory=dict)
    optimal_response_metadata: dict[str, Any] = Field(default_factory=dict)
    constant_response_metadata: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)


class StaticSensitivityRunSummary(BaseModel):
    """静磁场灵敏度 typed 运行摘要。"""

    run_id: str
    timestamp: str
    run_tag: str
    completion_status: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)


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
    network_resources: list[str] | None = None
    mapping: dict[str, dict[str, Any]] | None = None
    physical_mapping_revision: str | None = None


class PhysicalMappingsBody(BaseModel):
    base_revision: str
    mapping: dict[str, dict[str, Any]]
    constraints: dict[str, dict[str, list[str]]]


class HealthResponse(BaseModel):
    status: str
    instance_id: str
    hardware_busy: bool
    queued_hardware_jobs: int


class RestartResponse(BaseModel):
    status: str


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


@app.get("/api/health", response_model=HealthResponse)
def health():
    return {
        "status": "ok",
        "instance_id": INSTANCE_ID,
        "hardware_busy": manager.hardware_lock.locked(),
        "queued_hardware_jobs": manager.queued_hardware_count(),
    }


@app.post("/api/restart", response_model=RestartResponse, status_code=202)
def restart_backend(request: Request):
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None or not hasattr(server, "should_exit"):
        raise HTTPException(
            503,
            "当前后端不是由 GUI 启动入口运行，无法一键重启",
            headers={"X-Error-Code": "restart_unavailable"},
        )
    if not manager.begin_restart():
        raise _conflict("存在未结束的任务或硬件操作，完成后才能重启后端", "restart_busy")
    request.app.state.restart_requested = True
    server.should_exit = True
    return RestartResponse(status="restarting")


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


@app.put("/api/lockin-selection")
def update_lockin_selection(body: LockinSelectionBody):
    return _with_configuration_lock(lambda: select_lockin_device(
        body.device_id, device_library_revision=body.device_library_revision,
        physical_mapping_revision=body.physical_mapping_revision, root=ROOT,
    ))


@app.put("/api/control-targets/{mapping_key}/lockin", response_model=ControlTargetResponse)
def update_control_lockin(mapping_key: str, body: ControlLockinBody):
    return _with_short_hardware_lock(lambda: apply_control_target_lockin(
        mapping_key, body.device_library_revision, body.physical_mapping_revision,
        body.settings.model_dump(exclude_unset=True),
    ))


@app.post("/api/control-targets/{mapping_key}/lockin-capture")
def acquire_control_lockin(mapping_key: str, body: ControlLockinCaptureBody):
    def runner(job: Job):
        job.cancellation.raise_if_cancelled()
        return capture_control_target_lockin(
            mapping_key, body.device_library_revision, body.physical_mapping_revision,
            body.settings.model_dump(exclude_none=True),
            cancellation_check=job.cancellation.raise_if_cancelled,
        )
    return manager.create("lockin-capture", runner).public()


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
            network_resources=body.network_resources,
            mapping=body.mapping,
            physical_mapping_revision=body.physical_mapping_revision,
            root=ROOT,
        )
    )


@app.post("/api/device-library/discover-visa")
def discover_device_library():
    return _with_short_hardware_lock(lambda: discover_visa_devices(ROOT))


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


@app.post("/api/tools/output-loads")
def init_output_loads():
    job = manager.create(
        "load-init",
        lambda current: [
            result.to_dict()
            for result in initialize_output_loads(progress=lambda event: manager.event(current, event))
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
    if experiment_id in ARCHIVED_EXPERIMENTS:
        return ARCHIVED_EXPERIMENTS[experiment_id]
    try:
        return get_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _default_categories() -> dict[str, str]:
    return {definition.id: definition.category for definition in list_experiments(include_archived=True)}


def _experiment_schema(definition) -> dict[str, Any]:
    return apply_parameter_layout(
        definition.id,
        definition.schema(),
        _default_categories(),
    )


def _experiment_public(
    definition,
    catalog: dict[str, Any] | None = None,
    *,
    required_devices: list[str] | None = None,
):
    current = catalog or public_catalog(_default_categories())
    tag_id = current["assignments"].get(definition.id, definition.category)
    labels = {item["id"]: item["label"] for item in current["tags"]}
    return {
        **definition.public(required_devices=required_devices),
        "archived": definition.id in current["archived_experiments"],
        "archive_path": ARCHIVE_PATH if definition.id in ARCHIVED_EXPERIMENTS else None,
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
    definitions = list_experiments(include_archived=True)
    catalog = public_catalog({item.id: item.category for item in definitions})
    try:
        mapping = load_mapping() if any(item.required_mapping_keys for item in definitions) else {}
    except Exception:
        mapping = None
    return [
        _experiment_public(
            definition,
            catalog,
            required_devices=(
                definition._public_required_devices(mapping)
                if mapping is not None else list(definition.required_devices)
            ),
        )
        for definition in definitions
    ]


@app.get("/api/experiments/{experiment_id}")
def experiment(experiment_id: str):
    return _experiment_public(_experiment_or_404(experiment_id))


@app.put("/api/experiments/{experiment_id}/archive")
def update_experiment_archive(experiment_id: str, body: ExperimentArchiveBody):
    definition = _experiment_or_404(experiment_id)
    try:
        set_experiment_archived(experiment_id, body.archived, _default_categories())
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"保存归档状态失败：{exc}") from exc
    return {"ok": True, "experiment": _experiment_public(definition)}


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
    definition = _experiment_or_404(experiment_id)
    errors = definition.preflight(body.parameters)
    from lab_workflows.lockin_acquisition import experiment_acquisition_requests, acquisition_preflight
    acquisition = []
    if not errors:
        requests = experiment_acquisition_requests(experiment_id, body.parameters)
        if requests:
            try:
                mapping = load_mapping()
                acquisition = acquisition_preflight(mapping.get("lockin_r") or mapping["lockin_xy"], requests)
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(str(exc))
    return {"ok": not errors, "errors": errors, "acquisition": acquisition}


@app.post("/api/experiments/{experiment_id}/derive")
def experiment_derive(experiment_id: str, body: ExperimentBody) -> dict[str, Any]:
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
            can_analyze = definition.analysis_runner is not None
            if definition.id in WORKFLOW_RUN_FORMATS:
                identity = WORKFLOW_RUN_FORMATS[definition.id]
                config = getattr(identity, "read_config", _read_yaml)(path / "experiment_config.yaml")
                if identity.format_block_reason(config):
                    continue
                can_analyze = can_analyze and not identity.analysis_block_reason(config)
            if definition.id in RF_RUN_FORMATS:
                identity = RF_RUN_FORMATS[definition.id]
                config = _read_yaml(path / "experiment_config.yaml")
                if not identity.is_new_run(config):
                    continue
                can_analyze = can_analyze and not identity.analysis_block_reason(config)
            if definition.id == STATIC_SENSITIVITY_ID:
                config = _read_yaml(path / "experiment_config.yaml")
                can_analyze = can_analyze and not static_analysis_block_reason(config)
            if definition.id == xy_format.EXPERIMENT_ID:
                config = _read_yaml(path / "experiment_config.yaml")
                can_analyze = can_analyze and not xy_format.analysis_block_reason(config)
            if definition.id == NOISE_SPECTRUM_XY_KNOWN_NOISE_ID:
                config = _read_yaml(path / "experiment_config.yaml")
                can_analyze = can_analyze and not known_noise_analysis_block_reason(config)
            result.append({
                "id": path.name,
                "experiment_id": definition.id,
                "experiment_title": definition.title,
                "path": str(path),
                "artifacts": artifacts,
                "can_analyze": can_analyze,
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
    if identity := WORKFLOW_RUN_FORMATS.get(definition.id):
        if reason := identity.format_block_reason(getattr(identity, "read_config", _read_yaml)(path / "experiment_config.yaml")):
            raise HTTPException(422, reason)
    return path


def _assert_run_idle(definition, run_id: str):
    # 采集和存活会话可能继续写入同类目录，与目录修改共用任务注册锁。
    ids = {item.id for item in list_experiments() if item.data_type == definition.data_type}
    if any(
        item.status in {"queued", "running"} and (
            item.kind in {f"experiment:{value}" for value in ids}
            or item.kind in {f"session:{value}" for value in ids}
            or item.kind in {f"analysis:{value}:{run_id}" for value in ids}
            or item.kind in {RF_RUN_FORMATS[value].COMPARISON_JOB_KIND for value in ids if value in RF_RUN_FORMATS}
        ) for item in manager.jobs.values()
    ):
        raise _conflict("该数据正在采集、分析或对比，暂时不能修改目录", "run_busy")


@app.delete("/api/runs/{experiment_id}/{run_id}")
def delete_run(experiment_id: str, run_id: str):
    """选定运行目录的永久删除，与 GUI 采集/分析任务注册原子互斥。"""
    definition = _experiment_or_404(experiment_id)
    _validate_run_identifier(run_id)
    with manager.jobs_lock:
        _assert_run_idle(definition, run_id)
        path = checked_run_path(ROOT, definition.data_type, run_id, recursive=True)
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
    if definition.id in RF_RUN_FORMATS and manager.has_active_kind(RF_RUN_FORMATS[definition.id].COMPARISON_JOB_KIND):
        raise _conflict("对比任务正在读取结果，请稍后重新分析", "run_busy")
    if any(manager.has_active_kind(f"experiment:{item.id}") for item in list_experiments()
           if item.data_type == definition.data_type):
        raise _conflict("同类数据仍在采集或自动分析，请稍后重新分析", "run_busy")
    run_dir = _run_dir(body.experiment_id, run_id)
    if definition.id in RF_RUN_FORMATS:
        _validate_run_identifier(run_id)
        reason = RF_RUN_FORMATS[definition.id].analysis_block_reason(_read_yaml(run_dir / "experiment_config.yaml"))
        if reason:
            raise HTTPException(400, reason)
    if definition.id == STATIC_SENSITIVITY_ID:
        config = _read_yaml(run_dir / "experiment_config.yaml")
        if reason := static_analysis_block_reason(config):
            if config.get("completion_status") == "running" and not static_format_block_reason(config):
                raise _conflict(reason, "run_busy")
            raise HTTPException(400, reason)
    for service in _workflow_session_services():
        if service.adapter.id == definition.id:
            service.run_directory(run_id)
    if definition.id == xy_format.EXPERIMENT_ID:
        reason = xy_format.analysis_block_reason(_read_yaml(run_dir / "experiment_config.yaml"))
        if reason:
            raise HTTPException(400, reason)
    if definition.id == NOISE_SPECTRUM_XY_KNOWN_NOISE_ID:
        reason = known_noise_analysis_block_reason(_read_yaml(run_dir / "experiment_config.yaml"))
        if reason:
            raise HTTPException(400, reason)
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


MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID = "mx-y-optimal-control-rf-frequency-response"


@app.get(f"/api/experiments/{MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}/runs")
def mx_y_optimal_control_frequency_runs(limit: int = 50, offset: int = 0):
    """新格式运行列表；旧格式运行不进入专属历史。"""
    return _mx_y_optimal_control_frequency_runs(limit, offset)


@app.get("/api/runs/{experiment_id}/{run_id}/artifacts/{name}")
def artifact(experiment_id: str, run_id: str, name: str):
    run_dir = _run_dir(experiment_id, run_id)
    results = (run_dir / "results").resolve()
    if run_dir not in results.parents:
        raise HTTPException(404, "结果目录必须位于运行目录内")
    path = (results / name).resolve()
    if results not in path.parents or not path.is_file():
        raise HTTPException(404, "结果文件不存在")
    return FileResponse(path)


ZAW_CLOSED_LOOP_ID = "z-aw-closed-loop-waveform-correction"


def _validate_run_identifier(run_id: str) -> str:
    """运行 ID 必须是单段目录名，避免路径穿越。"""
    if not run_id or run_id.endswith((".", " ")) or any(c in run_id for c in "/\\:"):
        raise HTTPException(400, "运行 ID 必须是单个目录名")
    return run_id


def _read_yaml(path: Path) -> dict[str, Any] | None:
    """并发删除时容错读取 YAML；文件不存在或被截断返回 None。"""
    try:
        if not path.is_file():
            return None
        loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except (OSError, yaml.YAMLError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _build_zaw_closed_loop_summary(experiment_id: str, run_id: str) -> ZAWClosedLoopRunSummary:
    _validate_run_identifier(run_id)
    return ZAWClosedLoopRunSummary(**z_closed_loop_sessions.summary(run_id))


@app.get(
    f"/api/experiments/{MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID}/runs/{{run_id}}/summary",
    response_model=MxYOptimalControlFrequencyRunSummary,
)
def mx_y_optimal_control_frequency_run_summary(run_id: str):
    """Mx Y 最优控制 RF 频率响应专属摘要端点。"""
    return _build_mx_y_optimal_control_frequency_summary(
        MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID,
        run_id,
    )


def experiment_run_summary(run_id: str):
    """Z 闭环专属 GUI 历史 Tab 使用的运行摘要：仅解析已有 yaml，不连接硬件。"""
    return _build_zaw_closed_loop_summary(ZAW_CLOSED_LOOP_ID, run_id)


def _build_mx_y_optimal_control_frequency_summary(
    experiment_id: str,
    run_id: str,
) -> MxYOptimalControlFrequencyRunSummary:
    """只读取运行配置与已有分析结果，不重算采集数据。"""
    _validate_run_identifier(run_id)
    run_dir = _run_dir(experiment_id, run_id)
    config = _read_yaml(run_dir / "experiment_config.yaml")
    if not config:
        raise HTTPException(404, "运行配置不存在")
    analysis = _read_yaml(run_dir / "results" / "analysis.yaml") or {}
    parameters = config.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    comparison = config.get("comparison")
    if not isinstance(comparison, dict):
        comparison = {}
    optimal = analysis.get("optimal_control")
    constant = analysis.get("constant_control")
    artifacts_dir = run_dir / "results"
    artifacts: dict[str, str] = {}
    try:
        if artifacts_dir.is_dir():
            artifacts = {
                item.name: f"/api/runs/{experiment_id}/{run_id}/artifacts/{item.name}"
                for item in artifacts_dir.iterdir()
                if _is_existing_file(item) and item.suffix.lower() == ".png"
            }
    except OSError:
        artifacts = {}
    return MxYOptimalControlFrequencyRunSummary(
        run_id=run_id,
        timestamp=str(config.get("timestamp", "")),
        run_tag=str(parameters.get("RUN_TAG", "")),
        completion_status=str(config.get("completion_status", "unknown")),
        comparison_enabled=bool(comparison.get("enabled")),
        parameters=parameters,
        calibration=comparison,
        control_source=config.get("control_source") if isinstance(config.get("control_source"), dict) else {},
        optimal_response_metadata=optimal if isinstance(optimal, dict) else {},
        constant_response_metadata=constant if isinstance(constant, dict) else {},
        artifacts=artifacts,
    )


def _mx_y_optimal_control_frequency_runs(page_size: int, offset: int) -> dict[str, Any]:
    """按共用原始格式资格列出运行，尚未分析的分步采集同样可见。"""
    definition = _experiment_or_404(MX_Y_OPTIMAL_CONTROL_FREQUENCY_ID)
    base = (ROOT / "data" / definition.data_type).resolve()
    runs: list[dict[str, Any]] = []
    if base.is_dir():
        for path in base.iterdir():
            if not path.is_dir():
                continue
            config = _read_yaml(path / "experiment_config.yaml")
            if mx_y_optimal_frequency_format.format_block_reason(config):
                continue
            comparison = config.get("comparison")
            runs.append(
                {
                    "run_id": path.name,
                    "timestamp": str(config.get("timestamp", "")),
                    "run_tag": str(config.get("parameters", {}).get("RUN_TAG", "")),
                    "completion_status": str(config.get("completion_status", "unknown")),
                    "comparison_enabled": bool(comparison.get("enabled")) if isinstance(comparison, dict) else False,
                }
            )
    runs.sort(key=lambda item: item["run_id"], reverse=True)
    return {
        "total": len(runs),
        "offset": offset,
        "limit": page_size,
        "runs": runs[offset:offset + page_size],
    }


STATIC_SENSITIVITY_ID = "static-sensitivity"


def _static_sensitivity_runs(limit: int, offset: int) -> dict[str, Any]:
    definition = _experiment_or_404(STATIC_SENSITIVITY_ID)
    base = (ROOT / "data" / definition.data_type).resolve()
    runs: list[dict[str, Any]] = []
    if base.is_dir():
        for path in base.iterdir():
            if not path.is_dir():
                continue
            config = _read_yaml(path / "experiment_config.yaml")
            if not config or config.get("experiment_id", STATIC_SENSITIVITY_ID) != STATIC_SENSITIVITY_ID:
                continue
            config.setdefault("completion_status", "unknown")
            runs.append({
                "run_id": path.name,
                "timestamp": str(config.get("timestamp", "")),
                "run_tag": str(config.get("run_tag", "")),
                "completion_status": str(config.get("completion_status", "completed")),
            })
    runs.sort(key=lambda item: item["run_id"], reverse=True)
    return {"total": len(runs), "offset": offset, "limit": limit, "runs": runs[offset:offset + limit]}


def _build_static_sensitivity_summary(run_id: str) -> StaticSensitivityRunSummary:
    run_dir = _run_dir(STATIC_SENSITIVITY_ID, run_id)
    config = _read_yaml(run_dir / "experiment_config.yaml")
    if not config or config.get("experiment_id", STATIC_SENSITIVITY_ID) != STATIC_SENSITIVITY_ID:
        raise HTTPException(404, "该目录不是静磁场灵敏度运行")
    config.setdefault("completion_status", "unknown")
    analysis = _read_yaml(run_dir / "results" / "analysis.yaml") or _read_yaml(run_dir / "results" / "analysis.json") or {}
    # 只在内存中归一化历史结果键，不迁移或改写旧文件。
    for target, legacy in (("fit_valid", "fit_is_valid"), ("sensitivity_flat_fT_per_sqrt_Hz", "sens_flat_fT_per_sqrt_Hz"), ("fit_rejection_reasons", "rejection_reasons")):
        if target not in analysis and legacy in analysis:
            analysis[target] = analysis[legacy]
    results_dir = run_dir / "results"
    artifacts = {
        item.name: f"/api/runs/{STATIC_SENSITIVITY_ID}/{run_id}/artifacts/{item.name}"
        for item in results_dir.iterdir()
        if item.is_file()
    } if results_dir.is_dir() else {}
    parameters = config.get("parameters") or config.get("params")
    return StaticSensitivityRunSummary(
        run_id=run_id,
        timestamp=str(config.get("timestamp", "")),
        run_tag=str(config.get("run_tag", "")),
        completion_status=str(config.get("completion_status", "completed")),
        parameters=parameters if isinstance(parameters, dict) else {},
        analysis=analysis,
        artifacts=artifacts,
    )


@app.get(f"/api/experiments/{STATIC_SENSITIVITY_ID}/runs")
def static_sensitivity_runs(limit: int = 50, offset: int = 0):
    return _static_sensitivity_runs(max(1, min(limit, 200)), max(0, offset))


@app.get(
    f"/api/experiments/{STATIC_SENSITIVITY_ID}/runs/{{run_id}}/summary",
    response_model=StaticSensitivityRunSummary,
)
def static_sensitivity_run_summary(run_id: str):
    return _build_static_sensitivity_summary(run_id)


NOISE_SPECTRUM_XY_ID = "noise-spectrum-xy"


@app.get(f"/api/experiments/{NOISE_SPECTRUM_XY_ID}/runs")
def noise_spectrum_xy_runs(limit: int = 50, offset: int = 0):
    base = ROOT / "data" / _experiment_or_404(NOISE_SPECTRUM_XY_ID).data_type
    items = []
    for path in base.glob("*/experiment_config.yaml"):
        config = _read_yaml(path)
        if not config or config.get("experiment_id") != NOISE_SPECTRUM_XY_ID:
            continue
        parameters = config.get("parameters", {})
        items.append({
            "run_id": path.parent.name,
            "timestamp": str(config.get("timestamp", path.parent.name)),
            "run_tag": str(parameters.get("RUN_TAG", "")),
            "completion_status": str(config.get("completion_status", "completed")),
        })
    items.sort(key=lambda item: item["run_id"], reverse=True)
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    return {"total": len(items), "limit": limit, "offset": offset, "runs": items[offset:offset + limit]}


@app.get(f"/api/experiments/{NOISE_SPECTRUM_XY_ID}/runs/{{run_id}}/summary")
def noise_spectrum_xy_summary(run_id: str):
    directory = _run_dir(NOISE_SPECTRUM_XY_ID, run_id)
    config = _read_yaml(directory / "experiment_config.yaml")
    if not config or config.get("experiment_id") != NOISE_SPECTRUM_XY_ID:
        raise HTTPException(404, "不是 XY 控制噪声谱运行")
    parameters = config.get("parameters")
    if not isinstance(parameters, dict):
        raise HTTPException(409, "运行配置缺少参数快照")
    results = directory / "results"
    artifacts = {
        path.name: f"/api/runs/{NOISE_SPECTRUM_XY_ID}/{run_id}/artifacts/{path.name}?v={path.stat().st_mtime_ns}"
        for path in results.glob("*") if path.is_file()
    }
    analysis_status = config.get("analysis_status")
    quality = {}
    quality_path = results / "analysis_summary.json"
    if quality_path.is_file():
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            if not isinstance(quality, dict):
                raise ValueError("分析摘要必须是对象")
        except (OSError, ValueError):
            quality = {"status": "failed", "error": "分析摘要不可读，请重新分析"}
        analysis_status = quality.get("status", "failed")
    if not analysis_status:
        analysis_status = "completed" if "noise_spectra.npz" in artifacts else "not_started"
    return {
        "run_id": run_id,
        "timestamp": str(config.get("timestamp", run_id)),
        "run_tag": str(parameters.get("RUN_TAG", "")),
        "completion_status": str(config.get("completion_status", "completed")),
        "analysis_status": str(analysis_status),
        "analysis_error": str(quality.get("error", "") if quality else config.get("analysis_error", "")),
        "analysis_quality": quality,
        "parameters": parameters,
        "artifacts": artifacts,
    }


NOISE_SPECTRUM_XY_KNOWN_NOISE_ID = "noise-spectrum-xy-known-noise"
NOISE_SPECTRUM_XY_PROBE_AM_ID = "noise-spectrum-xy-uncontrolled-probe-am"


@app.get(f"/api/experiments/{NOISE_SPECTRUM_XY_KNOWN_NOISE_ID}/runs")
def noise_spectrum_xy_known_noise_runs(limit: int = 50, offset: int = 0):
    return _noise_spectrum_injection_runs(NOISE_SPECTRUM_XY_KNOWN_NOISE_ID, limit, offset)


@app.get(f"/api/experiments/{NOISE_SPECTRUM_XY_PROBE_AM_ID}/runs")
def noise_spectrum_xy_probe_am_runs(limit: int = 50, offset: int = 0):
    return _noise_spectrum_injection_runs(NOISE_SPECTRUM_XY_PROBE_AM_ID, limit, offset)


def _noise_spectrum_injection_runs(experiment_id: str, limit: int, offset: int):
    base = ROOT / "data" / _experiment_or_404(experiment_id).data_type
    items = []
    for path in base.glob("*/experiment_config.yaml"):
        config = _read_yaml(path)
        if not config or config.get("experiment_id") != experiment_id:
            continue
        parameters = config.get("parameters", {})
        items.append({
            "run_id": path.parent.name,
            "timestamp": str(config.get("timestamp", path.parent.name)),
            "run_tag": str(parameters.get("RUN_TAG", "")),
            "completion_status": str(config.get("completion_status", "completed")),
        })
    items.sort(key=lambda item: item["run_id"], reverse=True)
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    return {"total": len(items), "limit": limit, "offset": offset, "runs": items[offset:offset + limit]}


@app.get(f"/api/experiments/{NOISE_SPECTRUM_XY_KNOWN_NOISE_ID}/runs/{{run_id}}/summary")
def noise_spectrum_xy_known_noise_summary(run_id: str):
    return _noise_spectrum_injection_summary(NOISE_SPECTRUM_XY_KNOWN_NOISE_ID, run_id)


@app.get(f"/api/experiments/{NOISE_SPECTRUM_XY_PROBE_AM_ID}/runs/{{run_id}}/summary")
def noise_spectrum_xy_probe_am_summary(run_id: str):
    return _noise_spectrum_injection_summary(NOISE_SPECTRUM_XY_PROBE_AM_ID, run_id)


def _noise_spectrum_injection_summary(experiment_id: str, run_id: str):
    directory = _run_dir(experiment_id, run_id)
    config = _read_yaml(directory / "experiment_config.yaml")
    if not config or config.get("experiment_id") != experiment_id:
        raise HTTPException(404, "运行目录与 XY 噪声实验 ID 不匹配")
    parameters = config.get("parameters")
    if not isinstance(parameters, dict):
        raise HTTPException(409, "运行配置缺少参数快照")
    results = directory / "results"
    artifacts = {
        path.name: f"/api/runs/{experiment_id}/{run_id}/artifacts/{path.name}?v={path.stat().st_mtime_ns}"
        for path in results.glob("*") if path.is_file()
    }
    analysis_status = config.get("analysis_status")
    quality = {}
    quality_path = results / "analysis_summary.json"
    if quality_path.is_file():
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            if not isinstance(quality, dict):
                raise ValueError("分析摘要必须是对象")
        except (OSError, ValueError):
            quality = {"status": "failed", "error": "分析摘要不可读，请重新分析"}
        analysis_status = quality.get("status", "failed")
    if not analysis_status:
        analysis_status = "completed" if "noise_spectra.npz" in artifacts else "not_started"
    return {
        "run_id": run_id,
        "timestamp": str(config.get("timestamp", run_id)),
        "run_tag": str(parameters.get("RUN_TAG", "")),
        "completion_status": str(config.get("completion_status", "completed")),
        "analysis_status": str(analysis_status),
        "analysis_error": str(quality.get("error", "") if quality else config.get("analysis_error", "")),
        "analysis_quality": quality,
        "parameters": parameters,
        "artifacts": artifacts,
    }


BELL_BLOOM_Z_ID = "bell-bloom-z-field-calibration"


@app.get(f"/api/experiments/{BELL_BLOOM_Z_ID}/runs")
def bell_bloom_z_runs(limit: int = 50, offset: int = 0):
    base = ROOT / "data" / _experiment_or_404(BELL_BLOOM_Z_ID).data_type
    items = []
    for path in base.glob("*/experiment_config.yaml"):
        config = _read_yaml(path) or {}
        if config.get("experiment_id") == BELL_BLOOM_Z_ID and {"started_at", "run_tag", "completion_status"} <= config.keys():
            items.append({"run_id": path.parent.name, "timestamp": config["started_at"],
                          "run_tag": config["run_tag"], "completion_status": config["completion_status"]})
    items.sort(key=lambda item: item["timestamp"], reverse=True)
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    return {"total": len(items), "limit": limit, "offset": offset, "runs": items[offset:offset + limit]}


@app.get(f"/api/experiments/{BELL_BLOOM_Z_ID}/runs/{{run_id}}/summary")
def bell_bloom_z_summary(run_id: str):
    directory = _run_dir(BELL_BLOOM_Z_ID, run_id)
    config = _read_yaml(directory / "experiment_config.yaml") or {}
    if config.get("experiment_id") != BELL_BLOOM_Z_ID:
        raise HTTPException(404, "不是 Bell Bloom Z 标定运行")
    if not {"started_at", "run_tag", "completion_status", "parameters"} <= config.keys():
        raise HTTPException(409, "运行配置正在更新，请刷新后重试")
    analysis_path = directory / "results" / "analysis.yaml"
    analysis = _read_yaml(analysis_path) if analysis_path.is_file() else None
    # 拟合失败产生的 NaN 在 API 边界转换为 null，保留磁盘诊断原值。
    analysis = json.loads(json.dumps(analysis), parse_constant=lambda _: None)
    return {
        "run_id": run_id, "timestamp": config["started_at"], "run_tag": config["run_tag"],
        "completion_status": config["completion_status"], "parameters": config["parameters"],
        "analysis_status": config.get("analysis_status", "not_started"),
        "failure_reason": config.get("failure_reason", ""),
        "analysis_error": config.get("analysis_error", ""),
        "safety_shutdown": config.get("safety_shutdown", {}), "analysis": analysis,
        "artifacts": {path.name: f"/api/runs/{BELL_BLOOM_Z_ID}/{run_id}/artifacts/{path.name}?v={path.stat().st_mtime_ns}"
                      for path in (directory / "results").glob("*") if path.is_file()},
    }


DETECTION_CHAIN_FREQUENCY_ID = "detection-chain-frequency-response"


@app.get(f"/api/experiments/{DETECTION_CHAIN_FREQUENCY_ID}/runs")
def detection_chain_frequency_runs(limit: int = 50, offset: int = 0):
    base = ROOT / "data" / _experiment_or_404(DETECTION_CHAIN_FREQUENCY_ID).data_type
    items = []
    for path in base.glob("*/experiment_config.yaml"):
        config = _read_yaml(path)
        if not config or config.get("experiment_id") != DETECTION_CHAIN_FREQUENCY_ID:
            continue
        items.append({
            "run_id": path.parent.name,
            "timestamp": str(config.get("started_at", "")),
            "run_tag": str(config.get("run_tag", "")),
            "completion_status": str(config.get("completion_status", "unknown")),
        })
    items.sort(key=lambda item: item["timestamp"], reverse=True)
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    return {"total": len(items), "limit": limit, "offset": offset, "runs": items[offset:offset + limit]}


@app.get(f"/api/experiments/{DETECTION_CHAIN_FREQUENCY_ID}/runs/{{run_id}}/summary")
def detection_chain_frequency_summary(run_id: str):
    directory = _run_dir(DETECTION_CHAIN_FREQUENCY_ID, run_id)
    config = _read_yaml(directory / "experiment_config.yaml") or {}
    if config.get("experiment_id") != DETECTION_CHAIN_FREQUENCY_ID:
        raise HTTPException(404, "不是探测链路频率响应标定运行")
    results = directory / "results"
    return {
        "run_id": run_id,
        "timestamp": str(config.get("started_at", "")),
        "run_tag": str(config.get("run_tag", "")),
        "completion_status": str(config.get("completion_status", "unknown")),
        "parameters": config.get("parameters") if isinstance(config.get("parameters"), dict) else {},
        "analysis_status": config.get("analysis_status", "not_started"),
        "failure_reason": config.get("failure_reason") or "",
        "analysis_error": config.get("analysis_error") or "",
        "temperature_control": config.get("temperature_control") if isinstance(config.get("temperature_control"), dict) else None,
        "artifacts": {
            path.name: f"/api/runs/{DETECTION_CHAIN_FREQUENCY_ID}/{run_id}/artifacts/{path.name}?v={path.stat().st_mtime_ns}"
            for path in results.glob("*") if path.is_file()
        },
    }


def _rf_direct_aw_run_item(run_id: str, config: dict, directory: Path, *, identity=rf_aw_format) -> dict:
    reason = identity.comparison_block_reason(directory, config)
    return {
        "run_id": run_id,
        "name": split_run_name(run_id)[1],
        "starred": read_starred(directory) if identity is rf_aw_format else False,
        "timestamp": str(config.get("started_at", config.get("timestamp", ""))),
        "run_tag": str(config.get("run_tag", "")),
        "completion_status": str(config.get("completion_status", "unknown")),
        "phase_calibration_status": str(config.get("phase_calibration_status", "not_started")),
        "analysis_status": str(config.get("analysis_status", "not_started")),
        "can_compare": not reason,
        "comparison_block_reason": reason,
    }


@app.get(f"/api/experiments/{RF_DIRECT_AW_ID}/runs", response_model=RFDirectAWRunsResponse)
def rf_direct_aw_runs(limit: int = 50, offset: int = 0, starred_only: bool = False):
    return _rf_aw_runs(RF_DIRECT_AW_ID, limit, offset, starred_only=starred_only)


def _rf_aw_runs(experiment_id, limit, offset, *, starred_only=False):
    identity = RF_RUN_FORMATS[experiment_id]
    base = (ROOT / "data" / _experiment_or_404(experiment_id).data_type).resolve()
    items = []
    for path in base.glob("*/experiment_config.yaml"):
        directory = path.parent.resolve()
        if directory.parent != base:
            continue
        config = _read_yaml(path)
        if identity.is_new_run(config):
            item = _rf_direct_aw_run_item(path.parent.name, config, directory, identity=identity)
            if not starred_only or item["starred"]:
                items.append(item)
    items.sort(key=lambda item: item["timestamp"], reverse=True)
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    return {"runs": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}


def _manageable_rf_run(run_id: str, *, recursive=False):
    directory = checked_run_path(ROOT, rf_aw_format.DATA_TYPE, run_id, recursive=recursive)
    config = _read_yaml(directory / "experiment_config.yaml")
    if not is_rf_direct_aw_run(config):
        raise HTTPException(404, "不是 RF 灵敏度 DirectAW 新格式运行")
    return directory, config


@app.post(f"/api/experiments/{RF_DIRECT_AW_ID}/runs/{{run_id}}/rename", response_model=RFDirectAWRunItem)
def rename_rf_direct_aw_run(run_id: str, body: RFDirectAWRunRenameBody):
    with manager.jobs_lock:
        _assert_run_idle(_experiment_or_404(RF_DIRECT_AW_ID), run_id)
        directory, config = _manageable_rf_run(run_id, recursive=True)
        target = rename_directory(directory, body.name, config)
        return _rf_direct_aw_run_item(target.name, config, target)


@app.put(f"/api/experiments/{RF_DIRECT_AW_ID}/runs/{{run_id}}/star", response_model=RFDirectAWRunItem)
def star_rf_direct_aw_run(run_id: str, body: RFDirectAWRunStarBody):
    with manager.jobs_lock:
        directory, config = _manageable_rf_run(run_id)
        save_starred(directory, body.starred)
        return _rf_direct_aw_run_item(run_id, config, directory)


@app.get(f"/api/experiments/{RF_DIRECT_AW_ID}/runs/{{run_id}}/summary", response_model=RFDirectAWRunSummary)
def rf_direct_aw_summary(run_id: str):
    return _rf_aw_summary(RF_DIRECT_AW_ID, run_id)


def _rf_aw_summary(experiment_id, run_id):
    identity = RF_RUN_FORMATS[experiment_id]
    _validate_run_identifier(run_id)
    directory = _run_dir(experiment_id, run_id)
    config = _read_yaml(directory / "experiment_config.yaml")
    if not identity.is_new_run(config):
        raise HTTPException(404, "不是 RF 灵敏度 DirectAW 新格式运行")
    reason = identity.analysis_block_reason(config)
    results = directory / "results"
    safe_results = directory in results.resolve().parents
    analysis = _read_yaml(results / "analysis.yaml") if not reason and safe_results else None
    artifacts = {
        path.name: f"/api/runs/{experiment_id}/{quote(run_id, safe='')}/artifacts/{quote(path.name, safe='')}?v={path.stat().st_mtime_ns}"
        for path in results.glob("*") if path.is_file() and not reason and safe_results and results.resolve() in path.resolve().parents
    }
    controls = (analysis or {}).get("controls", {})
    if experiment_id in (RF_DIRECT_AW_ID, rf_known_noise_format.EXPERIMENT_ID) and isinstance(controls, dict):
        for version in controls:
            if not isinstance(version, str) or "\x00" in version:
                continue
            try:
                _validate_run_identifier(version)
            except HTTPException:
                continue
            for name in ("full_analysis.png", "full_analysis.pdf", "noise_psd.png", "noise_psd.pdf",
                         "response_curve.png", "response_curve.pdf", "sensitivity.png", "sensitivity.pdf",
                         "psd_avg.npz", "response_fit.npz", "sensitivity.npz", "noise_rejection_summary.json", "noise_record_quality.csv"):
                path = (directory / "controls" / version / "results" / name).resolve()
                if directory not in path.parents or not path.is_file():
                    continue
                artifacts[f"controls/{version}/{name}"] = (
                    f"/api/experiments/{experiment_id}/runs/{quote(run_id, safe='')}/controls/"
                    f"{quote(version, safe='')}/artifacts/{name}?v={path.stat().st_mtime_ns}")
    return {
        **_rf_direct_aw_run_item(run_id, config, directory, identity=identity),
        "parameters": config["parameters"], "signal_chain": config.get("signal_chain", identity.SIGNAL_CHAIN), "can_analyze": not reason,
        "analysis_block_reason": reason,
        "failure_reason": config.get("failure_reason") or "",
        "analysis_error": config.get("analysis_error") or "",
        "analysis": analysis,
        "xy_phase_calibration": config.get("xy_phase_calibration", {}),
        "safety_shutdown": config.get("safety_shutdown", {}),
        "actual_rates": config.get("actual_rates", {}),
        "hf2_response_snapshot": config.get("hf2_response_snapshot", {}),
        "hf2_noise_snapshot": config.get("hf2_noise_snapshot", {}),
        "hf2_calibration_snapshot": config.get("hf2_calibration_snapshot", {}),
        "stage_frequencies": config.get("stage_frequencies", {}),
        "response_component": identity.RESPONSE_COMPONENT, "noise_component": "X",
        "temperature_control": config.get("temperature_control"),
        "artifacts": artifacts,
    }


@app.get("/api/experiments/rf-sensitivity-aw/runs/{run_id}/controls/{version}/artifacts/{name}")
def rf_direct_aw_control_artifact(run_id: str, version: str, name: str):
    return _rf_aw_control_artifact(RF_DIRECT_AW_ID, run_id, version, name)


@app.get("/api/experiments/rf-sensitivity-aw-known-noise/runs/{run_id}/controls/{version}/artifacts/{name}")
def rf_known_noise_control_artifact(run_id: str, version: str, name: str):
    return _rf_aw_control_artifact(rf_known_noise_format.EXPERIMENT_ID, run_id, version, name)


def _rf_aw_control_artifact(experiment_id: str, run_id: str, version: str, name: str):
    _validate_run_identifier(version)
    if f"controls/{version}/{name}" not in _rf_aw_summary(experiment_id, run_id)["artifacts"]:
        raise HTTPException(404, "控制版本结果文件不存在")
    directory = _run_dir(experiment_id, run_id)
    path = (directory / "controls" / version / "results" / name).resolve()
    if directory not in path.parents or not path.is_file():
        raise HTTPException(404, "控制版本结果必须位于运行目录内")
    return FileResponse(path)


def _rf_direct_aw_comparison_dir(comparison_id: str, *, experiment_id=RF_DIRECT_AW_ID) -> Path:
    if len(comparison_id) != 32 or any(c not in "0123456789abcdef" for c in comparison_id):
        raise HTTPException(400, "对比 ID 无效")
    base = (ROOT / "results" / experiment_id / "comparisons").resolve()
    directory = (base / comparison_id).resolve()
    if ROOT.resolve() not in base.parents or directory.parent != base:
        raise HTTPException(400, "对比目录超出允许范围")
    return directory


@app.post(f"/api/experiments/{RF_DIRECT_AW_ID}/compare")
def rf_direct_aw_compare(body: RFDirectAWCompareBody):
    return _rf_aw_compare(RF_DIRECT_AW_ID, body)


def _rf_aw_compare(experiment_id, body):
    identity = RF_RUN_FORMATS[experiment_id]
    try:
        entries = normalize_comparison_runs([item.model_dump() for item in body.runs])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with manager.jobs_lock:
        if manager.has_active_kind(identity.COMPARISON_JOB_KIND):
            raise _conflict("对比任务正在执行", "comparison_running")
        for item in entries:
            _validate_run_identifier(item["run_id"])
            directory = _run_dir(experiment_id, item["run_id"])
            reason = identity.comparison_block_reason(directory, _read_yaml(directory / "experiment_config.yaml"))
            if reason:
                raise HTTPException(400, f"{item['run_id']}：{reason}")
            if manager.has_active_kind(f"analysis:{experiment_id}:{item['run_id']}"):
                raise _conflict("所选运行正在重新分析，请稍后对比", "run_busy")
            item["run_dir"] = str(directory)

        def execute(current: Job):
            output = _rf_direct_aw_comparison_dir(current.id, experiment_id=experiment_id)
            runner = rf_direct_aw_comparison_runner
            result = runner(entries, output,
                progress=lambda event: manager.event(current, event), cancellation=current.cancellation)
            return {**result, "comparison_id": current.id, "run_dir": str(output), "artifacts": {
                name: f"/api/experiments/{experiment_id}/comparisons/{current.id}/artifacts/{name}"
                for name in identity.COMPARISON_FILES
            }}

        return manager.create(identity.COMPARISON_JOB_KIND, execute, hardware_required=False).public()


@app.get(f"/api/experiments/{RF_DIRECT_AW_ID}/comparisons/{{comparison_id}}/artifacts/{{name}}")
def rf_direct_aw_comparison_artifact(comparison_id: str, name: str):
    return _rf_aw_comparison_artifact(RF_DIRECT_AW_ID, comparison_id, name)


def _rf_aw_comparison_artifact(experiment_id, comparison_id, name):
    directory = _rf_direct_aw_comparison_dir(comparison_id, experiment_id=experiment_id)
    manifest = directory / "comparison.json"
    if name not in RF_DIRECT_AW_COMPARISON_FILES or not manifest.is_file() or manifest.resolve().parent != directory:
        raise HTTPException(404, "对比结果尚未完整生成")
    path = (directory / name).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(404, "对比文件不存在")
    return FileResponse(path)



@app.get("/api/experiments/rf-sensitivity-aw-known-noise/runs", response_model=RFDirectAWRunsResponse)
def rf_known_noise_runs(limit: int = 50, offset: int = 0):
    return _rf_aw_runs(rf_known_noise_format.EXPERIMENT_ID, limit, offset)


@app.get("/api/experiments/rf-sensitivity-aw-known-noise/runs/{run_id}/summary", response_model=RFDirectAWRunSummary)
def rf_known_noise_summary(run_id: str):
    return _rf_aw_summary(rf_known_noise_format.EXPERIMENT_ID, run_id)


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
