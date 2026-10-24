"""无硬件预检、连接、初始条件、SDS 配置与显式安全恢复。"""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import time
from typing import Any
import numpy as np
from gs200 import GS200Instrument
from sds_acquisition import AcquisitionConfig, SDSAcquisition, SDSInstrument
from tec_controller import TECInstrument
from ...common import find_project_root, load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_signal_generator
from ...instrument_config import validate_mapping_usage
from ...experiment_runtime import check_cancelled
from ...steps import (
    DGChannelShutdown, DeviceSession, DisconnectTarget, STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport, ScopeCaptureSettings, ShutdownAction, TemperatureSwitchRestore,
    configure_fixed_dc_field, configure_fixed_rate_scope, configure_temperature_control,
    connect_signal_generator_routes, create_run_directory, next_auto_offset, next_auto_range_scale,
    run_safety_shutdown, set_temperature_switch, synchronize_connected_clocks,
)
from ...steps.state import StateGuard
from ..mx_z_field_calibration.preparation import _initial_state_snapshot
from ..mx_main_field_calibration.excitation import _set_pump_gate_on
from .models import MxMainFieldScopeNoiseSpectrumParams
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION, REQUIRED_MAPPING_KEYS
from .scan import build_scan_axes

EXECUTION_MODE = "typed_workflow"
SCOPE_MEMORY_MANAGEMENT = "FSRate"
SCOPE_RECORD_MAX_ATTEMPTS = 3


def preflight(params, root):
    root = Path(root)
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    return params, load_mapping(root)


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
    *, disconnect_tec: bool = True, configure_pump: bool = True, scope_channel: int | None = None,
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
                lambda: scope.set_channel_coupling(scope_channel if scope_channel is not None else params.scope_pd_channel, "DC"),
            )
        )

    pump_rf = devices.get("pump_rf")
    if configure_pump and pump_rf is not None and "pump_gate" in channels:
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
    if configure_pump and pump_rf is not None and "pump_carrier" in channels:

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
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )
    guard_errors = tuple(main_field_guard.restore()) if main_field_guard else ()
    return SafetyShutdownReport(
        action_errors=(*report.action_errors, *guard_errors),
        disconnect_errors=report.disconnect_errors,
        preserved_outputs=report.preserved_outputs,
    )


def configure_clocks(devices, mapping):
    return synchronize_connected_clocks(
        devices,
        mapping,
        {
            "z_field": "Z_magnetic_field",
            "xy_field": "Y_magnetic_field",
            "laser": "Pump_laser_power",
            "pump_rf": "Pump_modulation",
            "temp_switch": "Temp_Switch",
        },
        settle_wait=_sleep,
    )


def configure_initial_outputs(params, devices, channels, mapping):
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

    set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])


def configure_pump(params, devices, channels):
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



def _configure_scope(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    """以固定采样率模式配置 SDS CH1 AUTO 采集并验证真实 Nyquist。"""
    return configure_fixed_rate_scope(
        _scope_settings(params), devices, sleep=_sleep
    )


def initial_state(params):
    axes = build_scan_axes(params)
    state = dict(
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
            "off_settle_s": params.system_settle_s,
            "post_acquisition_wait_s": params.temp_switch_on_settle_s,
        },
        estimated_scan_duration_s=params.control_frequency_points
        * (
            params.system_settle_s
            + _scope_settings(params).capture_wait_s
            + params.temp_switch_on_settle_s
        ),
    )
    state.update(raw_format_version=RAW_FORMAT_VERSION, completion_status="running")
    return state
