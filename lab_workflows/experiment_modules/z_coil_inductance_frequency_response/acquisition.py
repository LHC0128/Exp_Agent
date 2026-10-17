"""SDS 配置、线性扫频、重复读帧与原始文件保存。"""
from __future__ import annotations
import json
import time
from typing import Any, Callable
import numpy as np
from sds_acquisition import AcquisitionConfig, ChannelConfig, SDSInstrument, TriggerConfig
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled
from ...steps import ScopeAutoRangeState, ScopeCaptureSettings, create_run_directory, next_auto_offset, next_auto_range_scale
from .models import ZCoilInductanceFrequencyResponseParams

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
    """构造频率轴，并在工作流外也提供可测试的确定性接口。

    声明 ``frequency_axis()`` 的实验自行决定刻度（例如电流频响实验的对数轴）；
    未声明的实验保持原有线性刻度，历史行为不变。
    """
    builder = getattr(params, "frequency_axis", None)
    if callable(builder):
        return np.asarray(builder(), dtype=float)
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


def _reference_channel(params: ZCoilInductanceFrequencyResponseParams) -> int | None:
    """返回可选的驱动参考通道；未声明的实验保持既有双通道采集。"""
    value = int(getattr(params, "scope_reference_channel", 0) or 0)
    return value if value > 0 else None


