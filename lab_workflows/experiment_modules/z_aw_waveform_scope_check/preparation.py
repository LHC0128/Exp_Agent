"""无硬件预检、控制源、映射连接与专用安全结束。"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Callable
import numpy as np
from sds_acquisition import SDSAcquisition, SDSInstrument
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...control_sources import build_applied_control, load_theory_control, load_z_calibration
from ...steps import (DGChannelShutdown, DeviceSession, DisconnectTarget, run_safety_shutdown,
    save_optimal_control_source_snapshot, validate_z_trigger_mapping)
from ...steps.routed_signal_generator import connect_signal_generator_routes


def preflight(params, root):
    """只核对参数、映射和安全限值，不连接任何仪器。"""
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping, limits = load_mapping(root), load_safety_limits(root)
    validate_z_trigger_mapping(mapping)
    if mapping["Z_magnetic_field"]["channel"] == mapping["Time_sequence_2"]["channel"]:
        raise ValueError("Z 任意波与共同触发不能占用同一物理通道")
    if mapping["scope_waveform"].get("instrument") != "sds_acquisition":
        raise ValueError("scope_waveform 必须映射到 SDS 示波器")
    for value in (params.trigger_offset_v - params.trigger_amplitude_vpp / 2,
                  params.trigger_offset_v + params.trigger_amplitude_vpp / 2):
        validate_safety_limit("Time_sequence_2", value, limits)
    return params, mapping, limits


@dataclass
class ControlSource:
    theory: Any
    calibration: Any
    applied: Any
    target_current_a: Any = None
    current_calibration: Any = None
    source_snapshot_writer: Callable[[Path], list[str]] | None = None
    target_control_scale: float | None = None


def prepare_control(params, root, *, measurement_mode="coil_voltage", current_calibration=None,
                    theory_override=None, applied_override=None, source_snapshot_writer=None,
                    target_control_scale=None):
    """同一准备步骤兼容实际电流实验提供的冻结命令波形。"""
    if (theory_override is None) != (applied_override is None):
        raise ValueError("theory_override 与 applied_override 必须同时提供")
    calibration = None
    if theory_override is None:
        theory = load_theory_control(Path(params.control_results_root), params.control_version)
        calibration = load_z_calibration(root, params.z_calibration_source_run)
        applied = build_applied_control(theory, calibration, params.control_scale,
            output_vpp=params.z_aw_output_vpp, output_offset_v=params.z_aw_output_offset_v)
    else:
        theory, applied = theory_override, applied_override
    target_current = None
    if measurement_mode == "current":
        if current_calibration is None:
            raise ValueError("current 测量模式必须提供电流耦合标定")
        scale = params.control_scale if target_control_scale is None else float(target_control_scale)
        target_current = scale * theory.omega_ctrl_hz / current_calibration.slope_hz_per_a
    elif measurement_mode != "coil_voltage":
        raise ValueError("未知的 Scope 波形测量模式")
    return ControlSource(theory, calibration, applied, target_current, current_calibration,
                         source_snapshot_writer, target_control_scale)


def save_source(run_dir, source):
    """先冻结实际下发的控制源，再开始采集。"""
    if source.source_snapshot_writer is None:
        for path, expected in ((source.theory.waveform_path, source.theory.waveform_sha256),
                               (source.theory.parameter_path, source.theory.parameter_sha256),
                               (source.calibration.analysis_path, source.calibration.analysis_sha256)):
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise ValueError("控制源或标定文件已改变，请重新执行 A03 任意波配置")
    files = (source.source_snapshot_writer(run_dir.raw) if source.source_snapshot_writer else
             save_optimal_control_source_snapshot(run_dir.raw, source.theory,
                                                  source.calibration, source.applied))
    if source.target_current_a is not None:
        path = run_dir.raw / "applied_control_waveform.npz"
        with np.load(path, allow_pickle=False) as data:
            payload = {key: data[key] for key in data.files}
        payload["target_current_a"] = np.asarray(source.target_current_a, dtype=float)
        np.savez(path, **payload)
    return list(files)


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接 Z/触发信号源和 SDS，使用映射路由而非固定资源。"""
    z_cfg = mapping["Z_magnetic_field"]
    trigger_cfg = mapping["Time_sequence_2"]
    if z_cfg.get("instrument") != "signal_generator":
        raise ValueError("Z_magnetic_field 必须映射到信号发生器")
    if trigger_cfg.get("instrument") != "signal_generator":
        raise ValueError("Time_sequence_2 必须映射到信号发生器")
    # 该实验要求两个物理端点位于同一台 DG4000。
    trigger_channel = validate_z_trigger_mapping(mapping)

    z_device, routed_channels = connect_signal_generator_routes(
        session,
        "z_control",
        {
            "z_field": ("Z_magnetic_field", z_cfg),
            "trigger": ("Time_sequence_2", trigger_cfg),
        },
    )
    scope_cfg = mapping["scope_waveform"]
    scope = session.connect(
        "scope",
        str(scope_cfg["resource"]),
        lambda: SDSInstrument(str(scope_cfg["resource"])),
    )
    devices = {
        "z_control": z_device,
        "scope": scope,
        "acquirer": session.bind("acquirer", SDSAcquisition(scope)),
    }
    if routed_channels["trigger"] != trigger_channel:
        raise ValueError("Time_sequence_2 路由通道与映射端点不一致")
    return devices, routed_channels


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
) -> Any:
    """停止示波器，关闭 Z 并保持共同触发当前状态。"""
    scope = devices.get("scope")
    z_control = devices.get("z_control")
    dg_channels = []
    if z_control is not None:
        if "z_field" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    z_control,
                    channels["z_field"],
                    "Z_magnetic_field",
                    "Z 任意波",
                )
            )
        if "trigger" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    z_control,
                    channels["trigger"],
                    "Time_sequence_2",
                    "共同触发",
                )
            )
    def stop_scope() -> None:
        if scope is not None:
            scope.trigger_stop()

    from ...steps.safety_shutdown import ShutdownAction

    return run_safety_shutdown(
        dg_channels=dg_channels,
        extra_actions=(ShutdownAction("停止 SDS 采集", stop_scope),),
        disconnect_targets=(
            DisconnectTarget("SDS", scope),
            DisconnectTarget("Z 信号源", z_control),
        ),
    )
