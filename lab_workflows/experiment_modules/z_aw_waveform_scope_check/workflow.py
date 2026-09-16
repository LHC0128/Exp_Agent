"""Z 任意波线圈波形一致性验证采集工作流。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sds_acquisition import (
    AcquisitionConfig,
    ChannelConfig,
    SDSAcquisition,
    SDSInstrument,
    TriggerConfig,
)

from ...common import find_project_root, load_mapping
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    ScopeAutoRangeState,
    create_run_directory,
    configure_optimal_control_trigger,
    configure_z_optimal_control_output,
    next_auto_offset,
    next_auto_range_scale,
    run_safety_shutdown,
    save_optimal_control_source_snapshot,
    synchronize_connected_clocks,
    validate_z_trigger_mapping,
)
from ...steps.routed_signal_generator import connect_signal_generator_routes
from ...control_sources import (
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)
from .models import ZAWWaveformScopeCheckParams


EXPERIMENT_ID = "z-aw-waveform-scope-check"
DATA_TYPE = "Z_AW_Waveform_Scope_Check"
EXECUTION_MODE = "typed_workflow"
SCOPE_MEMORY_MANAGEMENT = "FSRate"
SCOPE_TRIGGER_MODE = "SINGle"
SCOPE_TRIGGER_SOURCE = "C4"
SCOPE_TRIGGER_SLOPE = "FALLing"
SCOPE_COUPLING = "DC"
SCOPE_IMPEDANCE = "ONEMeg"
SCOPE_PROBE = 1.0
SCOPE_FRAME_RETRIES = 3
SCOPE_CAPTURE_GUARD_S = 0.25
SCOPE_STOP_TIMEOUT_S = 2.0
SCOPE_SOFT_RESET_SETTLE_S = 4.0


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


def _requested_sample_rate(theory: Any) -> float:
    """按理论任意波时间步长请求示波器采样率。"""
    steps = np.diff(np.asarray(theory.time_s, dtype=float))
    if steps.size == 0 or not np.all(np.isfinite(steps)):
        raise ValueError("理论控制波形时间轴无有效步长")
    step_s = float(np.median(steps))
    if step_s <= 0.0:
        raise ValueError("理论控制波形时间步长必须为正")
    return 1.0 / step_s


def _scope_config(
    params: ZAWWaveformScopeCheckParams,
    theory: Any,
    *,
    measured_scale_v_div: float,
    measured_offset_v: float,
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    """构造双通道 C3/C4 单次触发采集配置。"""
    period_s = 1.0 / float(theory.repeat_frequency_hz)
    duration_s = float(params.scope_cycles) * period_s
    sample_rate_sa_s = _requested_sample_rate(theory)
    channels = [
        ChannelConfig(
            number=channel,
            enabled=channel in {
                params.scope_measured_channel,
                params.scope_trigger_channel,
            },
            scale=(
                measured_scale_v_div
                if channel == params.scope_measured_channel
                else 1.0
            ),
            # SDS offset uses the opposite sign to the displayed waveform
            # center; C4's 0--5 V trigger is centered at -2.5 V.
            offset=(
                measured_offset_v
                if channel == params.scope_measured_channel
                else -2.5
            ),
            coupling=SCOPE_COUPLING,
            impedance=SCOPE_IMPEDANCE,
            probe=SCOPE_PROBE,
        )
        for channel in range(1, 5)
    ]
    config = AcquisitionConfig(
        sampling_rate=sample_rate_sa_s,
        sampling_time=duration_s,
        acquire_type="NORMal",
        memory_management=SCOPE_MEMORY_MANAGEMENT,
        acquire_delay=duration_s + 5.0,
        channels=channels,
        trigger=TriggerConfig(
            mode=SCOPE_TRIGGER_MODE,
            source=SCOPE_TRIGGER_SOURCE,
            type="EDGE",
            slope=SCOPE_TRIGGER_SLOPE,
            level=params.scope_trigger_level_v,
        ),
    )
    return config, {
        "requested_sample_rate_sa_s": sample_rate_sa_s,
        "requested_duration_s": duration_s,
        "requested_points": config.total_points,
        "period_s": period_s,
        "cycles": params.scope_cycles,
        "measured_channel": params.scope_measured_channel,
        "trigger_channel": params.scope_trigger_channel,
        "trigger_mode": SCOPE_TRIGGER_MODE,
        "trigger_source": SCOPE_TRIGGER_SOURCE,
        "trigger_slope": SCOPE_TRIGGER_SLOPE,
        "trigger_level_v": params.scope_trigger_level_v,
        "coupling": SCOPE_COUPLING,
        "impedance": SCOPE_IMPEDANCE,
        "probe": SCOPE_PROBE,
        "memory_management": SCOPE_MEMORY_MANAGEMENT,
        "initial_measured_scale_v_div": measured_scale_v_div,
        "initial_measured_offset_v": measured_offset_v,
    }


def _sleep_cancellable(seconds: float) -> None:
    """分段等待，以便长记录窗期间仍可响应取消。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _wait_for_scope_trigger(scope: SDSInstrument, timeout_s: float) -> None:
    """等待 C4 下降沿真正触发，避免强制 STOP 得到随机相位帧。"""
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while time.monotonic() < deadline:
        check_cancelled()
        try:
            status = str(scope.trigger_status()).strip().lower()
        except Exception:
            # 部分 SDS 固件对高频 STATus? 查询会返回 VISA 协议错误；
            # 交由固定采集窗口和后续 C4 下降沿检测判断是否真的触发。
            _sleep_cancellable(min(0.2, max(0.0, deadline - time.monotonic())))
            return
        if status in {"trig'd", "triggered"}:
            return
        if status not in {"ready", "wait", "waiting", "armed", "run", "running"}:
            if status in {"stop", "stopped"}:
                raise TimeoutError("SDS 在等待 C4 下降沿时进入 STOP 状态")
        time.sleep(0.02)
    raise TimeoutError(f"SDS 等待 C4 下降沿触发超时（>{timeout_s:.3g} s）")


