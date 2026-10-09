"""静磁场灵敏度工作流：直流色散与原始噪声采集，不执行离线拟合。"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from gs200 import GS200Instrument
from lockin_amplifier import (
    DAQConfig, DemodulatorConfig, HF2Instrument, OscillatorConfig, SignalInputConfig, daq, demod,
)
from tec_controller import TECInstrument

from ...common import WorkflowCancelled, load_mapping, load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...steps import (
    DGChannelShutdown, DeviceSession, DisconnectTarget, PhaseCalibrationConfig, ShutdownAction,
    STANDARD_PRESERVED_OUTPUTS, TemperatureSwitchRestore, calibrate_demod_phase,
    configure_fixed_dc_field, configure_temperature_control, connect_signal_generator_routes,
    create_run_directory, run_safety_shutdown, synchronize_connected_clocks,
)
from .acquisition import RuntimeCancellation, acquire_dispersion, set_temp_switch, wait_for_settle
from .models import StaticSensitivityParams
from ...steps.dispersion import dispersion_sample_count

EXPERIMENT_ID = "static-sensitivity"
DATA_TYPE = "Static_Magnetic_Field_Sensitivity"
DG_ROUTES = {
    "dg_sweep": ("Z_magnetic_field", "Time_sequence_2"),
    "dg_laser": ("Pump_laser_power", "Probe_laser_power"),
    "dg_comp": ("X_magnetic_field", "Y_magnetic_field"),
    "dg_mod": ("Pump_modulation", "Time_sequence"),
    "dg_temp": ("Temp_Switch",),
}


def connect_devices(session: DeviceSession, mapping):
    """按资源复用连接，逻辑通道由路由代理映射到实际端点。"""
    devices, channels = {}, {}
    for name, keys in DG_ROUTES.items():
        devices[name], routed = connect_signal_generator_routes(
            session, name, {key: (key, mapping[key]) for key in keys},
        )
        channels.update(routed)
    config = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect("gs200", config["resource"], lambda: GS200Instrument(config["resource"]))
    config = mapping["lockin_r"]
    devices["hf2"] = session.connect(
        "hf2", f"hf2://{config.get('host', '127.0.0.1')}/{config['device_id']}",
        lambda: HF2Instrument(host=config.get("host", "127.0.0.1"), port=config.get("port", 8005),
                              api_level=1, device_id=config["device_id"]),
    )
    config = mapping["temperature"]
    devices["tec"] = session.connect_optional(
        "tec", config["resource"], lambda: TECInstrument(port=config["resource"]), device_label="TEC103",
    )
    return devices, channels


def demod_config(params: StaticSensitivityParams, phase: float, *, noise: bool = False):
    return DemodulatorConfig(
        demod_index=params.hf2_demod_idx, enable=True,
        rate=params.hf2_noise_rate if noise else params.hf2_demod_rate,
        input_channel=0, osc_select=0, harmonic=1,
        time_constant=params.hf2_noise_tc if noise else params.hf2_demod_tc,
        order=params.hf2_demod_order, phase=phase,
    )


def hf2_snapshot(hf2, demod_idx: int):
    """与现有 Bell Bloom 标定一致，记录回读配置，不把请求值当作实际值。"""
    base = hf2.demod_path(demod_idx)
    return {
        "input": {"range": hf2.get_double(f"{hf2.sig_in_path(0)}/range"),
                  **{key: hf2.get_int(f"{hf2.sig_in_path(0)}/{key}") for key in ("ac", "diff", "imp50")}},
        "demod_idx": demod_idx,
        "demod": {**{key: hf2.get_double(f"{base}/{key}") for key in ("rate", "timeconstant", "phaseshift")},
                  **{key: hf2.get_int(f"{base}/{key}") for key in ("order", "oscselect", "harmonic", "enable", "adcselect")}},
        "oscillator_frequency_hz": hf2.get_double(f"{hf2.osc_path(0)}/freq"),
    }


def configure_outputs(params, devices, channels, mapping, directory):
    report_runtime_progress("initializing", "同步时钟并配置固定输出", 0, estimated_remaining_seconds=None)
    clocks = synchronize_connected_clocks(
        devices, mapping, {**{name: keys[0] for name, keys in DG_ROUTES.items()}, "hf2": "lockin_r"},
        settle_wait=wait_for_settle,
    )
    sweep = devices["dg_sweep"]
    for key in DG_ROUTES["dg_sweep"]:
        configure_fixed_dc_field(sweep, channels[key], key, 0.0)
        sweep.set_sync_state(False, channel=channels[key])
    for key, voltage in (("X_magnetic_field", params.x_magnetic_field), ("Y_magnetic_field", params.y_magnetic_field)):
        configure_fixed_dc_field(devices["dg_comp"], channels[key], key, voltage)
    for key, voltage in (("Pump_laser_power", params.pump_laser_power), ("Probe_laser_power", params.probe_laser_power)):
        validate_safety_limit(key, voltage)
        devices["dg_laser"].setup_dc(voltage, channel=channels[key])
    gs = devices["gs200"]
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field)
    gs.set_source_function(mapping["main_magnetic_field"]["source_function"])
    gs.set_current_limit(load_safety_limits()["main_magnetic_field"]["max"] / 1000)
    gs.set_current(params.main_magnetic_field / 1000)
    gs.set_output(True)

    temp, temp_channel = devices["dg_temp"], channels["Temp_Switch"]
    set_temp_switch(temp, temp_channel, True)
    report_runtime_progress("temperature", "等待初始化温度稳定", 0, estimated_remaining_seconds=None)
    temperature = configure_temperature_control(
        devices["tec"], params.temperature, channel=int(mapping["temperature"]["channel"]),
        tolerance_c=params.temp_tolerance_c, stable_reads=params.temp_stable_reads,
        poll_interval_s=params.temp_poll_interval_s, timeout_s=params.max_temp_wait_s,
        cancellation=RuntimeCancellation(),
    )

    pump = devices["dg_mod"]
    carrier, gate = channels["Pump_modulation"], channels["Time_sequence"]
    validate_safety_limit("Pump_modulation", params.pump_mod_amplitude)
    for voltage in (-params.pump_mod_amplitude / 2, params.pump_mod_amplitude / 2):
        validate_safety_limit("Pump_modulation", voltage)
    pump.set_burst_state(False, channel=carrier)
    pump.set_mod_state(False, channel=carrier)
    pump.setup_sine(freq=100e6, amplitude=params.pump_mod_amplitude, offset=0.0, phase=0.0, channel=carrier)
    pump.set_output(False, channel=carrier)
    pump.set_burst_mode("GATed", channel=carrier)
    pump.set_burst_trigger_source("EXTernal", channel=carrier)
    pump.set_burst_phase(0.0, channel=carrier)
    pump.set_burst_state(True, channel=carrier)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
    for voltage in (params.rf_gate_offset - params.rf_gate_amplitude / 2, params.rf_gate_offset + params.rf_gate_amplitude / 2):
        validate_safety_limit("Time_sequence", voltage)
    pump.set_burst_state(False, channel=gate)
    pump.set_mod_state(False, channel=gate)
    pump.setup_pulse(freq=params.pump_mod_freq, amplitude=params.rf_gate_amplitude,
                     offset=params.rf_gate_offset, width=params.pump_mod_duty / 100 / params.pump_mod_freq, channel=gate)
    pump.set_pulse_delay(params.rf_gate_delay, channel=gate)
    pump.set_pulse_leading("MINimum", channel=gate)
    pump.set_pulse_trailing("MINimum", channel=gate)
    pump.set_output(True, channel=carrier)

    hf2 = devices["hf2"]
    demod.configure_signal_input(hf2, SignalInputConfig(
        input_index=0, range=params.hf2_signal_range, ac_coupling=params.hf2_ac_coupling,
        diff=False, impedance=50 if params.hf2_50_ohm else 10_000,
    ))
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=0, frequency=params.pump_mod_freq, source="manual"))
    actual_rate = demod.configure_demodulator(hf2, demod_config(params, 0.0))
    directory.update_config(clock_sources=clocks, temperature_control=temperature.to_dict(),
                            actual_rates={"hf2_demod_rate_Sa_s": float(actual_rate)})
    report_runtime_progress("phase_calibration", "关闭温控并校准 HF2 相位", 0, estimated_remaining_seconds=None)
    set_temp_switch(temp, temp_channel, False)
    wait_for_settle(params.dispersion_temp_switch_off_settle_s)
    result = calibrate_demod_phase(hf2, PhaseCalibrationConfig(demod_idx=params.hf2_demod_idx),
                                   cancellation=RuntimeCancellation(), settle_wait=wait_for_settle)
    directory.update_config(phase_calibration=result.to_dict(), hf2_configuration=hf2_snapshot(hf2, params.hf2_demod_idx))
    # 异常由 run 的统一收尾恢复，避免局部恢复故障覆盖校相的原始异常。
    set_temp_switch(temp, temp_channel, True)
    wait_for_settle(params.dispersion_temp_switch_on_settle_s)
    return result.phase_shift_deg, float(actual_rate)


def acquire_noise(params, devices, channels, mapping, directory, phase, dispersion_rate):
    """沿用原工作点和量程策略；只保存原始 Y，不在采集期间计算 PSD。"""
    report_runtime_progress("noise", "开始多量程 Y 噪声采集", 40, estimated_remaining_seconds=None)
    hf2 = devices["hf2"]
    rate = float(demod.configure_demodulator(hf2, demod_config(params, phase, noise=True)))
    if not math.isfinite(rate) or rate <= 0 or int(rate * params.noise_duration) < 2:
        raise ValueError("HF2 实际噪声采样率无效或采集样本不足")
    directory.update_config(
        actual_rates={"hf2_demod_rate_Sa_s": dispersion_rate, "hf2_noise_rate_Sa_s": rate},
        hf2_noise_configuration=hf2_snapshot(hf2, params.hf2_demod_idx), gs200_range_measurements=[],
    )
    wait_for_settle(0.3)
    configure_fixed_dc_field(devices["dg_sweep"], channels["Z_magnetic_field"], "Z_magnetic_field", 0.0)
    wait_for_settle(0.5)
    measurements = []
    total = len(params.gs200_current_ranges) * params.noise_n_avg
    started = time.monotonic()
    completed = 0
    for current_range in params.gs200_current_ranges:
        check_cancelled()
        gs = devices["gs200"]
        gs.set_current_range(current_range)
        wait_for_settle(params.gs200_range_settle_time)
        actual_range, actual_current = float(gs.get_current_range()), float(gs.get_current())
        if not math.isfinite(actual_range) or not math.isfinite(actual_current) or not math.isclose(actual_range, current_range, rel_tol=1e-6):
            raise RuntimeError("GS200 实际量程与请求值不一致或回读无效")
        name = f"gs200_range_{current_range * 1000:g}mA"
        noise_dir = directory.raw / "noise_raw" / name
        noise_dir.mkdir(parents=True, exist_ok=True)
        measurement = dict(directory=name, current_range_set_A=current_range,
                           current_range_actual_A=actual_range, current_setpoint_actual_A=actual_current,
                           samples_saved=0)
        measurements.append(measurement)
        directory.update_config(gs200_range_measurements=measurements)
        for index in range(params.noise_n_avg):
            check_cancelled()
            set_temp_switch(devices["dg_temp"], channels["Temp_Switch"], False)
            wait_for_settle(0.5)
            config = DAQConfig(device=mapping["lockin_r"]["device_id"], trigger_type=0,
                              duration=params.noise_duration, grid_cols=int(rate * params.noise_duration),
                              grid_rows=1, grid_mode=2, signal_paths=["sample.y"])
            acquired = daq.acquire_data(hf2, config=config, demod_idx=params.hf2_demod_idx,
                                       actual_rate=rate, timeout=params.noise_duration + 5,
                                       cancellation_check=check_cancelled, strict_cleanup=True)
            waveform = next((np.asarray(item.values) for item in acquired if item.signal_name == "sample.y"), None)
            if waveform is None:
                raise ValueError("HF2 DAQ 未返回 sample.y")
            np.save(noise_dir / f"noise_{index:04d}.npy", waveform)
            measurement["samples_saved"] += 1
            directory.update_config(gs200_range_measurements=measurements)
            if waveform.ndim != 1 or waveform.size < 2 or not np.isfinite(waveform).all():
                raise ValueError("噪声波形维度、样本数或有限性无效；原始波形已保留")
            # 故障时复用外层统一收尾，不用局部恢复异常覆盖 DAQ 原始故障。
            set_temp_switch(devices["dg_temp"], channels["Temp_Switch"], True)
            wait_for_settle(2.0)
            completed += 1
            report_runtime_progress(
                "noise", f"噪声 {name}，{index + 1}/{params.noise_n_avg}", 40 + 60 * completed / total,
                estimated_remaining_seconds=(time.monotonic() - started) / completed * (total - completed),
            )


def safe_shutdown(params, devices, channels, restore_phase):
    actions = () if restore_phase is None else (ShutdownAction(
        "恢复 HF2 色散配置失败", lambda: demod.configure_demodulator(devices["hf2"], demod_config(params, restore_phase)),
    ),)
    return run_safety_shutdown(
        dg_channels=tuple(DGChannelShutdown(devices["dg_sweep"], channels[key], key, key, disable_sync=True)
                          for key in DG_ROUTES["dg_sweep"]),
        temperature_switch=TemperatureSwitchRestore(devices["dg_temp"], channels["Temp_Switch"]),
        extra_actions=actions, disconnect_targets=(DisconnectTarget("TEC", devices["tec"]),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence", "X_magnetic_field", "Y_magnetic_field"),
    )


def run(params: StaticSensitivityParams) -> Path:
    if any(key in os.environ for key in ("LAB_STATIC_CONFIG", "LAB_STATIC_ACQUISITION_ONLY")):
        raise ValueError("旧 LAB_STATIC_* 桥接已移除，请清除这些环境变量，使用 params/experiments/static-sensitivity.yaml")
    errors = params.validate()
    if errors:
        raise ValueError("；".join(errors))
    mapping = load_mapping()
    directory = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=params.schema_version)
    directory.update_config(
        experiment_id=EXPERIMENT_ID, execution_mode="typed_workflow", acquisition_method="dc_step",
        completion_status="running", analysis_status="not_started", started_at=datetime.now().astimezone().isoformat(),
        temperature_switch_off_mode="output_off", noise_z_condition="zero_dc_output_off",
        purpose="dispersion_and_sensitivity",
    )
    session = DeviceSession()
    report_runtime_progress("connecting", "连接实验设备", 0, estimated_remaining_seconds=None)
    try:
        check_cancelled()
        devices, channels = connect_devices(session, mapping)
    except BaseException as exc:
        session.cleanup_connection_failure()
        status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
        directory.update_config(completion_status=status, failure_reason=str(exc), finished_at=datetime.now().astimezone().isoformat())
        report_runtime_progress(status, f"连接结束：{status}", estimated_remaining_seconds=None)
        raise
    status, reason, phase = "failed", "", None
    failed = False
    try:
        directory.update_config(optional_connection_errors=session.optional_connection_errors())
        phase, rate = configure_outputs(params, devices, channels, mapping, directory)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("HF2 实际色散采样率无效")
        count = dispersion_sample_count(rate, params.dispersion_duration_s)
        directory.update_config(dispersion_acquisition={
            "method": "hf2_daq_exact", "raw_schema_version": 3,
            "requested_rate_sa_s": params.hf2_demod_rate, "actual_rate_sa_s": rate,
            "requested_duration_s": params.dispersion_duration_s,
            "actual_duration_s": count / rate, "samples_per_point": count,
        })
        report_runtime_progress("dispersion", "开始 Z 直流逐点色散", 0, estimated_remaining_seconds=None)
        acquire_dispersion(params, devices["dg_sweep"], channels["Z_magnetic_field"],
                           devices["dg_temp"], channels["Temp_Switch"], devices["hf2"], directory.raw / "dispersion_scan.npz")
        acquire_noise(params, devices, channels, mapping, directory, phase, rate)
        status = "completed"
    except BaseException as exc:
        failed = True
        status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
        reason = str(exc)
        raise
    finally:
        report_runtime_progress("cleanup", "恢复温控、Z 与 HF2 配置", estimated_remaining_seconds=None)
        report = safe_shutdown(params, devices, channels, phase)
        if report.errors:
            print("安全恢复失败：" + "；".join(report.errors), flush=True)
            if status == "completed":
                status, reason = "failed", "；".join(report.errors)
        try:
            directory.update_config(
                completion_status=status, failure_reason=reason, safety_shutdown=report.to_dict(),
                finished_at=datetime.now().astimezone().isoformat(),
                data_files=sorted(path.relative_to(directory.root).as_posix() for path in directory.raw.rglob("*") if path.is_file()),
            )
        except Exception as exc:
            if not failed:
                raise
            print(f"结束状态保存失败：{exc}", flush=True)
        report_runtime_progress(status, f"采集结束：{status}", estimated_remaining_seconds=None)
        if report.errors and not failed:
            raise RuntimeError("安全结束失败：" + "；".join(report.errors))
    return directory.root


def main() -> int:
    run(load_runtime_params(StaticSensitivityParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
