"""Z 线圈电感效应频率响应采集工作流。"""

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

from ...common import WorkflowCancelled, find_project_root, load_mapping, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    ScopeAutoRangeState,
    ScopeCaptureSettings,
    create_run_directory,
    configure_optimal_control_trigger,
    next_auto_offset,
    next_auto_range_scale,
    run_safety_shutdown,
    synchronize_connected_clocks,
    validate_z_trigger_mapping,
)
from ...steps.routed_signal_generator import connect_signal_generator_routes
from .models import ZCoilInductanceFrequencyResponseParams


EXPERIMENT_ID = "z-coil-inductance-frequency-response"
DATA_TYPE = "Z_Coil_Inductance_Frequency_Response"
EXECUTION_MODE = "typed_workflow"
SCOPE_MEMORY_MANAGEMENT = "FSRate"
SCOPE_TRIGGER_MODE = "SINGle"
SCOPE_TRIGGER_SOURCE = "C4"
SCOPE_TRIGGER_SLOPE = "FALLing"
SCOPE_COUPLING = "DC"
SCOPE_IMPEDANCE = "ONEMeg"
SCOPE_PROBE = 1.0
Z_BURST_TRIGGER_SLOPE = "NEGative"
SCOPE_FRAME_RETRIES = 3
SCOPE_CAPTURE_GUARD_S = 0.25
SCOPE_STOP_TIMEOUT_S = 2.0
SCOPE_SOFT_RESET_SETTLE_S = 4.0
# C4 是 100 Hz 共同触发时，短于一个周期的记录窗口可能只截到高电平，
# 即使 SDS 已经被下降沿触发，下载波形也无法再恢复该下降沿。保留两个周期
# 的窗口可以覆盖任意触发相位，并给边界采样留出余量。
SCOPE_TRIGGER_COVERAGE_CYCLES = 2.0


def build_frequency_axis(params: ZCoilInductanceFrequencyResponseParams) -> np.ndarray:
    """构造线性频率轴，并在工作流外也提供可测试的确定性接口。"""
    return np.linspace(
        params.frequency_start_hz,
        params.frequency_stop_hz,
        params.frequency_points,
        dtype=float,
    )


def sine_reference(
    time_s: np.ndarray,
    frequency_hz: float,
    amplitude_vpp: float,
    offset_v: float,
    phase_deg: float = 0.0,
) -> np.ndarray:
    """返回 Burst 相位定义下的理想 DG 正弦参考。"""
    time_s = np.asarray(time_s, dtype=float)
    phase_rad = np.deg2rad(float(phase_deg))
    return float(offset_v) + 0.5 * float(amplitude_vpp) * np.sin(
        2.0 * np.pi * float(frequency_hz) * time_s + phase_rad
    )


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """按物理量映射连接同一台 DG4000 和 SDS。"""
    z_cfg = mapping["Z_magnetic_field"]
    trigger_cfg = mapping["Time_sequence_2"]
    if z_cfg.get("instrument") != "signal_generator":
        raise ValueError("Z_magnetic_field 必须映射到信号发生器")
    if trigger_cfg.get("instrument") != "signal_generator":
        raise ValueError("Time_sequence_2 必须映射到信号发生器")
    trigger_channel = validate_z_trigger_mapping(mapping)
    z_control, channels = connect_signal_generator_routes(
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
        "z_control": z_control,
        "scope": scope,
        "acquirer": session.bind("acquirer", SDSAcquisition(scope)),
    }
    if channels["trigger"] != trigger_channel:
        raise ValueError("Time_sequence_2 路由通道与映射端点不一致")
    return devices, channels


