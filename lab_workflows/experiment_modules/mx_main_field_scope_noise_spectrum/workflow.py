"""Mx 主磁场示波器噪声谱采集工作流。"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
from gs200 import GS200Instrument
from sds_acquisition import (
    AcquisitionConfig,
    SDSAcquisition,
    SDSInstrument,
)
from tec_controller import TECInstrument

from ...common import (
    find_project_root,
    load_mapping,
    load_safety_limits,
    validate_safety_limit,
)
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ScopeAutoRangeState,
    ScopeCaptureSettings,
    ShutdownAction,
    TemperatureSwitchRestore,
    create_run_directory,
    acquire_autoranged_waveform,
    configure_fixed_dc_field,
    configure_fixed_rate_scope,
    configure_temperature_control,
    connect_signal_generator_routes,
    next_auto_offset,
    next_auto_range_scale,
    read_complete_scope_record,
    restore_main_field_state,
    run_safety_shutdown,
    set_temperature_switch,
    snapshot_main_field_state,
    synchronize_connected_clocks,
    temperature_gated_acquire,
    wait_for_temperature_stable,
)
from ...steps.state import StateGuard
from ..mx_z_field_calibration.workflow import (
    _initial_state_snapshot,
    _set_pump_gate_on,
)
from .models import MxMainFieldScopeNoiseSpectrumParams
from .scan import build_scan_axes


EXPERIMENT_ID = "mx-main-field-scope-noise-spectrum"
DATA_TYPE = "Mx_Main_Field_Scope_Noise_Spectrum"
EXECUTION_MODE = "typed_workflow"
SCOPE_MEMORY_MANAGEMENT = "FSRate"
SCOPE_RECORD_MAX_ATTEMPTS = 3


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


_AutoRangeState = ScopeAutoRangeState


def _sleep(seconds: float) -> None:
    """支持 GUI 取消的短间隔等待。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _validate_main_field_current(current_ma: float) -> float:
    """只执行全局安全校验；主场标定允许按实验约定外推。"""
    return float(validate_safety_limit("main_magnetic_field", float(current_ma)))


def _scope_settings(
    params: MxMainFieldScopeNoiseSpectrumParams,
) -> ScopeCaptureSettings:
    """将实验参数转换为共享 SDS 采集设置。"""
    return ScopeCaptureSettings(
        sample_rate_sa_s=params.scope_sample_rate_sa_s,
        duration_s=params.scope_duration_s,
        pd_channel=params.scope_pd_channel,
        trigger_mode=params.scope_trigger_mode,
        initial_scale_v_div=params.scope_initial_scale_v_div,
        offset_v=params.scope_offset_v,
        vertical_divisions=params.scope_vertical_divisions,
        scale_min_v_div=params.scope_scale_min_v_div,
        scale_max_v_div=params.scope_scale_max_v_div,
        auto_range_low_fraction=params.scope_auto_range_low_fraction,
        auto_range_high_fraction=params.scope_auto_range_high_fraction,
        auto_offset_tolerance_fraction=(
            params.scope_auto_offset_tolerance_fraction
        ),
        auto_range_max_attempts=params.scope_auto_range_max_attempts,
        welch_nperseg=params.welch_nperseg,
        maximum_frequency_hz=params.control_frequency_stop_hz,
        coupling="AC" if params.scope_ac_coupling else "DC",
        record_max_attempts=SCOPE_RECORD_MAX_ATTEMPTS,
        memory_management=SCOPE_MEMORY_MANAGEMENT,
    )


def _next_auto_range_scale(
    params: MxMainFieldScopeNoiseSpectrumParams,
    current_scale_v_div: float,
    peak_deviation_v: float,
) -> float:
    """根据波形相对中心的峰值偏差返回下一次采集使用的量程。"""
    return next_auto_range_scale(
        _scope_settings(params), current_scale_v_div, peak_deviation_v
    )


