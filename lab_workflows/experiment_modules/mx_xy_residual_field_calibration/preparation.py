"""无硬件预检、配置与既有连接/安全收尾边界。"""
from __future__ import annotations
from pathlib import Path
import time
from typing import Any
from sds_acquisition import AcquisitionConfig
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...instrument_config import validate_mapping_usage
from ...experiment_runtime import check_cancelled
from ...steps import ScopeCaptureSettings, configure_fixed_rate_scope, configure_temperature_control, create_run_directory, set_temperature_switch
# 沿用原实验已使用的连接/收尾实现，不继承其它实验编排及私有状态。
from ..mx_main_field_scope_noise_spectrum.preparation import (
    _connect_devices, _device_snapshot, _set_pump_gate_on, safe_shutdown, configure_clocks,
)
from .models import MxXYResidualFieldCalibrationParams
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION, REQUIRED_MAPPING_KEYS
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
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _scope_settings(params: MxXYResidualFieldCalibrationParams) -> ScopeCaptureSettings:
    return ScopeCaptureSettings(
        sample_rate_sa_s=params.scope_sample_rate_sa_s,
        duration_s=params.scope_duration_s,
        pd_channel=params.scope_pd_channel,
        trigger_mode=params.scope_trigger_mode,
        initial_scale_v_div=params.scope_initial_scale_v_div,
        offset_v=0.0,
        vertical_divisions=params.scope_vertical_divisions,
        scale_min_v_div=params.scope_scale_min_v_div,
        scale_max_v_div=params.scope_scale_max_v_div,
        auto_range_low_fraction=params.scope_auto_range_low_fraction,
        auto_range_high_fraction=params.scope_auto_range_high_fraction,
        auto_offset_tolerance_fraction=params.scope_auto_offset_tolerance_fraction,
        auto_range_max_attempts=params.scope_auto_range_max_attempts,
        welch_nperseg=params.scope_minimum_points,
        maximum_frequency_hz=0.0,
        coupling="DC",
        record_max_attempts=SCOPE_RECORD_MAX_ATTEMPTS,
        memory_management=SCOPE_MEMORY_MANAGEMENT,
        auto_offset_enabled=False,
    )


def _set_xy_off_state(devices: dict[str, Any], channels: dict[str, int]) -> None:
    xy_field = devices["xy_field"]
    for channel_name, safety_key in (
        ("x_field", "X_magnetic_field"),
        ("y_rf", "Y_magnetic_field"),
    ):
        validate_safety_limit(safety_key, 0.0)
        channel = channels[channel_name]
        xy_field.set_burst_state(False, channel=channel)
        xy_field.set_mod_state(False, channel=channel)
        xy_field.setup_dc(0.0, channel=channel)
        xy_field.set_output(False, channel=channel)


def _set_xy_scan_state(
    devices: dict[str, Any], channels: dict[str, int], x_v: float, y_v: float
) -> None:
    xy_field = devices["xy_field"]
    for channel_name, safety_key, value in (
        ("x_field", "X_magnetic_field", x_v),
        ("y_rf", "Y_magnetic_field", y_v),
    ):
        value = float(validate_safety_limit(safety_key, float(value)))
        channel = channels[channel_name]
        xy_field.set_burst_state(False, channel=channel)
        xy_field.set_mod_state(False, channel=channel)
        xy_field.setup_dc(value, channel=channel)
        xy_field.set_output(True, channel=channel)


def _set_main_field(device: Any, current_ma: float) -> None:
    current_ma = float(validate_safety_limit("main_magnetic_field", current_ma))
    device.set_current(current_ma / 1000.0)
    device.set_output(True)


def _configure_scope(
    params: MxXYResidualFieldCalibrationParams, devices: dict[str, Any]
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    return configure_fixed_rate_scope(_scope_settings(params), devices, sleep=_sleep)


def configure_initial_outputs(params, devices, channels, mapping):
    z_field = devices["z_field"]
    z_channel = channels["z_field"]
    validate_safety_limit("Z_magnetic_field", 0.0)
    z_field.set_burst_state(False, channel=z_channel)
    z_field.set_mod_state(False, channel=z_channel)
    z_field.setup_dc(0.0, channel=z_channel)
    z_field.set_output(False, channel=z_channel)
    _set_xy_off_state(devices, channels)

    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_laser_power_v)
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    validate_safety_limit("Probe_laser_power", params.probe_laser_power_v)
    laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])

    gs200 = devices["gs200"]
    gs_cfg = mapping["main_magnetic_field"]
    if gs_cfg.get("source_function"):
        gs200.set_source_function(gs_cfg["source_function"])
    limits = load_safety_limits()
    gs200.set_current_limit(float(limits["main_magnetic_field"]["max"]) / 1000.0)
    _set_main_field(gs200, params.scan_main_field_ma)

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
    _set_pump_gate_on(pump_rf, channels["pump_gate"], params.pump_gate_voltage_v)



def initial_state(params):
    estimated_captures = params.grid_points * params.point_repeats
    state = dict(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        geometry={
            "main_field": "Z by GS200",
            "pump": "Z",
            "probe": "X",
            "pd_detection": "SDS CH1 DC raw waveform mean",
            "z_auxiliary_field": "0 V output OFF",
        },
        scan_order="x_outer_y_forward",
        reference_strategy="none",
        optimum_metric="global_2d_bloch_fit_with_linear_time_drift",
        offset_strategy="fixed_zero",
        scale_strategy="adaptive_expand_and_shrink_at_every_grid_capture",
        analysis_during_acquisition=False,
        temperature_gating={
            "scope": "every_scope_attempt",
            "off_settle_s": params.system_settle_s,
            "on_settle_s": params.temp_switch_on_settle_s,
        },
        estimated_capture_count=estimated_captures,
        estimated_minimum_duration_s=estimated_captures
        * (
            params.system_settle_s
            + _scope_settings(params).capture_wait_s
            + params.temp_switch_on_settle_s
        ),
    )

    state.update(raw_format_version=RAW_FORMAT_VERSION, completion_status="running")
    return state
