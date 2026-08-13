"""Mx Z 最优控制实验共享步骤。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml
from lockin_amplifier import (
    DemodulatorConfig,
    OscillatorConfig,
    SignalInputConfig,
    demod,
)

from ..common import validate_safety_limit
from .arbitrary import ArbitraryWaveformSpec, upload_arbitrary
from .temperature import configure_temperature_control, set_temperature_switch


OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE = "NEGative"


def validate_z_trigger_mapping(
    mapping: dict[str, dict[str, Any]],
) -> int:
    """确认共同触发与 Z 控制同机，并返回触发通道。"""
    z_cfg = mapping["Z_magnetic_field"]
    trigger_cfg = mapping["Time_sequence_2"]
    if z_cfg["resource"] != trigger_cfg["resource"]:
        raise ValueError(
            "Z_magnetic_field 与 Time_sequence_2 必须位于同一台 DG4000"
        )
    return int(trigger_cfg["channel"])


def save_optimal_control_source_snapshot(
    raw_dir: Path,
    theory: Any,
    calibration: Any,
    applied: Any,
) -> list[str]:
    """复制外部控制输入，并保存实际下发的 Z 波形。"""
    waveform_copy = raw_dir / "source_optimal_control_waveform.csv"
    parameter_copy = raw_dir / "source_optimal_control_params.csv"
    calibration_copy = raw_dir / "source_z_calibration_analysis.yaml"
    shutil.copy2(theory.waveform_path, waveform_copy)
    shutil.copy2(theory.parameter_path, parameter_copy)
    shutil.copy2(calibration.analysis_path, calibration_copy)
    source_manifest = {
        "control_version": theory.version,
        "files": {
            waveform_copy.name: {
                "source": str(theory.waveform_path),
                "sha256": theory.waveform_sha256,
            },
            parameter_copy.name: {
                "source": str(theory.parameter_path),
                "sha256": theory.parameter_sha256,
            },
            calibration_copy.name: {
                "source": str(calibration.analysis_path),
                "sha256": calibration.analysis_sha256,
            },
        },
        "control_conversion": {
            "formula": (
                "V_Z(t) = CONTROL_SCALE * Omega_ctrl_Hz(t) "
                "/ K_Z_Hz_per_V"
            ),
            "calibration_slope_hz_per_v": calibration.slope_hz_per_v,
            "calibration_intercept_hz": calibration.intercept_hz,
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
            "minimum_v": applied.minimum_v,
            "maximum_v": applied.maximum_v,
            "output_minimum_v": applied.output_minimum_v,
            "output_maximum_v": applied.output_maximum_v,
            "max_abs_normalized": applied.max_abs_normalized,
        },
    }
    (raw_dir / "source_manifest.yaml").write_text(
        yaml.safe_dump(source_manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    np.savez(
        raw_dir / "applied_control_waveform.npz",
        time_s=theory.time_s,
        omega_ctrl_hz=theory.omega_ctrl_hz,
        voltage_v=applied.voltage_v,
        normalized=applied.normalized,
        repeat_frequency_hz=np.float64(theory.repeat_frequency_hz),
        calibration_slope_hz_per_v=np.float64(
            calibration.slope_hz_per_v
        ),
        calibration_intercept_hz=np.float64(
            calibration.intercept_hz
        ),
        amplitude_vpp=np.float64(applied.amplitude_vpp),
        offset_v=np.float64(applied.offset_v),
        output_minimum_v=np.float64(applied.output_minimum_v),
        output_maximum_v=np.float64(applied.output_maximum_v),
        max_abs_normalized=np.float64(applied.max_abs_normalized),
    )
    return [
        "raw/source_optimal_control_waveform.csv",
        "raw/source_optimal_control_params.csv",
        "raw/source_z_calibration_analysis.yaml",
        "raw/source_manifest.yaml",
        "raw/applied_control_waveform.npz",
    ]


def configure_optimal_control_trigger(
    params: Any,
    device: Any,
    channel: int,
    *,
    output: bool,
) -> None:
    """配置 Time_sequence_2 共同触发方波。"""
    low = params.trigger_offset_v - params.trigger_amplitude_vpp / 2.0
    high = params.trigger_offset_v + params.trigger_amplitude_vpp / 2.0
    validate_safety_limit("Time_sequence_2", low)
    validate_safety_limit("Time_sequence_2", high)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_square(
        freq=params.trigger_frequency_hz,
        amplitude=params.trigger_amplitude_vpp,
        offset=params.trigger_offset_v,
        dcycle=params.trigger_duty_percent,
        phase=0.0,
        channel=channel,
    )
    device.phase_init(channel=channel)
    device.set_output(bool(output), channel=channel)


def configure_z_optimal_control_output(
    params: Any,
    device: Any,
    channel: int,
    theory: Any,
    applied: Any,
) -> None:
    """上传并配置由外部下降沿启动的 Z 最优控制波形。"""
    for value in (
        applied.minimum_v,
        applied.maximum_v,
        applied.output_minimum_v,
        applied.output_maximum_v,
    ):
        validate_safety_limit("Z_magnetic_field", value)
    upload_arbitrary(
        device,
        ArbitraryWaveformSpec(
            values=applied.normalized,
            frequency=theory.repeat_frequency_hz,
            amplitude=applied.amplitude_vpp,
            offset=applied.offset_v,
            phase=0.0,
            channel=channel,
            output=False,
        ),
    )
    # DG4162 的 APPLy:USER 只可靠切换 USER 波形，参数需显式写入。
    device.set_frequency(theory.repeat_frequency_hz, channel=channel)
    device.set_amplitude(applied.amplitude_vpp, channel=channel)
    device.set_offset(applied.offset_v, channel=channel)
    device.set_burst_state(True, channel=channel)
    device.set_burst_mode("INFinity", channel=channel)
    device.set_burst_trigger_source("EXTernal", channel=channel)
    device.set_burst_trigger_slope(
        OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
        channel=channel,
    )
    device.set_burst_phase(
        params.control_burst_phase_deg % 360.0,
        channel=channel,
    )
    device.set_output(True, channel=channel)


def configure_main_field(
    params: Any,
    gs200: Any,
    main_field_mapping: dict[str, Any],
) -> None:
    """按配置设置 GS200；零电流时保持输出关闭。"""
    current_ma = float(params.main_magnetic_field_ma)
    validate_safety_limit("main_magnetic_field", current_ma)
    gs200.set_output(False)
    source_function = main_field_mapping.get("source_function")
    if source_function:
        gs200.set_source_function(source_function)
    gs200.set_current_limit(0.01)
    gs200.set_current(current_ma / 1000.0)
    gs200.set_output(current_ma != 0.0)


def configure_mx_z_optimal_control_workpoint(
    params: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
    theory: Any,
    applied: Any,
    *,
    demod_frequency_hz: float,
    cancellation: Any,
    xy_output_configurator: Callable[[], None],
    reference_clock_configurator: Callable[
        [dict[str, Any], dict[str, dict[str, Any]]],
        dict[str, dict[str, str]],
    ],
    pump_gate_setter: Callable[[Any, int, float], None],
    temperature_stability_waiter: Callable[..., float],
) -> tuple[float, float | None, dict[str, dict[str, str]], dict[str, Any]]:
    """配置两个 Z 最优控制实验共用的 Mx 工作点。"""
    clock_sources = reference_clock_configurator(devices, mapping)
    configure_optimal_control_trigger(
        params,
        devices["z_field"],
        channels["trigger"],
        output=False,
    )
    xy_output_configurator()
    configure_main_field(
        params,
        devices["gs200"],
        mapping["main_magnetic_field"],
    )

    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_laser_power_v)
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    validate_safety_limit("Probe_laser_power", params.probe_laser_power_v)
    laser.setup_dc(
        params.probe_laser_power_v,
        channel=channels["probe_laser"],
    )
    laser.set_output(True, channel=channels["probe_laser"])

    pump_rf = devices["pump_rf"]
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
    pump_gate_setter(
        pump_rf,
        channels["pump_gate"],
        params.pump_gate_voltage_v,
    )

    set_temperature_switch(
        devices["temp_switch"],
        True,
        channel=channels["temp_switch"],
    )
    temperature_status = configure_temperature_control(
        devices.get("tec"),
        params.temperature_c,
        channel=1,
        tolerance_c=params.temperature_tolerance_c,
        stable_reads=params.temperature_stable_reads,
        poll_interval_s=params.temperature_poll_interval_s,
        timeout_s=params.temperature_timeout_s,
        cancellation=cancellation,
        stability_waiter=temperature_stability_waiter,
    )

    hf2 = devices["hf2"]
    demod.configure_signal_input(
        hf2,
        SignalInputConfig(
            input_index=0,
            range=params.hf2_signal_range_v,
            ac_coupling=True,
            diff=False,
            impedance=50,
        ),
    )
    demod.configure_oscillator(
        hf2,
        OscillatorConfig(
            osc_index=params.demod_osc_idx,
            frequency=demod_frequency_hz,
        ),
    )
    phase_shift_deg = float(
        hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")
    )
    actual_rate = float(
        demod.configure_demodulator(
            hf2,
            DemodulatorConfig(
                demod_index=params.demod_idx,
                enable=True,
                rate=params.response_rate_sa_s,
                input_channel=0,
                osc_select=params.demod_osc_idx,
                harmonic=1,
                time_constant=params.response_time_constant_s,
                order=params.response_demod_order,
                phase=phase_shift_deg,
            ),
        )
    )

    configure_z_optimal_control_output(
        params,
        devices["z_field"],
        channels["z_field"],
        theory,
        applied,
    )
    devices["z_field"].set_output(True, channel=channels["trigger"])
    return (
        actual_rate,
        temperature_status.actual_temperature_c,
        clock_sources,
        {
            "demod_idx": params.demod_idx,
            "oscillator_idx": params.demod_osc_idx,
            "oscillator_frequency_hz": demod_frequency_hz,
            "requested_response_rate_sa_s": params.response_rate_sa_s,
            "actual_response_rate_sa_s": actual_rate,
            "response_time_constant_s": params.response_time_constant_s,
            "response_order": params.response_demod_order,
            "phase_shift_deg": phase_shift_deg,
        },
    )