def _wait_for_scope_stop(scope: SDSInstrument, timeout_s: float) -> None:
    """确认 SDS 已停止，避免在深存储帧提交前读取波形。"""
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while time.monotonic() < deadline:
        check_cancelled()
        try:
            if str(scope.trigger_status()).strip().upper() in {"STOP", "STOPPED"}:
                return
        except Exception:
            return
        time.sleep(0.05)
    raise TimeoutError(f"SDS 停止状态确认超时（>{timeout_s:.3g} s）")


def _scope_record_duration_s(
    scope: SDSInstrument,
    config: AcquisitionConfig,
) -> float:
    """优先按 SDS 回读值估算记录窗，失败时退回请求时长。"""
    try:
        sample_rate_sa_s = float(scope.get_sampling_rate())
        point_count = float(scope.get_actual_points())
        duration_s = point_count / sample_rate_sa_s
    except Exception:
        return float(config.sampling_time)
    if not np.isfinite(duration_s) or duration_s <= 0.0:
        return float(config.sampling_time)
    return max(float(config.sampling_time), duration_s)


def _capture_frame(
    devices: dict[str, Any],
    config: AcquisitionConfig,
    params: ZAWWaveformScopeCheckParams,
) -> dict[str, Any]:
    """触发一次并读取 C3/C4，要求两通道返回同一时间轴。"""
    scope: SDSInstrument = devices["scope"]
    acquirer: SDSAcquisition = devices["acquirer"]
    last_problem = "未开始采集"
    soft_reset_performed = False

    for attempt in range(1, SCOPE_FRAME_RETRIES + 1):
        check_cancelled()
        # 必须先设置单次触发模式，再进入 RUN；部分 SDS 固件在 RUN 后
        # 重写触发模式会立刻回到 STOP，导致尚未等待 C4 下降沿就误报失败。
        scope.set_trigger_mode(SCOPE_TRIGGER_MODE)
        scope.trigger_run()
        results: dict[int, Any] | None = None
        try:
            try:
                _wait_for_scope_trigger(scope, config.acquire_delay)
                # Trig'd 出现后深存储仍在提交数据；等待完整记录窗再冻结读取。
                _sleep_cancellable(
                    _scope_record_duration_s(scope, config) + SCOPE_CAPTURE_GUARD_S
                )
                check_cancelled()
                scope.trigger_stop()
                _wait_for_scope_stop(scope, SCOPE_STOP_TIMEOUT_S)
            except TimeoutError as exc:
                last_problem = str(exc)
            else:
                results = {
                    channel: acquirer.acquire_channel(
                        channel,
                        config.timebase_scale,
                        config.horizontal_divisions,
                        trim_points=0,
                    )
                    for channel in (
                        params.scope_measured_channel,
                        params.scope_trigger_channel,
                    )
                }
        finally:
            try:
                scope.trigger_stop()
            except Exception:
                pass

        if results is None:
            if attempt >= SCOPE_FRAME_RETRIES:
                break
            if soft_reset_performed:
                continue
            current_scale = float(
                scope.get_channel_scale(params.scope_measured_channel)
            )
            current_offset = float(
                scope.get_channel_offset(params.scope_measured_channel)
            )
            scope.reset()
            _sleep_cancellable(SCOPE_SOFT_RESET_SETTLE_S)
            acquirer.apply_config(config)
            scope.set_channel_scale(params.scope_measured_channel, current_scale)
            scope.set_channel_offset(params.scope_measured_channel, current_offset)
            soft_reset_performed = True
            continue

        measured = results[params.scope_measured_channel]
        trigger = results[params.scope_trigger_channel]
        measured_time = np.asarray(measured.time, dtype=float).reshape(-1)
        trigger_time = np.asarray(trigger.time, dtype=float).reshape(-1)
        measured_voltage = np.asarray(measured.voltage, dtype=float).reshape(-1)
        trigger_voltage = np.asarray(trigger.voltage, dtype=float).reshape(-1)
        trigger_edge_detected = False
        if measured_time.size < 2 or trigger_time.size < 2:
            last_problem = (
                f"第 {attempt} 次读取仅得到 C3={measured_time.size}、"
                f"C4={trigger_time.size} 点"
            )
        elif measured_time.size != measured_voltage.size:
            last_problem = "SDS C3 时间轴和电压长度不一致"
        elif trigger_time.size != trigger_voltage.size:
            last_problem = "SDS C4 时间轴和电压长度不一致"
        elif not np.all(np.isfinite(measured_voltage)) or not np.all(
            np.isfinite(trigger_voltage)
        ):
            last_problem = "SDS C3/C4 包含非有限值"
        else:
            trigger_edge_detected = bool(
                np.any(
                    (trigger_voltage[:-1] >= params.scope_trigger_level_v)
                    & (trigger_voltage[1:] < params.scope_trigger_level_v)
                )
            )
            if trigger_edge_detected:
                return {
                    "time_s": measured_time,
                    "measured_voltage_v": measured_voltage,
                    "trigger_time_s": trigger_time,
                    "trigger_voltage_v": trigger_voltage,
                    "measured_preamble": measured.preamble_dict,
                    "trigger_preamble": trigger.preamble_dict,
                }
            last_problem = (
                "SDS C4 未检测到下降沿，"
                f"范围 {float(np.min(trigger_voltage)):.6g}--"
                f"{float(np.max(trigger_voltage)):.6g} V"
            )

        if attempt >= SCOPE_FRAME_RETRIES:
            break
        if (
            measured_time.size < 2
            or trigger_time.size < 2
            or not trigger_edge_detected
        ):
            if soft_reset_performed:
                continue
            # 首个空帧或无触发沿帧后软复位并重放配置，恢复 SDS 采集状态。
            current_scale = float(
                scope.get_channel_scale(params.scope_measured_channel)
            )
            current_offset = float(
                scope.get_channel_offset(params.scope_measured_channel)
            )
            scope.reset()
            _sleep_cancellable(SCOPE_SOFT_RESET_SETTLE_S)
            acquirer.apply_config(config)
            scope.set_channel_scale(params.scope_measured_channel, current_scale)
            scope.set_channel_offset(params.scope_measured_channel, current_offset)
            soft_reset_performed = True
        else:
            _sleep_cancellable(SCOPE_CAPTURE_GUARD_S)

    raise RuntimeError(f"SDS C3/C4 未获得有效触发帧：{last_problem}")


