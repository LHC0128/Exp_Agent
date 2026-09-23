"""Bell Bloom Z DC × Pump 门控频率扫描，仅采集 R。"""

from datetime import datetime
from pathlib import Path
import math
import time

import numpy as np
from gs200 import GS200Instrument
from lockin_amplifier import HF2Instrument, SignalInputConfig, OscillatorConfig, DemodulatorConfig, demod
from tec_controller import TECInstrument

from ...common import WorkflowCancelled, load_mapping, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...steps import (
    DeviceSession, DGChannelShutdown, DisconnectTarget, ShutdownAction,
    STANDARD_PRESERVED_OUTPUTS, TemperatureSwitchRestore,
    configure_fixed_dc_field, configure_main_field, configure_temperature_control,
    create_run_directory, run_safety_shutdown, set_temperature_switch,
    synchronize_connected_clocks,
)
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from .definition import DATA_TYPE, EXPERIMENT_ID, MAPPING_KEYS, validate_hardware
from .models import BellBloomZFieldCalibrationParams

OFF_KEYS = ("Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field", "Time_sequence_2")
DG_KEYS = tuple(key for key in MAPPING_KEYS if key not in {"main_magnetic_field", "temperature", "lockin_r"})


class RuntimeCancellation:
    """将现有公共温控步骤接到 GUI 取消文件。"""

    @staticmethod
    def raise_if_cancelled():
        check_cancelled()


def sleep(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def connect_devices(session, mapping):
    devices = {}
    for key in DG_KEYS:
        config = mapping[key]
        devices[key] = session.connect(key, config["resource"], lambda c=config: create_signal_generator(c))
    config = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect("gs200", config["resource"], lambda: GS200Instrument(config["resource"]))
    config = mapping["lockin_r"]
    devices["hf2"] = session.connect(
        "hf2", f"hf2://{config['device_id']}",
        lambda: HF2Instrument(host=config.get("host", "127.0.0.1"), port=config.get("port", 8005),
                              api_level=1, device_id=config["device_id"]),
    )
    config = mapping["temperature"]
    devices["tec"] = session.connect_optional("tec", config["resource"], lambda: TECInstrument(port=config["resource"]), device_label="TEC103")
    return devices


def set_gate_frequency(params, gate, channel, frequency):
    """只更新脉冲频率与脉宽，读取实际值以拒绝仪器钳位。"""
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
    width = params.pump_mod_duty / 100 / frequency
    gate.set_frequency(frequency, channel=channel)
    gate.set_pulse_width(width, channel=channel)
    actual_frequency = gate.get_frequency(channel=channel)
    actual_width = gate.get_pulse_width(channel=channel)
    if not math.isclose(actual_frequency, frequency, rel_tol=1e-6) or not math.isclose(actual_width, width, rel_tol=0.01, abs_tol=1e-9):
        raise RuntimeError(f"门控回读不匹配：请求 {frequency} Hz / {width} s，实际 {actual_frequency} Hz / {actual_width} s")


def configure_outputs(params, devices, channels, mapping, directory):
    clocks = synchronize_connected_clocks(devices, mapping, {**{key: key for key in DG_KEYS}, "hf2": "lockin_r"})
    for key in OFF_KEYS:
        configure_fixed_dc_field(devices[key], channels[key], key, 0.0)
    devices["Z_magnetic_field"].set_sync_state(False, channel=channels["Z_magnetic_field"])
    configure_main_field(params, devices["gs200"], mapping["main_magnetic_field"])
    for key, voltage in (("Pump_laser_power", params.pump_laser_power_v), ("Probe_laser_power", params.probe_laser_power_v)):
        validate_safety_limit(key, voltage)
        devices[key].setup_dc(voltage, channel=channels[key])
        devices[key].set_output(True, channel=channels[key])
    set_temperature_switch(devices["Temp_Switch"], True, channel=channels["Temp_Switch"])
    temperature = configure_temperature_control(
        devices["tec"], params.temperature_c, channel=mapping["temperature"]["channel"],
        tolerance_c=params.temperature_tolerance_c, stable_reads=params.temperature_stable_reads,
        poll_interval_s=params.temperature_poll_interval_s, timeout_s=params.temperature_timeout_s,
        cancellation=RuntimeCancellation(),
    )
    carrier, carrier_channel = devices["Pump_modulation"], channels["Pump_modulation"]
    gate, gate_channel = devices["Time_sequence"], channels["Time_sequence"]
    validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
    carrier.set_output(False, channel=carrier_channel)
    carrier.set_burst_state(False, channel=carrier_channel)
    carrier.set_mod_state(False, channel=carrier_channel)
    carrier.setup_sine(freq=100e6, amplitude=params.pump_carrier_amplitude_vpp, offset=0, phase=0, channel=carrier_channel)
    carrier.set_output(False, channel=carrier_channel)
    carrier.set_burst_mode("GATed", channel=carrier_channel)
    carrier.set_burst_trigger_source("EXTernal", channel=carrier_channel)
    carrier.set_burst_phase(0.0, channel=carrier_channel)
    carrier.set_burst_state(True, channel=carrier_channel)

    initial_frequency = params.predicted_center(params.z_bias_start_v)
    validate_safety_limit("Time_sequence", 0.0)
    validate_safety_limit("Time_sequence", 5.0)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
    gate.set_output(False, channel=gate_channel)
    gate.set_burst_state(False, channel=gate_channel)
    gate.set_mod_state(False, channel=gate_channel)
    gate.setup_pulse(freq=initial_frequency, amplitude=5, offset=2.5,
                     width=params.pump_mod_duty / 100 / initial_frequency, channel=gate_channel)
    gate.set_pulse_delay(0, channel=gate_channel)
    gate.set_pulse_leading("MINimum", channel=gate_channel)
    gate.set_pulse_trailing("MINimum", channel=gate_channel)
    # 在开启门控之前验证首频点，后续每点同样回读。
    set_gate_frequency(params, gate, gate_channel, initial_frequency)
    gate.set_output(True, channel=gate_channel)
    carrier.set_output(True, channel=carrier_channel)

    hf2 = devices["hf2"]
    demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.hf2_signal_range_v, ac_coupling=True, diff=False, impedance=50))
    set_demod_frequency(hf2, initial_frequency)
    phase = hf2.get_double(f"{hf2.demod_path(0)}/phaseshift")
    actual_rate = demod.configure_demodulator(hf2, DemodulatorConfig(
        demod_index=0, enable=True, rate=params.response_rate_sa_s, input_channel=0,
        osc_select=0, harmonic=1, time_constant=params.response_time_constant_s,
        order=params.response_demod_order, phase=phase,
    ))
    if actual_rate * params.frequency_duration_s < 8:
        raise RuntimeError("HF2 实际采样率下每频点不足 8 个样本")
    snapshot = {
        "input": {"range": hf2.get_double(f"{hf2.sig_in_path(0)}/range"),
                  **{name: hf2.get_int(f"{hf2.sig_in_path(0)}/{name}") for name in ("ac", "diff", "imp50")}},
        "demod": {**{name: hf2.get_double(f"{hf2.demod_path(0)}/{name}") for name in ("rate", "timeconstant", "phaseshift")},
                  **{name: hf2.get_int(f"{hf2.demod_path(0)}/{name}") for name in ("order", "oscselect", "harmonic", "enable", "adcselect")}},
        "oscillator_frequency_hz": hf2.get_double(f"{hf2.osc_path(0)}/freq"),
    }
    directory.update_config(clock_sources=clocks, temperature_control=temperature.to_dict(),
                            actual_rates={"response_sa_s": actual_rate}, hf2_configuration=snapshot)
    return actual_rate


