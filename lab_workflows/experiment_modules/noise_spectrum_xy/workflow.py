# %% [markdown]
# # X/Y 正弦控制噪声谱采集
# 导入只声明接口；正式运行由 main() 加载参数后调用 run()。

# %% 导入
from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import Path
import math
import time

import numpy as np
import yaml
from tqdm import tqdm

from gs200 import GS200Instrument
from lab_workflows.common import (
    CancellationToken, WorkflowCancelled, find_project_root, load_mapping,
    load_safety_limits, validate_safety_limit,
)
from lab_workflows.instrument_config import instrument_config_snapshot
from lab_workflows.experiment_runtime import (
    check_cancelled,
    load_runtime_params,
)
from lab_workflows.experiment_modules.noise_spectrum_xy.models import (
    NoiseSpectrumXYParams,
    welch_settings,
)
from lab_workflows.steps import (
    DGChannelShutdown,
    DirectAWPhaseCalibrationConfig as XYPhaseCalibrationConfig,
    DeviceSession,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    PhaseCalibrationConfig,
    TemperatureSwitchRestore,
    calibrate_demod_phase,
    calibrate_direct_aw_phase as calibrate_xy_phase,
    configure_temperature_control,
    connect_signal_generator_routes,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
)
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)

EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
PURPOSE = "noise_spectroscopy"


class _RuntimeCancellation(CancellationToken):
    """把既有子进程取消文件接入共享硬件步骤的检查点。"""

    def raise_if_cancelled(self) -> None:
        super().raise_if_cancelled()
        check_cancelled()


def wait(seconds: float) -> None:
    """保留原等待时长，每 0.1 秒检查取消。"""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        check_cancelled()
        time.sleep(min(0.1, remaining))


