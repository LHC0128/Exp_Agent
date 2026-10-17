"""无硬件预检、理论来源冻结、Mx 工作点与安全收尾。"""

from pathlib import Path
from typing import Any

from ...common import load_mapping, validate_safety_limit
from ...control_sources import (
    AppliedControlWaveform,
    TheoryControlSource,
    ZCalibrationSource,
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)
from ...experiments.requirements import OPTIMAL_CONTROL
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (
    DGChannelShutdown,
    DisconnectTarget,
    OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    configure_mx_z_optimal_control_workpoint,
    run_safety_shutdown,
    validate_z_trigger_mapping,
    wait_for_temperature_stable,
)
from ..mx_z_optimal_control_rf_sensitivity.preparation import (
    _RuntimeCancellation,
    _configure_reference_clocks,
    _connect_control_devices,
    _initial_state_snapshot,
)
from ..mx_z_optimal_control_xy_rf_sensitivity.preparation import (
    save_source,
    source_fingerprint,
)
from ..mx_y_rf_sensitivity.workflow import _set_pump_gate_on
from .excitation import _configure_xy_idle
from .models import MxZOptimalControlXYZBalanceParams
from .scan import build_xyz_axes
from .run_format import DATA_TYPE, EXPERIMENT_ID, RAW_FORMAT_VERSION

EXECUTION_MODE = "typed_workflow"


def preflight(params, root):
    validate_mapping_usage(OPTIMAL_CONTROL, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_z_trigger_mapping(mapping)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def load_source(params: MxZOptimalControlXYZBalanceParams, root: Path):
    theory = load_theory_control(Path(params.control_results_root), params.control_version)
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    applied = build_applied_control(theory, calibration, params.control_scale,
        output_vpp=params.z_aw_output_vpp, output_offset_v=params.z_aw_output_offset_v)
    return None, theory, calibration, applied


def configure_workpoint(
    params: MxZOptimalControlXYZBalanceParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
    theory: TheoryControlSource,
    applied: AppliedControlWaveform,
):
    """配置 Mx 工作点：GS200 在 Z 起点开启，X/Y 置零关闭后启动 Z 控制。"""
    return configure_mx_z_optimal_control_workpoint(
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
    )


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZOptimalControlXYZBalanceParams,
    *,
    disconnect_tec: bool = True,
) -> SafetyShutdownReport:
    """不改写 Z 控制与共同触发；恢复温控，Pump 沿用已执行配置。"""
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "trigger" in channels:
        # 共同触发属于 PRESERVED_TIMING_OUTPUTS，run_safety_shutdown 只记录不改写。
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
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec") if disconnect_tec else None),),
        preserved_outputs=(
            *STANDARD_PRESERVED_OUTPUTS,
            "Time_sequence",
            "Z_magnetic_field",
        ),
    )


def initial_config(params: MxZOptimalControlXYZBalanceParams, _, theory: TheoryControlSource,
                   calibration: ZCalibrationSource, applied: AppliedControlWaveform):
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    return dict(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        raw_format_version=RAW_FORMAT_VERSION,
        scan_mode="nested_scan",
        measurement_mode="xyz_balance_grid",
        geometry={
            "optimal_control": "Z_magnetic_field DG4000 arbitrary waveform",
            "x_balance": "X_magnetic_field DG4000 DC",
            "y_balance": "Y_magnetic_field DG4000 DC",
            "z_balance": "main_magnetic_field GS200 current",
        },
        scan={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "z_field_ma": z_axis.tolist(),
            "canonical_shape": "Z, X, Y",
            "order": "Z outer, continuous X/Y serpentine",
            "points": int(x_axis.size * y_axis.size * z_axis.size),
            "single_axis_rule": "POINTS=1 requires START=STOP",
            "zero_output": "X/Y DC and GS200 output remain ON at zero",
        },
        control_source={
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "scale": params.control_scale,
            "burst_phase_deg": params.control_burst_phase_deg,
        },
        z_control_calibration={
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
            "slope": OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
            "wiring": "Time_sequence_2 CH2 to Z-control DG4000 Ext Trig",
            "physical_wiring_verified_by_software": False,
        },
        final_output_policy={
            "xyz_fields": "restore complete pre-run X/Y/GS200 state",
            "optimal_control": "preserve waveform and output ON",
            "trigger": "preserve current state on every exit path",
            "best_combination_remeasured": False,
        },
        acquisition_signals=["HF2 Demod0 R"],
    )
