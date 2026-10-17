"""无硬件资格、冻结来源、工作点配置与安全恢复。"""
from __future__ import annotations
from typing import Any
import numpy as np
from ...common import load_mapping, validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import (DeviceSession, configure_fixed_dc_field, configure_mx_z_optimal_control_workpoint,
    DGChannelShutdown, DisconnectTarget, STANDARD_PRESERVED_OUTPUTS, SafetyShutdownReport,
    ShutdownAction, TemperatureSwitchRestore, run_safety_shutdown, validate_z_trigger_mapping,
    wait_for_temperature_stable, OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE)
from .models import MxZOptimalControlRFParams
from ...instrument_config import validate_mapping_usage
from ...experiments.requirements import OPTIMAL_CONTROL
from ...lockin_compatibility import require_lockin_compatibility
from ...control_sources import applied_control_from_corrected
from ...current_feedback import load_corrected_control_waveform
from ..mx_z_field_calibration.workflow import _connect_devices, _configure_reference_clocks, _initial_state_snapshot
from ..mx_y_rf_sensitivity.workflow import _set_pump_gate_on
from .excitation import _configure_y_rf_output
class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接 Mx 控制实验设备，并声明 Z 源同机触发通道。"""
    devices, channels = _connect_devices(mapping, session)
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def _configure_common_outputs(
    params: MxZOptimalControlRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
    control_source: Any,
    applied: Any,
) -> tuple[float, float | None, dict[str, dict[str, str]], dict[str, Any]]:
    """配置可选 GS200 主场的 Mx 工作点并启动连续 Z 控制。

    ``control_source`` 只需提供 ``repeat_frequency_hz``，本实验传入
    闭环冻结波形，仍支持理论来源的实验传入 ``TheoryControlSource``。
    """
    def configure_xy_outputs() -> None:
        xy_field = devices["xy_field"]
        configure_fixed_dc_field(
            xy_field,
            channels["x_field"],
            "X_magnetic_field",
            params.x_dc_field_v,
        )
        _configure_y_rf_output(params, xy_field, channels["y_rf"])

    return configure_mx_z_optimal_control_workpoint(
        params,
        devices,
        channels,
        mapping,
        control_source,
        applied,
        demod_frequency_hz=params.y_rf_frequency_hz,
        cancellation=_RuntimeCancellation(),
        xy_output_configurator=configure_xy_outputs,
        reference_clock_configurator=_configure_reference_clocks,
        pump_gate_setter=_set_pump_gate_on,
        temperature_stability_waiter=wait_for_temperature_stable,
    )


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZOptimalControlRFParams,
    *, disconnect_tec: bool = True,
) -> SafetyShutdownReport:
    """保持 Z 控制和共同触发，关闭归零横向场。"""
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
    if devices.get("xy_field") is not None:
        for channel_name, safety_key, label in (
            ("x_field", "X_magnetic_field", "X 磁场"),
            ("y_rf", "rf_coil", "Y RF 场"),
        ):
            if channel_name in channels:
                dg_channels.append(
                    DGChannelShutdown(
                        devices["xy_field"],
                        channels[channel_name],
                        safety_key,
                        label,
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
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec") if disconnect_tec else None),),
        preserved_outputs=(
            *STANDARD_PRESERVED_OUTPUTS,
            "Time_sequence",
            "Z_magnetic_field",
        ),
    )


def preflight(params, root):
    validate_mapping_usage(OPTIMAL_CONTROL, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_z_trigger_mapping(mapping)
    require_lockin_compatibility("mx-z-optimal-control-rf-sensitivity", params.to_external(), mapping=mapping)
    return params, mapping


def load_source(params, root):
    control = load_corrected_control_waveform(root, params.corrected_control_source_run)
    return control, applied_control_from_corrected(control)


def initial_config(params, control, applied):
    residual_mode = params.phase_cal_rf_amplitude_vpp == 0.0
    measurement_mode = "residual_control_phase_scan" if residual_mode else "rf_sensitivity"
    warnings = []
    if not residual_mode and not np.isclose(params.y_rf_frequency_hz, control.repeat_frequency_hz, rtol=1e-9, atol=1e-9):
        warnings.append("Y RF/锁相频率与冻结波形重复频率不同；共同触发只固定采集起始相位，采集期间相对相位按频差演化")
    return dict(
        experiment_id="mx-z-optimal-control-rf-sensitivity",
        data_type="Mx_Z_Optimal_Control_RF_Sensitivity",
        execution_mode="typed_workflow",
        measurement_mode=measurement_mode,
        geometry={
            "gs200_main_field": (
                "zero_and_output_off_during_measurement"
                if params.main_magnetic_field_ma == 0.0
                else "configured_current_and_output_on_during_measurement"
            ),
            "gs200_main_field_current_ma": params.main_magnetic_field_ma,
            "control_field": "Z arbitrary waveform",
            "rf_field": (
                "Y DC residual-field compensation only"
                if residual_mode
                else "Y triggered sine with DC residual-field compensation"
            ),
            "pump": "Z",
            "probe": "X",
            "x_field_output": (
                "OFF" if params.x_dc_field_v == 0.0 else "fixed DC"
            ),
            "x_dc_field_v": params.x_dc_field_v,
            "y_rf_dc_offset_v": params.y_rf_offset_v,
        },
        residual_field_compensation={
            "x_source": "X_magnetic_field DC",
            "x_voltage_v": params.x_dc_field_v,
            "y_source": "rf_coil waveform DC offset",
            "y_voltage_v": params.y_rf_offset_v,
            "y_rf_zero_behavior": "pure DC at Y compensation voltage",
        },
        control_source={
            "mode": "corrected_run",
            "corrected_run": control.run_name,
            "waveform_sha256": control.waveform_sha256,
            "repeat_frequency_hz": control.repeat_frequency_hz,
            "burst_phase_deg": params.control_burst_phase_deg,
        },
        current_feedback_calibrations={
            "current_coupling_run": control.coupling_calibration_run,
            "current_coupling_sha256": control.coupling_calibration_sha256,
            "frequency_response_run": control.frequency_response_run,
            "frequency_response_sha256": control.frequency_response_sha256,
        },
        applied_control={
            "minimum_v": applied.minimum_v,
            "maximum_v": applied.maximum_v,
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
            "output_minimum_v": applied.output_minimum_v,
            "output_maximum_v": applied.output_maximum_v,
            "max_abs_normalized": applied.max_abs_normalized,
            "points": int(control.time_s.size),
        },
        trigger={
            "source": "Time_sequence_2",
            "frequency_hz": params.trigger_frequency_hz,
            "amplitude_vpp": params.trigger_amplitude_vpp,
            "offset_v": params.trigger_offset_v,
            "duty_percent": params.trigger_duty_percent,
            "slope": OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
            "wiring": (
                "Time_sequence_2 CH2 to Z-control DG4000 Ext Trig; "
                "Y-RF AC burst disabled while Y DC compensation remains applied"
                if residual_mode
                else "Time_sequence_2 CH2 split to Z-control and Y-RF DG4000 Ext Trig"
            ),
            "physical_wiring_verified_by_software": False,
        },
        warnings=warnings,
        acquisition_signals=(
            ["Demod0 R"]
            if residual_mode
            else [
                "Demod0 R/X/Y during phase calibration",
                "Demod0 R during amplitude/noise acquisition",
            ]
        ),
        linewidth_mode="amplitude_equivalent",
    )
