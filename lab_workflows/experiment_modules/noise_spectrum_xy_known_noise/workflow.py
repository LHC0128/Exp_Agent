# %% [markdown] Cell 0
# # X/Y 交流控制测量已知可控噪声谱采集脚本
#
# 在 Noise_Spectrum_XY_Ctrl 的被动谱学流程上叠加已知谱噪声注入：
# Z 通道连续播放分段平顶谱伪噪声，每个控制点在同一温控窗口内
# 相邻点交替关→开、开→关顺序，离线做差分与真值链定量比较。
# 离线差分、拟合和真值比较请运行 Noise_Spectrum_XY_Ctrl_Known_Noise_plot.py。

# %% Cell 3
# ========== 交互式绘图模式 ==========
import matplotlib
matplotlib.use("TkAgg")
from pathlib import Path
import sys
# 自动定位项目根目录（以 params/ 目录为标记）
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import time
import math
import os
import atexit
from datetime import datetime

# 设备库
from gs200 import GS200Instrument
from lab_workflows.common import load_mapping
from lab_workflows.instrument_config import instrument_config_snapshot
from lab_workflows.experiment_runtime import (
    apply_runtime_params,
    check_cancelled,
    load_runtime_params,
    report_runtime_progress,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.generation import (
    generate_known_noise_waveform,
    load_z_calibration,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.dispersion import (
    fit_dispersion_curve,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.models import (
    NOISE_POINTS,
    NoiseSpectrumXYKnownNoiseParams,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.models import (
    NoiseSpectrumXYProbeAMParams,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.acquisition import (
    acquire_pair,
    wait_for_settle,
)
from lab_workflows.steps import (
    ArbitraryWaveformSpec,
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
    upload_arbitrary,
)
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)

# ========== 实验参数 ==========
EXPERIMENT_ID = os.environ.get("LAB_EXPERIMENT_ID", "noise-spectrum-xy-known-noise")
IS_PROBE_AM = EXPERIMENT_ID == "noise-spectrum-xy-uncontrolled-probe-am"
PARAMS = load_runtime_params(
    NoiseSpectrumXYProbeAMParams if IS_PROBE_AM else NoiseSpectrumXYKnownNoiseParams
)
apply_runtime_params(globals(), PARAMS)
EXPERIMENT_TYPE = (
    "Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM" if IS_PROBE_AM
    else "Noise_Spectrum_XY_Ctrl_Known_Noise"
)
PURPOSE = "probe_am_uncontrolled_noise" if IS_PROBE_AM else "known_noise_verification"
PAIR_SETTLE_S = INJECTION_SETTLE_S if IS_PROBE_AM else Z_INJECTION_SETTLE_S

print("所有库导入成功")

# %% Cell 4
# 加载物理量→仪器映射
MAPPING = load_mapping(project_root)

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# 强类型模型是唯一 GUI/默认配置入口；大写名称仅作为工作流局部兼容别名。
print("配置已加载")

# %% Cell 4.5
# ---- 生成已知噪声波形（纯计算，无硬件）并加载 K_Z 标定 ----
NOISE_BANDS = list(zip(NOISE_BAND_STARTS_HZ, NOISE_BAND_STOPS_HZ,
                       NOISE_BAND_PSDS_V2_PER_HZ, strict=True))
NOISE_WAVEFORM = generate_known_noise_waveform(
    repeat_freq_hz=NOISE_REPEAT_FREQ_HZ, bands=NOISE_BANDS, seed=NOISE_SEED)
if IS_PROBE_AM and INJECT_CONTROLLED_NOISE:
    Z_NOISE_BANDS = list(zip(Z_NOISE_BAND_STARTS_HZ, Z_NOISE_BAND_STOPS_HZ,
                             Z_NOISE_BAND_PSDS_V2_PER_HZ, strict=True))
    Z_NOISE_WAVEFORM = generate_known_noise_waveform(
        repeat_freq_hz=Z_NOISE_REPEAT_FREQ_HZ,
        bands=Z_NOISE_BANDS, seed=Z_NOISE_SEED)
if not IS_PROBE_AM:
    Z_CALIBRATION = load_z_calibration(project_root, Z_CALIBRATION_SOURCE_RUN)
NOISE_REALIZED_SCALE = (NOISE_AMPLITUDE_VPP / 2.0 / NOISE_WAVEFORM.design_peak_v) ** 2
print(
    f"噪声波形: {NOISE_POINTS} 点 @ {NOISE_WAVEFORM.sample_rate_sa_s:.0f} Sa/s "
    f"(重复 {NOISE_REPEAT_FREQ_HZ:g} Hz, 谱线间隔 {NOISE_WAVEFORM.line_spacing_hz:g} Hz)"
)
print(
    f"设计峰值 {NOISE_WAVEFORM.design_peak_v:.4f} V, RMS {NOISE_WAVEFORM.rms_v:.4f} V; "
    f"实际注入谱 = 设计谱 × {NOISE_REALIZED_SCALE:.4g}"
)
if IS_PROBE_AM:
    print("Probe AOM AM 噪声以测量端 N_S1 报告，不进行绝对光功率换算")
else:
    print(f"真值换算: K_Z = {Z_CALIBRATION.k_hz_per_v:.2f} Hz/V ({Z_CALIBRATION.source_run}); "
          "不应用线圈频响")

# %% Cell 5
# 安全边界检查函数
def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错."""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if lo is not None and hi is not None:
        if value < lo or value > hi:
            raise ValueError(
                f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]"
            )
    return value


def build_target_scan_axes():
    """由目标 Omega_ctrl 轴反算正弦峰值电压轴。"""
    target_freq_hz = np.linspace(
        TARGET_NOISE_FREQ_START_HZ,
        TARGET_NOISE_FREQ_STOP_HZ,
        TARGET_NOISE_FREQ_POINTS,
    )
    envelope_v = (target_freq_hz - XY_CTRL_B_HZ) / XY_CTRL_K_HZ_PER_V
    envelope_max = float(np.max(envelope_v))

    for key in ("X_magnetic_field", "Y_magnetic_field"):
        validate_safety_limit(key, -envelope_max)
        validate_safety_limit(key, envelope_max)

    return target_freq_hz, envelope_v


def validate_noise_output_levels():
    """噪声注入输出极值与色散偏置全部先过安全限值。"""
    mapping_key = "Probe_AOM_AM" if IS_PROBE_AM else "Z_magnetic_field"
    values = (NOISE_AMPLITUDE_VPP / 2.0, -NOISE_AMPLITUDE_VPP / 2.0)
    if not IS_PROBE_AM:
        values += (DISPERSION_SPAN_V, -DISPERSION_SPAN_V)
    for value in values:
        validate_safety_limit(mapping_key, value)
    if IS_PROBE_AM and INJECT_CONTROLLED_NOISE:
        for value in (Z_NOISE_AMPLITUDE_VPP, Z_NOISE_AMPLITUDE_VPP / 2,
                      -Z_NOISE_AMPLITUDE_VPP / 2):
            validate_safety_limit("Z_magnetic_field", value)


validate_noise_output_levels()
TARGET_NOISE_FREQ_LIST_HZ, XY_ENVELOPE_VOLTAGE_LIST_V = build_target_scan_axes()

print("安全校验函数已定义")

# %% Cell 6
devices = {}
session = DeviceSession()

try:
    # ---- GS200: 主磁场 ----
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )
    print(f"GS200 已连接: {gs.idn()}")
    gs.set_source_function(gs_cfg["source_function"])
    # [经验] GS200 需硬件级电流保护
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    # ---- DG4000: X/Y 补偿磁场（Burst 载波输出） ----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp, _ = connect_signal_generator_routes(
        session,
        "dg_comp",
        {
            "x": ("X_magnetic_field", dg_comp_cfg),
            "y": ("Y_magnetic_field", MAPPING["Y_magnetic_field"]),
        },
        logical_channels={"x": 1, "y": 2},
    )
    print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
    devices["dg_comp"] = dg_comp

    # ---- DG4000: Z 噪声与 Time_sequence_2 共用外触发；Probe AM 使用独立信号源 ----
    sequence_cfg = MAPPING["Time_sequence_2"]
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep, _ = connect_signal_generator_routes(
        session, "dg_sweep",
        {"z": ("Z_magnetic_field", dg_sweep_cfg),
         "sequence_2": ("Time_sequence_2", sequence_cfg)},
        logical_channels={"z": 1, "sequence_2": 2},
    )
    if IS_PROBE_AM:
        dg_probe_cfg = MAPPING["Probe_AOM_Carrier"]
        dg_probe_noise_cfg = MAPPING["Probe_AOM_AM"]
        dg_probe_am, _ = connect_signal_generator_routes(
            session, "dg_probe_am",
            {"carrier": ("Probe_AOM_Carrier", dg_probe_cfg),
             "noise": ("Probe_AOM_AM", dg_probe_noise_cfg)},
            logical_channels={"carrier": 1, "noise": 2},
        )
        devices["dg_probe_am"] = dg_probe_am
    z_channel = int(dg_sweep_cfg["channel"])
    dg_sweep.set_output(False, channel=z_channel)
    dg_sweep.set_output_load("INF", channel=z_channel)
    dg_sweep.set_voltage_unit("VPP", channel=z_channel)
    print(f"XY 外触发 DG4000 已连接: {dg_sweep.idn()}")
    # Z 场和外触发映射同机；Probe AM 走独立 DG4000。
    devices["dg_sweep"] = dg_sweep

    # ---- DG4000: Pump 调制 ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod, _ = connect_signal_generator_routes(
        session,
        "dg_mod",
        {
            "carrier": ("Pump_modulation", dg_mod_cfg),
            "gate": ("Time_sequence", MAPPING["Time_sequence"]),
        },
        logical_channels={"carrier": 1, "gate": 2},
    )
    print(f"调制 DG4000 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # ---- DG900: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp, _ = connect_signal_generator_routes(
        session,
        "dg_temp",
        {"temp": ("Temp_Switch", dg_temp_cfg)},
        logical_channels={"temp": 2},
    )
    print(f'温控设备已连接: {dg_temp.idn()}')
    devices["dg_temp"] = dg_temp

    # ---- TEC103: 温度控制器 ----
    tec_cfg = MAPPING["temperature"]
    tec = session.connect_optional(
        "tec",
        tec_cfg["resource"],
        lambda: TECInstrument(port=tec_cfg["resource"]),
        device_label="TEC103",
    )
    devices["tec"] = tec

    # ---- DG900: Pump/Probe 光功率 ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_probe_cfg = MAPPING["Probe_laser_power"]
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
    hf2_cfg = MAPPING["lockin_r"]
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

    clock_routes = {
        "dg_comp": "X_magnetic_field",
        "dg_sweep": "Z_magnetic_field",
        "dg_mod": "Pump_modulation",
        "dg_temp": "Temp_Switch",
        "dg_laser": "Pump_laser_power",
        "hf2": "lockin_r",
    }
    if IS_PROBE_AM:
        clock_routes["dg_probe_am"] = "Probe_AOM_Carrier"
    clock_sources = synchronize_connected_clocks(
        devices,
        MAPPING,
        clock_routes,
    )

except Exception as e:
    print(f"设备连接失败: {e}")
    session.cleanup_connection_failure()
    raise

print(f"\n所有设备连接完成，共 {len(devices)} 个设备")

# %% Cell 7
hfi = devices["hf2"]
tec = devices["tec"]
gs = devices["gs200"]
dg_laser = devices["dg_laser"]
dg_comp = devices["dg_comp"]
dg_sweep = devices["dg_sweep"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]


def set_temp_switch(enabled, *, wait=False):
    """本实验测量时关闭物理输出；恢复开启电平后可等待温控恢复。"""
    channel = int(dg_temp_cfg["channel"])
    if not enabled:
        validate_safety_limit("Temp_Switch", 0.0)
        dg_temp.set_output(False, channel=channel)
        return
    set_temperature_switch(
        dg_temp,
        True,
        channel=channel,
        on_voltage=FIXED_PARAMS["Temp_Switch"],
    )
    if wait:
        wait_for_settle(TEMP_SWITCH_ON_SETTLE_S)


# ---- 1. Pump 光功率 ----
validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(
    FIXED_PARAMS["Pump_laser_power"],
    channel=int(dg_laser_cfg["channel"]),
)
print(f"Pump 光功率: {FIXED_PARAMS['Pump_laser_power']} V DC")

# ---- 2. Probe 光功率 ----
validate_safety_limit("Probe_laser_power", FIXED_PARAMS["Probe_laser_power"])
dg_laser.setup_dc(
    FIXED_PARAMS["Probe_laser_power"],
    channel=int(dg_probe_cfg["channel"]),
)
print(f"Probe 光功率: {FIXED_PARAMS['Probe_laser_power']} V DC")

# ---- 3. 主磁场（共振中心由主磁场决定，不使用 K_Z 标定的 f_0V） ----
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# ---- 4. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
set_temp_switch(True)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 5. 温度控制 ----
temperature_status = configure_temperature_control(
    tec,
    FIXED_PARAMS["temperature"],
    channel=1,
    tolerance_c=1.0,
    stable_reads=1,
    poll_interval_s=5.0,
    timeout_s=1200.0,
)
temp_now = temperature_status.actual_temperature_c
if temp_now is not None:
    print(f"温度已稳定: {temp_now:.2f} °C")

# ---- 6. 任意波上传（输出 OFF 待命；扫描全程不再更新） ----
def upload_noise_waveform(*, generator=None, channel=None, waveform=None,
                          repeat_freq_hz=None, amplitude_vpp=None):
    """上传噪声波形并回读幅度、频率、点数和负载。"""
    generator = generator if generator is not None else (dg_probe_am if IS_PROBE_AM else dg_sweep)
    channel = int(channel if channel is not None else
                  (dg_probe_noise_cfg["channel"] if IS_PROBE_AM else dg_sweep_cfg["channel"]))
    waveform = waveform if waveform is not None else NOISE_WAVEFORM
    repeat_freq_hz = float(repeat_freq_hz if repeat_freq_hz is not None else NOISE_REPEAT_FREQ_HZ)
    amplitude_vpp = float(amplitude_vpp if amplitude_vpp is not None else NOISE_AMPLITUDE_VPP)
    upload_arbitrary(generator, ArbitraryWaveformSpec(
        values=waveform.normalized,
        frequency=repeat_freq_hz,
        amplitude=amplitude_vpp,
        offset=0.0,
        phase=0.0,
        channel=channel,
        output=False,
    ))
    # [经验] DG4162 上传后必须显式重写参数并回读；DC 恢复路径由驱动选择 CUSTom。
    generator.set_frequency(repeat_freq_hz, channel=channel)
    generator.set_amplitude(amplitude_vpp, channel=channel)
    generator.set_offset(0.0, channel=channel)
    generator.wait_for_operation_complete()
    actual = {
        "shape": str(generator.get_shape(channel=channel)).strip().upper(),
        "points": int(generator.get_arb_points(channel=channel)),
        "amplitude_vpp": float(generator.get_amplitude(channel=channel)),
        "offset_v": float(generator.get_offset(channel=channel)),
        "frequency_hz": float(generator.get_frequency(channel=channel)),
        "voltage_unit": str(generator.get_voltage_unit(channel=channel)).strip().upper(),
        "load": str(generator.get_output_load(channel=channel)).strip().upper(),
    }
    expected = {
        "points": NOISE_POINTS,
        "amplitude_vpp": amplitude_vpp,
        "offset_v": 0.0,
        "frequency_hz": repeat_freq_hz,
        "voltage_unit": "VPP",
        "load": "INFINITY",
    }
    problems = [
        f"{key}: 请求 {value}，回读 {actual[key]}"
        for key, value in expected.items()
        if (
            actual[key] != value
            if isinstance(value, (str, int))
            else not math.isclose(actual[key], value, rel_tol=1e-5, abs_tol=1e-6)
        )
    ]
    if actual["shape"] not in {"USER", "CUSTOM"}:
        problems.insert(0, f"shape: 请求 USER/CUSTOM，回读 {actual['shape']}")
    generator.raise_for_errors()
    if problems:
        raise RuntimeError("噪声任意波设置回读不一致：" + "；".join(problems))
    return actual


if IS_PROBE_AM:
    carrier_channel = int(dg_probe_cfg["channel"])
    noise_channel = int(dg_probe_noise_cfg["channel"])
    validate_safety_limit("Probe_AOM_Carrier", PROBE_AOM_CARRIER_VPP)
    dg_probe_am.set_output(False, channel=carrier_channel)
    dg_probe_am.setup_sine(freq=PROBE_AOM_CARRIER_FREQ_HZ,
                           amplitude=PROBE_AOM_CARRIER_VPP,
                           offset=0.0, phase=0.0, channel=carrier_channel)
    dg_probe_am.set_output_load(50, channel=carrier_channel)
    dg_probe_am.set_mod_type("AM", channel=carrier_channel)
    dg_probe_am.set_mod_am_source("EXT", channel=carrier_channel)
    dg_probe_am.set_mod_am_depth(PROBE_AOM_AM_DEPTH_PERCENT, channel=carrier_channel)
    dg_probe_am.set_mod_state(True, channel=carrier_channel)
    dg_probe_am.set_output_load("INF", channel=noise_channel)
    dg_probe_am.set_voltage_unit("VPP", channel=noise_channel)

    def probe_am_shutdown_fallback():
        run_safety_shutdown(dg_channels=(
            DGChannelShutdown(dg_probe_am, noise_channel, "Probe_AOM_AM", "Probe AOM AM 噪声"),
            DGChannelShutdown(dg_probe_am, carrier_channel, "Probe_AOM_Carrier", "Probe AOM 载波"),
            DGChannelShutdown(dg_sweep, z_channel, "Z_magnetic_field", "Z 可控噪声"),
            DGChannelShutdown(dg_comp, 1, "X_magnetic_field", "X 正弦控制"),
            DGChannelShutdown(dg_comp, 2, "Y_magnetic_field", "Y 正弦控制"),
            DGChannelShutdown(dg_sweep, 2, "Time_sequence_2", "X/Y 共用触发"),
        ), temperature_switch=TemperatureSwitchRestore(dg_temp, 2))

    atexit.register(probe_am_shutdown_fallback)

upload_noise_waveform()
print(f"噪声任意波已上传: {NOISE_REPEAT_FREQ_HZ:g} Hz × {NOISE_POINTS} 点, "
      f"{NOISE_AMPLITUDE_VPP:g} Vpp, 输出 OFF 待命")
if IS_PROBE_AM and INJECT_CONTROLLED_NOISE:
    upload_noise_waveform(
        generator=dg_sweep, channel=z_channel, waveform=Z_NOISE_WAVEFORM,
        repeat_freq_hz=Z_NOISE_REPEAT_FREQ_HZ, amplitude_vpp=Z_NOISE_AMPLITUDE_VPP)
    print("Z 已知可控噪声任意波已上传，输出 OFF 待命")

# ---- 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

waveform_filename = "probe_am_noise_waveform.npy" if IS_PROBE_AM else "known_noise_waveform.npy"
np.save(raw_dir / waveform_filename, NOISE_WAVEFORM.normalized)
if IS_PROBE_AM and INJECT_CONTROLLED_NOISE:
    np.save(raw_dir / "controlled_z_noise_waveform.npy", Z_NOISE_WAVEFORM.normalized)

# ---- 保存实验配置到运行目录 ----
instrument_snapshot = instrument_config_snapshot(project_root)
if IS_PROBE_AM:
    config = {
        "experiment_id": EXPERIMENT_ID, "schema_version": PARAMS.schema_version,
        "execution_mode": "typed_workflow", "clock_sources": clock_sources,
        "parameters": PARAMS.to_external(), "experiment_type": EXPERIMENT_TYPE,
        "purpose": PURPOSE, "timestamp": timestamp,
        "device_library_revision": instrument_snapshot["device_library_revision"],
        "physical_mapping_revision": instrument_snapshot["physical_mapping_revision"],
        "device_library_snapshot": instrument_snapshot["device_library"],
        "physical_mapping_snapshot": instrument_snapshot["physical_mappings"],
        "mapping_snapshot": instrument_snapshot["resolved_mapping"],
        "scan_params": {
            "TARGET_NOISE_FREQ_START_HZ": TARGET_NOISE_FREQ_START_HZ,
            "TARGET_NOISE_FREQ_STOP_HZ": TARGET_NOISE_FREQ_STOP_HZ,
            "TARGET_NOISE_FREQ_POINTS": TARGET_NOISE_FREQ_POINTS,
            "xy_envelope_voltage_V": XY_ENVELOPE_VOLTAGE_LIST_V.tolist(),
            "XY_SETTLE_TIME_s": XY_SETTLE_TIME,
            "TEMP_SWITCH_ON_SETTLE_S": TEMP_SWITCH_ON_SETTLE_S,
        },
        "probe_am_noise": {
            "waveform_file": f"raw/{waveform_filename}", "points": NOISE_POINTS,
            "repeat_frequency_hz": float(NOISE_REPEAT_FREQ_HZ),
            "sample_rate_sa_s": float(NOISE_WAVEFORM.sample_rate_sa_s),
            "amplitude_vpp": float(NOISE_AMPLITUDE_VPP), "seed": int(NOISE_SEED),
            "design_peak_v": float(NOISE_WAVEFORM.design_peak_v),
            "rms_v": float(NOISE_WAVEFORM.rms_v),
            "bands": [{"start_hz": float(start), "stop_hz": float(stop),
                       "design_psd_v2_per_hz": float(psd)} for start, stop, psd in NOISE_BANDS],
            "carrier_frequency_hz": float(PROBE_AOM_CARRIER_FREQ_HZ),
            "carrier_amplitude_vpp": float(PROBE_AOM_CARRIER_VPP),
            "carrier_load_ohm": 50, "am_depth_percent": float(PROBE_AOM_AM_DEPTH_PERCENT),
            "actual_waveform_psd_source": "Welch PSD computed from scaled saved waveform",
            "measurement_units": "HF2 demodulator PSD; not calibrated to optical power",
        },
        "controlled_z_noise": {
            "enabled": bool(INJECT_CONTROLLED_NOISE),
            **({
                "waveform_file": "raw/controlled_z_noise_waveform.npy",
                "points": NOISE_POINTS,
                "repeat_frequency_hz": float(Z_NOISE_REPEAT_FREQ_HZ),
                "sample_rate_sa_s": float(Z_NOISE_WAVEFORM.sample_rate_sa_s),
                "amplitude_vpp": float(Z_NOISE_AMPLITUDE_VPP),
                "seed": int(Z_NOISE_SEED),
                "design_peak_v": float(Z_NOISE_WAVEFORM.design_peak_v),
                "bands": [{"start_hz": float(start), "stop_hz": float(stop),
                           "design_psd_v2_per_hz": float(psd)}
                          for start, stop, psd in Z_NOISE_BANDS],
            } if INJECT_CONTROLLED_NOISE else {}),
        },
        "xy_ctrl": {"XY_CTRL_FREQ_Hz": XY_CTRL_FREQ,
                    "XY_CTRL_PHASE_deg": XY_CTRL_PHASE, "XY_CTRL_QUAD_deg": XY_CTRL_QUAD},
        "fixed_params": FIXED_PARAMS,
        "hf2_daq": {"DAQ_duration_s": HF2_DAQ_DURATION, "DAQ_TC_s": HF2_DAQ_TC,
                    "DAQ_rate_Sa_s": HF2_DAQ_RATE},
    }
else:
    config = {
    "experiment_id": EXPERIMENT_ID,
    "schema_version": PARAMS.schema_version,
    "execution_mode": "typed_workflow",
    "clock_sources": clock_sources,
    "parameters": PARAMS.to_external(),
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": PURPOSE,
    "timestamp": timestamp,
    "device_library_revision": instrument_snapshot["device_library_revision"],
    "physical_mapping_revision": instrument_snapshot["physical_mapping_revision"],
    "device_library_snapshot": instrument_snapshot["device_library"],
    "physical_mapping_snapshot": instrument_snapshot["physical_mappings"],
    "mapping_snapshot": instrument_snapshot["resolved_mapping"],
    "scan_params": {
        "TARGET_NOISE_FREQ_START_HZ": TARGET_NOISE_FREQ_START_HZ,
        "TARGET_NOISE_FREQ_STOP_HZ": TARGET_NOISE_FREQ_STOP_HZ,
        "TARGET_NOISE_FREQ_POINTS": TARGET_NOISE_FREQ_POINTS,
        "target_noise_frequency_Hz": TARGET_NOISE_FREQ_LIST_HZ.tolist(),
        "xy_envelope_voltage_V": XY_ENVELOPE_VOLTAGE_LIST_V.tolist(),
        "XY_SETTLE_TIME_s": XY_SETTLE_TIME,
        "TEMP_SWITCH_ON_SETTLE_S": TEMP_SWITCH_ON_SETTLE_S,
        "temperature_switch_off_mode": "output_off",
        "per_point_policy": (
            "adjacent control points alternate OFF/ON and ON/OFF; both states settle "
            "and save immediately inside one temperature-switch-off window"
        ),
    },
    "known_noise": {
        "waveform_file": "raw/known_noise_waveform.npy",
        "points": NOISE_POINTS,
        "repeat_frequency_hz": float(NOISE_REPEAT_FREQ_HZ),
        "sample_rate_sa_s": float(NOISE_WAVEFORM.sample_rate_sa_s),
        "line_spacing_hz": float(NOISE_WAVEFORM.line_spacing_hz),
        "nyquist_hz": float(NOISE_WAVEFORM.sample_rate_sa_s / 2.0),
        "amplitude_vpp": float(NOISE_AMPLITUDE_VPP),
        "offset_v": 0.0,
        "seed": int(NOISE_SEED),
        "bands": [
            {"start_hz": float(start), "stop_hz": float(stop),
             "design_psd_v2_per_hz": float(psd)}
            for start, stop, psd in NOISE_BANDS
        ],
        "design_peak_v": float(NOISE_WAVEFORM.design_peak_v),
        "rms_v": float(NOISE_WAVEFORM.rms_v),
        "realized_psd_scale": float(NOISE_REALIZED_SCALE),
        "playback": "continuous_no_burst",
        "wiring_confirmation": "运行前人工确认 Z 链路与 K_Z 标定的电压定义一致",
    },
    "truth_chain": {
        "z_calibration_run": Z_CALIBRATION.source_run,
        "K_Z_Hz_per_V": float(Z_CALIBRATION.k_hz_per_v),
        "K_Z_uncertainty_Hz_per_V": float(Z_CALIBRATION.uncertainty_hz_per_v),
        "coil_frequency_response_applied": False,
        "note": "真值谱从 raw/known_noise_waveform.npy 数值 PSD × K_Z² 计算",
    },
    "xy_ctrl": {
        "XY_CTRL_FREQ_Hz": XY_CTRL_FREQ,
        "XY_CTRL_PHASE_deg": XY_CTRL_PHASE,
        "XY_CTRL_QUAD_deg": XY_CTRL_QUAD,
        "XY_CALIB_ENVELOPE_V": XY_CALIB_ENVELOPE_V,
        "XY_PHASE_CAL_TOL_DEG": XY_PHASE_CAL_TOL_DEG,
        "XY_PHASE_CAL_MAX_ITER": XY_PHASE_CAL_MAX_ITER,
        "XY_PHASE_CAL_MIN_R_V": XY_PHASE_CAL_MIN_R_V,
        "XY_PHASE_CAL_MIN_R_RATIO": XY_PHASE_CAL_MIN_R_RATIO,
        "XY_CTRL_K_HZ_PER_V": XY_CTRL_K_HZ_PER_V,
        "XY_CTRL_B_HZ": XY_CTRL_B_HZ,
        "calibration_envelope_range_V": [
            XY_CALIBRATION_MIN_ENVELOPE_V,
            XY_CALIBRATION_MAX_ENVELOPE_V,
        ],
        "waveform_mode": "burst_sine",
        "sine_offset_V": 0.0,
        "amplitude_semantics": "2 * envelope peak voltage (Vpp)",
        "XY_TRIGGER_FREQ_Hz": XY_TRIGGER_FREQ,
        "XY_TRIGGER_AMPLITUDE_Vpp": XY_TRIGGER_AMPLITUDE,
        "XY_TRIGGER_OFFSET_V": XY_TRIGGER_OFFSET,
        "XY_TRIGGER_DUTY_pct": XY_TRIGGER_DUTY,
        "XY_TRIGGER_PHASE_deg": XY_TRIGGER_PHASE,
        "trigger_source": (
            f"Time_sequence_2 CH2 {XY_TRIGGER_FREQ:g} Hz square -> "
            "splitter -> dg_comp CH1/CH2 Ext Trig"
        ),
        "trigger_policy": (
            "Time_sequence_2 CH2 output ON and phase init once -> "
            "each scan point: XY outputs OFF -> set_amplitude -> "
            "calibrated burst phases -> outputs ON awaiting shared external trigger; "
            "zero peak keeps XY outputs OFF"
        ),
    },
    "fixed_params": FIXED_PARAMS,
    "pump_modulation": {
        "pump_mod_freq_Hz": PUMP_MOD_FREQ,
        "pump_mod_amplitude_V": PUMP_MOD_AMPLITUDE,
        "pump_mod_duty_pct": PUMP_MOD_DUTY,
    },
    "hf2_demod": {
        "demod_idx": HF2_DEMOD_IDX,
        "osc_freq_Hz": HF2_OSC_FREQ,
        "signal_range_V": HF2_SIGNAL_RANGE,
        "demod_rate_Sa_s": HF2_DEMOD_RATE,
        "demod_TC_s": HF2_DEMOD_TC,
    },
    "hf2_daq": {
        "DAQ_duration_s": HF2_DAQ_DURATION,
        "DAQ_TC_s": HF2_DAQ_TC,
        "DAQ_rate_Sa_s": HF2_DAQ_RATE,
    },
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
print(f"实验配置已保存: {config_path}")

print("\n初始值设置完成")

# %% Cell 9
# ---- 配置 RF 开关方案（Pump 调制） ----
print("CH1: 配置 100MHz 连续正弦波...")
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE,
                  offset=0.0, phase=0.0, channel=1)
print(f"  100MHz, 幅度 {PUMP_MOD_AMPLITUDE*1000:.0f} mVpp → RF 开关 IN")

pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
print(f"\nCH2: 配置 {PUMP_MOD_FREQ/1e3:.0f} kHz 脉冲门控...")
print(f"  脉宽: {pulse_width*1e6:.2f} μs ({PUMP_MOD_DUTY}% duty)")
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                   offset=RF_GATE_OFFSET, width=pulse_width, channel=2)
print(f"  {PUMP_MOD_FREQ/1e3:.0f} kHz → RF 开关 CTRL")

dg_mod.set_output(True, channel=1)
dg_mod.set_output(True, channel=2)
dg_mod.set_sync_state(False, channel=2)
print("  dg_mod 输出: CH1(100MHz) ON, CH2(脉冲) ON")
print("  dg_mod CH2 SYNC 未作为 dg_comp 触发源")

def validate_trigger_square_levels():
    """检查 Time_sequence_2 方波触发电平。"""
    half_amp = XY_TRIGGER_AMPLITUDE / 2.0
    low_level = XY_TRIGGER_OFFSET - half_amp
    high_level = XY_TRIGGER_OFFSET + half_amp
    validate_safety_limit("Time_sequence_2", low_level)
    validate_safety_limit("Time_sequence_2", high_level)


def configure_sequence_2_trigger():
    """设置 Time_sequence_2 CH2 为三通共用的固定方波触发源。"""
    validate_trigger_square_levels()
    trigger_phase = XY_TRIGGER_PHASE % 360
    dg_sweep.set_burst_state(False, channel=2)
    dg_sweep.set_mod_state(False, channel=2)
    dg_sweep.setup_square(
        freq=XY_TRIGGER_FREQ,
        amplitude=XY_TRIGGER_AMPLITUDE,
        offset=XY_TRIGGER_OFFSET,
        dcycle=XY_TRIGGER_DUTY,
        phase=trigger_phase,
        channel=2,
    )
    dg_sweep.set_output(True, channel=2)
    dg_sweep.phase_init(channel=2)

    print(
        f"  Time_sequence_2 CH2: fixed SQUARE {XY_TRIGGER_FREQ} Hz, "
        f"phase={trigger_phase:.2f}°, output ON -> splitter -> dg_comp CH1/CH2 Ext Trig"
    )


configure_sequence_2_trigger()

print("\n✅ RF 开关方案配置完成")

# ---- HF2 解调器相位校准（设置 XY 磁场前执行） ----
print("\n关闭温度开关（相位校准前，消除温控磁场干扰）...")
set_temp_switch(False)

print("确认 X/Y 补偿磁场关闭...")
dg_comp.set_output(False, channel=1)
dg_comp.set_output(False, channel=2)

sig_in_cfg = SignalInputConfig(
    input_index=0, range=HF2_SIGNAL_RANGE,
    ac_coupling=True, diff=False, impedance=50,
)
demod.configure_signal_input(hfi, sig_in_cfg)
print(f"信号输入已配置: {HF2_SIGNAL_RANGE} V range, AC coupled")

osc_cfg = OscillatorConfig(
    osc_index=0, frequency=HF2_OSC_FREQ, source="manual",
)
demod.configure_oscillator(hfi, osc_cfg)
print(f"振荡器已配置: {HF2_OSC_FREQ/1e3:.0f} kHz")

demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True, rate=HF2_DEMOD_RATE,
    input_channel=0, osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER, phase=0.0,
)
actual_rate = demod.configure_demodulator(hfi, demod_cfg)
print(f"解调器 {HF2_DEMOD_IDX} 已配置: rate={actual_rate:.0f} Sa/s, TC={HF2_DEMOD_TC*1000:.3f} ms")

if IS_PROBE_AM:
    dg_probe_am.set_output(True, channel=int(dg_probe_cfg["channel"]))

print("正在进行相位校准...")
demod0_phase_result = calibrate_demod_phase(
    hfi,
    PhaseCalibrationConfig(
        demod_idx=HF2_DEMOD_IDX,
        tolerance_deg=1.0,
        max_attempts=5,
        settle_time=0.2,
    ),
)
calibrated_phase = demod0_phase_result.phase_shift_deg
print(f"相位校准完成: {calibrated_phase:.2f}°")

sample = demod0_phase_result.after_sample
print(f"校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

time.sleep(0.5)

print("恢复温度开关...")
set_temp_switch(True, wait=True)

# %% Cell 11
# ---- X/Y 控制信号配置（Burst 模式，Time_sequence_2 外触发同步）----
print("配置 X/Y 交流控制信号（Burst 模式，Time_sequence_2 共用外触发）...")


def validate_sine_peak(peak_v):
    """校验正弦波的正负峰值。"""
    peak_v = abs(float(peak_v))
    for key in ("X_magnetic_field", "Y_magnetic_field"):
        validate_safety_limit(key, -peak_v)
        validate_safety_limit(key, peak_v)
    return peak_v


def set_xy_sine_phase(phase_deg, outputs_on=True):
    """用 OFF→相位→ON 使两路重新等待同一外触发沿。"""
    phases = (
        (1, float(phase_deg) % 360.0),
        (2, (float(phase_deg) + XY_CTRL_QUAD) % 360.0),
    )
    for channel, _ in phases:
        dg_comp.set_output(False, channel=channel)
    for channel, phase in phases:
        dg_comp.set_burst_phase(phase, channel=channel)
    for channel, _ in phases:
        dg_comp.set_output(bool(outputs_on), channel=channel)


def configure_xy_sine(peak_v, phase_deg, outputs_on=False):
    """一次配置 X/Y 正弦模式与外触发无限 Burst。"""
    peak_v = validate_sine_peak(peak_v)
    if peak_v == 0.0:
        raise ValueError("初始正弦峰值必须大于 0")
    for channel in (1, 2):
        dg_comp.set_output(False, channel=channel)
        dg_comp.set_burst_state(False, channel=channel)
        dg_comp.set_mod_state(False, channel=channel)
        dg_comp.setup_sine(
            freq=XY_CTRL_FREQ,
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
    set_xy_sine_phase(phase_deg, outputs_on=outputs_on)


def set_xy_sine_peak(peak_v):
    """关断两路后调幅，再按已校准相位重新等待共用外触发。"""
    peak_v = validate_sine_peak(peak_v)
    for channel in (1, 2):
        dg_comp.set_output(False, channel=channel)
    if peak_v == 0.0:
        return
    for channel in (1, 2):
        dg_comp.set_amplitude(2.0 * peak_v, channel=channel)
    set_xy_sine_phase(XY_CTRL_PHASE, outputs_on=True)


configure_xy_sine(XY_CALIB_ENVELOPE_V, XY_CTRL_PHASE, outputs_on=False)
print(f"  正弦载波: {XY_CTRL_FREQ:.3f} Hz, offset=0 V")
print(
    f"  校相峰值: {XY_CALIB_ENVELOPE_V:.3f} V "
    f"({2.0 * XY_CALIB_ENVELOPE_V:.3f} Vpp), "
    f"X/Y 正交相位差={XY_CTRL_QUAD:.2f}°"
)
print("  X/Y 输出 OFF (等待正弦 Burst 整体相位校准)")
print("硬件同步要求: Time_sequence_2 CH2 的 100 Hz 固定方波经三通 -> dg_comp CH1/CH2 Ext Trig")

# %% Cell 12
# ============================================================
# XY_CTRL_PHASE 校准：保持 Time_sequence_2 基准不变，迭代正弦 Burst 相位
# ============================================================
print("=" * 60)
print("校准 XY_CTRL_PHASE（Time_sequence_2 固定基准 + 正弦 Burst 相位迭代）")
print("=" * 60)

try:
    def apply_noise_phase_and_measure(phase_deg):
        set_xy_sine_phase(phase_deg, outputs_on=True)
        set_temp_switch(False)
        try:
            time.sleep(1.0)
            sample_data = demod.read_demod_sample(hfi, demod_idx=HF2_DEMOD_IDX)
            phase_offset = float(math.degrees(sample_data["theta"]))
            print(f"  phase={phase_deg:.2f}°, theta={phase_offset:+.2f}°")
            return sample_data
        finally:
            set_temp_switch(True, wait=True)

    phase_result = calibrate_xy_phase(
        XY_CTRL_PHASE,
        apply_noise_phase_and_measure,
        XYPhaseCalibrationConfig(
            tolerance_deg=XY_PHASE_CAL_TOL_DEG,
            max_measurements=XY_PHASE_CAL_MAX_ITER,
            minimum_r_v=XY_PHASE_CAL_MIN_R_V,
            minimum_r_ratio=XY_PHASE_CAL_MIN_R_RATIO,
        ),
        cancellation_check=check_cancelled,
    )
    XY_CTRL_PHASE = phase_result.final_phase_deg
    if not phase_result.converged:
        print(f"\n达到最大测量次数 {XY_PHASE_CAL_MAX_ITER}，校准未收敛")

finally:
    set_temp_switch(True)
    set_xy_sine_phase(XY_CTRL_PHASE, outputs_on=True)
    print("\n温度开关: ON (已恢复)")

print("\n✅ XY_CTRL_PHASE 校准完成")
print(f"  XY_CTRL_PHASE = {XY_CTRL_PHASE:.2f}°")
if IS_PROBE_AM:
    probe_carrier_channel = int(dg_probe_cfg["channel"])
    probe_noise_channel = int(dg_probe_noise_cfg["channel"])
    dg_probe_am.set_output(True, channel=probe_carrier_channel)
    dg_probe_am.set_output(False, channel=probe_noise_channel)
    pair_generator, pair_channel = dg_probe_am, probe_noise_channel
    print("Probe AOM 载波和外部 AM 已启用，噪声输出 OFF")
else:
    dg_sweep.set_output(True, channel=int(dg_sweep_cfg["channel"]))
    pair_generator, pair_channel = dg_sweep, int(dg_sweep_cfg["channel"])
    print("Z 已知噪声输出: ON（后续仅在色散 DC 校准时临时关闭）")

with open(config_path, encoding="utf-8") as f:
    config_saved = yaml.safe_load(f)
config_saved["xy_ctrl"]["XY_CTRL_PHASE_deg"] = float(XY_CTRL_PHASE)
config_saved["xy_ctrl"]["XY_CTRL_QUAD_deg"] = float(XY_CTRL_QUAD)
config_saved["xy_ctrl"]["Y_CTRL_PHASE_deg"] = float((XY_CTRL_PHASE + XY_CTRL_QUAD) % 360)
config_saved["xy_ctrl"]["phase_cal_tolerance_deg"] = float(XY_PHASE_CAL_TOL_DEG)
config_saved["xy_ctrl"]["phase_cal_success"] = bool(phase_result.converged)
config_saved["xy_ctrl"]["phase_cal_best_r_V"] = float(phase_result.best_r_v)
config_saved["xy_ctrl"]["phase_cal_history"] = [
    item.to_dict() for item in phase_result.history
]
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
print("最终 X/Y Burst 相位已写入 experiment_config.yaml")

# %% Cell 13
# ============================================================
# 内置色散斜率扫描：z DC 偏置步进过共振点，对整条色散线形拟合零交叉点斜率
# 与静磁场灵敏度实验同条件：X/Y 控制场关、纯 DC 偏置。
# 线形起止两端各按参数剔除点数；长时间关闭温控时按间隔点数回插温控恢复。
# ============================================================
if not IS_PROBE_AM:
    print("=" * 60)
    print("色散斜率扫描（增益换算基准，色散线形拟合）")
    print("=" * 60)

    report_runtime_progress("dispersion", "扫描 z 偏置色散线形", None,
                            estimated_remaining_seconds=None)

    def measure_dispersion_slope():
        """z DC 步进扫描并对整条色散线形拟合；返回原始数据与拟合结果。"""
        z_channel = int(dg_sweep_cfg["channel"])
        offsets = np.linspace(-abs(DISPERSION_SPAN_V), abs(DISPERSION_SPAN_V),
                              DISPERSION_STEPS)
        y_means = np.full(offsets.size, np.nan)
        # Z 通道退出任意波改 DC：色散扫描是纯静态偏置，与噪声注入互斥。
        dg_sweep.set_output(False, channel=z_channel)
        dg_sweep.setup_dc(0.0, channel=z_channel)
        dg_comp.set_output(False, channel=1)
        dg_comp.set_output(False, channel=2)
        interval = int(DISPERSION_TEMP_SWITCH_INTERVAL_POINTS)
        try:
            for index, offset_v in enumerate(offsets):
                check_cancelled()
                # 每组重启温控关闭窗口，避免整段扫描期间温度持续漂移。
                if index == 0 or (interval > 0 and index % interval == 0):
                    set_temp_switch(False)
                    time.sleep(XY_SETTLE_TIME)
                validate_safety_limit("Z_magnetic_field", float(offset_v))
                dg_sweep.setup_dc(float(offset_v), channel=z_channel)
                dg_sweep.set_output(True, channel=z_channel)
                time.sleep(DISPERSION_STEP_SETTLE_S)
                samples = []
                for _ in range(DISPERSION_SAMPLES_PER_STEP):
                    samples.append(demod.read_demod_sample(hfi, demod_idx=HF2_DEMOD_IDX)["y"])
                    time.sleep(0.05)
                y_means[index] = float(np.median(samples))
                dg_sweep.set_output(False, channel=z_channel)
                if interval > 0 and (index + 1) % interval == 0 and index + 1 < offsets.size:
                    # 回插温控：打开温度开关按 TEMP_SWITCH_ON_SETTLE_S 等待后再关闭。
                    set_temp_switch(True, wait=True)
            dg_sweep.setup_dc(0.0, channel=z_channel)
            fit = fit_dispersion_curve(
                offsets, y_means,
                skip_start_points=int(DISPERSION_FIT_SKIP_START),
                skip_end_points=int(DISPERSION_FIT_SKIP_END),
                k_z_hz_per_v=float(Z_CALIBRATION.k_hz_per_v),
            )
            return {"offsets_v": offsets, "y_mean": y_means, **fit}
        finally:
            dg_sweep.setup_dc(0.0, channel=z_channel)
            dg_sweep.set_output(False, channel=z_channel)
            set_temp_switch(True, wait=True)
            # 恢复 XY 等待共用外触发，并重新上传噪声任意波（DC 切换已退出 USER）。
            set_xy_sine_phase(XY_CTRL_PHASE, outputs_on=True)
            upload_noise_waveform()
            dg_sweep.set_output(True, channel=z_channel)


    DISPERSION = measure_dispersion_slope()

    np.savez(raw_dir / "dispersion_scan.npz",
             offsets_v=DISPERSION["offsets_v"], y_mean=DISPERSION["y_mean"],
             fit_mask=DISPERSION["fit_mask"], popt=DISPERSION["popt"],
             n_points=DISPERSION["n_points"],
             slope_v_per_v=DISPERSION["slope_v_per_v"],
             slope_v_per_hz=DISPERSION["slope_v_per_hz"],
             gamma_v=DISPERSION["gamma_v"],
             gamma_hz=DISPERSION["gamma_hz"],
             center_v=DISPERSION["center_v"],
             center_hz=DISPERSION["center_hz"],
             r_squared=DISPERSION["r_squared"],
             rmse_v=DISPERSION["rmse_v"],
             fit_valid=DISPERSION["is_valid"],
             k_z_hz_per_v=Z_CALIBRATION.k_hz_per_v)
    config_saved["dispersion"] = {
        "span_v": float(DISPERSION_SPAN_V),
        "steps": int(DISPERSION_STEPS),
        "samples_per_step": int(DISPERSION_SAMPLES_PER_STEP),
        "condition": "xy_control_off_dc_bias",
        "method": "dispersion_line_fit",
        "fit_skip_start_points": int(DISPERSION_FIT_SKIP_START),
        "fit_skip_end_points": int(DISPERSION_FIT_SKIP_END),
        "fit_points": int(DISPERSION["n_points"]),
        "temp_switch_interval_points": int(DISPERSION_TEMP_SWITCH_INTERVAL_POINTS),
        "slope_v_per_v": DISPERSION["slope_v_per_v"],
        "slope_v_per_hz": DISPERSION["slope_v_per_hz"],
        "gamma_v": DISPERSION["gamma_v"],
        "gamma_hz": DISPERSION["gamma_hz"],
        "center_v": DISPERSION["center_v"],
        "center_hz": DISPERSION["center_hz"],
        "r_squared": DISPERSION["r_squared"],
        "rmse_v": DISPERSION["rmse_v"],
        "fit_valid": DISPERSION["is_valid"],
        "fit_rejection_reasons": DISPERSION["rejection_reasons"],
        "k_z_hz_per_v": float(Z_CALIBRATION.k_hz_per_v),
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
    print(
        f"色散线形拟合: dY/dV = {DISPERSION['slope_v_per_v']:.6g} V/V = "
        f"{DISPERSION['slope_v_per_hz']:.6g} V/Hz；"
        f"R²={DISPERSION['r_squared']:.4f}, RMSE={DISPERSION['rmse_v']:.3g} V, "
        f"参与拟合 {DISPERSION['n_points']} 点"
    )
    if not DISPERSION["is_valid"]:
        print("警告: 色散线形拟合质量未通过 —", "；".join(DISPERSION["rejection_reasons"]))

# %% Cell 15
# ===== 逐点扫描 + 每点注入关/开差分采集 =====

print("=" * 60)
print("目标噪声谱频率扫描（每点 on/off 差分）")
print("=" * 60)
print(
    f"目标范围: {TARGET_NOISE_FREQ_START_HZ:.1f} Hz -> "
    f"{TARGET_NOISE_FREQ_STOP_HZ:.1f} Hz, {TARGET_NOISE_FREQ_POINTS} 点"
)
print(
    f"反算 V_env: {XY_ENVELOPE_VOLTAGE_LIST_V[0]:.6f} V -> "
    f"{XY_ENVELOPE_VOLTAGE_LIST_V[-1]:.6f} V"
)
print(f"DAQ 采集: 时长 {HF2_DAQ_DURATION}s ×2 (off/on), TC={HF2_DAQ_TC*1e6:.2f}μs, rate={HF2_DAQ_RATE:.0f} Sa/s")
total_est = TARGET_NOISE_FREQ_POINTS * (
    XY_SETTLE_TIME + 2 * HF2_DAQ_DURATION + 2 * PAIR_SETTLE_S
    + TEMP_SWITCH_ON_SETTLE_S + 0.3
)
print(f"预计扫描采集与等待耗时: {total_est:.0f}s ≈ {total_est/3600:.1f}h（不含初始化、校相、色散扫描、通信及分析）")
print()

np.savez(
    raw_dir / "control_scan_axes.npz",
    target_noise_frequency_Hz=TARGET_NOISE_FREQ_LIST_HZ,
    xy_envelope_voltage_V=XY_ENVELOPE_VOLTAGE_LIST_V,
    calibration_K_Hz_per_V=np.float64(XY_CTRL_K_HZ_PER_V),
    calibration_B_Hz=np.float64(XY_CTRL_B_HZ),
)

# 切换到噪声采集用解调器参数（更短 TC，更高带宽）
print(f"切换到 DAQ 采集解调器参数: TC={HF2_DAQ_TC*1e6:.2f} μs, Rate={HF2_DAQ_RATE} Sa/s")
daq_demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True,
    rate=HF2_DAQ_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DAQ_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
actual_rate_daq = demod.configure_demodulator(hfi, daq_demod_cfg)
grid_cols = int(actual_rate_daq * HF2_DAQ_DURATION)
time.sleep(0.2)

t_start = time.time()

per_point_seconds = []


def safe_scan_outputs_off():
    """声明本实验需要关闭和保留的输出，交给共享安全步骤执行。"""
    if IS_PROBE_AM:
        dg_probe_am.set_output(False, channel=probe_noise_channel)
        dg_probe_am.set_mod_state(False, channel=probe_carrier_channel)
        noise_shutdown = (
            DGChannelShutdown(dg_probe_am, probe_noise_channel, "Probe_AOM_AM", "Probe AOM AM 噪声"),
            DGChannelShutdown(dg_probe_am, probe_carrier_channel, "Probe_AOM_Carrier", "Probe AOM 载波"),
            DGChannelShutdown(dg_sweep, z_channel, "Z_magnetic_field", "Z 可控噪声"),
        )
    else:
        noise_shutdown = (DGChannelShutdown(dg_sweep, pair_channel, "Z_magnetic_field", "Z 噪声注入"),)
    return run_safety_shutdown(
        dg_channels=(
            DGChannelShutdown(dg_comp, 1, "X_magnetic_field", "X 正弦控制"),
            DGChannelShutdown(dg_comp, 2, "Y_magnetic_field", "Y 正弦控制"),
            *noise_shutdown,
            DGChannelShutdown(dg_sweep, 2, "Time_sequence_2", "X/Y 共用触发"),
        ),
        temperature_switch=TemperatureSwitchRestore(dg_temp, 2),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


try:
    from lab_workflows.experiment_modules.noise_spectrum_xy.models import welch_settings
    actual_welch = welch_settings(float(actual_rate_daq), HF2_DAQ_DURATION,
                                  PARAMS.analysis_bin_width_hz)
    config_saved["hf2_daq"].update(actual_rate_Sa_s=float(actual_rate_daq), **actual_welch)
    config_saved["paired_acquisition"] = dict(
        order="alternating_off_on_on_off", first_point="off_on",
        settle_each_state_s=PAIR_SETTLE_S, timing_file="raw/pair_timing.jsonl")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
    print(f"Welch 实际分段: {actual_welch}")

    if IS_PROBE_AM:
        dg_sweep.set_output(bool(INJECT_CONTROLLED_NOISE), channel=z_channel)
        if bool(dg_sweep.get_output(channel=z_channel)) != bool(INJECT_CONTROLLED_NOISE):
            raise RuntimeError("Z 可控噪声输出状态回读与请求不一致")
        if INJECT_CONTROLLED_NOISE:
            wait_for_settle(Z_NOISE_SETTLE_S)
        print(f"Z 已知可控噪声: {'持续注入' if INJECT_CONTROLLED_NOISE else '保持关闭'}")

    def acquire_point_waveform():
        daq_cfg = DAQConfig(
            device=MAPPING["lockin_r"]["device_id"],
            trigger_type=0,                  # 连续模式
            duration=HF2_DAQ_DURATION,
            grid_cols=grid_cols,
            grid_rows=1,
            grid_mode=2,
            signal_paths=["sample.y"],
        )
        results = daq.acquire_data(
            hfi, daq_cfg, demod_idx=HF2_DEMOD_IDX,
            actual_rate=actual_rate_daq, timeout=HF2_DAQ_DURATION + 10.0,
        )
        return results[0].values

    for i, (target_freq_hz, envelope_v) in enumerate(
        zip(
            TARGET_NOISE_FREQ_LIST_HZ,
            XY_ENVELOPE_VOLTAGE_LIST_V,
            strict=True,
        )
    ):
        point_start = time.time()
        check_cancelled()

        set_xy_sine_peak(envelope_v)
        wait_for_settle(0.3)

        # 关闭温度开关（消除温控磁场干扰），同一窗口内完成 off/on 差分。
        set_temp_switch(False)
        wait_for_settle(XY_SETTLE_TIME)
        acquire_pair(i, raw_dir, pair_generator, pair_channel, acquire_point_waveform,
                     PAIR_SETTLE_S)

        set_temp_switch(True, wait=True)

        per_point_seconds.append(time.time() - point_start)
        # ETA 协议：首个采集点完成后给出，进入分析前由适配器清空。
        remaining = float(np.mean(per_point_seconds) * (TARGET_NOISE_FREQ_POINTS - i - 1))
        report_runtime_progress(
            "scan",
            f"控制点 {i + 1}/{TARGET_NOISE_FREQ_POINTS} 完成（Probe AM on/off 差分，目标 {target_freq_hz/1e3:.2f} kHz，{envelope_v:.3f} V）"
            if IS_PROBE_AM else
            f"控制点 {i + 1}/{TARGET_NOISE_FREQ_POINTS} 完成（on/off 差分，目标 {target_freq_hz/1e3:.2f} kHz，{envelope_v:.3f} V）",
            100.0 * (i + 1) / TARGET_NOISE_FREQ_POINTS,
            estimated_remaining_seconds=remaining,
        )

except Exception as e:
    print(f"\n❌ 扫描出错: {e}")
    print("正在执行异常安全恢复...")
    raise

finally:
    shutdown_report = safe_scan_outputs_off()
    if IS_PROBE_AM:
        atexit.unregister(probe_am_shutdown_fallback)
    if shutdown_report.errors:
        print("安全关闭警告: " + "；".join(shutdown_report.errors))
    else:
        print("安全关闭完成，温度开关已恢复 ON；Pump 载波和时序门控保持输出")

elapsed_total = time.time() - t_start
print(
    f"\n✅ 扫描完成！共 {TARGET_NOISE_FREQ_POINTS} 点 ×2 (off/on), "
    f"用时 {elapsed_total:.0f}s"
)
print(f"原始波形保存在: {raw_dir}")

with open(config_path, encoding="utf-8") as f:
    config_saved = yaml.safe_load(f)
config_saved["hf2_daq"]["actual_rate_Sa_s"] = float(actual_rate_daq)
config_saved["data_files"] = [
    "raw/control_scan_axes.npz",
    f"raw/{waveform_filename}",
    *(["raw/controlled_z_noise_waveform.npy"] if IS_PROBE_AM and INJECT_CONTROLLED_NOISE else []),
    *([] if IS_PROBE_AM else ["raw/dispersion_scan.npz"]),
    "raw/pair_timing.jsonl",
    *[
        name
        for i in range(TARGET_NOISE_FREQ_POINTS)
        for name in (f"raw/waveform_C{i:04d}_off.npy", f"raw/waveform_C{i:04d}_on.npy")
    ],
]
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
print(f"实际 DAQ 采样率: {actual_rate_daq} Sa/s (已写入 experiment_config.yaml)")

# 恢复解调器至相位校准参数
print("恢复解调器 TC...")
restore_demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True,
    rate=HF2_DEMOD_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
demod.configure_demodulator(hfi, restore_demod_cfg)
time.sleep(0.2)
print(f"解调器已恢复: TC={HF2_DEMOD_TC*1e6:.0f} μs, Rate={HF2_DEMOD_RATE} Sa/s")

report_runtime_progress("acquisition", "采集完成，等待离线分析", 100.0,
                        estimated_remaining_seconds=None)
print("其他设备保持连接")
