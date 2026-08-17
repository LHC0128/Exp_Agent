"""Mx 6221 主场任意波最优控制 RF 灵敏度采集工作流。"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from gs200 import GS200Instrument
from keithley_6221 import Keithley6221Instrument
from lockin_amplifier import HF2Instrument
from lockin_amplifier import (
    DemodulatorConfig,
    OscillatorConfig,
    SignalInputConfig,
    demod,
)
from tec_controller import TECInstrument

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    SafetyShutdownReport,
    ShutdownAction,
    STANDARD_PRESERVED_OUTPUTS,
    TemperatureSwitchRestore,
    connect_signal_generator_routes,
    configure_fixed_dc_field,
    configure_temperature_control,
    create_run_directory,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    wait_for_temperature_stable,
)
from ...steps.keithley_6221 import (
    Keithley6221CurrentRangeCheck,
    check_keithley_6221_compliance,
    configure_keithley_6221_optimal_control,
    require_keithley_6221_current_range,
    shutdown_keithley_6221,
)
from ..mx_y_rf_sensitivity.workflow import (
    _acquire_noise,
    _set_pump_gate_on,
)
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _acquire_amplitude_scan,
    _acquire_valid_rxy_point,
    _configure_y_rf_output,
    _reacquire_phase_outliers_once,
    _rearm_y_rf,
    _rxy_summary_arrays,
)
from .models import MxKeithley6221OptimalControlRFParams
from .phase import (
    fit_dispersion_phase_scan,
    fit_phase_scan,
    paired_phase_order,
)
from .phase_plot import plot_phase_calibration
from .sources import (
    AppliedCurrentWaveform,
    KeithleyCalibrationSource,
    TheoryControlSource,
    build_applied_current,
    load_keithley_calibration,
    load_theory_control,
)


EXPERIMENT_ID = "mx-keithley-6221-optimal-control-rf-sensitivity"
DATA_TYPE = "Mx_Keithley_6221_Optimal_Control_RF_Sensitivity"
EXECUTION_MODE = "typed_workflow"


class _Cancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
    devices: dict[str, Any] | None = None,
    channels: dict[str, int] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    devices = {} if devices is None else devices
    channels = {} if channels is None else channels
    gs = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect("gs200", gs["resource"], lambda: GS200Instrument(gs["resource"]))
    kcfg = mapping["keithley_6221_main_field"]
    devices["keithley"] = session.connect("keithley", kcfg["resource"], lambda: Keithley6221Instrument(kcfg["resource"]))
    trigger = mapping["Time_sequence_2"]
    devices["trigger"] = session.connect("trigger", trigger["resource"], lambda: create_signal_generator(trigger))
    channels["trigger"] = int(trigger["channel"])

    xcfg = mapping["X_magnetic_field"]
    rfcfg = mapping["rf_coil"]
    devices["xy_field"], routed = connect_signal_generator_routes(session, "xy_field", {"x_field": ("X_magnetic_field", xcfg), "y_rf": ("rf_coil", rfcfg)})
    channels.update(routed)
    lcfg = mapping["Pump_laser_power"]
    pcfg = mapping["Probe_laser_power"]
    devices["laser"], routed = connect_signal_generator_routes(session, "laser", {"pump_laser": ("Pump_laser_power", lcfg), "probe_laser": ("Probe_laser_power", pcfg)})
    channels.update(routed)
    ccfg = mapping["Pump_modulation"]
    gcfg = mapping["Time_sequence"]
    devices["pump_rf"], routed = connect_signal_generator_routes(session, "pump_rf", {"pump_carrier": ("Pump_modulation", ccfg), "pump_gate": ("Time_sequence", gcfg)})
    channels.update(routed)
    tcfg = mapping["Temp_Switch"]
    devices["temp_switch"] = session.connect("temp_switch", tcfg["resource"], lambda: create_signal_generator(tcfg))
    channels["temp_switch"] = int(tcfg["channel"])
    hcfg = mapping["lockin_r"]
    devices["hf2"] = session.connect("hf2", f"hf2://{hcfg.get('host', '127.0.0.1')}/{hcfg['device_id']}", lambda: HF2Instrument(host=hcfg.get("host", "127.0.0.1"), port=int(hcfg.get("port", 8005)), api_level=1, device_id=hcfg["device_id"]))
    tcfg = mapping["temperature"]
    devices["tec"] = session.connect_optional("tec", tcfg["resource"], lambda: TECInstrument(port=tcfg["resource"]), device_label="TEC103")
    return devices, channels


def _configure_clocks(devices: dict[str, Any], mapping: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    records = synchronize_connected_clocks(devices, mapping, {"trigger": "Time_sequence_2", "xy_field": "rf_coil", "laser": "Pump_laser_power", "pump_rf": "Pump_modulation", "temp_switch": "Temp_Switch", "hf2": "lockin_r"})
    return {name: {"target": str(record["target"]), "actual": str(record["actual"])} for name, record in records.items()}


def _configure_6221(
    source: Keithley6221Instrument,
    theory: TheoryControlSource,
    applied: AppliedCurrentWaveform,
    *,
    current_range_ma: float,
    compliance_v: float,
) -> float:
    """共享步骤薄包装：按理论波形配置 6221 ARB0 外触发最优控制。"""
    return configure_keithley_6221_optimal_control(
        source,
        theory,
        applied,
        current_range_ma=current_range_ma,
        compliance_v=compliance_v,
    )


def _check_6221_compliance(source: Any, context: str) -> None:
    """共享步骤薄包装：检查 Compliance 并在命中时安全关断。"""
    check_keithley_6221_compliance(source, context)


def _install_compliance_checker(devices: dict[str, Any]) -> None:
    source = devices["keithley"]
    devices["_compliance_checker"] = lambda context: _check_6221_compliance(source, context)


def _configure_hf2(params: MxKeithley6221OptimalControlRFParams, devices: dict[str, Any], frequency_hz: float) -> tuple[float, dict[str, Any]]:
    """配置 HF2 Demod0，保持原 RF 灵敏度采集的解调设置。"""
    hf2 = devices["hf2"]
    demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.hf2_signal_range_v, ac_coupling=True, diff=False, impedance=50))
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=frequency_hz))
    phase_shift_deg = float(hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift"))
    actual_rate = float(demod.configure_demodulator(hf2, DemodulatorConfig(demod_index=params.demod_idx, enable=True, rate=params.response_rate_sa_s, input_channel=0, osc_select=params.demod_osc_idx, harmonic=1, time_constant=params.response_time_constant_s, order=params.response_demod_order, phase=phase_shift_deg)))
    return actual_rate, {"demod_idx": params.demod_idx, "oscillator_idx": params.demod_osc_idx, "oscillator_frequency_hz": frequency_hz, "requested_response_rate_sa_s": params.response_rate_sa_s, "actual_response_rate_sa_s": actual_rate, "response_time_constant_s": params.response_time_constant_s, "response_order": params.response_demod_order, "phase_shift_deg": phase_shift_deg}


def _acquire_phase_scan(
    params: MxKeithley6221OptimalControlRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> tuple[float, dict[str, Any]]:
    """Y RF 触发相位校准：扫描 Y RF 相位，用 Demod R 信号拟合 |色散| 折叠模型。

    6221 变体固定使用 Y RF 校相（不支持控制波形相位扫描）；采集同步记录
    R/X/Y 并做跨相位离群重采。相位选择依赖 R 的物理复合模型
    R = |scale*(B-b0)/((B-b0)^2+w^2)|，B(phi) = |A e^{i phi} + C e^{i phi_c}|：
    改变相位即改变 Y RF 与剩磁射频成分合成的等效幅度，响应为色散线形的
    绝对值折叠；另保留 C + A|sin(phi-phi0)| 作为诊断对照模型。
    """
    axis = paired_phase_order(params.phase_axis_deg())
    mode = "y_rf_r_phase_calibration"
    phase_target = "y_rf_burst"
    summaries: list[dict[str, float]] = []
    attempts: list[int] = []
    files: list[str] = []
    dummy_file: str | None = None
    rf = devices["xy_field"]
    dummy_phase = float(axis[0])

    def set_dummy_phase() -> None:
        _rearm_y_rf(
            rf,
            channels["y_rf"],
            amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
            phase_deg=dummy_phase,
            offset_v=params.y_rf_offset_v,
        )

    print(
        "Y RF 校相 dummy 点: "
        f"phase={dummy_phase:.3f} deg（采集后丢弃）"
    )
    _, _, dummy_file = _acquire_valid_rxy_point(
        params,
        run_dir,
        devices,
        channels,
        file_stem="phase_dummy",
        metadata={
            "scanned_phase_deg": np.float64(dummy_phase),
            "y_rf_burst_phase_deg": np.float64(dummy_phase),
            "y_rf_amplitude_vpp": np.float64(
                params.phase_cal_rf_amplitude_vpp
            ),
            "y_rf_dc_offset_v": np.float64(params.y_rf_offset_v),
            "y_rf_output_on": np.uint8(True),
            "discarded_dummy": np.uint8(True),
        },
        set_y_rf=set_dummy_phase,
        settle_time_s=params.response_settle_time_s,
        duration_s=params.response_duration_s,
        actual_rate=actual_rate,
        device_id=device_id,
    )

    for index, phase_deg in enumerate(axis):
        check_cancelled()
        print(
            f"Y RF 校相 {index + 1}/{axis.size}: "
            f"phase={phase_deg:.3f} deg"
        )
        metadata = {
            "scanned_phase_deg": np.float64(phase_deg),
            "y_rf_burst_phase_deg": np.float64(phase_deg),
            "y_rf_amplitude_vpp": np.float64(
                params.phase_cal_rf_amplitude_vpp
            ),
            "y_rf_dc_offset_v": np.float64(params.y_rf_offset_v),
            "y_rf_output_on": np.uint8(True),
        }

        def set_phase(
            phase_deg: float = float(phase_deg),
        ) -> None:
            _rearm_y_rf(
                rf,
                channels["y_rf"],
                amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
                phase_deg=phase_deg,
                offset_v=params.y_rf_offset_v,
            )

        summary, attempt, filename = _acquire_valid_rxy_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"phase_{index:04d}",
            metadata=metadata,
            set_y_rf=set_phase,
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        attempts.append(attempt)
        files.append(filename)

    (
        cross_phase_report,
        initial_attempts,
        initial_files,
        cross_phase_reacquired,
    ) = _reacquire_phase_outliers_once(
        params,
        run_dir,
        devices,
        channels,
        axis,
        summaries,
        attempts,
        files,
        actual_rate=actual_rate,
        device_id=device_id,
    )

    r_mean = np.asarray(
        [item["r_scalar_mean_v"] for item in summaries],
        dtype=float,
    )
    r_std = np.asarray([item["r_std_v"] for item in summaries], dtype=float)
    x_mean, y_mean, complex_std = _rxy_summary_arrays(summaries)
    x_std = np.asarray(
        [item["x_std_v"] for item in summaries],
        dtype=float,
    )
    y_std = np.asarray(
        [item["y_std_v"] for item in summaries],
        dtype=float,
    )

    primary = fit_dispersion_phase_scan(
        axis,
        r_mean,
        r_std,
        y_rf_amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
        r_squared_min=params.phase_fit_r_squared_min,
        amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
    )
    _, diagnostic = fit_phase_scan(
        axis,
        r_mean,
        r_squared_min=params.phase_fit_r_squared_min,
        amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
    )
    final_outlier_phases = cross_phase_report["final_detection"][
        "outlier_phase_deg"
    ]
    if final_outlier_phases:
        reason = (
            "单次重采后仍存在跨相位异常点: "
            + ", ".join(
                f"{float(value):.6g}°"
                for value in final_outlier_phases
            )
        )
        primary = replace(
            primary,
            success=False,
            rejection_reasons=(*primary.rejection_reasons, reason),
        )

    phase_arrays: dict[str, Any] = {
        "scanned_phase_deg": axis,
        "y_rf_burst_phase_deg": axis,
        "r_mean_v": r_mean,
        "r_std_v": r_std,
        "x_mean_v": x_mean,
        "x_std_v": x_std,
        "y_mean_v": y_mean,
        "y_std_v": y_std,
        "complex_std_v": complex_std,
        "cross_phase_initial_residual_v": np.asarray(
            cross_phase_report["initial_detection"]["residual_v"],
            dtype=float,
        ),
        "cross_phase_final_residual_v": np.asarray(
            cross_phase_report["final_detection"]["residual_v"],
            dtype=float,
        ),
        "accepted_attempt_index": np.asarray(attempts, dtype=int),
        "accepted_file": np.asarray(files),
        "initial_accepted_attempt_index": np.asarray(
            initial_attempts,
            dtype=int,
        ),
        "initial_accepted_file": np.asarray(initial_files),
        "cross_phase_reacquired": cross_phase_reacquired,
        "actual_rate_sa_s": np.float64(actual_rate),
        "x_dc_field_v": np.float64(params.x_dc_field_v),
        "y_rf_dc_offset_v": np.float64(params.y_rf_offset_v),
    }
    payload = {
        "mode": mode,
        "phase_target": phase_target,
        "success": primary.success,
        "scan_completed": True,
        "fit_accepted": primary.success,
        "selected_phase_deg": primary.selected_phase_deg,
        "selected_y_rf_phase_deg": primary.selected_phase_deg,
        "primary_fit": primary.to_dict(),
        "diagnostic_fit": diagnostic.to_dict(),
        "cross_phase_reacquisition": cross_phase_report,
        "phase_scan": {
            "start_deg": params.phase_scan_start_deg,
            "stop_deg": params.phase_scan_stop_deg,
            "step_deg": params.phase_scan_step_deg,
            "points": int(axis.size),
            "rf_amplitude_vpp": params.phase_cal_rf_amplitude_vpp,
            "rf_dc_offset_v": params.y_rf_offset_v,
            "rf_output_on": True,
            "control_restored_phase_deg": None,
            "point_value": "mean(R/X/Y)",
            "fit_signal": "R",
            "fit_model": "abs(dispersion(B_eff(phi)))",
            "acquisition_order": "phi_then_phi_plus_180",
            "discarded_dummy_file": dummy_file,
        },
    }
    np.savez(run_dir.raw / "phase_scan.npz", **phase_arrays)
    (run_dir.results / "phase_calibration.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    plot_phase_calibration(run_dir.raw, run_dir.results, payload)
    if not primary.success:
        raise RuntimeError(
            "Y RF |色散| 折叠相位拟合不合格: "
            + "；".join(primary.rejection_reasons)
        )
    return float(primary.selected_phase_deg), payload


def _configure_trigger(
    params: MxKeithley6221OptimalControlRFParams,
    device: Any,
    channel: int,
    frequency_hz: float,
    *,
    output: bool,
) -> None:
    low = params.trigger_offset_v - params.trigger_amplitude_vpp / 2.0
    high = params.trigger_offset_v + params.trigger_amplitude_vpp / 2.0
    validate_safety_limit("Time_sequence_2", low)
    validate_safety_limit("Time_sequence_2", high)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_square(
        freq=frequency_hz,
        amplitude=params.trigger_amplitude_vpp,
        offset=params.trigger_offset_v,
        dcycle=params.trigger_duty_percent,
        phase=0.0,
        channel=channel,
    )
    device.phase_init(channel=channel)
    device.set_output(output, channel=channel)


def _configure_gs200(
    gs200: GS200Instrument,
    mapping: dict[str, dict[str, Any]],
    current_ma: float,
) -> float:
    """配置 Z 主磁场 GS200；6221 只驱动独立的 Z 小磁场线圈。"""
    validate_safety_limit("main_magnetic_field", current_ma)
    config = mapping["main_magnetic_field"]
    gs200.set_output(False)
    if config.get("source_function"):
        gs200.set_source_function(str(config["source_function"]))
    gs200.set_current_limit(0.01)
    gs200.set_current(float(current_ma) / 1000.0)
    output_on = not np.isclose(float(current_ma), 0.0, atol=1e-12)
    gs200.set_output(output_on)
    actual_a = float(gs200.get_current())
    if not np.isclose(actual_a, float(current_ma) / 1000.0, rtol=1e-6, atol=1e-9):
        raise RuntimeError(
            f"GS200 主磁场设定 {current_ma:.9g} mA，回读为 {actual_a * 1000.0:.9g} mA"
        )
    if bool(gs200.get_output()) != output_on:
        raise RuntimeError(
            "GS200 主磁场输出状态与设定不一致："
            f"期望 {'ON' if output_on else 'OFF'}"
        )
    return actual_a * 1000.0


def _shutdown_6221(source: Any) -> None:
    """共享步骤薄包装：中止波形、关闭输出、归零并回读确认。"""
    shutdown_keithley_6221(source)


def safe_shutdown(devices: dict[str, Any], channels: dict[str, int], params: MxKeithley6221OptimalControlRFParams) -> SafetyShutdownReport:
    source_errors: list[str] = []
    for source in (devices.get("keithley"), devices.get("gs200")):
        if source is None:
            continue
        try:
            if source is devices.get("keithley"):
                _shutdown_6221(source)
            else:
                source.set_output(False)
                source.set_current(0.0)
                current_a = float(source.get_current())
                if abs(current_a) > 1e-12 or bool(source.get_output()):
                    raise RuntimeError(
                        f"GS200 回读为 {current_a:.9g} A、输出 "
                        f"{'ON' if source.get_output() else 'OFF'}"
                    )
        except Exception as exc:
            source_errors.append(str(exc))
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("trigger") is not None and "trigger" in channels:
        dg_channels.append(DGChannelShutdown(devices["trigger"], channels["trigger"], "Time_sequence_2", "6221 共同触发"))
    if devices.get("xy_field") is not None:
        for name, key, label in (("x_field", "X_magnetic_field", "X 磁场"), ("y_rf", "rf_coil", "Y RF 场")):
            if name in channels:
                dg_channels.append(DGChannelShutdown(devices["xy_field"], channels[name], key, label))
    extras: list[ShutdownAction] = []
    if devices.get("pump_rf") is not None and "pump_gate" in channels:
        extras.append(ShutdownAction("保持 Pump RF 开关失败", lambda: _set_pump_gate_on(devices["pump_rf"], channels["pump_gate"], params.pump_gate_voltage_v)))
    if devices.get("pump_rf") is not None and "pump_carrier" in channels:
        def keep_pump_carrier_on() -> None:
            validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
            devices["pump_rf"].setup_sine(
                params.pump_carrier_frequency_hz,
                params.pump_carrier_amplitude_vpp,
                offset=0.0,
                phase=0.0,
                channel=channels["pump_carrier"],
            )
            devices["pump_rf"].set_output(True, channel=channels["pump_carrier"])

        extras.append(ShutdownAction("保持 Pump 100MHz 载波开启失败", keep_pump_carrier_on))
    temperature = TemperatureSwitchRestore(devices["temp_switch"], channels["temp_switch"]) if devices.get("temp_switch") is not None and "temp_switch" in channels else None
    preserved = tuple(
        key for key in STANDARD_PRESERVED_OUTPUTS if key != "main_magnetic_field"
    )
    report = run_safety_shutdown(dg_channels=dg_channels, temperature_switch=temperature, extra_actions=extras, disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),), preserved_outputs=(*preserved, "Time_sequence"))
    return SafetyShutdownReport(action_errors=(*source_errors, *report.action_errors), disconnect_errors=report.disconnect_errors, preserved_outputs=report.preserved_outputs)


def _source_snapshot(
    run_dir: Any,
    theory: TheoryControlSource,
    calibration: KeithleyCalibrationSource,
    applied: AppliedCurrentWaveform,
    current_range: Keithley6221CurrentRangeCheck,
) -> list[str]:
    files = []
    for source, name in ((theory.waveform_path, "source_optimal_control_waveform.csv"), (theory.parameter_path, "source_optimal_control_params.csv"), (calibration.analysis_path, "source_keithley_calibration_analysis.yaml")):
        target = run_dir.raw / name
        shutil.copy2(source, target)
        files.append(f"raw/{name}")
    np.savez(run_dir.raw / "applied_control_waveform.npz", time_s=theory.time_s, omega_ctrl_hz=theory.omega_ctrl_hz, current_ma=applied.current_ma, normalized=applied.normalized, repeat_frequency_hz=np.float64(theory.repeat_frequency_hz), calibration_slope_hz_per_ma=np.float64(calibration.slope_hz_per_ma), calibration_intercept_hz=np.float64(calibration.intercept_hz), current_minimum_ma=np.float64(applied.minimum_ma), current_maximum_ma=np.float64(applied.maximum_ma), current_amplitude_peak_ma=np.float64(applied.amplitude_peak_ma), current_offset_ma=np.float64(applied.offset_ma), selected_current_range_ma=np.float64(current_range.selected_range_ma), required_current_range_ma=np.float64(current_range.required_peak_ma), current_range_margin_ma=np.float64(current_range.margin_ma), current_range_utilization_fraction=np.float64(current_range.utilization_fraction), current_range_satisfied=np.bool_(current_range.satisfies))
    files.append("raw/applied_control_waveform.npz")
    return files


def run(params: MxKeithley6221OptimalControlRFParams) -> Path:
    root = find_project_root()
    if not params.confirm_gs200_connected:
        raise ValueError("必须确认 GS200 已接入 Z 主磁场线圈（6221 接 Z 小磁场线圈）")
    mapping = load_mapping(root)
    theory = load_theory_control(Path(params.control_results_root), params.control_version)
    calibration = load_keithley_calibration(root, params.keithley_calibration_source_run)
    applied = build_applied_current(theory, calibration, params.control_scale)
    current_range = require_keithley_6221_current_range(
        params.keithley_current_range_ma,
        applied.minimum_ma,
        applied.maximum_ma,
    )
    warnings: list[str] = []
    if not np.isclose(
        params.y_rf_frequency_hz,
        theory.theory_rf_frequency_hz,
        rtol=1e-9,
        atol=1e-6,
    ):
        warning = (
            "Y RF/HF2 频率与理论 rf 频率不同；共同触发只固定采集起始相位，"
            "采集期间相对相位按频差演化"
        )
        warnings.append(warning)
        print(f"[WARN] {warning}")
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=params.schema_version, project_root=root)
    source_files = _source_snapshot(
        run_dir,
        theory,
        calibration,
        applied,
        current_range,
    )
    inactive_normalized = float(-applied.offset_ma / applied.amplitude_peak_ma)
    gs200_output = "ON" if not np.isclose(params.main_magnetic_field_ma, 0.0) else "OFF"
    run_dir.update_config(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE, measurement_mode="rf_sensitivity", geometry={"control_field": "Keithley 6221 Z small-field coil", "main_field": "GS200 Z main-field coil", "gs200_main_field_current_ma": params.main_magnetic_field_ma, "gs200_output": gs200_output, "gs200_physical_connection_confirmed": params.confirm_gs200_connected, "control_current_min_ma": applied.minimum_ma, "control_current_max_ma": applied.maximum_ma}, control_source={"version": theory.version, "repeat_frequency_hz": theory.repeat_frequency_hz, "theory_rf_frequency_hz": theory.theory_rf_frequency_hz, "rf_periods_per_waveform": theory.rf_periods_per_waveform, "y_rf_frequency_hz": params.y_rf_frequency_hz, "formula": "I_mA(t) = CONTROL_SCALE * (Omega_ctrl_Hz(t) - f_0mA_Hz) / K_f_Hz_per_mA"}, keithley_calibration={"source_run": calibration.run_name, "slope_hz_per_ma": calibration.slope_hz_per_ma, "intercept_hz": calibration.intercept_hz, "r_squared": calibration.r_squared}, keithley_source_configuration={"coil": "Z small-field coil", "range_ma": params.keithley_current_range_ma, "range_a": params.keithley_current_range_ma / 1000.0, "autorange": False, "response": params.keithley_output_response, "analog_filter": False, "compliance_v": params.keithley_compliance_v}, gs200_source_configuration={"coil": "Z main-field coil", "setpoint_ma": params.main_magnetic_field_ma, "output": gs200_output}, trigger={"source": "Time_sequence_2", "frequency_hz": theory.repeat_frequency_hz, "slope": "NEGative", "keithley_line": 1, "ignore": "OFF", "inactive_value_normalized": inactive_normalized, "inactive_current_ma": 0.0, "wiring": "Time_sequence_2 CH2 split to 6221 Line 1 and Y RF DG4000 Ext Trig; 6221 Line 2 unused"}, applied_control={"minimum_ma": applied.minimum_ma, "maximum_ma": applied.maximum_ma, "amplitude_peak_ma": applied.amplitude_peak_ma, "offset_ma": applied.offset_ma, "points": int(applied.current_ma.size), "current_range_check": current_range.to_dict()}, warnings=warnings, acquisition_signals=["Demod0 R/X/Y during phase calibration", "Demod0 R during amplitude/noise acquisition"])
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    connection_complete = False
    try:
        _connect_devices(mapping, session, devices, channels)
        connection_complete = True
        _install_compliance_checker(devices)
        _configure_trigger(params, devices["trigger"], channels["trigger"], theory.repeat_frequency_hz, output=False)
        actual_gs200_ma = _configure_gs200(
            devices["gs200"], mapping, params.main_magnetic_field_ma
        )
        run_dir.update_config(actual_gs200_main_field_ma=actual_gs200_ma)
        xy = devices["xy_field"]
        configure_fixed_dc_field(xy, channels["x_field"], "X_magnetic_field", params.x_dc_field_v)
        _configure_y_rf_output(params, xy, channels["y_rf"])
        laser = devices["laser"]
        laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"]); laser.set_output(True, channel=channels["pump_laser"])
        laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"]); laser.set_output(True, channel=channels["probe_laser"])
        pump = devices["pump_rf"]
        pump.setup_sine(params.pump_carrier_frequency_hz, params.pump_carrier_amplitude_vpp, offset=0.0, phase=0.0, channel=channels["pump_carrier"]); pump.set_output(True, channel=channels["pump_carrier"])
        _set_pump_gate_on(pump, channels["pump_gate"], params.pump_gate_voltage_v)
        set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
        configure_temperature_control(devices.get("tec"), params.temperature_c, channel=1, tolerance_c=params.temperature_tolerance_c, stable_reads=params.temperature_stable_reads, poll_interval_s=params.temperature_poll_interval_s, timeout_s=params.temperature_timeout_s, cancellation=_Cancellation(), stability_waiter=wait_for_temperature_stable)
        clocks = _configure_clocks(devices, mapping)
        configured_inactive = _configure_6221(
            devices["keithley"],
            theory,
            applied,
            current_range_ma=params.keithley_current_range_ma,
            compliance_v=params.keithley_compliance_v,
        )
        _configure_trigger(params, devices["trigger"], channels["trigger"], theory.repeat_frequency_hz, output=True)
        actual_rate, hf2_snapshot = _configure_hf2(params, devices, params.y_rf_frequency_hz)
        device_id = str(mapping["lockin_r"]["device_id"])
        selected_phase, phase_payload = _acquire_phase_scan(params, run_dir, devices, channels, actual_rate, device_id)
        run_dir.update_config(clock_sources=clocks, hf2_configuration=hf2_snapshot, phase_calibration=phase_payload, selected_y_rf_phase_deg=selected_phase, trigger_inactive_value_normalized=configured_inactive, trigger_inactive_current_ma=0.0)
        _acquire_amplitude_scan(params, run_dir, devices, channels, actual_rate, device_id, selected_phase)
        actual_noise_rate = _acquire_noise(params, run_dir, devices, channels, device_id, set_y_rf_off_state=lambda: configure_fixed_dc_field(xy, channels["y_rf"], "Y_magnetic_field", params.y_rf_offset_v), y_rf_dc_v=params.y_rf_offset_v, y_rf_output_on=params.y_rf_offset_v != 0.0)
        completion_status = "completed"
        run_dir.update_config(completion_status=completion_status, failure_reason=None, actual_rates={"response_sa_s": actual_rate, "noise_sa_s": actual_noise_rate}, data_files=[*source_files, "raw/phase_scan.npz", "raw/amplitude_scan.npz", *[f"raw/noise_{i:03d}.npz" for i in range(params.noise_n_avg)]])
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists(): run_dir.update_config(completion_status="failed", failure_reason=failure_reason)
        raise
    finally:
        report = safe_shutdown(devices, channels, params)
        if not connection_complete:
            session.cleanup_connection_failure()
        shutdown_errors = report.errors
        final_failure_reason = failure_reason
        if shutdown_errors:
            suffix = "安全关断错误: " + "；".join(shutdown_errors)
            final_failure_reason = f"{failure_reason}；{suffix}" if failure_reason else suffix
        final_status = "failed" if shutdown_errors else completion_status
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=final_status,
                failure_reason=final_failure_reason,
                safety_shutdown=report.to_dict(),
            )


def main() -> int:
    run(load_runtime_params(MxKeithley6221OptimalControlRFParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