def _capture_with_auto_range(
    params: ZAWWaveformScopeCheckParams,
    devices: dict[str, Any],
    config: AcquisitionConfig,
    auto_range: ScopeAutoRangeState,
) -> dict[str, Any]:
    """自动调整 C3 量程/偏置后返回最终采集帧。"""
    scope: SDSInstrument = devices["scope"]
    accepted: dict[str, Any] | None = None
    for _attempt in range(params.scope_auto_range_max_attempts):
        check_cancelled()
        frame = _capture_frame(devices, config, params)
        # 必须记录采集本帧时的设置，不能使用随后自动调整的新量程。
        frame["scale_used_v_div"] = float(auto_range.scale_v_div)
        frame["offset_used_v"] = float(auto_range.offset_v)
        voltage = frame["measured_voltage_v"]
        waveform_min = float(np.min(voltage))
        waveform_max = float(np.max(voltage))
        half_span = auto_range.scale_v_div * params.scope_vertical_divisions / 2.0
        peak_deviation = 0.5 * (waveform_max - waveform_min)
        next_scale = next_auto_range_scale(
            _scope_settings(params),
            auto_range.scale_v_div,
            peak_deviation,
        )
        next_offset = next_auto_offset(
            _scope_settings(params),
            auto_range.scale_v_div,
            auto_range.offset_v,
            waveform_min,
            waveform_max,
        )
        display_peak = max(
            abs(waveform_min + auto_range.offset_v),
            abs(waveform_max + auto_range.offset_v),
        )
        if display_peak >= params.scope_auto_range_high_fraction * half_span:
            next_scale = max(next_scale, auto_range.scale_v_div)
        accepted = frame
        if next_scale == auto_range.scale_v_div and next_offset == auto_range.offset_v:
            break
        if next_scale != auto_range.scale_v_div:
            scope.set_channel_scale(params.scope_measured_channel, next_scale)
            auto_range.scale_v_div = float(
                scope.get_channel_scale(params.scope_measured_channel)
            )
        if next_offset != auto_range.offset_v:
            scope.set_channel_offset(params.scope_measured_channel, next_offset)
            auto_range.offset_v = float(
                scope.get_channel_offset(params.scope_measured_channel)
            )
        for channel_config in config.channels:
            if channel_config.number == params.scope_measured_channel:
                channel_config.scale = auto_range.scale_v_div
                channel_config.offset = auto_range.offset_v
                break
    if accepted is None:
        raise RuntimeError("SDS C3 自动量程未获得有效波形")
    accepted.update(
        {
            "actual_rate_sa_s": 1.0
            / float(np.median(np.diff(accepted["time_s"]))),
        }
    )
    if not np.isfinite(accepted["actual_rate_sa_s"]) or accepted["actual_rate_sa_s"] <= 0:
        raise RuntimeError("SDS C3 实际采样率无效")
    return accepted


