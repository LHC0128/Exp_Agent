"""Mx Z 最优控制 XY 泄露响应采集工作流。"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    ArbitraryWaveformSpec,
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    configure_mx_z_optimal_control_workpoint,
    create_run_directory,
    restore_main_field_state,
    run_safety_shutdown,
    save_optimal_control_source_snapshot,
    snapshot_main_field_state,
    upload_arbitrary,
    validate_z_trigger_mapping,
    wait_for_temperature_stable,
)
from ..mx_y_rf_sensitivity.workflow import (
    _acquire_valid_r_point,
    _set_pump_gate_on,
)
from ..mx_z_field_calibration.workflow import (
    _configure_reference_clocks,
    _connect_devices,
    _initial_state_snapshot,
)
from ..mx_z_optimal_control_rf_sensitivity.sources import (
    AppliedControlWaveform,
    TheoryControlSource,
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)
from .models import MxZOptimalControlXYLeakageParams
from .scan import (
    build_xy_axes,
    first_acquired_minimum,
    iter_serpentine_grid,
    signed_control_hardware,
)


EXPERIMENT_ID = "mx-z-optimal-control-xy-leakage-response"
DATA_TYPE = "Mx_Z_Optimal_Control_XY_Leakage_Response"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    devices, channels = _connect_devices(mapping, session)
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def _configure_xy_idle(
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """在共同触发开启前把 X/Y 安全置为 0 V 且关闭。"""
    xy = devices["xy_field"]
    for channel_name, safety_key in (
        ("x_field", "X_magnetic_field"),
        ("y_rf", "Y_magnetic_field"),
    ):
        validate_safety_limit(safety_key, 0.0)
        channel = channels[channel_name]
        xy.set_output(False, channel=channel)
        xy.set_burst_state(False, channel=channel)
        xy.set_mod_state(False, channel=channel)
        xy.setup_dc(0.0, channel=channel)
        xy.set_output(False, channel=channel)


class _XYWaveformController:
    """保持 USER 波形缓存，并在零点与任意波模式间显式切换。"""

    def __init__(
        self,
        device: Any,
        trigger_device: Any,
        channels: dict[str, int],
        theory: TheoryControlSource,
        applied: AppliedControlWaveform,
        params: MxZOptimalControlXYLeakageParams,
    ) -> None:
        self.device = device
        self.trigger_device = trigger_device
        self.channels = channels
        self.theory = theory
        self.applied = applied
        self.params = params
        self.mode = {"x_field": "dc", "y_rf": "dc"}

    def _configure_channel(
        self,
        channel_name: str,
        safety_key: str,
        signed_amplitude_vpp: float,
    ) -> dict[str, float | bool]:
        channel = self.channels[channel_name]
        amplitude, phase, output_on = signed_control_hardware(
            signed_amplitude_vpp,
            self.params.control_burst_phase_deg,
        )
        self.device.set_output(False, channel=channel)
        if not output_on:
            validate_safety_limit(safety_key, 0.0)
            if self.mode[channel_name] != "dc":
                self.device.set_burst_state(False, channel=channel)
                self.device.set_mod_state(False, channel=channel)
                self.device.setup_dc(0.0, channel=channel)
                self.mode[channel_name] = "dc"
            self.device.set_output(False, channel=channel)
            return {
                "hardware_amplitude_vpp": 0.0,
                "hardware_phase_deg": phase,
                "output_on": False,
            }

        half_range = amplitude / 2.0
        for voltage in (
            -half_range,
            half_range,
            float(np.min(self.applied.normalized)) * half_range,
            float(np.max(self.applied.normalized)) * half_range,
        ):
            validate_safety_limit(safety_key, voltage)
        if self.mode[channel_name] != "aw":
            upload_arbitrary(
                self.device,
                ArbitraryWaveformSpec(
                    values=self.applied.normalized,
                    frequency=self.theory.repeat_frequency_hz,
                    amplitude=amplitude,
                    offset=0.0,
                    phase=0.0,
                    channel=channel,
                    output=False,
                ),
            )
            self.device.set_frequency(
                self.theory.repeat_frequency_hz,
                channel=channel,
            )
            self.device.set_offset(0.0, channel=channel)
            self.device.set_burst_state(True, channel=channel)
            self.device.set_burst_mode("INFinity", channel=channel)
            self.device.set_burst_trigger_source(
                "EXTernal",
                channel=channel,
            )
            self.device.set_burst_trigger_slope(
                "POSitive",
                channel=channel,
            )
            self.mode[channel_name] = "aw"
        self.device.set_amplitude(amplitude, channel=channel)
        self.device.set_burst_phase(phase, channel=channel)
        return {
            "hardware_amplitude_vpp": amplitude,
            "hardware_phase_deg": phase,
            "output_on": True,
        }

    def apply(
        self,
        x_signed_vpp: float,
        y_signed_vpp: float,
    ) -> dict[str, float | bool]:
        """关闭触发，配置并同时 arm X/Y，再开启共同触发。"""
        self.trigger_device.set_output(
            False,
            channel=self.channels["trigger"],
        )
        self.device.set_output(False, channel=self.channels["x_field"])
        self.device.set_output(False, channel=self.channels["y_rf"])
        x_hardware = self._configure_channel(
            "x_field",
            "X_magnetic_field",
            x_signed_vpp,
        )
        y_hardware = self._configure_channel(
            "y_rf",
            "Y_magnetic_field",
            y_signed_vpp,
        )
        if bool(x_hardware["output_on"]):
            self.device.set_output(True, channel=self.channels["x_field"])
        if bool(y_hardware["output_on"]):
            self.device.set_output(True, channel=self.channels["y_rf"])
        self.trigger_device.set_output(
            True,
            channel=self.channels["trigger"],
        )
        return {
            "x_hardware_amplitude_vpp": float(
                x_hardware["hardware_amplitude_vpp"]
            ),
            "x_hardware_phase_deg": float(
                x_hardware["hardware_phase_deg"]
            ),
            "x_output_on": bool(x_hardware["output_on"]),
            "y_hardware_amplitude_vpp": float(
                y_hardware["hardware_amplitude_vpp"]
            ),
            "y_hardware_phase_deg": float(
                y_hardware["hardware_phase_deg"]
            ),
            "y_output_on": bool(y_hardware["output_on"]),
        }


def _save_scan_aggregate(
    run_dir: Any,
    params: MxZOptimalControlXYLeakageParams,
    records: list[dict[str, Any]],
    actual_rate: float,
    best: dict[str, Any],
) -> None:
    x_axis, y_axis = build_xy_axes(params)
    shape = (x_axis.size, y_axis.size)
    r_mean = np.full(shape, np.nan, dtype=float)
    r_scalar_mean = np.full(shape, np.nan, dtype=float)
    r_std = np.full(shape, np.nan, dtype=float)
    accepted_attempt = np.full(shape, -1, dtype=int)
    acquisition_order = np.full(shape, -1, dtype=int)
    x_phase = np.full(shape, np.nan, dtype=float)
    y_phase = np.full(shape, np.nan, dtype=float)
    x_output = np.zeros(shape, dtype=np.uint8)
    y_output = np.zeros(shape, dtype=np.uint8)
    accepted_file = np.full(shape, "", dtype="<U128")
    for record in records:
        index = (int(record["x_index"]), int(record["y_index"]))
        r_mean[index] = float(record["r_mean_v"])
        r_scalar_mean[index] = float(record["r_scalar_mean_v"])
        r_std[index] = float(record["r_std_v"])
        accepted_attempt[index] = int(record["accepted_attempt_index"])
        acquisition_order[index] = int(record["acquisition_index"])
        x_phase[index] = float(record["x_hardware_phase_deg"])
        y_phase[index] = float(record["y_hardware_phase_deg"])
        x_output[index] = np.uint8(bool(record["x_output_on"]))
        y_output[index] = np.uint8(bool(record["y_output_on"]))
        accepted_file[index] = str(record["accepted_file"])
    np.savez(
        run_dir.raw / "xy_leakage_scan.npz",
        x_signed_amplitude_vpp=x_axis,
        y_signed_amplitude_vpp=y_axis,
        r_mean_v=r_mean,
        r_scalar_mean_v=r_scalar_mean,
        r_std_v=r_std,
        accepted_attempt_index=accepted_attempt,
        acquisition_order=acquisition_order,
        x_hardware_amplitude_vpp=np.abs(x_axis)[:, None]
        * np.ones((1, y_axis.size)),
        y_hardware_amplitude_vpp=np.ones((x_axis.size, 1))
        * np.abs(y_axis)[None, :],
        x_hardware_phase_deg=x_phase,
        y_hardware_phase_deg=y_phase,
        x_output_on=x_output,
        y_output_on=y_output,
        accepted_file=accepted_file,
        actual_rate_sa_s=np.float64(actual_rate),
        best_x_index=np.int64(best["x_index"]),
        best_y_index=np.int64(best["y_index"]),
        best_x_signed_amplitude_vpp=np.float64(best["x_signed_vpp"]),
        best_y_signed_amplitude_vpp=np.float64(best["y_signed_vpp"]),
        best_r_mean_v=np.float64(best["r_mean_v"]),
        best_r_std_v=np.float64(best["r_std_v"]),
        best_combination_remeasured=np.uint8(False),
    )


def _acquire_grid(
    params: MxZOptimalControlXYLeakageParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    controller: _XYWaveformController,
    actual_rate: float,
    device_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = params.x_control_amp_points * params.y_control_amp_points
    for acquisition_index, x_index, y_index, x_vpp, y_vpp in (
        iter_serpentine_grid(params)
    ):
        check_cancelled()
        x_amplitude, x_phase, x_output = signed_control_hardware(
            x_vpp,
            params.control_burst_phase_deg,
        )
        y_amplitude, y_phase, y_output = signed_control_hardware(
            y_vpp,
            params.control_burst_phase_deg,
        )
        hardware = {
            "x_hardware_amplitude_vpp": x_amplitude,
            "x_hardware_phase_deg": x_phase,
            "x_output_on": x_output,
            "y_hardware_amplitude_vpp": y_amplitude,
            "y_hardware_phase_deg": y_phase,
            "y_output_on": y_output,
        }
        print(
            f"XY 网格 {acquisition_index + 1}/{total}: "
            f"X={x_vpp:+.6f} Vpp, Y={y_vpp:+.6f} Vpp"
        )
        metadata = {
            "acquisition_index": np.int64(acquisition_index),
            "x_index": np.int64(x_index),
            "y_index": np.int64(y_index),
            "x_signed_amplitude_vpp": np.float64(x_vpp),
            "y_signed_amplitude_vpp": np.float64(y_vpp),
            **{
                key: np.uint8(value)
                if isinstance(value, bool)
                else np.float64(value)
                for key, value in hardware.items()
            },
        }
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"grid_X{x_index:03d}_Y{y_index:03d}",
            metadata=metadata,
            set_y_rf=lambda x=x_vpp, y=y_vpp: controller.apply(x, y),
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        record = {
            "acquisition_index": acquisition_index,
            "x_index": x_index,
            "y_index": y_index,
            "x_signed_vpp": x_vpp,
            "y_signed_vpp": y_vpp,
            **hardware,
            **summary,
            "accepted_attempt_index": attempt,
            "accepted_file": filename,
        }
        records.append(record)
    best = dict(first_acquired_minimum(records))
    _save_scan_aggregate(run_dir, params, records, actual_rate, best)
    return records, best


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZOptimalControlXYLeakageParams,
) -> SafetyShutdownReport:
    """保留 Z/X/Y 当前输出，仅关闭归零共同触发并恢复温控。"""
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "trigger" in channels:
        dg_channels.append(
            DGChannelShutdown(
                devices["z_field"],
                channels["trigger"],
                "Time_sequence_2",
                "共同触发",
            )
        )

    extra_actions: list[ShutdownAction] = []
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
                "Pump_modulation",
                params.pump_carrier_amplitude_vpp,
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
            ShutdownAction(
                "保持 Pump 100MHz 载波开启失败",
                keep_pump_carrier_on,
            )
        )

    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(
            devices["temp_switch"],
            channels["temp_switch"],
        )
    return run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(
            *STANDARD_PRESERVED_OUTPUTS,
            "Time_sequence",
            "Z_magnetic_field",
            "X_magnetic_field",
            "Y_magnetic_field",
        ),
    )


def run(params: MxZOptimalControlXYLeakageParams) -> Path:
    """执行固定 Z 最优控制下的 XY 同形任意波二维扫描。"""
    root = find_project_root()
    mapping = load_mapping(root)
    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    applied = build_applied_control(
        theory,
        calibration,
        params.control_scale,
        output_vpp=params.z_aw_output_vpp,
        output_offset_v=params.z_aw_output_offset_v,
    )
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    source_files = save_optimal_control_source_snapshot(
        run_dir.raw,
        theory,
        calibration,
        applied,
    )
    x_axis, y_axis = build_xy_axes(params)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        scan_mode="nested_scan",
        geometry={
            "control_field": "Z arbitrary waveform",
            "leakage_compensation_fields": "X/Y same-shape arbitrary waveforms",
            "pump": "Z",
            "probe": "X",
        },
        scan={
            "x_signed_amplitude_vpp": x_axis.tolist(),
            "y_signed_amplitude_vpp": y_axis.tolist(),
            "order": "X outer, Y serpentine",
            "points": int(x_axis.size * y_axis.size),
            "zero_output": "0 V DC + output OFF",
            "negative_amplitude": "absolute Vpp + 180 deg burst phase",
        },
        control_source={
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "scale": params.control_scale,
            "burst_phase_deg": params.control_burst_phase_deg,
        },
        z_calibration={
            "source_run": calibration.run_name,
            "slope_hz_per_v": calibration.slope_hz_per_v,
            "intercept_hz": calibration.intercept_hz,
            "r_squared": calibration.r_squared,
            "analysis_sha256": calibration.analysis_sha256,
        },
        applied_control={
            "minimum_v": applied.minimum_v,
            "maximum_v": applied.maximum_v,
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
            "max_abs_normalized": applied.max_abs_normalized,
            "points": int(theory.time_s.size),
        },
        trigger={
            "source": "Time_sequence_2",
            "frequency_hz": params.trigger_frequency_hz,
            "amplitude_vpp": params.trigger_amplitude_vpp,
            "offset_v": params.trigger_offset_v,
            "duty_percent": params.trigger_duty_percent,
            "slope": "POSitive",
            "wiring": (
                "Time_sequence_2 CH2 split to Z-control and X/Y-control "
                "DG4000 Ext Trig"
            ),
            "physical_wiring_verified_by_software": False,
        },
        final_output_policy={
            "completed": "preserve Z and measured-grid X/Y minimum",
            "cancelled_or_failed": "preserve current Z/X/Y state",
            "trigger": "0 V DC + output OFF on every exit path",
            "best_combination_remeasured": False,
        },
        acquisition_signals=["HF2 Demod0 R"],
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    main_field_state = None
    completion_status = "failed"
    failure_reason: str | None = None
    best: dict[str, Any] | None = None
    try:
        check_cancelled()
        try:
            devices, channels = _connect_control_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        main_field_state = snapshot_main_field_state(devices["gs200"])
        snapshot = _initial_state_snapshot(devices, channels)
        snapshot["main_field_before_configuration"] = asdict(
            main_field_state
        )
        run_dir.update_config(device_snapshot=snapshot)

        (
            actual_rate,
            actual_temperature,
            clock_sources,
            hf2_snapshot,
        ) = configure_mx_z_optimal_control_workpoint(
            params,
            devices,
            channels,
            mapping,
            theory,
            applied,
            demod_frequency_hz=params.demod_frequency_hz,
            cancellation=_RuntimeCancellation(),
            xy_output_configurator=lambda: _configure_xy_idle(
                devices,
                channels,
            ),
            reference_clock_configurator=_configure_reference_clocks,
            pump_gate_setter=_set_pump_gate_on,
            temperature_stability_waiter=wait_for_temperature_stable,
        )
        run_dir.update_config(
            clock_sources=clock_sources,
            hf2_configuration=hf2_snapshot,
            initial_temperature_c=actual_temperature,
        )
        controller = _XYWaveformController(
            devices["xy_field"],
            devices["z_field"],
            channels,
            theory,
            applied,
            params,
        )
        _, best = _acquire_grid(
            params,
            run_dir,
            devices,
            channels,
            controller,
            actual_rate,
            str(mapping["lockin_r"]["device_id"]),
        )
        final_hardware = controller.apply(
            float(best["x_signed_vpp"]),
            float(best["y_signed_vpp"]),
        )
        _sleep(max(2.0 / params.trigger_frequency_hz, 0.02))
        devices["z_field"].set_output(False, channel=channels["trigger"])
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            actual_rates={"response_sa_s": actual_rate},
            measured_grid_minimum={
                "x_index": int(best["x_index"]),
                "y_index": int(best["y_index"]),
                "x_signed_amplitude_vpp": float(best["x_signed_vpp"]),
                "y_signed_amplitude_vpp": float(best["y_signed_vpp"]),
                "r_mean_v": float(best["r_mean_v"]),
                "r_std_v": float(best["r_std_v"]),
                "acquisition_index": int(best["acquisition_index"]),
                "combined_state_remeasured": False,
            },
            final_xy_hardware=final_hardware,
            data_files=[*source_files, "raw/xy_leakage_scan.npz"],
        )
        print(f"Mx Z 最优控制 XY 泄露响应采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed",
                failure_reason=failure_reason,
                current_xy_preserved=True,
            )
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        main_field_restore_errors: list[str] = []
        if main_field_state is not None and devices.get("gs200") is not None:
            try:
                restore_main_field_state(
                    devices["gs200"],
                    main_field_state,
                )
            except Exception as exc:
                main_field_restore_errors.append(str(exc))
        if shutdown_report.errors or main_field_restore_errors:
            print(
                "安全恢复警告: "
                + "；".join(
                    [*shutdown_report.errors, *main_field_restore_errors]
                )
            )
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                best_point_applied=completion_status == "completed"
                and best is not None,
                xy_state_preserved=True,
                safety_shutdown=shutdown_report.to_dict(),
                main_field_restore={
                    "attempted": main_field_state is not None,
                    "success": not main_field_restore_errors,
                    "errors": main_field_restore_errors,
                },
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxZOptimalControlXYLeakageParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
