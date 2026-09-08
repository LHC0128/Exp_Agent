"""Mx Z 最优控制 + DG4000 Z 偏置的 XYZ 平衡场采集工作流。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...current_feedback import load_corrected_control_waveform
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    configure_mx_z_optimal_control_workpoint,
    create_run_directory,
    run_safety_shutdown,
    save_corrected_control_source_snapshot,
    save_optimal_control_source_snapshot,
    validate_z_trigger_mapping,
    wait_for_temperature_stable,
)
from ...steps.dg_channel_state import (
    DGChannelState,
    restore_dg_channel,
    snapshot_dg_channel,
)
from ..mx_y_rf_sensitivity.workflow import _set_pump_gate_on
from ..mx_z_field_calibration.workflow import (
    _configure_reference_clocks,
    _connect_devices,
    _initial_state_snapshot,
)
from ..mx_z_optimal_control_rf_sensitivity.sources import (
    build_applied_control,
    corrected_control_contract,
    load_theory_control,
    load_z_calibration,
)
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _acquire_valid_rxy_point,
)
from .models import MxZOptimalControlDG4000BiasXYZBalanceParams
from .scan import build_xyz_axes, first_acquired_minimum, iter_serpentine_grid


EXPERIMENT_ID = "mx-z-optimal-control-dg4000-bias-xyz-balance"
DATA_TYPE = "Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接不含 GS200 的 Mx 设备，并确认 Z 控制与触发同机。"""
    devices, channels = _connect_devices(
        mapping,
        session,
        include_main_field=False,
    )
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def _configure_xy_idle(
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """启动最优控制前把 X/Y 安全置为 0 V 且关闭。"""
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


def _validate_bias_envelope(
    applied: Any,
    bias_v: float,
    output_vpp: float,
) -> float:
    """校验偏置叠加后的完整 Z 任意波包络并返回总 offset。"""
    bias = float(bias_v)
    target_offset = float(applied.offset_v) + bias
    values = (
        float(applied.minimum_v) + bias,
        float(applied.maximum_v) + bias,
        target_offset - abs(float(output_vpp)) / 2.0,
        target_offset + abs(float(output_vpp)) / 2.0,
    )
    for value in values:
        validate_safety_limit("Z_magnetic_field", value)
    return target_offset


class _XYZBiasController:
    """设置 X/Y DC，并仅通过 DG4000 offset 叠加 Z 偏置。"""

    def __init__(
        self,
        xy_device: Any,
        z_device: Any,
        channels: dict[str, int],
        applied: Any,
        output_vpp: float,
    ) -> None:
        self.xy_device = xy_device
        self.z_device = z_device
        self.channels = channels
        self.applied = applied
        self.output_vpp = float(output_vpp)
        self._last_bias_v: float | None = None

    def apply(self, x_v: float, y_v: float, bias_v: float) -> None:
        x_v = float(validate_safety_limit("X_magnetic_field", x_v))
        y_v = float(validate_safety_limit("Y_magnetic_field", y_v))
        target_offset = _validate_bias_envelope(
            self.applied,
            bias_v,
            self.output_vpp,
        )
        if self._last_bias_v is None or float(bias_v) != self._last_bias_v:
            self.z_device.set_offset(
                target_offset,
                channel=self.channels["z_field"],
            )
            self._last_bias_v = float(bias_v)
        for channel_name, value in (("x_field", x_v), ("y_rf", y_v)):
            channel = self.channels[channel_name]
            self.xy_device.set_burst_state(False, channel=channel)
            self.xy_device.set_mod_state(False, channel=channel)
            self.xy_device.setup_dc(value, channel=channel)
            self.xy_device.set_output(True, channel=channel)


def _save_scan_aggregate(
    run_dir: Any,
    params: MxZOptimalControlDG4000BiasXYZBalanceParams,
    records: list[dict[str, Any]],
    actual_rate: float,
    best: dict[str, Any],
    calibration: Any,
    applied: Any,
) -> None:
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    shape = (z_axis.size, x_axis.size, y_axis.size)
    arrays = {
        name: np.full(shape, np.nan, dtype=float)
        for name in (
            "r_mean_v",
            "x_mean_v",
            "y_mean_v",
            "r_std_v",
            "x_std_v",
            "y_std_v",
            "complex_std_v",
        )
    }
    accepted_attempt = np.full(shape, -1, dtype=int)
    acquisition_order = np.full(shape, -1, dtype=int)
    accepted_file = np.full(shape, "", dtype="<U200")
    for record in records:
        index = (
            int(record["z_index"]),
            int(record["x_index"]),
            int(record["y_index"]),
        )
        for name, values in arrays.items():
            values[index] = float(record[name])
        accepted_attempt[index] = int(record["accepted_attempt_index"])
        acquisition_order[index] = int(record["acquisition_index"])
        accepted_file[index] = str(record["accepted_file"])
    z_bias_frequency_hz = calibration.slope_hz_per_v * z_axis
    z_bias_absolute_bz_nt = (
        calibration.intercept_hz + z_bias_frequency_hz
    ) / params.bz_gamma_hz_per_nt
    z_control_waveform_mean_frequency_hz = (
        calibration.intercept_hz
        + calibration.slope_hz_per_v
        * (float(np.mean(applied.voltage_v)) + z_axis)
    )
    z_control_waveform_mean_bz_nt = (
        z_control_waveform_mean_frequency_hz / params.bz_gamma_hz_per_nt
    )
    np.savez(
        run_dir.raw / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_bias_v=z_axis,
        z_bias_frequency_hz=z_bias_frequency_hz,
        z_bias_absolute_bz_nt=z_bias_absolute_bz_nt,
        z_control_waveform_mean_bz_nt=z_control_waveform_mean_bz_nt,
        **arrays,
        accepted_attempt_index=accepted_attempt,
        acquisition_order=acquisition_order,
        accepted_file=accepted_file,
        actual_rate_sa_s=np.float64(actual_rate),
        z_control_waveform_mean_v=np.float64(
            float(np.mean(applied.voltage_v))
        ),
        z_control_waveform_min_v=z_axis + np.float64(applied.minimum_v),
        z_control_waveform_max_v=z_axis + np.float64(applied.maximum_v),
        best_z_index=np.int64(best["z_index"]),
        best_x_index=np.int64(best["x_index"]),
        best_y_index=np.int64(best["y_index"]),
        best_z_bias_v=np.float64(best["z_bias_v"]),
        best_x_field_v=np.float64(best["x_field_v"]),
        best_y_field_v=np.float64(best["y_field_v"]),
        best_r_mean_v=np.float64(best["r_mean_v"]),
        best_r_std_v=np.float64(best["r_std_v"]),
        best_combination_remeasured=np.uint8(False),
    )


def _acquire_grid(
    params: MxZOptimalControlDG4000BiasXYZBalanceParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    controller: _XYZBiasController,
    actual_rate: float,
    device_id: str,
    calibration: Any,
    applied: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = params.x_field_points * params.y_field_points * params.z_bias_points
    for (
        acquisition_index,
        z_index,
        x_index,
        y_index,
        bias_v,
        x_v,
        y_v,
    ) in iter_serpentine_grid(params):
        check_cancelled()
        print(
            f"XYZ 偏置网格 {acquisition_index + 1}/{total}: "
            f"X={x_v:+.6f} V, Y={y_v:+.6f} V, Z bias={bias_v:+.6f} V"
        )
        metadata = {
            "acquisition_index": np.int64(acquisition_index),
            "z_index": np.int64(z_index),
            "x_index": np.int64(x_index),
            "y_index": np.int64(y_index),
            "z_bias_v": np.float64(bias_v),
            "z_bias_frequency_hz": np.float64(
                calibration.slope_hz_per_v * bias_v
            ),
            "z_bias_absolute_bz_nt": np.float64(
                (calibration.intercept_hz + calibration.slope_hz_per_v * bias_v)
                / params.bz_gamma_hz_per_nt
            ),
            "z_control_waveform_mean_bz_nt": np.float64(
                (
                    calibration.intercept_hz
                    + calibration.slope_hz_per_v
                    * (float(np.mean(applied.voltage_v)) + bias_v)
                )
                / params.bz_gamma_hz_per_nt
            ),
            "x_field_v": np.float64(x_v),
            "y_field_v": np.float64(y_v),
            "z_output_offset_v": np.float64(
                applied.offset_v + bias_v
            ),
            "x_output_on": np.uint8(True),
            "y_output_on": np.uint8(True),
            "gs200_used": np.uint8(False),
        }
        summary, attempt, filename = _acquire_valid_rxy_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=(
                f"grid_Z{z_index:03d}_X{x_index:03d}_Y{y_index:03d}"
            ),
            metadata=metadata,
            set_y_rf=lambda x=x_v, y=y_v, bias=bias_v: controller.apply(
                x,
                y,
                bias,
            ),
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        records.append(
            {
                "acquisition_index": acquisition_index,
                "z_index": z_index,
                "x_index": x_index,
                "y_index": y_index,
                "z_bias_v": bias_v,
                "x_field_v": x_v,
                "y_field_v": y_v,
                **summary,
                "accepted_attempt_index": attempt,
                "accepted_file": filename,
            }
        )
    best = dict(first_acquired_minimum(records))
    _save_scan_aggregate(
        run_dir,
        params,
        records,
        actual_rate,
        best,
        calibration,
        applied,
    )
    return records, best


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZOptimalControlDG4000BiasXYZBalanceParams,
) -> SafetyShutdownReport:
    """关闭共同触发、恢复温控，同时暂时保持 Z 控制供状态恢复。"""
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "trigger" in channels:
        dg_channels.append(
            DGChannelShutdown(
                devices["z_field"],
                channels["trigger"],
                "Time_sequence_2",
                "最优控制触发",
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
        ),
    )


def run(params: MxZOptimalControlDG4000BiasXYZBalanceParams) -> Path:
    """执行 DG4000 Z 偏置下的 XYZ 网格扫描。"""
    root = find_project_root()
    model_errors = params.validate(root)
    if model_errors:
        raise ValueError("；".join(model_errors))
    mapping = load_mapping(root)
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    corrected = None
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(
            root,
            params.corrected_control_source_run,
        )
        theory, applied = corrected_control_contract(corrected)
    else:
        theory = load_theory_control(
            Path(params.control_results_root),
            params.control_version,
        )
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
    source_files = (
        save_corrected_control_source_snapshot(
            run_dir.raw, corrected, theory, applied
        )
        if corrected is not None
        else save_optimal_control_source_snapshot(
            run_dir.raw, theory, calibration, applied
        )
    )
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        scan_mode="nested_scan",
        geometry={
            "optimal_control": "Z_magnetic_field DG4000 arbitrary waveform",
            "z_bias": "same DG4000 Z channel offset",
            "x_balance": "X_magnetic_field DG4000 DC",
            "y_balance": "Y_magnetic_field DG4000 DC",
            "gs200": "physically disconnected and not used",
        },
        scan={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "z_bias_v": z_axis.tolist(),
            "canonical_shape": "Z bias, X, Y",
            "order": "Z bias outer, continuous X/Y serpentine",
            "points": int(x_axis.size * y_axis.size * z_axis.size),
            "single_axis_rule": "POINTS=1 requires START=STOP",
            "zero_output": "X/Y DC outputs remain ON at zero",
        },
        control_source={
            "mode": params.control_waveform_source,
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "scale": params.control_scale if corrected is None else None,
            "burst_phase_deg": params.control_burst_phase_deg,
            "corrected_run": corrected.run_name if corrected is not None else None,
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
        },
        current_feedback_calibrations=(
            {
                "current_coupling_run": corrected.coupling_calibration_run,
                "current_coupling_sha256": corrected.coupling_calibration_sha256,
                "frequency_response_run": corrected.frequency_response_run,
                "frequency_response_sha256": corrected.frequency_response_sha256,
            }
            if corrected is not None
            else None
        ),
        z_control_calibration={
            "source_run": calibration.run_name,
            "slope_hz_per_v": calibration.slope_hz_per_v,
            "intercept_hz": calibration.intercept_hz,
            "r_squared": calibration.r_squared,
            "analysis_sha256": calibration.analysis_sha256,
            "gamma_hz_per_nt": params.bz_gamma_hz_per_nt,
        },
        applied_control={
            "minimum_v": applied.minimum_v,
            "maximum_v": applied.maximum_v,
            "mean_v": float(np.mean(applied.voltage_v)),
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
            "slope": OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
            "wiring": "Time_sequence_2 CH2 to Z-control DG4000 Ext Trig",
            "physical_wiring_verified_by_software": False,
        },
        final_output_policy={
            "xyz_fields": "restore complete pre-run X/Y and Z DG4000 state",
            "optimal_control": "restore complete pre-run Z channel state",
            "trigger": "0 V DC + output OFF on every exit path",
            "best_combination_remeasured": False,
            "gs200_used": False,
        },
        acquisition_signals=["HF2 Demod0 X", "HF2 Demod0 Y", "HF2 Demod0 R"],
        interpretation={
            "criterion": "empirical minimum of Demod0 R",
            "bloch_fit_available": False,
            "periodic_bloch_required_for_model_fit": True,
        },
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    z_state: DGChannelState | None = None
    trigger_state: DGChannelState | None = None
    xy_states: dict[str, DGChannelState] = {}
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
        z_state = snapshot_dg_channel(devices["z_field"], channels["z_field"])
        trigger_state = snapshot_dg_channel(
            devices["z_field"],
            channels["trigger"],
        )
        xy_states = {
            "x_field": snapshot_dg_channel(
                devices["xy_field"],
                channels["x_field"],
            ),
            "y_rf": snapshot_dg_channel(
                devices["xy_field"],
                channels["y_rf"],
            ),
        }
        snapshot = _initial_state_snapshot(devices, channels)
        snapshot["gs200"] = {"used": False, "physically_disconnected": True}
        snapshot["z_channel_before_configuration"] = asdict(z_state)
        snapshot["trigger_channel_before_configuration"] = asdict(trigger_state)
        snapshot["xy_channels_before_configuration"] = {
            name: asdict(state) for name, state in xy_states.items()
        }
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
            xy_output_configurator=lambda: _configure_xy_idle(devices, channels),
            reference_clock_configurator=_configure_reference_clocks,
            pump_gate_setter=_set_pump_gate_on,
            temperature_stability_waiter=wait_for_temperature_stable,
            configure_main_field_output=False,
        )
        run_dir.update_config(
            clock_sources=clock_sources,
            hf2_configuration=hf2_snapshot,
            initial_temperature_c=actual_temperature,
        )
        controller = _XYZBiasController(
            devices["xy_field"],
            devices["z_field"],
            channels,
            applied,
            params.z_aw_output_vpp,
        )
        _, best = _acquire_grid(
            params,
            run_dir,
            devices,
            channels,
            controller,
            actual_rate,
            str(mapping["lockin_r"]["device_id"]),
            calibration,
            applied,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            actual_rates={"response_sa_s": actual_rate},
            measured_grid_minimum={
                "z_index": int(best["z_index"]),
                "x_index": int(best["x_index"]),
                "y_index": int(best["y_index"]),
                "z_bias_v": float(best["z_bias_v"]),
                "x_field_v": float(best["x_field_v"]),
                "y_field_v": float(best["y_field_v"]),
                "r_mean_v": float(best["r_mean_v"]),
                "r_std_v": float(best["r_std_v"]),
                "acquisition_index": int(best["acquisition_index"]),
                "remeasured": False,
            },
            data_files=[*source_files, "raw/xyz_balance_scan.npz"],
        )
        print(f"Mx Z DG4000 偏置 XYZ 平衡场采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed",
                failure_reason=failure_reason,
            )
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        restore_errors: list[str] = list(shutdown_report.errors)
        if devices.get("xy_field") is not None:
            for channel_name in ("y_rf", "x_field"):
                state = xy_states.get(channel_name)
                channel = channels.get(channel_name)
                if state is None or channel is None:
                    continue
                try:
                    restore_dg_channel(devices["xy_field"], channel, state)
                except Exception as exc:
                    restore_errors.append(f"{channel_name}: {exc}")
        if z_state is not None and devices.get("z_field") is not None:
            try:
                restore_dg_channel(
                    devices["z_field"],
                    channels["z_field"],
                    z_state,
                )
            except Exception as exc:
                restore_errors.append(f"z_field: {exc}")
        if trigger_state is not None and devices.get("z_field") is not None:
            try:
                restore_dg_channel(
                    devices["z_field"],
                    channels["trigger"],
                    trigger_state,
                )
            except Exception as exc:
                restore_errors.append(f"trigger: {exc}")
        if restore_errors:
            print("安全恢复警告: " + "；".join(restore_errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                xyz_restore={
                    "attempted": bool(xy_states),
                    "success": not any(
                        error.startswith(("x_field:", "y_rf:"))
                        for error in restore_errors
                    ),
                },
                z_channel_restore={
                    "attempted": z_state is not None,
                    "success": not any(
                        error.startswith("z_field:")
                        for error in restore_errors
                    ),
                },
                trigger_channel_restore={
                    "attempted": trigger_state is not None,
                    "success": not any(
                        error.startswith("trigger:")
                        for error in restore_errors
                    ),
                },
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "gs200_used": False,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxZOptimalControlDG4000BiasXYZBalanceParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