def set_demod_frequency(hf2, frequency):
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=0, frequency=frequency))
    if not math.isclose(hf2.get_double(f"{hf2.osc_path(0)}/freq"), frequency, rel_tol=1e-6):
        raise RuntimeError("HF2 解调频率回读与门控频率不匹配")


def acquire_point(params, devices, channels, directory, metadata, actual_rate, device_id):
    for attempt in range(params.r_point_max_attempts):
        check_cancelled()
        try:
            set_temperature_switch(devices["Temp_Switch"], False, channel=channels["Temp_Switch"])
            sleep(params.temp_switch_off_lead_s)
            sleep(params.frequency_settle_time_s)
            payload = acquire_r(devices["hf2"], device_id=device_id, demod_idx=0,
                                actual_rate_sa_s=actual_rate, duration_s=params.frequency_duration_s)
            summary = summarize_r(payload)
            accepted = summary["r_std_v"] <= params.r_bad_point_std_threshold_v
            filename = f"frequency_{metadata['frequency_index']:04d}_attempt_{attempt:02d}.npz"
            # 在恢复温控前落盘，恢复失败也不丢掉刚采集的数据。
            np.savez(directory / filename, time_s=payload["time_s"], r_v=payload["r"],
                     actual_rate_sa_s=actual_rate, attempt_index=attempt,
                     quality_accepted=accepted, **metadata, **summary)
        finally:
            set_temperature_switch(devices["Temp_Switch"], True, channel=channels["Temp_Switch"])
            time.sleep(params.temp_switch_on_lag_s)
        check_cancelled()
        if accepted:
            return summary, filename
        print(f"R 标准差 {summary['r_std_v']:.6g} V 超限，第 {attempt + 1} 次采集被拒绝")
    raise RuntimeError(f"频点 {metadata['frequency_hz']} Hz 连续 {params.r_point_max_attempts} 次 R 标准差超限")