def _scope_settings(params: ZAWWaveformScopeCheckParams) -> Any:
    """构造自动量程共享函数所需的最小设置对象。"""
    from ...steps.scope_waveform import ScopeCaptureSettings

    return ScopeCaptureSettings(
        sample_rate_sa_s=1.0,
        duration_s=1.0,
        pd_channel=params.scope_measured_channel,
        trigger_mode=SCOPE_TRIGGER_MODE,
        initial_scale_v_div=params.scope_initial_scale_v_div,
        offset_v=0.0,
        vertical_divisions=params.scope_vertical_divisions,
        scale_min_v_div=params.scope_scale_min_v_div,
        scale_max_v_div=params.scope_scale_max_v_div,
        auto_range_low_fraction=params.scope_auto_range_low_fraction,
        auto_range_high_fraction=params.scope_auto_range_high_fraction,
        auto_offset_tolerance_fraction=0.05,
        auto_range_max_attempts=params.scope_auto_range_max_attempts,
        welch_nperseg=8,
        maximum_frequency_hz=1.0,
    )


def _save_frame(
    run_dir: Any,
    index: int,
    frame: dict[str, Any],
    *,
    requested_rate_sa_s: float,
    requested_duration_s: float,
    scope_config: dict[str, Any],
) -> str:
    """保存一次 C3/C4 原始采集及关键诊断元数据。"""
    relative = f"raw/scope_capture_{index:03d}.npz"
    np.savez(
        run_dir.root / relative,
        time_s=np.asarray(frame["time_s"], dtype=float),
        measured_voltage_v=np.asarray(frame["measured_voltage_v"], dtype=float),
        trigger_time_s=np.asarray(frame["trigger_time_s"], dtype=float),
        trigger_voltage_v=np.asarray(frame["trigger_voltage_v"], dtype=float),
        requested_rate_sa_s=np.float64(requested_rate_sa_s),
        actual_rate_sa_s=np.float64(frame["actual_rate_sa_s"]),
        requested_duration_s=np.float64(requested_duration_s),
        actual_duration_s=np.float64(
            np.median(np.diff(frame["time_s"]))
            * (len(frame["time_s"]) - 1)
        ),
        scale_used_v_div=np.float64(frame["scale_used_v_div"]),
        offset_used_v=np.float64(frame["offset_used_v"]),
        measured_preamble_json=np.array(
            json.dumps(frame["measured_preamble"], ensure_ascii=False)
        ),
        trigger_preamble_json=np.array(
            json.dumps(frame["trigger_preamble"], ensure_ascii=False)
        ),
        scope_config_json=np.array(
            json.dumps(scope_config, ensure_ascii=False, sort_keys=True)
        ),
    )
    return relative