# %% 参数与设备步骤
def build_target_scan_axes(params: NoiseSpectrumXYParams, limits):
    """由目标 Omega_ctrl 轴反算正弦峰值电压轴。"""
    target_freq_hz = np.linspace(
        params.target_noise_freq_start_hz,
        params.target_noise_freq_stop_hz,
        params.target_noise_freq_points,
    )
    envelope_v = (target_freq_hz - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v
    envelope_max = float(np.max(envelope_v))

    for key in ("X_magnetic_field", "Y_magnetic_field"):
        validate_safety_limit(key, -envelope_max, limits)
        validate_safety_limit(key, envelope_max, limits)

    return target_freq_hz, envelope_v


def connect_devices(mapping):
    """按既有路由连接并同步时钟；失败释放已建立的连接。"""
    devices = {}
    session = DeviceSession()

    try:
        # ---- GS200: 主磁场 ----
        gs_cfg = mapping["main_magnetic_field"]
        gs = session.connect(
            "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
        )
        print(f"GS200 已连接: {gs.idn()}")
        devices["gs200"] = gs

        # ---- DG4000: X/Y 补偿磁场（Burst 载波输出） ----
        dg_comp_cfg = mapping["X_magnetic_field"]
        dg_comp, _ = connect_signal_generator_routes(
            session,
            "dg_comp",
            {
                "x": ("X_magnetic_field", dg_comp_cfg),
                "y": ("Y_magnetic_field", mapping["Y_magnetic_field"]),
            },
            logical_channels={"x": 1, "y": 2},
        )
        print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
        # Y 通道: 通过同一设备 CH2 控制
        devices["dg_comp"] = dg_comp

        # ---- DG4000: Z 磁场关闭；Time_sequence_2 为 dg_comp 共用外触发 ----
        dg_sweep_cfg = mapping["Z_magnetic_field"]
        dg_sweep, _ = connect_signal_generator_routes(
            session,
            "dg_sweep",
            {
                "z": ("Z_magnetic_field", dg_sweep_cfg),
                "sequence_2": ("Time_sequence_2", mapping["Time_sequence_2"]),
            },
            logical_channels={"z": 1, "sequence_2": 2},
        )
        print(f"Z 场 DG4000 已连接: {dg_sweep.idn()}")
        devices["dg_sweep"] = dg_sweep

        # ---- DG4000: Pump 调制 ----
        dg_mod_cfg = mapping["Pump_modulation"]
        dg_mod, _ = connect_signal_generator_routes(
            session,
            "dg_mod",
            {
                "carrier": ("Pump_modulation", dg_mod_cfg),
                "gate": ("Time_sequence", mapping["Time_sequence"]),
            },
            logical_channels={"carrier": 1, "gate": 2},
        )
        print(f"调制 DG4000 已连接: {dg_mod.idn()}")
        devices["dg_mod"] = dg_mod

        # ---- DG900: 温度开关 ----
        dg_temp_cfg = mapping["Temp_Switch"]
        dg_temp, _ = connect_signal_generator_routes(
            session,
            "dg_temp",
            {"temp": ("Temp_Switch", dg_temp_cfg)},
            logical_channels={"temp": 2},
        )
        print(f'温控设备已连接: {dg_temp.idn()}')
        devices["dg_temp"] = dg_temp

        # ---- TEC103: 温度控制器 ----
        tec_cfg = mapping["temperature"]
        tec = session.connect_optional(
            "tec",
            tec_cfg["resource"],
            lambda: TECInstrument(port=tec_cfg["resource"]),
            device_label="TEC103",
        )
        devices["tec"] = tec

        # ---- DG900: Pump/Probe 光功率 ----
        dg_laser_cfg = mapping["Pump_laser_power"]
        dg_probe_cfg = mapping["Probe_laser_power"]
        dg_laser, _ = connect_signal_generator_routes(
            session,
            "dg_laser",
            {
                "pump": ("Pump_laser_power", dg_laser_cfg),
                "probe": ("Probe_laser_power", dg_probe_cfg),
            },
            logical_channels={"pump": 1, "probe": 2},
        )
        print(f'光功率 {dg_laser_cfg["model"]} 已连接: {dg_laser.idn()}')
        devices["dg_laser"] = dg_laser

        # ---- HF2: 锁相放大器 ----
        hf2_cfg = mapping["lockin_r"]
        hfi = session.connect(
            "hf2",
            f"hf2://{hf2_cfg.get('host', '127.0.0.1')}/{hf2_cfg['device_id']}",
            lambda: HF2Instrument(
                host=hf2_cfg.get("host", "127.0.0.1"),
                port=hf2_cfg.get("port", 8005),
                api_level=1,
                device_id=hf2_cfg["device_id"],
            ),
        )
        print(f"HF2 已连接: {hfi.idn}")
        devices["hf2"] = hfi

        clock_sources = synchronize_connected_clocks(
            devices,
            mapping,
            {
                "dg_comp": "X_magnetic_field",
                "dg_sweep": "Z_magnetic_field",
                "dg_mod": "Pump_modulation",
                "dg_temp": "Temp_Switch",
                "dg_laser": "Pump_laser_power",
                "hf2": "lockin_r",
            },
        )

    except BaseException as e:
        print(f"设备连接失败: {e}")
        try:
            session.cleanup_connection_failure()
        except Exception as cleanup_error:
            print(f"连接清理警告: {cleanup_error}")
        raise

    print(f"\n所有设备连接完成，共 {len(devices)} 个设备")
    return devices, clock_sources


def set_temp_switch(dg_temp, enabled, params: NoiseSpectrumXYParams, limits, *, restore_wait=False):
    """本实验测量时关闭物理输出；恢复开启电平后可等待温控恢复。"""
    channel = 2  # 路由设备使用逻辑通道，实际端点由映射解析。
    if not enabled:
        validate_safety_limit("Temp_Switch", 0.0, limits)
        dg_temp.set_output(False, channel=channel)
        return
    validate_safety_limit("Temp_Switch", params.temp_switch, limits)
    set_temperature_switch(
        dg_temp,
        True,
        channel=channel,
        on_voltage=params.temp_switch,
    )
    if restore_wait:
        wait(params.temp_switch_on_settle_s)


def configure_initial_outputs(devices, params: NoiseSpectrumXYParams, mapping, limits):
    """设置固定输出和温度；调用方负责统一安全收尾。"""
    tec = devices["tec"]
    gs = devices["gs200"]
    dg_laser = devices["dg_laser"]
    dg_sweep = devices["dg_sweep"]
    dg_temp = devices["dg_temp"]
    gs.set_source_function(mapping["main_magnetic_field"]["source_function"])
    # GS200 保留硬件级电流保护。
    gs.set_current_limit(limits["main_magnetic_field"]["max"] / 1000.0)
    validate_safety_limit("Z_magnetic_field", 0.0, limits)
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    check_cancelled()
    # ---- 1. Pump 光功率 ----
    validate_safety_limit("Pump_laser_power", params.pump_laser_power, limits)
    dg_laser.setup_dc(
        params.pump_laser_power,
        channel=1,
    )
    print(f"Pump 光功率: {params.pump_laser_power} V DC")

    # ---- 2. Probe 光功率 ----
    validate_safety_limit("Probe_laser_power", params.probe_laser_power, limits)
    dg_laser.setup_dc(
        params.probe_laser_power,
        channel=2,
    )
    print(f"Probe 光功率: {params.probe_laser_power} V DC")

    # ---- 3. 主磁场 ----
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field, limits)
    gs.set_current(params.main_magnetic_field / 1000.0)
    gs.set_output(True)
    print(f"主磁场: {params.main_magnetic_field} mA")

    # ---- 4. 温度开关 (ON) ----
    validate_safety_limit("Temp_Switch", params.temp_switch, limits)
    set_temp_switch(dg_temp, True, params, limits)
    print(f"温度开关: ON ({params.temp_switch} V)")

    # ---- 5. 温度控制 ----
    temperature_status = configure_temperature_control(
        tec,
        params.temperature,
        channel=1,
        tolerance_c=1.0,
        stable_reads=1,
        poll_interval_s=5.0,
        timeout_s=1200.0,
        cancellation=_RuntimeCancellation(),
    )
    temp_now = temperature_status.actual_temperature_c
    if temp_now is not None:
        print(f"温度已稳定: {temp_now:.2f} °C")

    # ---- 6. Z 磁场输出确认关闭 ----
    validate_safety_limit("Z_magnetic_field", 0.0, limits)
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    print("Z 磁场: DC 0V, 输出 OFF (本实验不使用)")