def _scope_config(
    params: ZCoilInductanceFrequencyResponseParams,
    frequency_hz: float,
    *,
    measured_scale_v_div: float,
    measured_offset_v: float,
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    """构造当前频率的双通道 SDS 单次触发采集配置。"""
    period_s = 1.0 / float(frequency_hz)
    waveform_duration_s = float(params.scope_cycles) * period_s
    trigger_duration_s = (
        SCOPE_TRIGGER_COVERAGE_CYCLES / float(params.trigger_frequency_hz)
    )
    duration_s = max(waveform_duration_s, trigger_duration_s)
    sample_rate_sa_s = _sample_rate_for_frequency(params, frequency_hz)
    channels = [
        ChannelConfig(
            number=channel,
            enabled=channel
            in {params.scope_measured_channel, params.scope_trigger_channel},
            scale=measured_scale_v_div if channel == params.scope_measured_channel else 1.0,
            # SDS offset 与显示波形中心符号相反；触发方波显示为 0--5 V。
            offset=measured_offset_v if channel == params.scope_measured_channel else -2.5,
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
        "maximum_sample_rate_sa_s": float(params.scope_sample_rate_sa_s),
        "requested_duration_s": duration_s,
        "requested_waveform_duration_s": waveform_duration_s,
        "requested_trigger_coverage_s": trigger_duration_s,
        "trigger_coverage_cycles": SCOPE_TRIGGER_COVERAGE_CYCLES,
        "requested_points": config.total_points,
        "period_s": period_s,
        "cycles": int(params.scope_cycles),
        "frequency_hz": float(frequency_hz),
        "measured_channel": int(params.scope_measured_channel),
        "trigger_channel": int(params.scope_trigger_channel),
        "trigger_mode": SCOPE_TRIGGER_MODE,
        "trigger_source": SCOPE_TRIGGER_SOURCE,
        "trigger_slope": SCOPE_TRIGGER_SLOPE,
        "trigger_level_v": float(params.scope_trigger_level_v),
        "coupling": SCOPE_COUPLING,
        "impedance": SCOPE_IMPEDANCE,
        "probe": SCOPE_PROBE,
        "memory_management": SCOPE_MEMORY_MANAGEMENT,
        "initial_measured_scale_v_div": float(measured_scale_v_div),
        "initial_measured_offset_v": float(measured_offset_v),
    }


def _sample_rate_for_frequency(
    params: ZCoilInductanceFrequencyResponseParams,
    frequency_hz: float,
) -> float:
    """按频率选择采样率，兼容旧实验的固定采样率行为。

    电流频响实验可声明最低采样率和每周期采样点数。未声明这两个
    参数的旧模型仍返回原有固定采样率，避免改变历史实验行为。
    """
    maximum_rate = float(params.scope_sample_rate_sa_s)
    minimum_rate = float(getattr(params, "scope_min_sample_rate_sa_s", maximum_rate))
    samples_per_cycle = float(getattr(params, "scope_samples_per_cycle", maximum_rate))
    requested = max(minimum_rate, samples_per_cycle * float(frequency_hz))
    return min(maximum_rate, requested)


def _scope_settings(params: ZCoilInductanceFrequencyResponseParams) -> ScopeCaptureSettings:
    return ScopeCaptureSettings(
        sample_rate_sa_s=float(params.scope_sample_rate_sa_s),
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
        maximum_frequency_hz=params.frequency_stop_hz,
    )


def _wait_for_scope_trigger(scope: SDSInstrument, timeout_s: float) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while time.monotonic() < deadline:
        check_cancelled()
        status = str(scope.trigger_status()).strip().lower()
        if status in {"trig'd", "triggered", "stop", "stopped"}:
            if status in {"stop", "stopped"}:
                raise TimeoutError("SDS 等待触发时进入 STOP 状态")
            return
        time.sleep(0.02)
    raise TimeoutError(f"SDS 等待 CH4 下降沿触发超时（>{timeout_s:.3g} s）")


def _wait_for_scope_stop(scope: SDSInstrument, timeout_s: float) -> None:
    """确认 SDS 已经停止，避免在深存储帧提交前读取波形。"""
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while time.monotonic() < deadline:
        check_cancelled()
        if str(scope.trigger_status()).strip().upper() in {"STOP", "STOPPED"}:
            return
        time.sleep(0.05)
    raise TimeoutError(f"SDS 停止状态确认超时（>{timeout_s:.3g} s）")


def _scope_record_duration_s(
    scope: SDSInstrument,
    config: AcquisitionConfig,
) -> float:
    """优先使用 SDS 回读值估算完整记录窗，失败时退回请求时长。"""
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
    params: ZCoilInductanceFrequencyResponseParams,
    *,
    capture_guard_s: float = SCOPE_CAPTURE_GUARD_S,
    before_scope_wait: Callable[[], None] | None = None,
) -> dict[str, Any]:
    scope: SDSInstrument = devices["scope"]
    acquirer: SDSAcquisition = devices["acquirer"]
    last_problem = "未开始采集"
    soft_reset_performed = False

    for attempt in range(1, SCOPE_FRAME_RETRIES + 1):
        check_cancelled()
        scope.trigger_run()
        # RUN 后重写 SINGle，兼容当前 SDS 固件；采集函数随后等待完整记录窗。
        scope.set_trigger_mode(SCOPE_TRIGGER_MODE)
        if before_scope_wait is not None:
            # SDS 已进入等待状态，再启动共同触发和被测输出，保证相位相干。
            before_scope_wait()
        try:
            # SINGle 触发状态出现后，SDS 仍需要完成整个深存储窗口；
            # 立即 STOP 会让 DATA? 返回空帧，尤其容易发生在低频首帧。
            _sleep_cancellable(
                _scope_record_duration_s(scope, config) + float(capture_guard_s)
            )
            check_cancelled()
            scope.trigger_stop()
            _wait_for_scope_stop(scope, SCOPE_STOP_TIMEOUT_S)
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
            try:
                _falling_edge_time(
                    trigger_time,
                    trigger_voltage,
                    params.scope_trigger_level_v,
                )
                trigger_edge_detected = True
            except ValueError:
                last_problem = (
                    "SDS C4 未检测到下降沿触发，"
                    f"波形范围 {float(np.min(trigger_voltage)):.6g}--"
                    f"{float(np.max(trigger_voltage)):.6g} V；"
                    "请检查 CH4 接线/量程，或确认共同触发输出正在运行"
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

        if attempt >= SCOPE_FRAME_RETRIES:
            break
        if (
            measured_time.size < 2
            or trigger_time.size < 2
            or not trigger_edge_detected
        ):
            if soft_reset_performed:
                continue
            # SDS1204X HD 某些固件在第一次 DATA? 空帧后需要软复位，
            # 复位后重新应用当前采集配置再重试，避免整项实验立即失败。
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
            _sleep_cancellable(float(capture_guard_s))

    raise RuntimeError(f"SDS C3/C4 有效点数不足：{last_problem}")


def _capture_with_auto_range(
    params: ZCoilInductanceFrequencyResponseParams,
    devices: dict[str, Any],
    config: AcquisitionConfig,
    auto_range: ScopeAutoRangeState,
    *,
    capture_guard_s: float = SCOPE_CAPTURE_GUARD_S,
    before_scope_wait: Callable[[], None] | None = None,
) -> dict[str, Any]:
    scope: SDSInstrument = devices["scope"]
    settings = _scope_settings(params)
    accepted: dict[str, Any] | None = None
    for _attempt in range(params.scope_auto_range_max_attempts):
        check_cancelled()
        frame = _capture_frame(
            devices,
            config,
            params,
            capture_guard_s=capture_guard_s,
            before_scope_wait=before_scope_wait,
        )
        voltage = frame["measured_voltage_v"]
        waveform_min = float(np.min(voltage))
        waveform_max = float(np.max(voltage))
        peak_deviation = 0.5 * (waveform_max - waveform_min)
        next_scale = next_auto_range_scale(
            settings,
            auto_range.scale_v_div,
            peak_deviation,
        )
        next_offset = next_auto_offset(
            settings,
            auto_range.scale_v_div,
            auto_range.offset_v,
            waveform_min,
            waveform_max,
        )
        half_span = auto_range.scale_v_div * params.scope_vertical_divisions / 2.0
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
    dt = float(np.median(np.diff(accepted["time_s"])))
    accepted.update(
        {
            "scale_used_v_div": float(auto_range.scale_v_div),
            "offset_used_v": float(auto_range.offset_v),
            "actual_rate_sa_s": 1.0 / dt,
        }
    )
    if not np.isfinite(accepted["actual_rate_sa_s"]) or accepted["actual_rate_sa_s"] <= 0:
        raise RuntimeError("SDS C3 实际采样率无效")
    return accepted


def _falling_edge_time(time_s: np.ndarray, voltage_v: np.ndarray, level_v: float) -> float:
    before = voltage_v[:-1] >= level_v
    after = voltage_v[1:] < level_v
    indices = np.flatnonzero(before & after)
    if indices.size == 0:
        raise ValueError("CH4 未检测到下降沿触发")
    index = int(indices[0])
    v0, v1 = float(voltage_v[index]), float(voltage_v[index + 1])
    t0, t1 = float(time_s[index]), float(time_s[index + 1])
    if v1 == v0:
        return t0
    fraction = (level_v - v0) / (v1 - v0)
    return t0 + float(np.clip(fraction, 0.0, 1.0)) * (t1 - t0)


def _save_capture(
    run_dir: Any,
    frequency_index: int,
    repeat_index: int,
    frame: dict[str, Any],
    params: ZCoilInductanceFrequencyResponseParams,
    frequency_hz: float,
    scope_snapshot: dict[str, Any],
) -> str:
    relative = f"raw/scope_f{frequency_index:03d}_r{repeat_index:02d}.npz"
    edge_time = _falling_edge_time(
        np.asarray(frame["trigger_time_s"], dtype=float),
        np.asarray(frame["trigger_voltage_v"], dtype=float),
        params.scope_trigger_level_v,
    )
    theory_time_s = np.asarray(frame["time_s"], dtype=float) - edge_time
    theory_voltage_v = sine_reference(
        theory_time_s,
        frequency_hz,
        params.drive_amplitude_vpp,
        params.drive_offset_v,
    )
    np.savez(
        run_dir.root / relative,
        time_s=np.asarray(frame["time_s"], dtype=float),
        measured_voltage_v=np.asarray(frame["measured_voltage_v"], dtype=float),
        trigger_time_s=np.asarray(frame["trigger_time_s"], dtype=float),
        trigger_voltage_v=np.asarray(frame["trigger_voltage_v"], dtype=float),
        theory_time_s=theory_time_s,
        theory_voltage_v=theory_voltage_v,
        trigger_edge_time_s=np.float64(edge_time),
        frequency_hz=np.float64(frequency_hz),
        drive_amplitude_vpp=np.float64(params.drive_amplitude_vpp),
        drive_offset_v=np.float64(params.drive_offset_v),
        phase_deg=np.float64(0.0),
        actual_rate_sa_s=np.float64(frame["actual_rate_sa_s"]),
        scale_used_v_div=np.float64(frame["scale_used_v_div"]),
        offset_used_v=np.float64(frame["offset_used_v"]),
        scope_config_json=np.array(
            json.dumps(scope_snapshot, ensure_ascii=False, sort_keys=True)
        ),
        measured_preamble_json=np.array(
            json.dumps(frame["measured_preamble"], ensure_ascii=False)
        ),
        trigger_preamble_json=np.array(
            json.dumps(frame["trigger_preamble"], ensure_ascii=False)
        ),
    )
    return relative


def _sleep_cancellable(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _configure_sine(
    device: Any,
    channel: int,
    params: ZCoilInductanceFrequencyResponseParams,
    frequency_hz: float,
    *,
    output: bool,
    initialize: bool = True,
) -> None:
    """初始化或切换 Z 正弦，保持外部触发 Burst 配置。"""
    minimum = params.drive_offset_v - params.drive_amplitude_vpp / 2.0
    maximum = params.drive_offset_v + params.drive_amplitude_vpp / 2.0
    validate_safety_limit("Z_magnetic_field", minimum)
    validate_safety_limit("Z_magnetic_field", maximum)
    device.set_output(False, channel=channel)
    if initialize:
        device.set_burst_state(False, channel=channel)
        device.set_mod_state(False, channel=channel)
        device.setup_sine(
            float(frequency_hz),
            float(params.drive_amplitude_vpp),
            offset=float(params.drive_offset_v),
            phase=0.0,
            channel=channel,
        )
    device.set_frequency(float(frequency_hz), channel=channel)
    device.set_amplitude(float(params.drive_amplitude_vpp), channel=channel)
    device.set_offset(float(params.drive_offset_v), channel=channel)
    device.set_phase_adjust(0.0, channel=channel)
    if initialize:
        device.set_burst_mode("INFinity", channel=channel)
        device.set_burst_phase(0.0, channel=channel)
        device.set_burst_trigger_source("EXTernal", channel=channel)
        device.set_burst_trigger_slope(Z_BURST_TRIGGER_SLOPE, channel=channel)
        device.set_burst_state(True, channel=channel)
    device.set_output(bool(output), channel=channel)


def safe_shutdown(devices: dict[str, Any], channels: dict[str, int]) -> Any:
    """停止 SDS，并关闭 Z 与共同触发两个 DG 通道。"""
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
                    "Z 正弦驱动",
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


def run_frequency_response(
    params: ZCoilInductanceFrequencyResponseParams,
    *,
    experiment_id: str = EXPERIMENT_ID,
    data_type: str = DATA_TYPE,
    acquisition_signals: list[str] | None = None,
    physical_limit: str | None = None,
    completion_label: str = "Z 线圈电感效应频率响应",
    capture_validator: Callable[[dict[str, Any]], None] | None = None,
    capture_guard_s: float | None = None,
) -> Path:
    """执行可复用的 Z 正弦扫频和 SDS 双通道采集。"""
    root = find_project_root()
    mapping = load_mapping(root)
    frequencies = build_frequency_axis(params)
    run_dir = create_run_directory(
        data_type,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    files: list[str] = []
    frequency_records: list[dict[str, Any]] = []
    effective_capture_guard_s = (
        float(getattr(params, "scope_capture_guard_s", SCOPE_CAPTURE_GUARD_S))
        if capture_guard_s is None
        else float(capture_guard_s)
    )
    if effective_capture_guard_s < 0.0:
        raise ValueError("capture_guard_s 不能为负数")
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
        run_dir.update_config(
            experiment_id=experiment_id,
            data_type=data_type,
            execution_mode=EXECUTION_MODE,
            mapping_keys=["Z_magnetic_field", "Time_sequence_2", "scope_waveform"],
            acquisition_signals=(
                acquisition_signals
                or ["SDS CH3 Z coil voltage", "SDS CH4 trigger reference"]
            ),
            scan_mode="point_by_point",
            frequency_axis_hz=frequencies.tolist(),
            drive={
                "waveform": "SINusoid",
                "amplitude_vpp": float(params.drive_amplitude_vpp),
                "offset_v": float(params.drive_offset_v),
                "phase_deg": 0.0,
                "burst_trigger": "external falling edge",
            },
            trigger={
                "source": "Time_sequence_2",
                "frequency_hz": float(params.trigger_frequency_hz),
                "amplitude_vpp": float(params.trigger_amplitude_vpp),
                "offset_v": float(params.trigger_offset_v),
                "duty_percent": float(params.trigger_duty_percent),
                "scope_source": SCOPE_TRIGGER_SOURCE,
                "scope_slope": SCOPE_TRIGGER_SLOPE,
                "scope_level_v": float(params.scope_trigger_level_v),
            },
            clock_sources=clock_sources,
            physical_limit=(
                physical_limit
                or "仅观察线圈端电压频率响应；理论值为 DG 设定值，不能单独反演唯一 L。"
            ),
        )

        trigger_device = devices["z_control"]
        z_channel = channels["z_field"]
        trigger_channel = channels["trigger"]
        configure_optimal_control_trigger(
            params,
            trigger_device,
            trigger_channel,
            output=False,
        )
        _configure_sine(
            trigger_device,
            z_channel,
            params,
            float(frequencies[0]),
            output=False,
        )

        for frequency_index, frequency in enumerate(frequencies):
            check_cancelled()
            frequency = float(frequency)
            if frequency_index:
                trigger_device.set_output(False, channel=trigger_channel)
                _configure_sine(
                    trigger_device,
                    z_channel,
                    params,
                    frequency,
                    output=False,
                    initialize=False,
                )
            config, scope_snapshot = _scope_config(
                params,
                frequency,
                measured_scale_v_div=params.scope_initial_scale_v_div,
                measured_offset_v=0.0,
            )
            devices["acquirer"].apply_config(config)
            scope = devices["scope"]
            actual_sample_rate = float(scope.get_sampling_rate())
            scope_snapshot.update(
                {
                    "actual_sample_rate_sa_s": actual_sample_rate,
                    "actual_points": int(scope.get_actual_points()),
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
                }
            )
            run_dir.update_config(
                scope_configuration_last=scope_snapshot,
                actual_rates={"scope_sa_s": scope_snapshot["actual_sample_rate_sa_s"]},
            )
            auto_range = ScopeAutoRangeState(
                scale_v_div=scope_snapshot["actual_initial_scale_v_div"],
                offset_v=scope_snapshot["actual_initial_offset_v"],
            )
            _sleep_cancellable(params.frequency_settle_s)
            if not bool(getattr(params, "coherent_rearm", False)):
                trigger_device.set_output(True, channel=trigger_channel)
                trigger_device.set_output(True, channel=z_channel)
            point_files: list[str] = []
            for repeat_index in range(params.scope_repeats):
                check_cancelled()
                before_scope_wait = None
                if bool(getattr(params, "coherent_rearm", False)):
                    # 输出必须在 SDS 进入 SINGle 等待后再启动，否则连续 CH4
                    # 边沿会让不同重复采集到任意相位。
                    trigger_device.set_output(False, channel=trigger_channel)
                    trigger_device.set_output(False, channel=z_channel)

                    def _start_coherent_outputs() -> None:
                        # 每次重复均将共同触发方波和正弦相位归零，避免
                        # 仅切换输出状态却沿用上一次周期相位。
                        trigger_device.phase_init(channel=trigger_channel)
                        trigger_device.phase_init(channel=z_channel)
                        # 先启动被测正弦，再启动共同触发；这样 CH4 下降沿
                        # 发生在 Z 通道已经稳定输出之后，避免两次 SCPI
                        # 写入间的不可预测延迟转化为测量相位误差。
                        trigger_device.set_output(True, channel=z_channel)
                        trigger_device.set_output(True, channel=trigger_channel)

                    before_scope_wait = _start_coherent_outputs
                frame = _capture_with_auto_range(
                    params,
                    devices,
                    config,
                    auto_range,
                    capture_guard_s=effective_capture_guard_s,
                    before_scope_wait=before_scope_wait,
                )
                if capture_validator is not None:
                    capture_validator(frame)
                relative = _save_capture(
                    run_dir,
                    frequency_index,
                    repeat_index,
                    frame,
                    params,
                    frequency,
                    scope_snapshot,
                )
                files.append(relative)
                point_files.append(relative)
                print(
                    f"频率 {frequency_index + 1}/{len(frequencies)} "
                    f"({frequency:.6g} Hz)，采集 {repeat_index + 1}/{params.scope_repeats}，"
                    f"{len(frame['time_s'])} 点，{frame['actual_rate_sa_s']:.6g} Sa/s"
                )
            frequency_records.append(
                {
                    "frequency_index": frequency_index,
                    "frequency_hz": frequency,
                    "scope_configuration": scope_snapshot,
                    "files": point_files,
                }
            )

        (run_dir.raw / "frequency_index.json").write_text(
            json.dumps(frequency_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        np.savez(
            run_dir.raw / "frequency_index.npz",
            frequency_hz=np.asarray(frequencies, dtype=float),
            frequency_index=np.arange(len(frequencies), dtype=int),
            capture_files=np.asarray(
                [item for record in frequency_records for item in record["files"]],
                dtype="U",
            ),
        )
        files.extend(["raw/frequency_index.json", "raw/frequency_index.npz"])
        completion_status = "completed"
        print(f"{completion_label}采集完成: {run_dir.root}")
        return run_dir.root
    except WorkflowCancelled as exc:
        completion_status = "cancelled"
        failure_reason = str(exc)
        raise
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


def run(params: ZCoilInductanceFrequencyResponseParams) -> Path:
    """执行原有线圈端电压频率响应实验。"""
    return run_frequency_response(params)


def main() -> int:
    params = load_runtime_params(ZCoilInductanceFrequencyResponseParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