def _configure_outputs(
    params: ZAWWaveformScopeCheckParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    theory: Any,
    applied: Any,
) -> None:
    """配置共同触发和 Z 任意波，输出前完成安全校验。"""
    trigger_device = devices["z_control"]
    trigger_channel = channels["trigger"]
    z_channel = channels["z_field"]
    configure_optimal_control_trigger(
        params,
        trigger_device,
        trigger_channel,
        output=False,
    )
    configure_z_optimal_control_output(
        params,
        trigger_device,
        z_channel,
        theory,
        applied,
    )


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
) -> Any:
    """停止示波器并关闭 Z/共同触发两个通道。"""
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


def run_scope_check(
    params: ZAWWaveformScopeCheckParams,
    *,
    experiment_id: str = EXPERIMENT_ID,
    data_type: str = DATA_TYPE,
    measurement_mode: str = "coil_voltage",
    current_calibration: Any | None = None,
    theory_override: Any | None = None,
    applied_override: Any | None = None,
    source_snapshot_writer: Callable[[Path], list[str]] | None = None,
    target_control_scale: float | None = None,
) -> Path:
    """执行 Z 任意波多次示波器采集，可将 CH3 解释为采样电阻电压。"""
    root = find_project_root()
    mapping = load_mapping(root)
    if (theory_override is None) != (applied_override is None):
        raise ValueError("theory_override 与 applied_override 必须同时提供")
    calibration = None
    if theory_override is None:
        theory = load_theory_control(Path(params.control_results_root), params.control_version)
        calibration = load_z_calibration(root, params.z_calibration_source_run)
        applied = build_applied_control(
            theory,
            calibration,
            params.control_scale,
            output_vpp=params.z_aw_output_vpp,
            output_offset_v=params.z_aw_output_offset_v,
        )
    else:
        theory = theory_override
        applied = applied_override
    if measurement_mode == "current":
        if current_calibration is None:
            raise ValueError("current 测量模式必须提供电流耦合标定")
        effective_scale = (
            params.control_scale
            if target_control_scale is None
            else float(target_control_scale)
        )
        target_current_a = (
            effective_scale
            * theory.omega_ctrl_hz
            / current_calibration.slope_hz_per_a
        )
    else:
        target_current_a = None
    run_dir = create_run_directory(
        data_type,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    files: list[str] = []
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = _connect_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        clock_sources = synchronize_connected_clocks(
            {"z_control": devices["z_control"]},
            mapping,
            {"z_control": "Z_magnetic_field"},
            settle_s=0.2,
        )
        source_files = (
            source_snapshot_writer(run_dir.raw)
            if source_snapshot_writer is not None
            else save_optimal_control_source_snapshot(
                run_dir.raw, theory, calibration, applied
            )
        )
        files.extend(source_files)
        if target_current_a is not None:
            with np.load(run_dir.raw / "applied_control_waveform.npz") as data:
                payload = {key: data[key] for key in data.files}
            payload["target_current_a"] = np.asarray(target_current_a, dtype=float)
            np.savez(run_dir.raw / "applied_control_waveform.npz", **payload)
            source_files = [
                item for item in source_files if item != "raw/applied_control_waveform.npz"
            ] + ["raw/applied_control_waveform.npz"]
        run_dir.update_config(
            experiment_id=experiment_id,
            data_type=data_type,
            execution_mode=EXECUTION_MODE,
            mapping_keys=["Z_magnetic_field", "Time_sequence_2", "scope_waveform"],
            acquisition_signals=(
                [
                    "SDS CH3 low-side sense-resistor voltage",
                    "SDS CH4 trigger reference",
                ]
                if measurement_mode == "current"
                else ["SDS CH3 Z coil voltage", "SDS CH4 trigger reference"]
            ),
            measurement_mode=measurement_mode,
            control_source={
                "version": theory.version,
                "waveform_sha256": theory.waveform_sha256,
                "parameter_sha256": theory.parameter_sha256,
                "repeat_frequency_hz": theory.repeat_frequency_hz,
                "period_s": 1.0 / theory.repeat_frequency_hz,
            },
            z_calibration=(
                {
                    "source_run": calibration.run_name,
                    "slope_hz_per_v": calibration.slope_hz_per_v,
                    "intercept_hz": calibration.intercept_hz,
                    "r_squared": calibration.r_squared,
                }
                if calibration is not None
                else None
            ),
            current_coupling_calibration=(
                {
                    "source_run": current_calibration.run_name,
                    "analysis_sha256": current_calibration.analysis_sha256,
                    "slope_hz_per_a": current_calibration.slope_hz_per_a,
                    "sense_resistor_ohm": current_calibration.sense_resistor_ohm,
                }
                if current_calibration is not None
                else None
            ),
            applied_control={
                "control_scale": (
                    params.control_scale
                    if target_control_scale is None
                    else target_control_scale
                ),
                "amplitude_vpp": applied.amplitude_vpp,
                "offset_v": applied.offset_v,
                "minimum_v": applied.minimum_v,
                "maximum_v": applied.maximum_v,
                "output_minimum_v": applied.output_minimum_v,
                "output_maximum_v": applied.output_maximum_v,
                "points": int(theory.time_s.size),
            },
            trigger={
                "source": "Time_sequence_2",
                "frequency_hz": params.trigger_frequency_hz,
                "amplitude_vpp": params.trigger_amplitude_vpp,
                "offset_v": params.trigger_offset_v,
                "duty_percent": params.trigger_duty_percent,
                "slope": "NEGative",
                "scope_source": SCOPE_TRIGGER_SOURCE,
                "scope_slope": SCOPE_TRIGGER_SLOPE,
                "scope_level_v": params.scope_trigger_level_v,
            },
            clock_sources=clock_sources,
        )
        _configure_outputs(params, devices, channels, theory, applied)
        config, scope_snapshot = _scope_config(
            params,
            theory,
            measured_scale_v_div=params.scope_initial_scale_v_div,
            measured_offset_v=0.0,
        )
        devices["acquirer"].apply_config(config)
        scope = devices["scope"]
        actual_rate = float(scope.get_sampling_rate())
        actual_points = int(scope.get_actual_points())
        scope_snapshot.update(
            {
                "actual_sample_rate_sa_s": actual_rate,
                "actual_points": actual_points,
                "actual_memory_management": str(scope.get_memory_management()),
                "memory_depth": str(scope.get_memory_depth()),
                "actual_initial_scale_v_div": float(
                    scope.get_channel_scale(params.scope_measured_channel)
                ),
                "actual_initial_offset_v": float(
                    scope.get_channel_offset(params.scope_measured_channel)
                ),
                "actual_trigger_scale_v_div": float(
                    scope.get_channel_scale(params.scope_trigger_channel)
                ),
                "actual_trigger_offset_v": float(
                    scope.get_channel_offset(params.scope_trigger_channel)
                ),
                "actual_trigger_coupling": str(
                    scope.get_channel_coupling(params.scope_trigger_channel)
                ),
                "actual_trigger_impedance": str(
                    scope.get_channel_impedance(params.scope_trigger_channel)
                ),
                "actual_trigger_probe": float(
                    scope.get_channel_probe(params.scope_trigger_channel)
                ),
            }
        )
        run_dir.update_config(scope_configuration=scope_snapshot)
        auto_range = ScopeAutoRangeState(
            scale_v_div=scope_snapshot["actual_initial_scale_v_div"],
            offset_v=scope_snapshot["actual_initial_offset_v"],
        )
        # 触发方波在示波器完成配置后开启，确保第一帧不会在配置阶段丢失。
        devices["z_control"].set_output(True, channel=channels["trigger"])
        for index in range(params.scope_repeats):
            check_cancelled()
            frame = _capture_with_auto_range(params, devices, config, auto_range)
            relative = _save_frame(
                run_dir,
                index,
                frame,
                requested_rate_sa_s=scope_snapshot["requested_sample_rate_sa_s"],
                requested_duration_s=scope_snapshot["requested_duration_s"],
                scope_config=scope_snapshot,
            )
            files.append(relative)
            print(
                f"Z 任意波示波器采集 {index + 1}/{params.scope_repeats}: "
                f"{len(frame['time_s'])} 点，实际 {frame['actual_rate_sa_s']:.6g} Sa/s"
            )
        completion_status = "completed"
        print(f"Z 任意波线圈波形验证采集完成 ({measurement_mode}): {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels)
        if shutdown_report.errors:
            failure_reason = (
                f"{failure_reason}；安全恢复失败: {'；'.join(shutdown_report.errors)}"
                if failure_reason
                else "安全恢复失败: " + "；".join(shutdown_report.errors)
            )
            completion_status = "failed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=failure_reason,
            data_files=files,
            safety_shutdown=shutdown_report.to_dict(),
        )


def run(params: ZAWWaveformScopeCheckParams) -> Path:
    """执行原有线圈端电压波形验证实验。"""
    return run_scope_check(params)


def main() -> int:
    params = load_runtime_params(ZAWWaveformScopeCheckParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