def build_run_config(params: NoiseSpectrumXYParams, project_root, clock_sources, timestamp, target_frequencies, envelopes):
    """保存原参数、扫描轴和设备快照，保持历史字段不变。"""
    fixed_params = {
        "Pump_laser_power": params.pump_laser_power,
        "Probe_laser_power": params.probe_laser_power,
        "temperature": params.temperature,
        "Temp_Switch": params.temp_switch,
        "main_magnetic_field": params.main_magnetic_field,
    }
    instrument_snapshot = instrument_config_snapshot(project_root)
    config = {
        "experiment_id": "noise-spectrum-xy",
        "schema_version": params.schema_version,
        "execution_mode": "typed_workflow",
        "clock_sources": clock_sources,
        "parameters": params.to_external(),
        "experiment_type": EXPERIMENT_TYPE,
        "purpose": PURPOSE,
        "timestamp": timestamp,
        "device_library_revision": instrument_snapshot["device_library_revision"],
        "physical_mapping_revision": instrument_snapshot["physical_mapping_revision"],
        "device_library_snapshot": instrument_snapshot["device_library"],
        "physical_mapping_snapshot": instrument_snapshot["physical_mappings"],
        "mapping_snapshot": instrument_snapshot["resolved_mapping"],
        "scan_params": {
            "TARGET_NOISE_FREQ_START_HZ": params.target_noise_freq_start_hz,
            "TARGET_NOISE_FREQ_STOP_HZ": params.target_noise_freq_stop_hz,
            "TARGET_NOISE_FREQ_POINTS": params.target_noise_freq_points,
            "target_noise_frequency_Hz": target_frequencies.tolist(),
            "xy_envelope_voltage_V": envelopes.tolist(),
            "XY_SETTLE_TIME_s": params.xy_settle_time,
            "TEMP_SWITCH_ON_SETTLE_S": params.temp_switch_on_settle_s,
            "temperature_switch_off_mode": "output_off",
        },
        "xy_ctrl": {
            "XY_CTRL_FREQ_Hz": params.xy_ctrl_freq,
            "XY_CTRL_PHASE_deg": params.xy_ctrl_phase,
            "XY_CTRL_QUAD_deg": params.xy_ctrl_quad,
            "XY_CALIB_ENVELOPE_V": params.xy_calib_envelope_v,
            "XY_PHASE_CAL_TOL_DEG": params.xy_phase_cal_tol_deg,
            "XY_PHASE_CAL_MAX_ITER": params.xy_phase_cal_max_iter,
            "XY_PHASE_CAL_MIN_R_V": params.xy_phase_cal_min_r_v,
            "XY_PHASE_CAL_MIN_R_RATIO": params.xy_phase_cal_min_r_ratio,
            "XY_CTRL_K_HZ_PER_V": params.xy_ctrl_k_hz_per_v,
            "XY_CTRL_B_HZ": params.xy_ctrl_b_hz,
            "calibration_envelope_range_V": [
                params.xy_calibration_min_envelope_v,
                params.xy_calibration_max_envelope_v,
            ],
            "waveform_mode": "burst_sine",
            "sine_offset_V": 0.0,
            "amplitude_semantics": "2 * envelope peak voltage (Vpp)",
            "XY_TRIGGER_FREQ_Hz": params.xy_trigger_freq,
            "XY_TRIGGER_AMPLITUDE_Vpp": params.xy_trigger_amplitude,
            "XY_TRIGGER_OFFSET_V": params.xy_trigger_offset,
            "XY_TRIGGER_DUTY_pct": params.xy_trigger_duty,
            "XY_TRIGGER_PHASE_deg": params.xy_trigger_phase,
            "trigger_source": (
                f"Time_sequence_2 CH2 {params.xy_trigger_freq:g} Hz square -> "
                "splitter -> dg_comp CH1/CH2 Ext Trig"
            ),
            "trigger_policy": (
                "Time_sequence_2 CH2 output ON and phase init once -> "
                "each scan point: XY outputs OFF -> set_amplitude -> "
                "calibrated burst phases -> outputs ON awaiting shared external trigger; "
                "zero peak keeps XY outputs OFF"
            ),
        },
        "fixed_params": fixed_params,
        "pump_modulation": {
            "pump_mod_freq_Hz": params.pump_mod_freq,
            "pump_mod_amplitude_V": params.pump_mod_amplitude,
            "pump_mod_duty_pct": params.pump_mod_duty,
        },
        "hf2_demod": {
            "demod_idx": params.hf2_demod_idx,
            "osc_freq_Hz": params.hf2_osc_freq,
            "signal_range_V": params.hf2_signal_range,
            "demod_rate_Sa_s": params.hf2_demod_rate,
            "demod_TC_s": params.hf2_demod_tc,
        },
        "hf2_daq": {
            "DAQ_duration_s": params.hf2_daq_duration,
            "DAQ_TC_s": params.hf2_daq_tc,
            "DAQ_rate_Sa_s": params.hf2_daq_rate,
        },
    }
    return config


