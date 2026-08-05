"""Mx XY 剩磁二维校准采集工作流。"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from sds_acquisition import AcquisitionConfig

from ...common import find_project_root, load_mapping, load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    ScopeAutoRangeState,
    ScopeCaptureSettings,
    acquire_autoranged_waveform,
    configure_fixed_rate_scope,
    configure_temperature_control,
    create_run_directory,
    read_complete_scope_record,
    restore_main_field_state,
    set_temperature_switch,
    snapshot_main_field_state,
    synchronize_connected_clocks,
    temperature_gated_acquire,
    wait_for_temperature_stable,
)
from ...steps.state import StateGuard
from ..mx_main_field_scope_noise_spectrum.workflow import (
    _connect_devices,
    _device_snapshot,
    _set_pump_gate_on,
    safe_shutdown,
)
from .models import MxXYResidualFieldCalibrationParams
from .scan import build_xy_axes, iter_grid


EXPERIMENT_ID = "mx-xy-residual-field-calibration"
DATA_TYPE = "Mx_XY_Residual_Field_Calibration"
EXECUTION_MODE = "typed_workflow"
SCOPE_MEMORY_MANAGEMENT = "FSRate"
SCOPE_RECORD_MAX_ATTEMPTS = 3


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


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


def _configure_outputs(
    params: MxXYResidualFieldCalibrationParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
) -> tuple[float | None, dict[str, dict[str, Any]]]:
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

    set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
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
    params: MxXYResidualFieldCalibrationParams, devices: dict[str, Any]
) -> tuple[AcquisitionConfig, dict[str, Any]]:
    return configure_fixed_rate_scope(_scope_settings(params), devices, sleep=_sleep)


def _capture_waveform(
    params: MxXYResidualFieldCalibrationParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: AcquisitionConfig,
    auto_range: ScopeAutoRangeState,
) -> dict[str, Any]:
    settings = _scope_settings(params)

    def capture() -> Any:
        return temperature_gated_acquire(
            temp_switch=devices["temp_switch"],
            temp_channel=channels["temp_switch"],
            off_settle_s=params.temp_switch_off_settle_s,
            on_settle_s=params.temp_switch_on_settle_s,
            acquire=lambda: read_complete_scope_record(
                settings,
                devices,
                scope_config,
                check_cancelled=check_cancelled,
                sleep=_sleep,
            ),
            check_cancelled=check_cancelled,
            sleep=_sleep,
            set_temperature_switch=set_temperature_switch,
            cancellation=_RuntimeCancellation(),
        )

    started_unix_s = time.time()
    payload = acquire_autoranged_waveform(
        settings,
        scope_config,
        devices["scope"],
        auto_range,
        capture=capture,
        check_cancelled=check_cancelled,
    )
    ended_unix_s = time.time()
    voltage = np.asarray(payload["voltage_v"], dtype=float)
    payload.update(
        capture_started_unix_s=started_unix_s,
        capture_ended_unix_s=ended_unix_s,
        capture_midpoint_unix_s=0.5 * (started_unix_s + ended_unix_s),
        pd_mean_v=float(np.mean(voltage)),
        pd_std_v=float(np.std(voltage)),
    )
    return payload


def _save_capture(
    path: Path,
    payload: dict[str, Any],
    *,
    phase: str,
    sequence_index: int,
    repeat_index: int,
    main_field_ma: float,
    x_v: float,
    y_v: float,
    x_index: int = -1,
    y_index: int = -1,
) -> None:
    diagnostics = {
        name: value
        for name, value in payload.items()
        if name.startswith("attempt_")
    }
    np.savez_compressed(
        path,
        time_s=np.asarray(payload["time_s"], dtype=float),
        voltage_v=np.asarray(payload["voltage_v"], dtype=float),
        phase=np.array(phase),
        sequence_index=np.int64(sequence_index),
        repeat_index=np.int64(repeat_index),
        x_index=np.int64(x_index),
        y_index=np.int64(y_index),
        main_field_ma=np.float64(main_field_ma),
        x_field_v=np.float64(x_v),
        y_field_v=np.float64(y_v),
        x_output_on=np.bool_(phase == "grid"),
        y_output_on=np.bool_(phase == "grid"),
        capture_started_unix_s=np.float64(payload["capture_started_unix_s"]),
        capture_ended_unix_s=np.float64(payload["capture_ended_unix_s"]),
        capture_midpoint_unix_s=np.float64(payload["capture_midpoint_unix_s"]),
        pd_mean_v=np.float64(payload["pd_mean_v"]),
        pd_std_v=np.float64(payload["pd_std_v"]),
        actual_rate_sa_s=np.float64(payload["actual_rate_sa_s"]),
        actual_duration_s=np.float64(payload["actual_duration_s"]),
        scale_used_v_div=np.float64(payload["scale_used_v_div"]),
        offset_used_v=np.float64(payload["offset_used_v"]),
        next_scale_v_div=np.float64(payload["next_scale_v_div"]),
        next_offset_v=np.float64(payload["next_offset_v"]),
        **diagnostics,
    )


def _acquire_all(
    params: MxXYResidualFieldCalibrationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: AcquisitionConfig,
    auto_range: ScopeAutoRangeState,
) -> list[str]:
    x_axis, y_axis = build_xy_axes(params)
    axes_relative = "raw/xy_residual_scan_axes.npz"
    np.savez(
        run_dir.root / axes_relative,
        x_field_v=x_axis,
        y_field_v=y_axis,
        order=np.array("x_outer_y_forward"),
        scan_main_field_ma=np.float64(params.scan_main_field_ma),
    )
    files = [axes_relative]
    sequence_index = 0

    _set_main_field(devices["gs200"], params.scan_main_field_ma)
    total = params.grid_points
    for point_index, (x_index, y_index, x_v, y_v) in enumerate(iter_grid(params)):
        for repeat in range(params.point_repeats):
            check_cancelled()
            _set_xy_scan_state(devices, channels, x_v, y_v)
            print(
                f"XY 网格 {point_index + 1}/{total}，重复 {repeat + 1}/{params.point_repeats}: "
                f"X={x_v:.6g} V，Y={y_v:.6g} V"
            )
            payload = _capture_waveform(params, devices, channels, scope_config, auto_range)
            relative = f"raw/grid_X{x_index:03d}_Y{y_index:03d}_R{repeat:03d}.npz"
            _save_capture(
                run_dir.root / relative,
                payload,
                phase="grid",
                sequence_index=sequence_index,
                repeat_index=repeat,
                main_field_ma=params.scan_main_field_ma,
                x_v=x_v,
                y_v=y_v,
                x_index=x_index,
                y_index=y_index,
            )
            files.append(relative)
            sequence_index += 1

    return files


def run(params: MxXYResidualFieldCalibrationParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    estimated_captures = params.grid_points * params.point_repeats
    run_dir.update_config(
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
            "off_settle_s": params.temp_switch_off_settle_s,
            "on_settle_s": params.temp_switch_on_settle_s,
        },
        estimated_capture_count=estimated_captures,
        estimated_minimum_duration_s=estimated_captures
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
        actual_temperature, clock_sources = _configure_outputs(params, devices, channels, mapping)
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
        auto_range = ScopeAutoRangeState(
            scale_v_div=float(scope_snapshot["actual_initial_scale_v_div"]),
            offset_v=float(scope_snapshot["actual_initial_offset_v"]),
            allow_shrink=True,
        )
        data_files = _acquire_all(params, run_dir, devices, channels, scope_config, auto_range)
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            data_files=data_files,
            final_scope_scale_v_div=auto_range.scale_v_div,
            final_scope_offset_v=auto_range.offset_v,
        )
        print(f"Mx XY 剩磁二维校准采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(completion_status="failed", failure_reason=failure_reason)
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params, main_field_guard)
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                main_field_restored=not main_field_guard.restore_errors,
            )


def main() -> int:
    params = load_runtime_params(MxXYResidualFieldCalibrationParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
