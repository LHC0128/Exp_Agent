"""无硬件预检、理论来源冻结、Mx 工作点及显式输出保留。"""
from pathlib import Path
from ...common import load_mapping
from ...control_sources import load_theory_control, load_z_calibration, build_applied_control
from ...experiments.requirements import OPTIMAL_CONTROL
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (configure_mx_z_optimal_control_workpoint, validate_z_trigger_mapping,
    STANDARD_PRESERVED_OUTPUTS, ShutdownAction, DisconnectTarget, TemperatureSwitchRestore, run_safety_shutdown,
    OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE)
from ..mx_z_optimal_control_rf_sensitivity.preparation import (
    _connect_control_devices, _initial_state_snapshot, _RuntimeCancellation, _configure_reference_clocks,
    _set_pump_gate_on, wait_for_temperature_stable)
from ..mx_z_optimal_control_xy_rf_sensitivity.preparation import source_fingerprint, save_source
from ...common import validate_safety_limit
from .excitation import _configure_xy_idle
from .scan import build_xy_axes
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION


def preflight(params, root):
    validate_mapping_usage(OPTIMAL_CONTROL, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_z_trigger_mapping(mapping)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def load_source(params, root):
    theory = load_theory_control(Path(params.control_results_root), params.control_version)
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    applied = build_applied_control(theory, calibration, params.control_scale,
        output_vpp=params.z_aw_output_vpp, output_offset_v=params.z_aw_output_offset_v)
    return None, theory, calibration, applied


def configure_workpoint(params, devices, channels, mapping, theory, applied):
    return configure_mx_z_optimal_control_workpoint(params, devices, channels, mapping, theory, applied,
        demod_frequency_hz=params.demod_frequency_hz, cancellation=_RuntimeCancellation(),
        xy_output_configurator=lambda: _configure_xy_idle(devices, channels),
        reference_clock_configurator=_configure_reference_clocks, pump_gate_setter=_set_pump_gate_on,
        temperature_stability_waiter=wait_for_temperature_stable)


def safe_shutdown(devices, channels, params, *, disconnect_tec=True):
    """不改写 Z/XY/共同触发；恢复温控，Pump 沿用已执行配置。"""
    actions = []
    pump = devices.get("pump_rf")
    if pump is not None and "pump_gate" in channels:
        actions.append(ShutdownAction("保持 Pump 门控失败", lambda: _set_pump_gate_on(
            pump, channels["pump_gate"], params.pump_gate_voltage_v)))
    if pump is not None and "pump_carrier" in channels:
        def keep_carrier():
            validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
            pump.setup_sine(params.pump_carrier_frequency_hz, params.pump_carrier_amplitude_vpp,
                offset=0., phase=0., channel=channels["pump_carrier"])
            pump.set_output(True, channel=channels["pump_carrier"])
        actions.append(ShutdownAction("保持 Pump 载波失败", keep_carrier))
    temperature = (TemperatureSwitchRestore(devices["temp_switch"], channels["temp_switch"])
        if devices.get("temp_switch") is not None and "temp_switch" in channels else None)
    return run_safety_shutdown(temperature_switch=temperature, extra_actions=actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec") if disconnect_tec else None),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field"))


def initial_config(params, _, theory, calibration, applied):
    x_axis, y_axis = build_xy_axes(params)
    return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode="typed_workflow",
        raw_format_version=RAW_FORMAT_VERSION, scan_mode="nested_scan",
        geometry={"control_field": "Z arbitrary waveform", "leakage_compensation_fields": "X/Y same-shape arbitrary waveforms",
            "pump": "Z", "probe": "X"},
        scan={"x_signed_amplitude_vpp": x_axis.tolist(), "y_signed_amplitude_vpp": y_axis.tolist(),
            "order": "X outer, Y serpentine", "points": int(x_axis.size * y_axis.size),
            "zero_output": "0 V DC + output OFF", "negative_amplitude": "absolute Vpp + 180 deg burst phase"},
        control_source={"version": theory.version, "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256, "repeat_frequency_hz": theory.repeat_frequency_hz,
            "scale": params.control_scale, "burst_phase_deg": params.control_burst_phase_deg},
        z_calibration={"source_run": calibration.run_name, "slope_hz_per_v": calibration.slope_hz_per_v,
            "intercept_hz": calibration.intercept_hz, "r_squared": calibration.r_squared,
            "analysis_sha256": calibration.analysis_sha256},
        applied_control={"minimum_v": applied.minimum_v, "maximum_v": applied.maximum_v,
            "amplitude_vpp": applied.amplitude_vpp, "offset_v": applied.offset_v,
            "max_abs_normalized": applied.max_abs_normalized, "points": int(theory.time_s.size)},
        trigger={"source": "Time_sequence_2", "frequency_hz": params.trigger_frequency_hz,
            "amplitude_vpp": params.trigger_amplitude_vpp, "offset_v": params.trigger_offset_v,
            "duty_percent": params.trigger_duty_percent, "slope": OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
            "wiring": "Time_sequence_2 split to Z-control and X/Y-control DG4000 Ext Trig",
            "physical_wiring_verified_by_software": False},
        final_output_policy={"completed": "preserve Z and measured-grid X/Y minimum",
            "cancelled_or_failed": "preserve current Z/X/Y state", "trigger": "preserve current state on every exit path",
            "best_combination_remeasured": False}, acquisition_signals=["HF2 Demod0 R"])