def validate_pump_levels(params: NoiseSpectrumXYParams, limits):
    """连接前及配置前校验 Pump 载波、占空比和门控电平。"""
    validate_safety_limit("Pump_modulation", params.pump_mod_amplitude, limits)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty, limits)
    for level in (params.rf_gate_offset - params.rf_gate_amplitude / 2,
                  params.rf_gate_offset + params.rf_gate_amplitude / 2):
        validate_safety_limit("Time_sequence", level, limits)


def configure_pump(dg_mod, params: NoiseSpectrumXYParams, limits):
    """配置一次 Pump 载波和门控，后续校相和扫描保持运行。"""
    validate_pump_levels(params, limits)
    print("CH1: 配置 100MHz 连续正弦波...")
    dg_mod.setup_sine(freq=100e6, amplitude=params.pump_mod_amplitude,
                      offset=0.0, phase=0.0, channel=1)
    print(f"  100MHz, 幅度 {params.pump_mod_amplitude*1000:.0f} mVpp → RF 开关 IN")

    pulse_width = (params.pump_mod_duty / 100.0) / params.pump_mod_freq
    print(f"\nCH2: 配置 {params.pump_mod_freq/1e3:.0f} kHz 脉冲门控...")
    print(f"  脉宽: {pulse_width*1e6:.2f} μs ({params.pump_mod_duty}% duty)")
    dg_mod.setup_pulse(freq=params.pump_mod_freq, amplitude=params.rf_gate_amplitude,
                       offset=params.rf_gate_offset, width=pulse_width, channel=2)
    print(f"  {params.pump_mod_freq/1e3:.0f} kHz → RF 开关 CTRL")

    # 启用 Pump 调制输出；dg_comp 外触发由 Time_sequence_2 CH2 经三通提供。
    dg_mod.set_output(True, channel=1)
    dg_mod.set_output(True, channel=2)
    dg_mod.set_sync_state(False, channel=2)
    print("  dg_mod 输出: CH1(100MHz) ON, CH2(脉冲) ON")
    print("  dg_mod CH2 SYNC 未作为 dg_comp 触发源")


def validate_trigger_square_levels(params: NoiseSpectrumXYParams, limits):
    """检查 Time_sequence_2 方波触发电平。"""
    half_amp = params.xy_trigger_amplitude / 2.0
    low_level = params.xy_trigger_offset - half_amp
    high_level = params.xy_trigger_offset + half_amp
    validate_safety_limit("Time_sequence_2", low_level, limits)
    validate_safety_limit("Time_sequence_2", high_level, limits)


def configure_sequence_2_trigger(dg_sweep, params: NoiseSpectrumXYParams, limits):
    """设置 Time_sequence_2 CH2 为三通共用的固定方波触发源。"""
    validate_trigger_square_levels(params, limits)
    trigger_phase = params.xy_trigger_phase % 360
    dg_sweep.set_burst_state(False, channel=2)
    dg_sweep.set_mod_state(False, channel=2)
    dg_sweep.setup_square(
        freq=params.xy_trigger_freq,
        amplitude=params.xy_trigger_amplitude,
        offset=params.xy_trigger_offset,
        dcycle=params.xy_trigger_duty,
        phase=trigger_phase,
        channel=2,
    )
    dg_sweep.set_output(True, channel=2)
    dg_sweep.phase_init(channel=2)

    print(
        f"  Time_sequence_2 CH2: fixed SQUARE {params.xy_trigger_freq} Hz, "
        f"phase={trigger_phase:.2f}°, output ON -> splitter -> dg_comp CH1/CH2 Ext Trig"
    )