def _scope_config(
    params: ZCoilInductanceFrequencyResponseParams,
    frequency_hz: float,
    *,
    measured_scale_v_div: float,
    measured_offset_v: float,
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    """构造当前频率的 SDS 单次触发采集配置，按需启用驱动参考通道。"""
    period_s = 1.0 / float(frequency_hz)
    settle_cycles = int(getattr(params, "response_settle_cycles", 0))
    margin_cycles = int(getattr(params, "capture_margin_cycles", 0))
    # 声明丢弃周期的实验（相干启动后存在 L/R 瞬态）需要把丢弃窗口计入记录时长；
    # 未声明的历史实验保持原有时长。
    waveform_duration_s = (
        settle_cycles + int(params.scope_cycles) + margin_cycles
    ) * period_s
    trigger_duration_s = (
        SCOPE_TRIGGER_COVERAGE_CYCLES / float(params.trigger_frequency_hz)
    )
    duration_s = max(waveform_duration_s, trigger_duration_s)
    centered_capture = bool(getattr(params, "scope_centered_capture", False))
    if centered_capture:
        # SDS 的 timebase_delay=0 把触发点放在记录窗中央，触发后的可用窗口
        # 只有总时长的一半，因此必须先按所需触发后窗口翻倍。
        duration_s *= 2.0
    sample_rate_sa_s = _sample_rate_for_frequency(params, frequency_hz)
    reference_channel = _reference_channel(params)
    reference_scale_v_div = float(
        getattr(params, "scope_reference_scale_v_div", 1.0))
    channels = []
    for channel in range(1, 5):
        enabled = channel in {
            params.scope_measured_channel, params.scope_trigger_channel,
        } or channel == reference_channel
        if channel == params.scope_measured_channel:
            scale, offset = measured_scale_v_div, measured_offset_v
        elif channel == reference_channel:
            # 驱动参考通道的幅度由设定值决定，使用固定量程，不参与自动量程。
            scale, offset = reference_scale_v_div, -float(params.drive_offset_v)
        else:
            # SDS offset 与显示波形中心符号相反；触发方波显示为 0--5 V。
            scale, offset = 1.0, -2.5
        channels.append(ChannelConfig(
            number=channel,
            enabled=enabled,
            scale=scale,
            offset=offset,
            coupling=SCOPE_COUPLING,
            impedance=SCOPE_IMPEDANCE,
            probe=SCOPE_PROBE,
        ))
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
        "requested_after_trigger_s": max(waveform_duration_s, trigger_duration_s),
        "requested_waveform_duration_s": waveform_duration_s,
        "requested_trigger_coverage_s": trigger_duration_s,
        "trigger_coverage_cycles": SCOPE_TRIGGER_COVERAGE_CYCLES,
        "centered_capture": centered_capture,
        "response_settle_cycles": settle_cycles,
        "capture_margin_cycles": margin_cycles,
        "requested_points": config.total_points,
        "period_s": period_s,
        "cycles": int(params.scope_cycles),
        "reference_channel": reference_channel,
        "reference_scale_v_div": reference_scale_v_div,
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
    capture_channels = [
        params.scope_measured_channel,
        params.scope_trigger_channel,
    ]
    reference_channel = _reference_channel(params)
    if reference_channel is not None and reference_channel not in capture_channels:
        capture_channels.append(reference_channel)

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
                for channel in capture_channels
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
                frame = {
                    "time_s": measured_time,
                    "measured_voltage_v": measured_voltage,
                    "trigger_time_s": trigger_time,
                    "trigger_voltage_v": trigger_voltage,
                    "measured_preamble": measured.preamble_dict,
                    "trigger_preamble": trigger.preamble_dict,
                }
                if reference_channel is None:
                    return frame
                reference = results[reference_channel]
                reference_voltage = np.asarray(
                    reference.voltage, dtype=float).reshape(-1)
                if (
                    reference_voltage.size != measured_voltage.size
                    or not np.all(np.isfinite(reference_voltage))
                ):
                    last_problem = "SDS 驱动参考通道数据无效"
                else:
                    frame["reference_voltage_v"] = reference_voltage
                    frame["reference_preamble"] = reference.preamble_dict
                    return frame

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
    payload = {
        "time_s": np.asarray(frame["time_s"], dtype=float),
        "measured_voltage_v": np.asarray(frame["measured_voltage_v"], dtype=float),
        "trigger_time_s": np.asarray(frame["trigger_time_s"], dtype=float),
        "trigger_voltage_v": np.asarray(frame["trigger_voltage_v"], dtype=float),
        "theory_time_s": theory_time_s,
        "theory_voltage_v": theory_voltage_v,
        "trigger_edge_time_s": np.float64(edge_time),
        "frequency_hz": np.float64(frequency_hz),
        "drive_amplitude_vpp": np.float64(params.drive_amplitude_vpp),
        "drive_offset_v": np.float64(params.drive_offset_v),
        "phase_deg": np.float64(0.0),
        "actual_rate_sa_s": np.float64(frame["actual_rate_sa_s"]),
        "scale_used_v_div": np.float64(frame["scale_used_v_div"]),
        "offset_used_v": np.float64(frame["offset_used_v"]),
        "scope_config_json": np.array(
            json.dumps(scope_snapshot, ensure_ascii=False, sort_keys=True)
        ),
        "measured_preamble_json": np.array(
            json.dumps(frame["measured_preamble"], ensure_ascii=False)
        ),
        "trigger_preamble_json": np.array(
            json.dumps(frame["trigger_preamble"], ensure_ascii=False)
        ),
    }
    if "reference_voltage_v" in frame:
        payload["reference_voltage_v"] = np.asarray(
            frame["reference_voltage_v"], dtype=float)
        payload["reference_preamble_json"] = np.array(
            json.dumps(frame["reference_preamble"], ensure_ascii=False)
        )
    np.savez(run_dir.root / relative, **payload)
    return relative


def _sleep_cancellable(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def prepare_scope(params, devices, frequency, auto_range, *, operations=None):
    """应用当前频率窗口，回读并延续 SDS 实际量程和偏置。"""
    config_builder = operations._scope_config if operations else _scope_config
    config, snapshot = config_builder(params, float(frequency),
        measured_scale_v_div=auto_range.scale_v_div, measured_offset_v=auto_range.offset_v)
    devices["acquirer"].apply_config(config)
    scope = devices["scope"]
    snapshot.update(actual_sample_rate_sa_s=float(scope.get_sampling_rate()),
        actual_points=int(scope.get_actual_points()),
        actual_memory_management=str(scope.get_memory_management()), memory_depth=str(scope.get_memory_depth()),
        actual_initial_scale_v_div=float(scope.get_channel_scale(params.scope_measured_channel)),
        actual_initial_offset_v=float(scope.get_channel_offset(params.scope_measured_channel)),
        actual_trigger_scale_v_div=float(scope.get_channel_scale(params.scope_trigger_channel)),
        actual_trigger_offset_v=float(scope.get_channel_offset(params.scope_trigger_channel)))
    auto_range.scale_v_div = snapshot["actual_initial_scale_v_div"]
    auto_range.offset_v = snapshot["actual_initial_offset_v"]
    return config, snapshot


def acquire_run(ctx):
    """纯电学协议：逐频配置、等待、重复采集并逐帧落盘；不操作温控。"""
    from .run_format import RAW_FORMAT_VERSION
    params, ops = ctx.params, ctx.operations
    frequencies = ops.build_frequency_axis(params)
    directory = create_run_directory(ctx.data_type, params.run_tag, params.to_external(),
        schema_version=params.schema_version, project_root=ctx.root)
    ctx.run_directory, ctx.run_dir = directory, directory.root
    files, records = [], []
    directory.update_config(experiment_id=ctx.experiment_id, data_type=ctx.data_type,
        raw_format_version=RAW_FORMAT_VERSION, execution_mode="typed_workflow", completion_status="running",
        mapping_keys=["Z_magnetic_field", "Time_sequence_2", "scope_waveform"],
        acquisition_signals=ctx.acquisition_signals, scan_mode="point_by_point",
        frequency_axis_hz=frequencies.tolist(), session_id=ctx.session_id,
        drive=dict(waveform="SINusoid", amplitude_vpp=float(params.drive_amplitude_vpp),
            offset_v=float(params.drive_offset_v), phase_deg=0.0, burst_trigger="external falling edge",
            **ctx.drive_metadata),
        trigger=dict(source="Time_sequence_2", frequency_hz=float(params.trigger_frequency_hz),
            amplitude_vpp=float(params.trigger_amplitude_vpp), offset_v=float(params.trigger_offset_v),
            duty_percent=float(params.trigger_duty_percent), scope_source=SCOPE_TRIGGER_SOURCE,
            scope_slope=SCOPE_TRIGGER_SLOPE, scope_level_v=float(params.scope_trigger_level_v)),
        clock_sources=ctx.clock_sources, physical_limit=ctx.physical_limit)
    generator = ctx.devices["z_control"]
    z_channel, trigger_channel = ctx.channels["z_field"], ctx.channels["trigger"]
    # 每轮从 UI 初始值开始；同一轮的频点和重复之间才继承量程和偏置。
    auto_range = ScopeAutoRangeState(params.scope_initial_scale_v_div, 0.0)
    status, failure = "failed", None
    try:
        for frequency_index, frequency in enumerate(frequencies):
            check_cancelled()
            frequency = float(frequency)
            generator.set_output(False, channel=trigger_channel)
            ops._configure_sine(generator, z_channel, params, frequency, output=False, initialize=False)
            config, snapshot = prepare_scope(params, ctx.devices, frequency, auto_range, operations=ops)
            ctx.scope_config, ctx.scope_snapshot = config, snapshot
            directory.update_config(scope_configuration_last=snapshot,
                actual_rates={"scope_sa_s": snapshot["actual_sample_rate_sa_s"]})
            ops._sleep_cancellable(params.frequency_settle_s)
            if not bool(getattr(params, "coherent_rearm", False)):
                generator.set_output(True, channel=trigger_channel)
                generator.set_output(True, channel=z_channel)
            point_files = []
            record = dict(frequency_index=frequency_index, frequency_hz=frequency,
                          scope_configuration=snapshot, files=point_files)
            records.append(record)
            for repeat_index in range(params.scope_repeats):
                check_cancelled()
                before_scope_wait = None
                if bool(getattr(params, "coherent_rearm", False)):
                    generator.set_output(False, channel=trigger_channel)
                    generator.set_output(False, channel=z_channel)

                    def start_coherent_outputs():
                        generator.phase_init(channel=trigger_channel)
                        generator.phase_init(channel=z_channel)
                        generator.set_output(True, channel=z_channel)
                        generator.set_output(True, channel=trigger_channel)

                    before_scope_wait = start_coherent_outputs
                frame = ops._capture_with_auto_range(params, ctx.devices, config, auto_range,
                    capture_guard_s=ctx.capture_guard_s, before_scope_wait=before_scope_wait)
                if ctx.capture_validator is not None:
                    ctx.capture_validator(frame)
                relative = ops._save_capture(directory, frequency_index, repeat_index, frame, params, frequency, snapshot)
                files.append(relative)
                point_files.append(relative)
                ctx.outputs.update({"Z 正弦": f"{frequency:.6g} Hz / ON", "共同触发": "ON", "Scope": "STOP"})
                print(f"频率 {frequency_index + 1}/{len(frequencies)} ({frequency:.6g} Hz)，"
                    f"采集 {repeat_index + 1}/{params.scope_repeats}，{len(frame['time_s'])} 点，"
                    f"{frame['actual_rate_sa_s']:.6g} Sa/s")
        status = "completed"
        print(f"{ctx.completion_label}采集完成: {directory.root}")
        return files + ["raw/frequency_index.json", "raw/frequency_index.npz"]
    except BaseException as exc:
        failure = exc
        status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
        raise
    finally:
        # 即使后续帧失败，已保存帧仍有完整索引；写盘错误不覆盖采集原始异常。
        try:
            (directory.raw / "frequency_index.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            np.savez(directory.raw / "frequency_index.npz", frequency_hz=np.asarray(frequencies, dtype=float),
                frequency_index=np.arange(len(frequencies), dtype=int), capture_files=np.asarray(files, dtype="U"))
            directory.update_config(completion_status=status, failure_reason=str(failure) if failure else None,
                data_files=files + ["raw/frequency_index.json", "raw/frequency_index.npz"])
        except Exception as exc:
            ctx.persistence_errors.append(f"保存采集索引或状态失败：{exc}")
            if failure is None:
                raise
