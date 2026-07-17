"""T2 光学 FID 标定采集工作流。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from gs200 import GS200Instrument
from lockin_amplifier import DemodulatorConfig, HF2Instrument, SignalInputConfig, demod
from sds_acquisition import (
    AcquisitionConfig,
    ChannelConfig,
    SDSAcquisition,
    SDSInstrument,
    TriggerConfig,
)
from tec_controller import TECInstrument

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    create_run_directory,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    wait_for_temperature_stable,
)
from .models import T2CalibrationParams


EXPERIMENT_ID = "t2-calibration"
DATA_TYPE = "T2_Calibration"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    """把 GUI 取消文件适配为共享温控步骤需要的取消接口。"""

    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


@dataclass(slots=True)
class _AutoRangeState:
    """在整个 Probe 功率扫描期间保持示波器自动量程状态。"""

    scale: float


def _sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def safe_shutdown(
    devices: dict[str, Any], channels: dict[str, int], params: T2CalibrationParams
) -> SafetyShutdownReport:
    """按参考工作流恢复安全状态，保持主磁场和 Pump/Probe 输出。"""
    dg_channels: list[DGChannelShutdown] = []
    rf_switch = devices.get("rf_switch")
    extra_actions: list[ShutdownAction] = []
    if rf_switch is not None:
        gate_channel = channels.get("rf_gate")
        if gate_channel is not None:
            gate_low = params.rf_gate_offset - params.rf_gate_amplitude / 2
            gate_high = params.rf_gate_offset + params.rf_gate_amplitude / 2
            extra_actions.extend((
                ShutdownAction(
                    "关闭 RF 门控 Burst 失败",
                    lambda: (
                        validate_safety_limit("Time_sequence", gate_low),
                        validate_safety_limit("Time_sequence", gate_high),
                        rf_switch.set_burst_state(False, channel=gate_channel),
                    ),
                ),
                ShutdownAction(
                    "保持 RF 门控输出开启失败",
                    lambda: (
                        validate_safety_limit("Time_sequence", gate_low),
                        validate_safety_limit("Time_sequence", gate_high),
                        rf_switch.set_output(True, channel=gate_channel),
                    ),
                ),
            ))

    for key, safety_key in (
        ("x_field", "X_magnetic_field"),
        ("y_field", "Y_magnetic_field"),
        ("z_field", "Z_magnetic_field"),
    ):
        device = devices.get(key)
        channel = channels.get(key)
        if device is None or channel is None:
            continue
        dg_channels.append(DGChannelShutdown(device, channel, safety_key, key))
    if devices.get("z_field") is not None and "time_sequence_2" in channels:
        dg_channels.append(DGChannelShutdown(
            devices["z_field"],
            channels["time_sequence_2"],
            "Time_sequence_2",
            "时序通道 2",
        ))

    temperature_switch = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_switch = TemperatureSwitchRestore(
            devices["temp_switch"], channels["temp_switch"]
        )
    scope = devices.get("scope")
    if scope is not None:
        extra_actions.append(ShutdownAction("停止示波器触发失败", scope.trigger_stop))
    return run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_switch,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


def _identity(device: Any) -> str | None:
    try:
        return str(device.idn())
    except Exception:
        return None


def _device_snapshot(mapping: dict[str, dict[str, Any]], devices: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "main_magnetic_field",
        "X_magnetic_field",
        "Y_magnetic_field",
        "Z_magnetic_field",
        "Time_sequence_2",
        "Pump_laser_power",
        "Probe_laser_power",
        "Pump_modulation",
        "Time_sequence",
        "Temp_Switch",
        "scope_waveform",
        "lockin_xy",
        "temperature",
    )
    identities = {
        name: identity
        for name, device in devices.items()
        if (identity := _identity(device)) is not None
    }
    return {
        "mapping": {key: mapping[key] for key in keys if key in mapping},
        "identity_readback": identities,
    }


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}

    gs_cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )
    devices["gs200"].set_current_limit(0.015)

    for semantic, key in (
        ("x_field", "X_magnetic_field"),
        ("y_field", "Y_magnetic_field"),
        ("z_field", "Z_magnetic_field"),
    ):
        cfg = mapping[key]
        devices[semantic] = session.connect(
            semantic,
            cfg["resource"],
            lambda cfg=cfg: create_signal_generator(cfg),
        )
        channels[semantic] = int(cfg["channel"])
    time_sequence_2_cfg = mapping["Time_sequence_2"]
    if time_sequence_2_cfg["resource"] != mapping["Z_magnetic_field"]["resource"]:
        raise ValueError("Time_sequence_2 与 Z_magnetic_field 必须位于同一物理设备")
    channels["time_sequence_2"] = int(time_sequence_2_cfg["channel"])

    pump_cfg = mapping["Pump_laser_power"]
    probe_cfg = mapping["Probe_laser_power"]
    if pump_cfg["resource"] != probe_cfg["resource"]:
        raise ValueError("Pump_laser_power 与 Probe_laser_power 必须位于同一物理设备")
    devices["laser"] = session.connect(
        "laser", pump_cfg["resource"], lambda: create_signal_generator(pump_cfg)
    )
    channels["pump"] = int(pump_cfg["channel"])
    channels["probe"] = int(probe_cfg["channel"])

    carrier_cfg = mapping["Pump_modulation"]
    gate_cfg = mapping["Time_sequence"]
    if carrier_cfg["resource"] != gate_cfg["resource"]:
        raise ValueError("Pump_modulation 与 Time_sequence 必须位于同一物理设备")
    devices["rf_switch"] = session.connect(
        "rf_switch", carrier_cfg["resource"], lambda: create_signal_generator(carrier_cfg)
    )
    channels["aom_carrier"] = int(carrier_cfg["channel"])
    channels["rf_gate"] = int(gate_cfg["channel"])

    temp_cfg = mapping["Temp_Switch"]
    devices["temp_switch"] = session.connect(
        "temp_switch", temp_cfg["resource"], lambda: create_signal_generator(temp_cfg)
    )
    channels["temp_switch"] = int(temp_cfg["channel"])

    scope_cfg = mapping["scope_waveform"]
    devices["scope"] = session.connect(
        "scope", scope_cfg["resource"], lambda: SDSInstrument(scope_cfg["resource"])
    )
    devices["acquirer"] = session.bind("acquirer", SDSAcquisition(devices["scope"]))

    hf_cfg = mapping["lockin_xy"]
    devices["hf2"] = session.connect(
        "hf2",
        f"hf2://{hf_cfg.get('host', '127.0.0.1')}/{hf_cfg['device_id']}",
        lambda: HF2Instrument(
            host=hf_cfg.get("host", "127.0.0.1"),
            port=int(hf_cfg.get("port", 8005)),
            api_level=1,
            device_id=hf_cfg["device_id"],
        ),
    )

    tec_cfg = mapping["temperature"]
    devices["tec"] = session.connect(
        "tec", tec_cfg["resource"], lambda: TECInstrument(port=tec_cfg["resource"])
    )
    return devices, channels


def _configure_scope(params: T2CalibrationParams, devices: dict[str, Any]) -> tuple[AcquisitionConfig, dict[str, Any]]:
    channel_configs: list[ChannelConfig] = []
    for channel in range(1, 5):
        if channel == params.scope_pd_channel:
            channel_configs.append(ChannelConfig(number=channel, enabled=True, scale=0.5, offset=0.0, coupling="DC", impedance="ONEMeg", probe=1.0))
        elif channel == params.scope_trig_channel:
            channel_configs.append(ChannelConfig(number=channel, enabled=True, scale=2.0, offset=2.5, coupling="DC", impedance="ONEMeg", probe=1.0))
        else:
            channel_configs.append(ChannelConfig(number=channel, enabled=False))
    config = AcquisitionConfig(
        sampling_rate=params.scope_sample_rate,
        sampling_time=params.scope_duration,
        acquire_type="NORMal",
        acquire_delay=0.5,
        channels=channel_configs,
        trigger=TriggerConfig(
            mode="NORMal",
            source=f"C{params.scope_trig_channel}",
            type="EDGE",
            slope=params.scope_trig_slope,
            level=1.5,
        ),
    )
    acquirer: SDSAcquisition = devices["acquirer"]
    scope: SDSInstrument = devices["scope"]
    acquirer.apply_config(config)
    scope.set_memory_depth("100K")
    _sleep(0.1)
    return config, {
        "requested_sample_rate_Sa_s": params.scope_sample_rate,
        "actual_sample_rate_Sa_s": float(scope.get_sampling_rate()),
        "memory_depth": str(scope.get_memory_depth()),
        "actual_points": int(scope.get_actual_points()),
        "pd_channel": params.scope_pd_channel,
        "trigger_channel": params.scope_trig_channel,
        "trigger_source": f"C{params.scope_trig_channel}",
        "trigger_slope": params.scope_trig_slope,
        "trigger_level_V": 1.5,
    }


def _configure_outputs(
    params: T2CalibrationParams,
    devices: dict[str, Any],
    channels: dict[str, int],
) -> dict[str, Any]:
    tec = devices["tec"]
    validate_safety_limit("temperature", params.tec_temperature)
    tec.set_target_temperature(params.tec_temperature, channel=1)
    tec.set_enable(True, channel=1)
    actual_temperature = wait_for_temperature_stable(
        tec,
        params.tec_temperature,
        channel=1,
        tolerance_c=1.0,
        stable_reads=1,
        poll_interval_s=5.0,
        timeout_s=1200.0,
        cancellation=_RuntimeCancellation(),
    )

    gs200 = devices["gs200"]
    validate_safety_limit("main_magnetic_field", params.main_field_ma)
    gs200.set_current(params.main_field_ma / 1000.0)
    validate_safety_limit("main_magnetic_field", params.main_field_ma)
    gs200.set_output(True)

    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_power)
    laser.setup_dc(params.pump_power, channel=channels["pump"])
    initial_probe = params.probe_power_values[0] if params.do_power_scan else params.probe_power
    validate_safety_limit("Probe_laser_power", initial_probe)
    laser.setup_dc(initial_probe, channel=channels["probe"])

    rf = devices["rf_switch"]
    validate_safety_limit("Pump_modulation", params.aom_carrier_amplitude)
    rf.setup_sine(
        params.aom_carrier_freq,
        params.aom_carrier_amplitude,
        offset=0.0,
        channel=channels["aom_carrier"],
    )
    validate_safety_limit("Pump_modulation", params.aom_carrier_amplitude)
    rf.set_output(True, channel=channels["aom_carrier"])

    hf2 = devices["hf2"]
    demod.configure_signal_input(
        hf2,
        SignalInputConfig(input_index=0, range=params.hf2_signal_range, ac_coupling=True, diff=False, impedance=50),
    )
    actual_rate = demod.configure_demodulator(
        hf2,
        DemodulatorConfig(
            demod_index=params.hf2_demod_idx,
            enable=True,
            rate=params.hf2_demod_rate,
            input_channel=0,
            osc_select=0,
            harmonic=1,
            time_constant=params.hf2_demod_tc,
            order=params.hf2_demod_order,
            phase=0.0,
        ),
    )
    hf2.set_double(f"{hf2.osc_path(0)}/freq", params.hf2_osc_freq)
    hf2.sync()
    demod_path = hf2.demod_path(params.hf2_demod_idx)

    gate_low = params.rf_gate_offset - params.rf_gate_amplitude / 2
    gate_high = params.rf_gate_offset + params.rf_gate_amplitude / 2
    validate_safety_limit("Time_sequence", gate_low)
    validate_safety_limit("Time_sequence", gate_high)
    gate_channel = channels["rf_gate"]
    rf.set_burst_state(False, channel=gate_channel)
    rf.set_mod_state(False, channel=gate_channel)
    rf.apply_wave(
        "PULSe",
        freq=params.rf_gate_freq,
        amp=params.rf_gate_amplitude,
        offset=params.rf_gate_offset,
        channel=gate_channel,
    )
    validate_safety_limit("PUMP_MOD_DUTY", params.rf_gate_duty)
    rf.set_pulse_dcycle(params.rf_gate_duty, channel=gate_channel)
    rf.set_burst_mode("TRIGgered", channel=gate_channel)
    rf.set_burst_ncycles(params.burst_ncycles, channel=gate_channel)
    rf.set_burst_period(params.burst_period, channel=gate_channel)
    rf.set_burst_trigger_source("INTernal", channel=gate_channel)
    validate_safety_limit("Time_sequence", gate_high)
    rf.set_burst_state(True, channel=gate_channel)
    validate_safety_limit("Time_sequence", gate_high)
    rf.set_sync_state(True, channel=gate_channel)
    validate_safety_limit("Time_sequence", gate_high)
    rf.set_output(True, channel=gate_channel)

    return {
        "temperature": {"target_C": params.tec_temperature, "actual_C": float(actual_temperature), "channel": 1},
        "main_field": {"current_mA": params.main_field_ma, "hardware_current_limit_A": 0.015},
        "laser": {"pump_V": params.pump_power, "initial_probe_V": float(initial_probe), "pump_channel": channels["pump"], "probe_channel": channels["probe"]},
        "rf_gate": {
            "carrier_frequency_Hz": params.aom_carrier_freq,
            "carrier_amplitude_V": params.aom_carrier_amplitude,
            "gate_frequency_Hz": params.rf_gate_freq,
            "gate_amplitude_Vpp": params.rf_gate_amplitude,
            "gate_offset_V": params.rf_gate_offset,
            "gate_duty_pct": params.rf_gate_duty,
            "burst_ncycles": params.burst_ncycles,
            "burst_period_s": params.burst_period,
            "trigger_source": "INTernal",
        },
        "hf2": {
            "device_id": hf2.device_id,
            "demod_index": params.hf2_demod_idx,
            "requested_rate_Sa_s": params.hf2_demod_rate,
            "actual_rate_Sa_s": float(actual_rate),
            "actual_rate_readback_Sa_s": float(hf2.get_double(f"{demod_path}/rate")),
            "actual_time_constant_s": float(hf2.get_double(f"{demod_path}/timeconstant")),
            "actual_order": int(hf2.get_int(f"{demod_path}/order")),
            "actual_adcselect": int(hf2.get_int(f"{demod_path}/adcselect")),
            "actual_oscselect": int(hf2.get_int(f"{demod_path}/oscselect")),
            "actual_oscillator_frequency_Hz": float(hf2.get_double(f"{hf2.osc_path(0)}/freq")),
        },
    }


def _acquire_fid(
    params: T2CalibrationParams,
    probe_power: float,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: AcquisitionConfig,
    auto_range: _AutoRangeState,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[float]]:
    check_cancelled()
    laser = devices["laser"]
    scope: SDSInstrument = devices["scope"]
    acquirer: SDSAcquisition = devices["acquirer"]
    temp_switch = devices["temp_switch"]
    validate_safety_limit("Probe_laser_power", probe_power)
    laser.setup_dc(probe_power, channel=channels["probe"])

    waveforms: list[np.ndarray] = []
    scales: list[float] = []
    time_axis: np.ndarray | None = None
    current_scale = auto_range.scale

    for repeat in range(params.acq_repeats):
        check_cancelled()
        result = None
        for retry in range(3):
            check_cancelled()
            try:
                set_temperature_switch(temp_switch, False, channel=channels["temp_switch"])
                _sleep(0.5)
                if not scope.wait_for_trigger(timeout=10.0):
                    print(f"第 {repeat + 1} 次触发超时，跳过")
                    break
                check_cancelled()
                _sleep(scope_config.acquire_delay)
                scope.trigger_stop()
                result = acquirer.acquire_channel(
                    params.scope_pd_channel,
                    scope_config.timebase_scale,
                    scope_config.horizontal_divisions,
                    # 必须先读取示波器的完整实际记录，再按真实时间轴截取触发后 FID。
                    # 示波器可能把请求的 1 MSa/s 仲裁为 2 MSa/s；若按请求点数
                    # 从记录开头裁剪，会把触发后的半段完全丢弃。
                    trim_points=0,
                )
            finally:
                set_temperature_switch(temp_switch, True, channel=channels["temp_switch"])

            abs_max = float(np.max(np.abs(result.voltage)))
            full_scale = current_scale * params.vert_divs
            need_retry = False
            if abs_max < 0.4 * full_scale and current_scale > params.scale_min * 2:
                current_scale /= 2
                auto_range.scale = current_scale
                scope.set_channel_scale(params.scope_pd_channel, current_scale)
                need_retry = True
            if abs_max > 0.9 * full_scale and current_scale < params.scale_max / 2:
                current_scale *= 2
                auto_range.scale = current_scale
                scope.set_channel_scale(params.scope_pd_channel, current_scale)
                need_retry = True
            if need_retry and retry < 2:
                scope.trigger_run()
                _sleep(0.02)
                result = None
                continue
            break

        if result is None:
            scope.trigger_run()
            _sleep(0.02)
            continue
        waveforms.append(np.asarray(result.voltage, dtype=float))
        scales.append(float(current_scale))
        if time_axis is None:
            time_axis = np.asarray(result.time, dtype=float)
        scope.trigger_run()
        _sleep(0.02)

    if not waveforms or time_axis is None:
        raise RuntimeError(f"Probe={probe_power:.6g} V 未采集到有效 FID 波形")
    waveform_array = np.asarray(waveforms)
    if time_axis.size < 2:
        raise RuntimeError(
            f"Probe={probe_power:.6g} V 的示波器记录不含足够的时间轴数据"
        )
    sample_interval = float(np.median(np.diff(time_axis)))
    trigger_tolerance = abs(sample_interval) * 0.25
    mask = time_axis >= -trigger_tolerance
    if np.count_nonzero(mask) < 8:
        raise RuntimeError(
            f"Probe={probe_power:.6g} V 的示波器记录不含足够的触发后 FID 数据"
        )
    fid_time = time_axis[mask].copy()
    fid_time[np.abs(fid_time) <= trigger_tolerance] = 0.0
    fid_waveforms = waveform_array[:, mask]
    return fid_time, fid_waveforms, np.mean(fid_waveforms, axis=0), scales


def run(params: T2CalibrationParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        parameters=params.to_external(),
        pump_power_V=params.pump_power,
        aom_carrier_freq_Hz=params.aom_carrier_freq,
        aom_carrier_amplitude_V=params.aom_carrier_amplitude,
        rf_gate_freq_Hz=params.rf_gate_freq,
        rf_gate_amplitude_V=params.rf_gate_amplitude,
        rf_gate_offset_V=params.rf_gate_offset,
        rf_gate_duty_pct=params.rf_gate_duty,
        burst_ncycles=params.burst_ncycles,
        burst_period_s=params.burst_period,
        probe_power_V=params.probe_power,
        main_field_mA=params.main_field_ma,
        scope={
            "pd_channel": params.scope_pd_channel,
            "trig_channel": params.scope_trig_channel,
            "trig_slope": params.scope_trig_slope,
            "sample_rate_Hz": params.scope_sample_rate,
            "duration_s": params.scope_duration,
            "acq_repeats": params.acq_repeats,
        },
        temperature_C=params.tec_temperature,
        compatibility={
            "stable_data_type": DATA_TYPE,
            "raw_files": ["fid_waveforms.npz", "fid_pNN.npz", "power_scan_results.npz", "power_scan_waveforms.npz"],
            "fixed_parameter_aliases": params._fixed_aliases,
        },
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    data_files: list[str] = []
    try:
        check_cancelled()
        try:
            devices, channels = _connect_devices(mapping, session)
            clock_sources = synchronize_connected_clocks(
                devices,
                mapping,
                {
                    "x_field": "X_magnetic_field",
                    "z_field": "Z_magnetic_field",
                    "laser": "Pump_laser_power",
                    "rf_switch": "Pump_modulation",
                    "temp_switch": "Temp_Switch",
                    "hf2": "lockin_xy",
                },
            )
            for semantic, key in (
                ("x_field", "X_magnetic_field"),
                ("y_field", "Y_magnetic_field"),
                ("z_field", "Z_magnetic_field"),
            ):
                validate_safety_limit(key, 0.0)
                devices[semantic].set_output(False, channel=channels[semantic])
            # 时钟同步通过后保持温控开启；仅在实际采集窗口内临时关闭。
            set_temperature_switch(
                devices["temp_switch"],
                True,
                channel=channels["temp_switch"],
            )
        except Exception:
            # 与参考工作流一致：仅连接阶段失败时释放所有已建立的连接。
            session.cleanup_connection_failure()
            raise
        run_dir.update_config(
            device_snapshot=_device_snapshot(mapping, devices),
            clock_sources=clock_sources,
        )
        hardware = _configure_outputs(params, devices, channels)
        scope_config, scope_snapshot = _configure_scope(params, devices)
        hardware["scope"] = scope_snapshot
        run_dir.update_config(
            hardware_configuration=hardware,
            actual_rates={
                "hf2_demod_Sa_s": hardware["hf2"]["actual_rate_readback_Sa_s"],
                "scope_Sa_s": scope_snapshot["actual_sample_rate_Sa_s"],
            },
        )
        auto_range = _AutoRangeState(
            scale=float(scope_config.channels[params.scope_pd_channel - 1].scale)
        )

        if params.do_power_scan:
            records: list[dict[str, Any]] = []
            probe_power_values = params.probe_power_values
            for index, power in enumerate(probe_power_values):
                check_cancelled()
                print(f"Probe 功率点 {index + 1}/{len(probe_power_values)}: {power:.3f} V")
                time_fid, waveforms_fid, average, scales = _acquire_fid(
                    params, power, devices, channels, scope_config, auto_range
                )
                actual_sample_rate = 1.0 / float(np.median(np.diff(time_fid)))
                point_name = f"fid_p{index:02d}.npz"
                np.savez(
                    run_dir.raw / point_name,
                    time=time_fid,
                    avg_waveform=average,
                    waveforms=waveforms_fid,
                    probe_power=power,
                    scale_used=scales,
                    sample_rate=actual_sample_rate,
                    burst_duration=params.burst_duration,
                    rf_gate_freq=params.rf_gate_freq,
                    rf_gate_duty=params.rf_gate_duty,
                    trigger_slope=params.scope_trig_slope,
                )
                data_files.append(f"raw/{point_name}")
                records.append({
                    "time": time_fid,
                    "average": average,
                    "power": power,
                    "scales": scales,
                    "sample_rate": actual_sample_rate,
                })
            np.savez(
                run_dir.raw / "power_scan_results.npz",
                probe_power=[item["power"] for item in records],
                scale_used=[item["scales"][-1] for item in records],
            )
            np.savez(
                run_dir.raw / "power_scan_waveforms.npz",
                t_axis=records[0]["time"],
                avg_stack=np.asarray([item["average"] for item in records]),
                probe_power=[item["power"] for item in records],
                sample_rate=records[0]["sample_rate"],
            )
            data_files.extend(["raw/power_scan_results.npz", "raw/power_scan_waveforms.npz"])
        else:
            time_fid, waveforms_fid, average, scales = _acquire_fid(
                params, params.probe_power, devices, channels, scope_config, auto_range
            )
            actual_sample_rate = 1.0 / float(np.median(np.diff(time_fid)))
            np.savez(
                run_dir.raw / "fid_waveforms.npz",
                waveforms=waveforms_fid,
                avg_waveform=average,
                time=time_fid,
                sample_rate=actual_sample_rate,
                burst_duration=params.burst_duration,
                rf_gate_freq=params.rf_gate_freq,
                rf_gate_duty=params.rf_gate_duty,
                trigger_slope=params.scope_trig_slope,
                acq_repeats=params.acq_repeats,
                scale_used=np.asarray(scales),
                pd_channel=params.scope_pd_channel,
                trig_channel=params.scope_trig_channel,
            )
            data_files.append("raw/fid_waveforms.npz")
        run_dir.update_config(data_files=data_files, completion_status="completed")
        print(f"T2 采集完成: {run_dir.root}")
        return run_dir.root
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                safety_shutdown=shutdown_report.to_dict(),
                device_disconnect={
                "tec_disconnected": not shutdown_report.disconnect_errors,
                "other_devices_preserved": True,
                "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(T2CalibrationParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