def calibrate_hf2_phase(hfi, dg_comp, dg_temp, params: NoiseSpectrumXYParams, limits):
    """关闭温控和 XY，完成 HF2 校相后恢复温控。"""
    # ---- HF2 解调器相位校准（设置 XY 磁场前执行） ----
    print("\n关闭温度开关（相位校准前，消除温控磁场干扰）...")
    set_temp_switch(dg_temp, False, params, limits)

    # 确认 X/Y 补偿磁场处于关闭状态（尚未配置）
    print("确认 X/Y 补偿磁场关闭...")
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)

    # 配置信号输入
    sig_in_cfg = SignalInputConfig(
        input_index=0, range=params.hf2_signal_range,
        ac_coupling=True, diff=False, impedance=50,
    )
    demod.configure_signal_input(hfi, sig_in_cfg)
    print(f"信号输入已配置: {params.hf2_signal_range} V range, AC coupled")

    # 配置振荡器
    osc_cfg = OscillatorConfig(
        osc_index=0, frequency=params.hf2_osc_freq, source="manual",
    )
    demod.configure_oscillator(hfi, osc_cfg)
    print(f"振荡器已配置: {params.hf2_osc_freq/1e3:.0f} kHz")

    # 配置解调器
    demod_cfg = DemodulatorConfig(
        demod_index=0, enable=True, rate=params.hf2_demod_rate,
        input_channel=0, osc_select=0, harmonic=1,
        time_constant=params.hf2_demod_tc, order=params.hf2_demod_order, phase=0.0,
    )
    actual_rate = demod.configure_demodulator(hfi, demod_cfg)
    print(f"解调器 0 已配置: rate={actual_rate:.0f} Sa/s, TC={params.hf2_demod_tc*1000:.3f} ms")

    # 自动相位校准
    print("正在进行相位校准...")
    demod0_phase_result = calibrate_demod_phase(
        hfi,
        PhaseCalibrationConfig(
            demod_idx=params.hf2_demod_idx,
            tolerance_deg=1.0,
            max_attempts=5,
            settle_time=0.2,
        ),
        cancellation=_RuntimeCancellation(),
    )
    calibrated_phase = demod0_phase_result.phase_shift_deg
    print(f"相位校准完成: {calibrated_phase:.2f}°")

    # 读一组样本验证
    sample = demod0_phase_result.after_sample
    print(f"校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

    wait(0.5)

    # 恢复温控
    print("恢复温度开关...")
    set_temp_switch(dg_temp, True, params, limits, restore_wait=True)
    return calibrated_phase


def validate_sine_peak(peak_v, limits):
    """校验正弦波的正负峰值。"""
    peak_v = abs(float(peak_v))
    for key in ("X_magnetic_field", "Y_magnetic_field"):
        validate_safety_limit(key, -peak_v, limits)
        validate_safety_limit(key, peak_v, limits)
    return peak_v


def set_xy_sine_phase(dg_comp, phase_deg, quad_deg, outputs_on=True):
    """用 OFF→相位→ON 使两路重新等待同一外触发沿。"""
    phases = (
        (1, float(phase_deg) % 360.0),
        (2, (float(phase_deg) + quad_deg) % 360.0),
    )
    for channel, _ in phases:
        dg_comp.set_output(False, channel=channel)
    for channel, phase in phases:
        dg_comp.set_burst_phase(phase, channel=channel)
    for channel, _ in phases:
        dg_comp.set_output(bool(outputs_on), channel=channel)


def configure_xy_sine(dg_comp, params: NoiseSpectrumXYParams, limits):
    """一次配置 X/Y 正弦模式与外触发无限 Burst。"""
    peak_v = validate_sine_peak(params.xy_calib_envelope_v, limits)
    if peak_v == 0.0:
        raise ValueError("初始正弦峰值必须大于 0")
    for channel in (1, 2):
        dg_comp.set_output(False, channel=channel)
        dg_comp.set_burst_state(False, channel=channel)
        dg_comp.set_mod_state(False, channel=channel)
        dg_comp.setup_sine(
            freq=params.xy_ctrl_freq,
            amplitude=2.0 * peak_v,
            offset=0.0,
            phase=0.0,
            channel=channel,
        )
        dg_comp.set_output(False, channel=channel)
        dg_comp.set_burst_state(True, channel=channel)
        dg_comp.set_burst_mode("INFinity", channel=channel)
        dg_comp.set_burst_trigger_source("EXTernal", channel=channel)
        dg_comp.set_burst_trigger_slope("POSitive", channel=channel)
    set_xy_sine_phase(dg_comp, params.xy_ctrl_phase, params.xy_ctrl_quad, outputs_on=False)


def set_xy_sine_peak(dg_comp, peak_v, phase_deg, quad_deg, limits):
    """关断两路后调幅，再按已校准相位重新等待共用外触发。"""
    peak_v = validate_sine_peak(peak_v, limits)
    for channel in (1, 2):
        dg_comp.set_output(False, channel=channel)
    if peak_v == 0.0:
        return
    for channel in (1, 2):
        dg_comp.set_amplitude(2.0 * peak_v, channel=channel)
    # 调幅可能改变实际响应相位；复用校相的重触发顺序，不重配正弦/Burst。
    set_xy_sine_phase(dg_comp, phase_deg, quad_deg, outputs_on=True)


def measure_xy_phase(phase_deg, *, hfi, dg_comp, dg_temp, params: NoiseSpectrumXYParams, limits):
    """测量一次正弦响应相位；成功后恢复温控并等待。"""
    set_xy_sine_phase(dg_comp, phase_deg, params.xy_ctrl_quad, outputs_on=True)
    set_temp_switch(dg_temp, False, params, limits)
    wait(1.0)
    sample_data = demod.read_demod_sample(hfi, demod_idx=params.hf2_demod_idx)
    phase_offset = float(math.degrees(sample_data["theta"]))
    print(f"  phase={phase_deg:.2f}°, theta={phase_offset:+.2f}°")
    print(f"  样本: R={sample_data['r']:.6f}, X={sample_data['x']:.6f}, Y={sample_data['y']:.6f}")
    # 失败直接交给 run 的统一收尾，避免恢复等待覆盖原异常。
    set_temp_switch(dg_temp, True, params, limits, restore_wait=True)
    return sample_data


def calibrate_xy_control(hfi, dg_comp, dg_temp, params: NoiseSpectrumXYParams, limits):
    """初始化正弦 Burst，迭代校相并返回本次最终相位。"""
    configure_xy_sine(dg_comp, params, limits)
    print(f"  正弦载波: {params.xy_ctrl_freq:.3f} Hz, offset=0 V")
    print(
        f"  校相峰值: {params.xy_calib_envelope_v:.3f} V "
        f"({2.0 * params.xy_calib_envelope_v:.3f} Vpp), "
        f"X/Y 正交相位差={params.xy_ctrl_quad:.2f}°"
    )
    print("  X/Y 输出 OFF (等待正弦 Burst 整体相位校准)")
    print("硬件同步要求: Time_sequence_2 CH2 的 100 Hz 固定方波经三通 -> dg_comp CH1/CH2 Ext Trig")
    print(
        f"相位关系: Time_sequence_2 trigger={params.xy_trigger_phase:.2f}° @ "
        f"{params.xy_trigger_freq:.1f} Hz, sine burst phase={params.xy_ctrl_phase:.2f}°"
    )

    # XY_CTRL_PHASE 校准：保持 Time_sequence_2 基准不变，迭代正弦 Burst 相位
    print("=" * 60)
    print("校准 XY_CTRL_PHASE（Time_sequence_2 固定基准 + 正弦 Burst 相位迭代）")
    print("=" * 60)
    phase_result = calibrate_xy_phase(
        params.xy_ctrl_phase,
        partial(measure_xy_phase, hfi=hfi, dg_comp=dg_comp, dg_temp=dg_temp,
                params=params, limits=limits),
        XYPhaseCalibrationConfig(
            tolerance_deg=params.xy_phase_cal_tol_deg,
            max_measurements=params.xy_phase_cal_max_iter,
            minimum_r_v=params.xy_phase_cal_min_r_v,
            minimum_r_ratio=params.xy_phase_cal_min_r_ratio,
        ),
        cancellation_check=check_cancelled,
    )
    xy_phase = phase_result.final_phase_deg
    if not phase_result.converged:
        print(f"\n达到最大测量次数 {params.xy_phase_cal_max_iter}，校准未收敛")
    # 仅校相正常返回后重新启用 XY；失败由 run 统一关闭。
    set_temp_switch(dg_temp, True, params, limits)
    set_xy_sine_phase(dg_comp, xy_phase, params.xy_ctrl_quad, outputs_on=True)
    print("\n✅ XY_CTRL_PHASE 校准完成")
    print(f"  XY_CTRL_PHASE = {xy_phase:.2f}°")
    print(f"  XY_CTRL_QUAD = {params.xy_ctrl_quad:.2f}°")
    print(f"  Y_CTRL_PHASE = {(xy_phase + params.xy_ctrl_quad) % 360:.2f}°")
    return phase_result


def safe_scan_outputs_off(devices):
    """声明本实验需要关闭和保留的输出，交给共享安全步骤执行。"""
    return run_safety_shutdown(
        dg_channels=(
            DGChannelShutdown(devices["dg_comp"], 1, "X_magnetic_field", "X 正弦控制"),
            DGChannelShutdown(devices["dg_comp"], 2, "Y_magnetic_field", "Y 正弦控制"),
            DGChannelShutdown(devices["dg_sweep"], 1, "Z_magnetic_field", "Z 场"),
            DGChannelShutdown(devices["dg_sweep"], 2, "Time_sequence_2", "X/Y 共用触发"),
        ),
        temperature_switch=TemperatureSwitchRestore(devices["dg_temp"], 2),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


# %% 运行入口
def run(params: NoiseSpectrumXYParams, *, project_root: Path | None = None) -> Path:
    """执行采集，成功后返回运行目录；导入模块不会运行实验。"""
    project_root = Path(project_root).resolve() if project_root is not None else find_project_root(Path(__file__).parent)
    # 直接调用也经过既有类型/有限值校验，且后续状态不写回调用方模型。
    params = NoiseSpectrumXYParams.from_external(params.to_external())
    errors = params.validate(project_root)
    if errors:
        raise ValueError("；".join(errors))
    mapping = load_mapping(project_root)
    limits = load_safety_limits(project_root)
    target_frequencies, envelopes = build_target_scan_axes(params, limits)
    validate_sine_peak(params.xy_calib_envelope_v, limits)
    validate_trigger_square_levels(params, limits)
    validate_pump_levels(params, limits)
    check_cancelled()
    devices, clock_sources = connect_devices(mapping)
    pbar = None
    failure = None
    try:
        check_cancelled()
        configure_initial_outputs(devices, params, mapping, limits)
        hfi = devices["hf2"]
        tec = devices["tec"]
        dg_comp = devices["dg_comp"]
        dg_sweep = devices["dg_sweep"]
        dg_mod = devices["dg_mod"]
        dg_temp = devices["dg_temp"]
        # ---- 创建运行目录 ----
        timestamp = datetime.now().strftime("%m%d_%H%M")
        run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{params.run_tag}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir := run_dir / "raw").mkdir(exist_ok=True)
        (run_dir / "results").mkdir(exist_ok=True)
        print(f"运行目录: {run_dir}")
        config_saved = build_run_config(params, project_root, clock_sources, timestamp, target_frequencies, envelopes)
        config_path = run_dir / "experiment_config.yaml"
        with config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
        configure_pump(dg_mod, params, limits)
        configure_sequence_2_trigger(dg_sweep, params, limits)
        calibrated_phase = calibrate_hf2_phase(hfi, dg_comp, dg_temp, params, limits)
        phase_result = calibrate_xy_control(hfi, dg_comp, dg_temp, params, limits)
        xy_phase = phase_result.final_phase_deg
        config_saved["xy_ctrl"]["XY_CTRL_PHASE_deg"] = float(xy_phase)
        config_saved["xy_ctrl"]["XY_CTRL_QUAD_deg"] = float(params.xy_ctrl_quad)
        config_saved["xy_ctrl"]["Y_CTRL_PHASE_deg"] = float((xy_phase + params.xy_ctrl_quad) % 360)
        config_saved["xy_ctrl"]["phase_cal_tolerance_deg"] = float(params.xy_phase_cal_tol_deg)
        config_saved["xy_ctrl"]["phase_cal_success"] = bool(phase_result.converged)
        config_saved["xy_ctrl"]["phase_cal_best_r_V"] = float(phase_result.best_r_v)
        config_saved["xy_ctrl"]["phase_cal_history"] = [
            item.to_dict() for item in phase_result.history
        ]
        config_saved["xy_ctrl"]["trigger_square_freq_Hz"] = float(params.xy_trigger_freq)
        config_saved["xy_ctrl"]["trigger_square_phase_deg"] = float(params.xy_trigger_phase)
        config_saved["xy_ctrl"]["trigger_source"] = (
            f"Time_sequence_2 CH2 {params.xy_trigger_freq:g} Hz square -> "
            "splitter -> dg_comp CH1/CH2 Ext Trig"
        )
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
        print("最终 X/Y Burst 相位已写入 experiment_config.yaml")

        # ===== 逐点扫描目标噪声频率 + HF2 DAQ 采集 =====

        print("=" * 60)
        print("目标噪声谱频率扫描")
        print("=" * 60)
        print(
            f"目标范围: {params.target_noise_freq_start_hz:.1f} Hz -> "
            f"{params.target_noise_freq_stop_hz:.1f} Hz, {params.target_noise_freq_points} 点"
        )
        print(
            f"反算 V_env: {envelopes[0]:.6f} V -> "
            f"{envelopes[-1]:.6f} V"
        )
        print(f"DAQ 采集: 时长 {params.hf2_daq_duration}s, TC={params.hf2_daq_tc*1e6:.2f}μs, rate={params.hf2_daq_rate:.0f} Sa/s")
        total_est = params.target_noise_freq_points * (
            params.xy_settle_time + params.hf2_daq_duration + params.temp_switch_on_settle_s + 0.3
        )
        print(f"预计扫描采集与等待耗时: {total_est:.0f}s ≈ {total_est/3600:.1f}h（不含初始化、校相、通信及分析）")
        np.savez(
            raw_dir / "control_scan_axes.npz",
            target_noise_frequency_Hz=target_frequencies,
            xy_envelope_voltage_V=envelopes,
            calibration_K_Hz_per_V=np.float64(params.xy_ctrl_k_hz_per_v),
            calibration_B_Hz=np.float64(params.xy_ctrl_b_hz),
        )
        # 切换到噪声采集用解调器参数（更短 TC，更高带宽）
        print(f"切换到 DAQ 采集解调器参数: TC={params.hf2_daq_tc*1e6:.2f} μs, Rate={params.hf2_daq_rate} Sa/s")
        daq_demod_cfg = DemodulatorConfig(
            demod_index=0, enable=True,
            rate=params.hf2_daq_rate, input_channel=0,
            osc_select=0, harmonic=1,
            time_constant=params.hf2_daq_tc, order=params.hf2_demod_order,
            phase=calibrated_phase,
        )
        actual_rate_daq = demod.configure_demodulator(hfi, daq_demod_cfg)
        grid_cols = int(actual_rate_daq * params.hf2_daq_duration)
        wait(0.2)

        t_start = time.time()

        # try/finally 确保异常时恢复温度开关
        # mininterval 大于单点耗时即可让 set_postfix 和 update 触发合并到一次 display。
        pbar = tqdm(
            total=params.target_noise_freq_points,
            desc="Scanning",
            unit="pt",
            mininterval=5.0,
        )
        # 在安全恢复保护内校验硬件实际采样率，并保存派生段长，不作为可调参数。
        actual_welch = welch_settings(float(actual_rate_daq), params.hf2_daq_duration,
                                      params.analysis_bin_width_hz)
        config_saved["hf2_daq"].update(actual_rate_Sa_s=float(actual_rate_daq), **actual_welch)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
        print(f"Welch 实际分段: {actual_welch}")
        for i, (target_freq_hz, envelope_v) in enumerate(
            zip(
                target_frequencies,
                envelopes,
                strict=True,
            )
        ):
            check_cancelled()

            set_xy_sine_peak(dg_comp, envelope_v, xy_phase, params.xy_ctrl_quad, limits)
            wait(0.3)

            # 关闭温度开关（消除温控磁场干扰）
            set_temp_switch(dg_temp, False, params, limits)

            # 等待系统稳定
            wait(params.xy_settle_time)

            # 读取当前温度；失败不回刷进度，留到循环末 update 前一次性刷新
            temp_str = ""
            try:
                temp_now = tec.get_temperature(channel=1)
                temp_str = f", T={temp_now:.1f}°C"
            except WorkflowCancelled:
                raise
            except Exception:
                pass

            # 配置 HF2 DAQ 采集（每点独立配置）
            daq_cfg = DAQConfig(
                device=mapping["lockin_r"]["device_id"],
                trigger_type=0,                  # 连续模式
                duration=params.hf2_daq_duration,
                grid_cols=grid_cols,
                grid_rows=1,
                grid_mode=2,
                signal_paths=["sample.y"],       # 采集幅值信号
            )

            # 采集解调时域信号
            try:
                results = daq.acquire_data(
                    hfi, daq_cfg, demod_idx=0,
                    actual_rate=actual_rate_daq, timeout=params.hf2_daq_duration + 10.0,
                )
                waveform = results[0].values
            except WorkflowCancelled:
                raise
            except Exception as e:
                tqdm.write(
                    f"  [{i:4d}/{params.target_noise_freq_points}] "
                    f"Omega={target_freq_hz:.3f}Hz, V_env={envelope_v:.6f}V: "
                    f"DAQ 采集失败: {e}"
                )
                waveform = np.array([np.nan])

            check_cancelled()
            # 保存原始波形（.npy 二进制格式）
            np.save(raw_dir / f"waveform_C{i:04d}.npy", waveform)

            # 恢复温度开关
            set_temp_switch(dg_temp, True, params, limits, restore_wait=True)

            pbar.set_postfix_str(
                f"Omega={target_freq_hz/1e3:.2f}kHz, V={envelope_v:.3f}{temp_str}"
            )
            pbar.update(1)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        # 即使关闭进度条时再次 Ctrl+C，也必须尝试硬件收尾。
        try:
            if pbar is not None:
                try:
                    pbar.close()
                except Exception as exc:
                    print(f"进度条关闭警告: {exc}")
        finally:
            try:
                shutdown_report = safe_scan_outputs_off(devices)
                if shutdown_report.errors:
                    print("安全关闭警告: " + "；".join(shutdown_report.errors))
                else:
                    print("安全关闭完成，温度开关已恢复 ON；Pump 载波和时序门控保持输出")
            except Exception as exc:
                if failure is None:
                    raise
                print(f"安全关闭警告: {exc}")
    elapsed_total = time.time() - t_start
    print(
        f"\n✅ 扫描完成！共 {params.target_noise_freq_points} 点, "
        f"用时 {elapsed_total:.0f}s"
    )
    print(f"原始波形保存在: {raw_dir}")

    # 保存实际采样率和原始文件列表到 experiment_config.yaml
    config_saved["hf2_daq"]["actual_rate_Sa_s"] = float(actual_rate_daq)
    config_saved["data_files"] = [
        "raw/control_scan_axes.npz",
        *[
            f"raw/waveform_C{i:04d}.npy"
            for i in range(params.target_noise_freq_points)
        ],
    ]
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
    print(f"实际 DAQ 采样率: {actual_rate_daq} Sa/s (已写入 experiment_config.yaml)")

    # 恢复解调器至相位校准参数
    print("恢复解调器 TC...")
    restore_demod_cfg = DemodulatorConfig(
        demod_index=0, enable=True,
        rate=params.hf2_demod_rate, input_channel=0,
        osc_select=0, harmonic=1,
        time_constant=params.hf2_demod_tc, order=params.hf2_demod_order,
        phase=calibrated_phase,
    )
    demod.configure_demodulator(hfi, restore_demod_cfg)
    wait(0.2)
    print(f"解调器已恢复: TC={params.hf2_demod_tc*1e6:.0f} μs, Rate={params.hf2_demod_rate} Sa/s")

    print("其他设备保持连接")
    return run_dir


def main() -> None:
    run(load_runtime_params(NoiseSpectrumXYParams))


if __name__ == "__main__":
    main()