def acquire_scan(params, devices, channels, directory, actual_rate, device_id):
    z_values = params.z_axis()
    total = len(z_values) * len(params.frequency_axis(float(z_values[0])))
    completed = 0
    started = time.monotonic()
    files, centers, finished_z = [], [], []
    for z_index, z_value in enumerate(z_values):
        check_cancelled()
        z = float(z_value)
        validate_safety_limit("Z_magnetic_field", z)
        device, channel = devices["Z_magnetic_field"], channels["Z_magnetic_field"]
        device.setup_dc(z, channel=channel)
        device.set_output(True, channel=channel)
        if device.get_shape(channel=channel).strip('"').upper() != "DC" or not device.get_output(channel=channel):
            raise RuntimeError("Z DC 模式或输出状态验证失败")
        point_dir = directory.raw / f"z_{z_index:03d}"
        point_dir.mkdir()
        frequencies = params.frequency_axis(z)
        means, stds, accepted_files = [], [], []
        for index, frequency_value in enumerate(frequencies):
            check_cancelled()
            frequency = float(frequency_value)
            set_gate_frequency(params, devices["Time_sequence"], channels["Time_sequence"], frequency)
            set_demod_frequency(devices["hf2"], frequency)
            metadata = dict(z_index=z_index, z_bias_v=z, z_bias_commanded_v=z,
                            predicted_center_hz=params.predicted_center(z), frequency_index=index,
                            frequency_hz=frequency, pump_duty_percent=params.pump_mod_duty,
                            pulse_width_s=params.pump_mod_duty / 100 / frequency)
            summary, filename = acquire_point(params, devices, channels, point_dir, metadata, actual_rate, device_id)
            means.append(summary["r_mean_v"])
            stds.append(summary["r_std_v"])
            accepted_files.append(filename)
            completed += 1
            report_runtime_progress("acquisition", f"Z={z:+.3f} V，f={frequency:.1f} Hz，{completed}/{total}",
                                    85 * completed / total,
                                    estimated_remaining_seconds=(time.monotonic() - started) / completed * (total - completed))
        summary_path = point_dir / "frequency_scan.npz"
        np.savez(summary_path, z_bias_v=z, z_bias_commanded_v=z, predicted_center_hz=params.predicted_center(z),
                 frequency_hz=frequencies, r_mean_v=means, r_std_v=stds,
                 actual_rate_sa_s=actual_rate, accepted_file=np.asarray(accepted_files))
        files.append(summary_path.relative_to(directory.root).as_posix())
        centers.append(params.predicted_center(z))
        finished_z.append(z)
        # 每完成一条曲线更新索引，取消后只分析完整频扫。
        np.savez(directory.raw / "z_scan_index.npz", z_bias_v=finished_z,
                 predicted_center_hz=centers, summary_file=np.asarray(files))


def safe_shutdown(params, devices, channels):
    return run_safety_shutdown(
        dg_channels=[DGChannelShutdown(devices[key], channels[key], key, key,
                                        disable_sync=key == "Z_magnetic_field") for key in OFF_KEYS],
        temperature_switch=TemperatureSwitchRestore(devices["Temp_Switch"], channels["Temp_Switch"]),
        extra_actions=(
            ShutdownAction("恢复 Pump 零偏参考频率失败", lambda: set_gate_frequency(params, devices["Time_sequence"], channels["Time_sequence"], params.zero_bias_center_frequency_hz)),
            ShutdownAction("恢复 HF2 零偏参考频率失败", lambda: set_demod_frequency(devices["hf2"], params.zero_bias_center_frequency_hz)),
        ),
        disconnect_targets=(DisconnectTarget("TEC", devices["tec"]),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


def run(params: BellBloomZFieldCalibrationParams) -> Path:
    mapping = load_mapping()
    validate_hardware(mapping)
    directory = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=params.schema_version)
    directory.update_config(experiment_id=EXPERIMENT_ID, execution_mode="typed_workflow",
                            started_at=datetime.now().astimezone().isoformat(), completion_status="running",
                            analysis_status="not_started", analysis_during_acquisition=False, acquisition_signals=["R"])
    session = DeviceSession()
    try:
        check_cancelled()
        devices = connect_devices(session, mapping)
    except Exception as exc:
        session.cleanup_connection_failure()
        directory.update_config(completion_status="cancelled" if isinstance(exc, WorkflowCancelled) else "failed", failure_reason=str(exc))
        raise
    channels = {key: int(mapping[key]["channel"]) for key in DG_KEYS}
    status, reason = "failed", ""
    try:
        directory.update_config(optional_connection_errors=session.optional_connection_errors())
        actual_rate = configure_outputs(params, devices, channels, mapping, directory)
        acquire_scan(params, devices, channels, directory, actual_rate, mapping["lockin_r"]["device_id"])
        status = "completed"
    except WorkflowCancelled as exc:
        status, reason = "cancelled", str(exc)
        raise
    except Exception as exc:
        reason = str(exc)
        raise
    finally:
        report = safe_shutdown(params, devices, channels)
        if status == "completed" and report.errors:
            status, reason = "failed", "；".join(report.errors)
        directory.update_config(completion_status=status, failure_reason=reason, safety_shutdown=report.to_dict(),
                                data_files=[path.relative_to(directory.root).as_posix() for path in directory.raw.rglob("*.npz")])
        report_runtime_progress(status, f"采集结束：{status}", estimated_remaining_seconds=None)
        if report.errors:
            print("安全结束失败：" + "；".join(report.errors))
    if report.errors:
        raise RuntimeError("安全结束失败：" + "；".join(report.errors))
    return directory.root


def main() -> int:
    run(load_runtime_params(BellBloomZFieldCalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