def _next_auto_offset(
    params: MxMainFieldScopeNoiseSpectrumParams,
    current_scale_v_div: float,
    current_offset_v: float,
    waveform_min_v: float,
    waveform_max_v: float,
) -> float:
    """按 SDS 符号约定返回使波形上下边界居中的通道偏置。"""
    return next_auto_offset(
        _scope_settings(params),
        current_scale_v_div,
        current_offset_v,
        waveform_min_v,
        waveform_max_v,
    )


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接除 HF2 外的 Mx 连续 Pump 与 SDS 采集设备。"""
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}

    gs_cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )

    z_cfg = mapping["Z_magnetic_field"]
    devices["z_field"] = session.connect(
        "z_field", z_cfg["resource"], lambda: create_signal_generator(z_cfg)
    )
    channels["z_field"] = int(z_cfg["channel"])

    x_cfg = mapping["X_magnetic_field"]
    y_cfg = mapping["Y_magnetic_field"]
    devices["xy_field"], routed_channels = connect_signal_generator_routes(
        session,
        "xy_field",
        {
            "x_field": ("X_magnetic_field", x_cfg),
            "y_rf": ("Y_magnetic_field", y_cfg),
        },
    )
    channels.update(routed_channels)

    pump_cfg = mapping["Pump_laser_power"]
    probe_cfg = mapping["Probe_laser_power"]
    devices["laser"], routed_channels = connect_signal_generator_routes(
        session,
        "laser",
        {
            "pump_laser": ("Pump_laser_power", pump_cfg),
            "probe_laser": ("Probe_laser_power", probe_cfg),
        },
    )
    channels.update(routed_channels)

    carrier_cfg = mapping["Pump_modulation"]
    gate_cfg = mapping["Time_sequence"]
    devices["pump_rf"], routed_channels = connect_signal_generator_routes(
        session,
        "pump_rf",
        {
            "pump_carrier": ("Pump_modulation", carrier_cfg),
            "pump_gate": ("Time_sequence", gate_cfg),
        },
    )
    channels.update(routed_channels)

    temp_cfg = mapping["Temp_Switch"]
    devices["temp_switch"] = session.connect(
        "temp_switch",
        temp_cfg["resource"],
        lambda: create_signal_generator(temp_cfg),
    )
    channels["temp_switch"] = int(temp_cfg["channel"])

    scope_cfg = mapping["scope_waveform"]
    devices["scope"] = session.connect(
        "scope",
        scope_cfg["resource"],
        lambda: SDSInstrument(scope_cfg["resource"]),
    )
    devices["acquirer"] = session.bind(
        "acquirer", SDSAcquisition(devices["scope"])
    )

    tec_cfg = mapping["temperature"]
    devices["tec"] = session.connect_optional(
        "tec",
        tec_cfg["resource"],
        lambda: TECInstrument(port=tec_cfg["resource"]),
        device_label="TEC103",
    )
    return devices, channels


def _device_snapshot(
    mapping: dict[str, dict[str, Any]],
    devices: dict[str, Any],
    channels: dict[str, int],
) -> dict[str, Any]:
    snapshot = _initial_state_snapshot(devices, channels)
    keys = (
        "main_magnetic_field",
        "X_magnetic_field",
        "Y_magnetic_field",
        "Z_magnetic_field",
        "Pump_laser_power",
        "Probe_laser_power",
        "Pump_modulation",
        "Time_sequence",
        "Temp_Switch",
        "scope_waveform",
        "temperature",
    )
    snapshot["mapping"] = {key: mapping[key] for key in keys if key in mapping}
    return snapshot


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxMainFieldScopeNoiseSpectrumParams,
    main_field_guard: StateGuard | None = None,
) -> SafetyShutdownReport:
    """停止 SDS、恢复 DC 耦合、关闭辅助场并恢复温控与 GS200 状态。"""
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "z_field" in channels:
        dg_channels.append(
            DGChannelShutdown(
                devices["z_field"],
                channels["z_field"],
                "Z_magnetic_field",
                "Z 辅助场",
            )
        )
    if devices.get("xy_field") is not None:
        if "x_field" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"],
                    channels["x_field"],
                    "X_magnetic_field",
                    "X 磁场",
                )
            )
        if "y_rf" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"],
                    channels["y_rf"],
                    "Y_magnetic_field",
                    "Y 磁场",
                )
            )

    extra_actions: list[ShutdownAction] = []
    scope = devices.get("scope")
    if scope is not None:
        extra_actions.append(ShutdownAction("停止示波器触发失败", scope.trigger_stop))
        extra_actions.append(
            ShutdownAction(
                "恢复示波器直流耦合失败",
                lambda: scope.set_channel_coupling(params.scope_pd_channel, "DC"),
            )
        )

    pump_rf = devices.get("pump_rf")
    if pump_rf is not None and "pump_gate" in channels:
        extra_actions.append(
            ShutdownAction(
                "保持 Pump RF 开关 5V 常开失败",
                lambda: _set_pump_gate_on(
                    pump_rf,
                    channels["pump_gate"],
                    params.pump_gate_voltage_v,
                ),
            )
        )
    if pump_rf is not None and "pump_carrier" in channels:

        def keep_pump_carrier_on() -> None:
            validate_safety_limit(
                "Pump_modulation", params.pump_carrier_amplitude_vpp
            )
            pump_rf.setup_sine(
                params.pump_carrier_frequency_hz,
                params.pump_carrier_amplitude_vpp,
                offset=0.0,
                phase=0.0,
                channel=channels["pump_carrier"],
            )
            pump_rf.set_output(True, channel=channels["pump_carrier"])

        extra_actions.append(
            ShutdownAction("保持 Pump 100MHz 载波开启失败", keep_pump_carrier_on)
        )

    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(
            devices["temp_switch"], channels["temp_switch"]
        )
    report = run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )
    guard_errors = tuple(main_field_guard.restore()) if main_field_guard else ()
    return SafetyShutdownReport(
        action_errors=(*report.action_errors, *guard_errors),
        disconnect_errors=report.disconnect_errors,
        preserved_outputs=report.preserved_outputs,
    )


def _configure_outputs(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
) -> tuple[float | None, dict[str, dict[str, Any]]]:
    """配置连续 Pump/Probe、固定 XY 补偿、初始主场和温控。"""
    clock_sources = synchronize_connected_clocks(
        devices,
        mapping,
        {
            "z_field": "Z_magnetic_field",
            "xy_field": "Y_magnetic_field",
            "laser": "Pump_laser_power",
            "pump_rf": "Pump_modulation",
            "temp_switch": "Temp_Switch",
        },
    )

    z_field = devices["z_field"]
    validate_safety_limit("Z_magnetic_field", 0.0)
    z_field.set_burst_state(False, channel=channels["z_field"])
    z_field.set_mod_state(False, channel=channels["z_field"])
    z_field.setup_dc(0.0, channel=channels["z_field"])
    z_field.set_output(False, channel=channels["z_field"])

    xy_field = devices["xy_field"]
    for channel_name, safety_key, voltage_v in (
        ("x_field", "X_magnetic_field", params.x_dc_field_v),
        ("y_rf", "Y_magnetic_field", params.y_dc_field_v),
    ):
        configure_fixed_dc_field(
            xy_field,
            channels[channel_name],
            safety_key,
            voltage_v,
        )

    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_laser_power_v)
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    validate_safety_limit("Probe_laser_power", params.probe_laser_power_v)
    laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])

    axes = build_scan_axes(params)
    initial_current_ma = _validate_main_field_current(
        float(axes["main_field_current_ma"][0])
    )
    gs200 = devices["gs200"]
    gs_cfg = mapping["main_magnetic_field"]
    if gs_cfg.get("source_function"):
        gs200.set_source_function(gs_cfg["source_function"])
    limits = load_safety_limits()
    gs200.set_current_limit(float(limits["main_magnetic_field"]["max"]) / 1000.0)
    gs200.set_current(initial_current_ma / 1000.0)
    gs200.set_output(True)

    pump_rf = devices["pump_rf"]
    validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
    pump_rf.setup_sine(
        params.pump_carrier_frequency_hz,
        params.pump_carrier_amplitude_vpp,
        offset=0.0,
        phase=0.0,
        channel=channels["pump_carrier"],
    )
    pump_rf.set_output(True, channel=channels["pump_carrier"])
    _set_pump_gate_on(
        pump_rf,
        channels["pump_gate"],
        params.pump_gate_voltage_v,
    )

    set_temperature_switch(
        devices["temp_switch"], True, channel=channels["temp_switch"]
    )
    temperature_status = configure_temperature_control(
        devices.get("tec"),
        params.temperature_c,
        channel=1,
        tolerance_c=params.temperature_tolerance_c,
        stable_reads=params.temperature_stable_reads,
        poll_interval_s=params.temperature_poll_interval_s,
        timeout_s=params.temperature_timeout_s,
        cancellation=_RuntimeCancellation(),
        stability_waiter=wait_for_temperature_stable,
    )
    return temperature_status.actual_temperature_c, clock_sources


def _configure_scope(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    """以固定采样率模式配置 SDS CH1 AUTO 采集并验证真实 Nyquist。"""
    return configure_fixed_rate_scope(
        _scope_settings(params), devices, sleep=_sleep
    )


def _temperature_gated_acquire(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    acquire: Callable[[], Any],
) -> Any:
    """关闭温控后采集，并保证温控恢复和规定等待。"""
    return temperature_gated_acquire(
        temp_switch=devices["temp_switch"],
        temp_channel=channels["temp_switch"],
        off_settle_s=params.temp_switch_off_settle_s,
        on_settle_s=params.temp_switch_on_settle_s,
        acquire=acquire,
        check_cancelled=check_cancelled,
        sleep=_sleep,
        set_temperature_switch=set_temperature_switch,
        cancellation=_RuntimeCancellation(),
    )


def _read_scope_record(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
    scope_config: AcquisitionConfig,
) -> Any:
    """等待示波器提交完整帧后读取，必要时自动恢复波形导出状态。"""
    return read_complete_scope_record(
        _scope_settings(params),
        devices,
        scope_config,
        check_cancelled=check_cancelled,
        sleep=_sleep,
    )


def _acquire_scope_point(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: AcquisitionConfig,
    auto_range: _AutoRangeState,
) -> dict[str, Any]:
    """联合调整量程和偏置，返回最终接受的 PD 波形。"""
    scope: SDSInstrument = devices["scope"]
    attempt_scales: list[float] = []
    attempt_offsets: list[float] = []
    attempt_abs_max: list[float] = []
    attempt_min: list[float] = []
    attempt_max: list[float] = []
    attempt_center: list[float] = []
    attempt_peak_deviation: list[float] = []
    attempt_display_edge_fraction: list[float] = []
    result: Any | None = None
    accepted_scale = float(auto_range.scale_v_div)
    accepted_offset = float(auto_range.offset_v)

    for attempt in range(params.scope_auto_range_max_attempts):
        check_cancelled()
        scale_used = float(auto_range.scale_v_div)
        offset_used = float(auto_range.offset_v)
        attempt_scales.append(scale_used)
        attempt_offsets.append(offset_used)
        result = _temperature_gated_acquire(
            params,
            devices,
            channels,
            acquire=lambda: _read_scope_record(params, devices, scope_config),
        )
        voltage = np.asarray(result.voltage, dtype=float).reshape(-1)
        if voltage.size < params.welch_nperseg:
            raise RuntimeError(
                f"示波器有效样本数 {voltage.size} 少于 WELCH_NPERSEG={params.welch_nperseg}"
            )
        if not np.all(np.isfinite(voltage)):
            raise RuntimeError("示波器 PD 波形包含非有限值")
        waveform_min_v = float(np.min(voltage))
        waveform_max_v = float(np.max(voltage))
        waveform_center_v = 0.5 * (waveform_min_v + waveform_max_v)
        peak_deviation_v = 0.5 * (waveform_max_v - waveform_min_v)
        display_center_v = -offset_used
        peak_from_display_center_v = max(
            abs(waveform_min_v - display_center_v),
            abs(waveform_max_v - display_center_v),
        )
        half_span_v = (
            scale_used * params.scope_vertical_divisions / 2.0
        )
        abs_max_v = float(np.max(np.abs(voltage)))
        attempt_abs_max.append(abs_max_v)
        attempt_min.append(waveform_min_v)
        attempt_max.append(waveform_max_v)
        attempt_center.append(waveform_center_v)
        attempt_peak_deviation.append(peak_deviation_v)
        attempt_display_edge_fraction.append(
            peak_from_display_center_v / half_span_v
        )
        next_scale = _next_auto_range_scale(
            params, scale_used, peak_deviation_v
        )
        next_offset = _next_auto_offset(
            params,
            scale_used,
            offset_used,
            waveform_min_v,
            waveform_max_v,
        )
        # 波形靠近当前显示边界时可能已经截顶；先平移到中心，不能依据截顶后
        # 偏小的峰峰值继续缩小量程。
        if (
            next_offset != offset_used
            and next_scale < scale_used
            and peak_from_display_center_v
            >= params.scope_auto_range_high_fraction * half_span_v
        ):
            next_scale = scale_used
        accepted_scale = scale_used
        accepted_offset = offset_used
        scale_changed = next_scale != scale_used
        offset_changed = next_offset != offset_used
        if not scale_changed and not offset_changed:
            break

        if scale_changed:
            scope.set_channel_scale(params.scope_pd_channel, next_scale)
            actual_scale_v_div = float(
                scope.get_channel_scale(params.scope_pd_channel)
            )
            if not np.isfinite(actual_scale_v_div) or actual_scale_v_div <= 0.0:
                raise RuntimeError("示波器回读的通道量程无效")
        else:
            actual_scale_v_div = scale_used
        auto_range.scale_v_div = actual_scale_v_div
        # 改变量程后再次显式写入 offset，避免示波器垂直参考策略改变其数值。
        scope.set_channel_offset(params.scope_pd_channel, next_offset)
        actual_offset_v = float(scope.get_channel_offset(params.scope_pd_channel))
        if not np.isfinite(actual_offset_v):
            raise RuntimeError("示波器回读的通道偏置不是有限值")
        auto_range.offset_v = actual_offset_v
        for channel_config in scope_config.channels:
            if channel_config.number == params.scope_pd_channel:
                channel_config.scale = actual_scale_v_div
                channel_config.offset = actual_offset_v
                break
        if attempt + 1 < params.scope_auto_range_max_attempts:
            result = None
            continue
        break

    if result is None:
        raise RuntimeError("示波器自动量程未获得有效波形")
    time_s = np.asarray(result.time, dtype=float).reshape(-1)
    voltage_v = np.asarray(result.voltage, dtype=float).reshape(-1)
    if time_s.size != voltage_v.size or time_s.size < 2:
        raise RuntimeError("示波器时间轴与 PD 波形长度不一致")
    sample_interval_s = float(np.median(np.diff(time_s)))
    if not np.isfinite(sample_interval_s) or sample_interval_s <= 0.0:
        raise RuntimeError("示波器真实采样间隔无效")
    actual_rate_sa_s = 1.0 / sample_interval_s
    if actual_rate_sa_s <= 2.0 * params.control_frequency_stop_hz:
        raise RuntimeError(
            "示波器波形真实采样率不足："
            f"{actual_rate_sa_s:.9g} Sa/s 的 Nyquist 不能覆盖 "
            f"{params.control_frequency_stop_hz:.9g} Hz"
        )
    return {
        "time_s": time_s,
        "voltage_v": voltage_v,
        "actual_rate_sa_s": actual_rate_sa_s,
        "actual_duration_s": sample_interval_s * max(0, time_s.size - 1),
        "scale_used_v_div": accepted_scale,
        "offset_used_v": accepted_offset,
        "next_scale_v_div": float(auto_range.scale_v_div),
        "next_offset_v": float(auto_range.offset_v),
        "attempt_count": len(attempt_scales),
        "attempt_scales_v_div": np.asarray(attempt_scales, dtype=float),
        "attempt_offsets_v": np.asarray(attempt_offsets, dtype=float),
        "attempt_abs_max_v": np.asarray(attempt_abs_max, dtype=float),
        "attempt_min_v": np.asarray(attempt_min, dtype=float),
        "attempt_max_v": np.asarray(attempt_max, dtype=float),
        "attempt_center_v": np.asarray(attempt_center, dtype=float),
        "attempt_peak_deviation_v": np.asarray(
            attempt_peak_deviation, dtype=float
        ),
        "attempt_display_edge_fraction": np.asarray(
            attempt_display_edge_fraction, dtype=float
        ),
    }


def _save_point(
    path: Path,
    payload: dict[str, Any],
    *,
    point_index: int,
    control_frequency_hz: float,
    main_field_current_ma: float,
    params: MxMainFieldScopeNoiseSpectrumParams,
) -> None:
    np.savez(
        path,
        time_s=np.asarray(payload["time_s"], dtype=float),
        voltage_v=np.asarray(payload["voltage_v"], dtype=float),
        point_index=np.int64(point_index),
        control_frequency_hz=np.float64(control_frequency_hz),
        larmor_frequency_hz=np.float64(control_frequency_hz),
        main_field_current_ma=np.float64(main_field_current_ma),
        requested_rate_sa_s=np.float64(params.scope_sample_rate_sa_s),
        actual_rate_sa_s=np.float64(payload["actual_rate_sa_s"]),
        requested_duration_s=np.float64(params.scope_duration_s),
        actual_duration_s=np.float64(payload["actual_duration_s"]),
        scale_used_v_div=np.float64(payload["scale_used_v_div"]),
        offset_used_v=np.float64(payload["offset_used_v"]),
        next_scale_v_div=np.float64(payload["next_scale_v_div"]),
        next_offset_v=np.float64(payload["next_offset_v"]),
        attempt_count=np.int64(payload["attempt_count"]),
        attempt_scales_v_div=np.asarray(
            payload["attempt_scales_v_div"], dtype=float
        ),
        attempt_offsets_v=np.asarray(payload["attempt_offsets_v"], dtype=float),
        attempt_abs_max_v=np.asarray(payload["attempt_abs_max_v"], dtype=float),
        attempt_min_v=np.asarray(payload["attempt_min_v"], dtype=float),
        attempt_max_v=np.asarray(payload["attempt_max_v"], dtype=float),
        attempt_center_v=np.asarray(payload["attempt_center_v"], dtype=float),
        attempt_peak_deviation_v=np.asarray(
            payload["attempt_peak_deviation_v"], dtype=float
        ),
        attempt_display_edge_fraction=np.asarray(
            payload["attempt_display_edge_fraction"], dtype=float
        ),
        temp_switch_off_settle_s=np.float64(params.temp_switch_off_settle_s),
        temp_switch_on_settle_s=np.float64(params.temp_switch_on_settle_s),
    )


def _acquire_scan(
    params: MxMainFieldScopeNoiseSpectrumParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: AcquisitionConfig,
    auto_range: _AutoRangeState,
) -> list[str]:
    axes = build_scan_axes(params)
    control_axis = axes["control_frequency_hz"]
    current_axis = axes["main_field_current_ma"]
    axes_path = run_dir.raw / "main_field_scope_noise_scan_axes.npz"
    np.savez(
        axes_path,
        control_frequency_hz=control_axis,
        larmor_frequency_hz=axes["larmor_frequency_hz"],
        main_field_current_ma=current_axis,
        main_field_calibration_hz_per_ma=np.float64(
            params.main_field_calibration_hz_per_ma
        ),
        main_field_calibration_intercept_hz=np.float64(
            params.main_field_calibration_intercept_hz
        ),
        calibration_extrapolation_enabled=np.bool_(True),
    )

    files = ["raw/main_field_scope_noise_scan_axes.npz"]
    gs200 = devices["gs200"]
    for index, (control_hz, current_ma) in enumerate(
        zip(control_axis, current_axis, strict=True)
    ):
        check_cancelled()
        print(
            f"主场示波器噪声点 {index + 1}/{control_axis.size}: "
            f"f_L={control_hz:.3f} Hz，I={current_ma:.9f} mA"
        )
        _validate_main_field_current(float(current_ma))
        gs200.set_current(float(current_ma) / 1000.0)
        gs200.set_output(True)
        payload = _acquire_scope_point(
            params,
            devices,
            channels,
            scope_config,
            auto_range,
        )
        relative = f"raw/waveform_I{index:04d}.npz"
        _save_point(
            run_dir.root / relative,
            payload,
            point_index=index,
            control_frequency_hz=float(control_hz),
            main_field_current_ma=float(current_ma),
            params=params,
        )
        files.append(relative)
    return files


def run(params: MxMainFieldScopeNoiseSpectrumParams) -> Path:
    """执行 500 点 GS200 主场示波器噪声谱采集。"""
    root = find_project_root()
    mapping = load_mapping(root)
    axes = build_scan_axes(params)
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        geometry={
            "main_field": "Z scanned by GS200",
            "pump": "Z",
            "probe": "X",
            "pd_detection": "SDS CH1 raw waveform",
            "z_auxiliary_field": "0 V output OFF",
            "x_dc_field": {
                "voltage_v": params.x_dc_field_v,
                "output_on": params.x_dc_field_v != 0.0,
            },
            "y_dc_field": {
                "voltage_v": params.y_dc_field_v,
                "output_on": params.y_dc_field_v != 0.0,
            },
        },
        analysis_during_acquisition=False,
        acquisition_signals=["SDS CH1 PD voltage"],
        control_axis={
            "definition": "control_Hz = Larmor_Hz = K_f_Hz_per_mA * current_mA + f_0mA_Hz",
            "start_hz": params.control_frequency_start_hz,
            "stop_hz": params.control_frequency_stop_hz,
            "points": params.control_frequency_points,
            "main_field_current_start_ma": float(axes["main_field_current_ma"][0]),
            "main_field_current_stop_ma": float(axes["main_field_current_ma"][-1]),
        },
        main_field_calibration={
            "source_run": params.main_field_calibration_source_run,
            "slope_hz_per_ma": params.main_field_calibration_hz_per_ma,
            "intercept_hz": params.main_field_calibration_intercept_hz,
            "extrapolation_enabled": True,
        },
        scope_request={
            "pd_channel": params.scope_pd_channel,
            "trigger_mode": params.scope_trigger_mode,
            "frame_trigger_action": "FTRIG",
            "requires_input_trigger_edge": False,
            "coupling": "AC" if params.scope_ac_coupling else "DC",
            "memory_management": SCOPE_MEMORY_MANAGEMENT,
            "sample_rate_sa_s": params.scope_sample_rate_sa_s,
            "duration_s": params.scope_duration_s,
            "requested_points": params.scope_requested_points,
        },
        auto_range={
            "initial_scale_v_div": params.scope_initial_scale_v_div,
            "scale_min_v_div": params.scope_scale_min_v_div,
            "scale_max_v_div": params.scope_scale_max_v_div,
            "vertical_divisions": params.scope_vertical_divisions,
            "low_fraction": params.scope_auto_range_low_fraction,
            "high_fraction": params.scope_auto_range_high_fraction,
            "offset_tolerance_fraction": (
                params.scope_auto_offset_tolerance_fraction
            ),
            "max_attempts": params.scope_auto_range_max_attempts,
            "persists_across_points": True,
        },
        temperature_gating={
            "scope": "every_scope_attempt",
            "off_voltage_v": 0.0,
            "on_voltage_v": 5.0,
            "off_settle_s": params.temp_switch_off_settle_s,
            "post_acquisition_wait_s": params.temp_switch_on_settle_s,
        },
        estimated_scan_duration_s=params.control_frequency_points
        * (
            params.temp_switch_off_settle_s
            + _scope_settings(params).capture_wait_s
            + params.temp_switch_on_settle_s
        ),
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    main_field_guard = StateGuard()
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = _connect_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        main_field_state = snapshot_main_field_state(devices["gs200"])
        main_field_guard.add(
            "恢复 GS200 运行前状态",
            lambda: restore_main_field_state(devices["gs200"], main_field_state),
        )
        device_snapshot = _device_snapshot(mapping, devices, channels)
        device_snapshot["main_field_before_configuration"] = asdict(main_field_state)
        run_dir.update_config(device_snapshot=device_snapshot)

        actual_temperature, clock_sources = _configure_outputs(
            params, devices, channels, mapping
        )
        scope_config, scope_snapshot = _configure_scope(params, devices)
        run_dir.update_config(
            initial_temperature_c=actual_temperature,
            temperature_control={
                "target_temperature_c": params.temperature_c,
                "actual_temperature_c": actual_temperature,
                "controlled_by_experiment": actual_temperature is not None,
                "control_source": (
                    "tec103" if actual_temperature is not None else "external_software"
                ),
            },
            clock_sources=clock_sources,
            scope_configuration=scope_snapshot,
            actual_rates={"scope_sa_s": scope_snapshot["actual_sample_rate_sa_s"]},
        )
        auto_range = _AutoRangeState(
            scale_v_div=float(scope_snapshot["actual_initial_scale_v_div"]),
            offset_v=float(scope_snapshot["actual_initial_offset_v"]),
        )
        data_files = _acquire_scan(
            params,
            run_dir,
            devices,
            channels,
            scope_config,
            auto_range,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            data_files=data_files,
            final_scope_scale_v_div=auto_range.scale_v_div,
            final_scope_offset_v=auto_range.offset_v,
        )
        print(f"Mx 主磁场示波器噪声谱采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed", failure_reason=failure_reason
            )
        raise
    finally:
        shutdown_report = safe_shutdown(
            devices, channels, params, main_field_guard
        )
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                main_field_restored=not main_field_guard.restore_errors,
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxMainFieldScopeNoiseSpectrumParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
