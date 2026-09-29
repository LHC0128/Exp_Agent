"""探测链路频率响应标定采集工作流。"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import yaml
from scipy import signal
from lockin_amplifier import DAQConfig, DemodulatorConfig, HF2Instrument, OscillatorConfig, SignalInputConfig, daq, demod
from tec_controller import TECInstrument

from ...common import WorkflowCancelled, find_project_root, load_mapping, load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...steps import (
    DGChannelShutdown, DeviceSession, DisconnectTarget, TemperatureSwitchRestore,
    configure_temperature_control, connect_signal_generator_routes,
    create_run_directory, run_safety_shutdown, set_temperature_switch,
    synchronize_connected_clocks, temperature_gated_acquire,
)
from .models import DetectionChainFrequencyResponseParams


class _RuntimeCancellation:
    """将 GUI 取消请求传给温度稳定等待。"""

    def raise_if_cancelled(self) -> None:
        check_cancelled()


def _wait(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, deadline - time.monotonic()))


def run(params: DetectionChainFrequencyResponseParams, *, project_root: Path | None = None) -> Path:
    """连接映射设备，采集 HF2 Y 波形及频率响应。"""
    root = project_root or find_project_root()
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping, limits = load_mapping(root), load_safety_limits(root)
    for key, value in (
        ("Pump_laser_power", params.pump_laser_power_v),
        ("Probe_laser_power", params.probe_laser_power_v),
        ("temperature", params.temperature_c),
        ("Probe_AOM_Carrier", params.probe_aom_carrier_amplitude_vpp),
        ("Probe_AOM_AM", params.probe_aom_am_amplitude_vpp / 2),
        ("Probe_AOM_AM", -params.probe_aom_am_amplitude_vpp / 2),
    ):
        validate_safety_limit(key, value, limits)
    if params.hf2_demod_idx != int(mapping["lockin_r"]["demod_idx"]):
        raise ValueError("HF2_DEMOD_IDX 与 lockin_r 映射不一致")

    directory = create_run_directory(
        "Detection_Chain_Frequency_Response", params.run_tag, params.to_external(),
        schema_version=params.schema_version, project_root=root,
    )
    directory.update_config(
        experiment_id="detection-chain-frequency-response",
        execution_mode="typed_workflow",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        completion_status="running",
    )
    session = DeviceSession()
    connected = False
    aom = tec = temp_switch = None
    channels: dict[str, int] = {}
    status, failure = "failed", None
    try:
        report_runtime_progress("connect", "连接映射设备", 1)
        check_cancelled()
        tec_cfg = mapping["temperature"]
        tec = session.connect_optional(
            "tec", str(tec_cfg["resource"]),
            lambda: TECInstrument(port=tec_cfg["resource"]),
            device_label="TEC103",
        )
        aom, aom_channels = connect_signal_generator_routes(
            session, "probe_aom", {
                "carrier": ("Probe_AOM_Carrier", mapping["Probe_AOM_Carrier"]),
                "am": ("Probe_AOM_AM", mapping["Probe_AOM_AM"]),
            }, logical_channels={"carrier": 1, "am": 2},
        )
        laser, laser_channels = connect_signal_generator_routes(
            session, "laser", {
                "pump": ("Pump_laser_power", mapping["Pump_laser_power"]),
                "probe": ("Probe_laser_power", mapping["Probe_laser_power"]),
            }, logical_channels={"pump": 1, "probe": 2},
        )
        channels = {**aom_channels, **laser_channels}
        temp_switch, temp_channels = connect_signal_generator_routes(
            session, "temp_switch", {"temp": ("Temp_Switch", mapping["Temp_Switch"])},
            logical_channels={"temp": 2},
        )
        channels["temp_switch"] = temp_channels["temp"]
        hf2_cfg = mapping["lockin_r"]
        hf2 = session.connect(
            "hf2",
            f"hf2://{hf2_cfg['device_id']}@{hf2_cfg.get('host', '127.0.0.1')}:{hf2_cfg.get('port', 8005)}",
            lambda: HF2Instrument(
                host=hf2_cfg.get("host", "127.0.0.1"),
                port=hf2_cfg.get("port", 8005),
                api_level=1, device_id=hf2_cfg["device_id"],
            ),
        )
        clock_devices = {"probe_aom": aom, "laser": laser, "hf2": hf2}
        clock_keys = {"probe_aom": "Probe_AOM_Carrier", "laser": "Pump_laser_power", "hf2": "lockin_r"}
        clock_devices["temp_switch"] = temp_switch
        clock_keys["temp_switch"] = "Temp_Switch"
        clocks = synchronize_connected_clocks(clock_devices, mapping, clock_keys)
        connected = True
        directory.update_config(
            clock_sources=clocks,
            temperature_switch={
                "gated_per_point": True,
                "mapping_key": "Temp_Switch",
                "off_lead_s": params.temp_switch_off_lead_s,
                "on_lag_s": params.temp_switch_on_lag_s,
            },
        )

        report_runtime_progress("setup", "设置 Pump/Probe 光功率并确认温控方式", 5)
        check_cancelled()
        laser.setup_dc(params.pump_laser_power_v, channel=channels["pump"])
        laser.set_output(True, channel=channels["pump"])
        laser.setup_dc(params.probe_laser_power_v, channel=channels["probe"])
        laser.set_output(True, channel=channels["probe"])
        set_temperature_switch(temp_switch, True, channel=channels["temp_switch"])
        temp = configure_temperature_control(
            tec, params.temperature_c, channel=int(tec_cfg["channel"]),
            cancellation=_RuntimeCancellation(),
        )
        directory.update_config(
            temperature_control=temp.to_dict(),
            tec_connection_error=session.optional_connection_errors().get("tec"),
        )

        report_runtime_progress("setup", "配置 HF2 和 Probe AOM", 12)
        demod.configure_signal_input(
            hf2, SignalInputConfig(
                input_index=0, range=params.hf2_signal_range_v,
                ac_coupling=False, diff=False, impedance=50,
            ),
        )
        demod.configure_oscillator(
            hf2, OscillatorConfig(osc_index=0, frequency=params.hf2_osc_freq_hz, source="manual"),
        )
        phase_shift_deg = float(hf2.get_double(f"{hf2.demod_path(params.hf2_demod_idx)}/phaseshift"))
        actual_rate = demod.configure_demodulator(
            hf2, DemodulatorConfig(
                demod_index=params.hf2_demod_idx, enable=True,
                rate=params.hf2_daq_rate_sa_s, input_channel=0,
                osc_select=0, harmonic=1,
                time_constant=params.hf2_daq_tc_s,
                order=params.hf2_demod_order,
                phase=phase_shift_deg,
            ),
        )
        if actual_rate <= 0:
            raise RuntimeError("HF2 返回无效采样率")
        max_baseband = max(
            abs(params.am_freq_start_hz - params.hf2_osc_freq_hz),
            abs(params.am_freq_stop_hz - params.hf2_osc_freq_hz),
        )
        if max_baseband >= actual_rate / 2:
            raise ValueError("HF2 实际采样率不足以覆盖扫描范围的基带频率")
        directory.update_config(
            actual_rates={"hf2_demod_sa_s": actual_rate},
            hf2_configuration={
                "demod_idx": params.hf2_demod_idx,
                "osc_freq_hz": params.hf2_osc_freq_hz,
                "input_range_v": params.hf2_signal_range_v,
                "input_ac_coupling": False,
                "input_differential": False,
                "input_impedance_ohm": 50,
                "input_channel": 0,
                "oscillator_index": 0,
                "oscillator_source": "manual",
                "harmonic": 1,
                "phase_shift_deg": phase_shift_deg,
                "demod_order": params.hf2_demod_order,
                "demod_time_constant_s": params.hf2_daq_tc_s,
                "requested_rate_sa_s": params.hf2_daq_rate_sa_s,
                "actual_rate_sa_s": actual_rate,
            },
        )
        aom.set_output(False, channel=channels["carrier"])
        aom.set_output(False, channel=channels["am"])
        aom.setup_sine(
            params.probe_aom_carrier_freq_hz, params.probe_aom_carrier_amplitude_vpp,
            channel=channels["carrier"],
        )
        aom.set_mod_type("AM", channel=channels["carrier"])
        aom.set_mod_am_source("EXT", channel=channels["carrier"])
        aom.set_mod_state(True, channel=channels["carrier"])

        freqs = np.linspace(params.am_freq_start_hz, params.am_freq_stop_hz, params.am_freq_points)
        peaks, peak_freqs = np.full(freqs.size, np.nan), np.full(freqs.size, np.nan)
        started = time.monotonic()
        for index, freq in enumerate(freqs):
            check_cancelled()
            report_runtime_progress("acquire", f"频率点 {index + 1}/{freqs.size}: {freq:g} Hz",
                                    15 + 75 * index / freqs.size)
            if index == 0:
                aom.setup_sine(float(freq), params.probe_aom_am_amplitude_vpp,
                               channel=channels["am"])
            else:
                aom.set_frequency(float(freq), channel=channels["am"])
            config = DAQConfig(
                device=hf2_cfg["device_id"], trigger_type=0,
                duration=params.hf2_daq_duration_s,
                grid_cols=max(8, int(round(actual_rate * params.hf2_daq_duration_s))),
                grid_rows=1, grid_mode=2, signal_paths=["sample.y"],
            )
            def acquire_point():
                return daq.acquire_data(
                    hf2, config=config, demod_idx=params.hf2_demod_idx,
                    actual_rate=actual_rate,
                    timeout=max(5.0, params.hf2_daq_duration_s + 3.0),
                )

            results = temperature_gated_acquire(
                temp_switch=temp_switch, temp_channel=channels["temp_switch"],
                off_settle_s=params.temp_switch_off_lead_s + params.am_settle_time_s,
                on_settle_s=params.temp_switch_on_lag_s,
                acquire=acquire_point, check_cancelled=check_cancelled,
                sleep=_wait, set_temperature_switch=set_temperature_switch,
                cancellation=_RuntimeCancellation(),
            )
            y = next((
                np.asarray(item.values, dtype=float).reshape(-1)
                for item in results
                if str(item.signal_name).lower() == "y"
                or str(item.signal_name).lower().endswith(".y")
            ), None)
            if y is None or y.size < 8:
                raise RuntimeError(f"频率点 {index + 1} 未取得足够的 HF2 Y 数据")
            np.save(directory.raw / f"waveform_{index:04d}.npy", y)
            psd_freq, psd = signal.welch(
                y, fs=actual_rate, nperseg=min(params.psd_nperseg, y.size),
                scaling="density",
            )
            baseband = abs(float(freq) - params.hf2_osc_freq_hz)
            window = np.abs(psd_freq - baseband) <= 2000
            # 零基带信号是直流，Welch 默认去均值后无法用谱峰表示。
            if baseband > 0 and np.any(window):
                peak = np.flatnonzero(window)[int(np.argmax(psd[window]))]
                peaks[index] = float(np.sqrt(psd[peak]))
                peak_freqs[index] = float(psd_freq[peak])
            remaining = (time.monotonic() - started) / (index + 1) * (freqs.size - index - 1)
            report_runtime_progress(
                "acquire", f"已完成 {index + 1}/{freqs.size} 个频率点",
                15 + 75 * (index + 1) / freqs.size,
                estimated_remaining_seconds=remaining,
            )

        np.savez(
            directory.raw / "frequency_response.npz",
            am_frequencies_hz=freqs, peak_amplitudes=peaks,
            peak_frequencies_hz=peak_freqs, actual_rate=actual_rate,
        )
        (directory.results / "results_summary.yaml").write_text(
            yaml.safe_dump({
                "am_frequencies_hz": freqs.tolist(),
                "peak_amplitudes": peaks.tolist(),
                "peak_frequencies_hz": peak_freqs.tolist(),
                "actual_rate_sa_s": float(actual_rate),
            }, allow_unicode=True),
            encoding="utf-8",
        )
        directory.update_config(
            data_files=[f"raw/waveform_{i:04d}.npy" for i in range(freqs.size)]
            + ["raw/frequency_response.npz"],
        )
        status = "completed"
        report_runtime_progress("complete", "采集完成", 100)
    except WorkflowCancelled as exc:
        status, failure = "cancelled", str(exc)
        raise
    except Exception as exc:
        failure = str(exc)
        raise
    finally:
        if connected:
            shutdown = run_safety_shutdown(
                dg_channels=(
                    DGChannelShutdown(aom, channels["carrier"], "Probe_AOM_Carrier", "Probe AOM 载波"),
                    DGChannelShutdown(aom, channels["am"], "Probe_AOM_AM", "Probe AOM AM"),
                ),
                temperature_switch=TemperatureSwitchRestore(temp_switch, channels["temp_switch"]),
                disconnect_targets=(DisconnectTarget("TEC", tec),),
            )
            directory.update_config(safety_shutdown=shutdown.to_dict())
            if not shutdown.completed and status == "completed":
                status, failure = "failed", "；".join(shutdown.errors)
        else:
            session.cleanup_connection_failure()
        directory.update_config(completion_status=status, failure_reason=failure)
    if status != "completed":
        raise RuntimeError(failure or "安全关闭失败")
    return directory.root


def main() -> int:
    run(load_runtime_params(DetectionChainFrequencyResponseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
