"""多控制预检与独立重跑目录；校相始终在每个控制 × Scale 点内完成。"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import hashlib
import numpy as np
from ...control_sources import load_theory_control, resolve_control_results_root
from ...steps import create_run_directory
from ..rf_sensitivity_direct_aw import preparation as shared
from ..rf_sensitivity_direct_aw.waveform import synthesize_xy
from ..rf_sensitivity_aw_known_noise.acquisition import make_control_directory
from . import run_format


def scaled_envelope(params, envelope, scale):
    """缩放完整物理控制包络，再按 K/B 标定转电压；不缩放独立主场。"""
    factor = params.control_scale_factor()
    total = float(scale) * factor
    with np.errstate(over="ignore", invalid="ignore"):
        omega = np.asarray(envelope["omega_ctrl_Hz"]) * total
        voltage = (omega - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v
    if not np.isfinite(total) or not np.isfinite(omega).all() or not np.isfinite(voltage).all():
        raise ValueError("扫描倍率 × 控制补偿后的倍率或包络不是有限数值")
    return dict(envelope, omega_ctrl_Hz=omega, envelope_V=voltage, allow_zero_envelope=True,
                control_scale=float(scale), control_scale_factor=factor, effective_control_scale=total)


def preflight(params, root):
    params, mapping, first = shared.preflight(params, root, experiment_id=run_format.EXPERIMENT_ID)
    base = resolve_control_results_root("oc_sens")
    for relative in params.control_waveform_files:
        path = (base / relative).resolve()
        if path.name != "optimal_control_waveform.csv" or path.parent.name != "waveforms" or path.parent.parent.parent != base.resolve():
            raise ValueError("控制文件必须是 oc_sens/<控制名>/waveforms/optimal_control_waveform.csv")
        source = load_theory_control(base, path.parent.parent.name)
        if not np.isclose(source.theory_rf_frequency_hz, params.z_rf_freq, rtol=1e-6, atol=1e-6):
            raise ValueError(f"{path.parent.parent.name} 的设计 RF 频率与 Z_RF_FREQ 不一致，不自动缩放时间")
        child = replace(params, arb_waveform_file=relative, control_waveform_files=[relative])
        _, _, envelope = shared.preflight(child, root, experiment_id=run_format.EXPERIMENT_ID)
        for scale in params.control_scales:
            synthesize_xy(child, scaled_envelope(child, envelope, scale), child.xy_ctrl_phase)
    if params.resume_run_id:
        from .resume import resume_plan
        resume_plan(params, root)
    return params, mapping, first


def new_directory(parent, selected, control_type):
    previous, inherited = parent.directory, deepcopy(parent.state)
    if previous and parent.state["completion_status"] in ("running", "acquired"):
        previous.update_config(completion_status="failed", failure_reason="被显式重跑替代，原始数据保留")
    suffix = 0
    while True:
        tag = parent.params.run_tag if not suffix else f"{parent.params.run_tag}_{suffix:02d}"
        try:
            parent.directory = create_run_directory(run_format.DATA_TYPE, tag, parent.params.to_external(),
                schema_version=parent.params.schema_version, project_root=parent.root)
            break
        except FileExistsError:
            suffix += 1
    parent.state = shared.initial_state(parent.params, identity=run_format)
    parent.state.update(raw_format_version=run_format.RAW_FORMAT_VERSION,
                        phase_calibration_scope=run_format.PHASE_CALIBRATION_SCOPE)
    parent.run_parameters = parent.params.to_external()
    if previous:
        parent.state["previous_run_id"] = previous.root.name
    for key in ("clock_sync", "temperature_control", "optional_connection_errors"):
        if key in inherited:
            parent.state[key] = inherited[key]
    parent.controls = {}
    for relative in parent.params.control_waveform_files:
        params = replace(parent.params, arb_waveform_file=relative, control_waveform_files=[relative])
        ctx = control_type(params, project_root=parent.root, session_id=parent.session_id)
        ctx.devices = parent.devices
        ctx.state.update(control_source_sha256=hashlib.sha256(Path(ctx.envelope["source_waveform_path"]).read_bytes()).hexdigest(),
                         raw_format_version=run_format.RAW_FORMAT_VERSION,
                         phase_calibration_scope=run_format.PHASE_CALIBRATION_SCOPE,
                         control_metrics={"envelope_rms_V": float(np.sqrt(np.mean(ctx.envelope["envelope_V"] ** 2))),
                                          "envelope_peak_V": float(np.max(np.abs(ctx.envelope["envelope_V"])))})
        ctx.directory = make_control_directory(parent.directory, relative, params, ctx.state)
        version = ctx.directory.root.name
        parent.controls[version] = ctx
    parent.save()
