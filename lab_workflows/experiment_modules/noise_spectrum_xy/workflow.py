# %% [markdown] Cell 0
# # X/Y 交流控制噪声谱测量采集脚本
#
# 从 Noise_Spectrum_XY_Ctrl.ipynb 拆分出来的采集部分。
# 离线 PSD 计算、拟合和绘图请运行 Noise_Spectrum_XY_Ctrl_plot.py。

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
import json
import math
from datetime import datetime
import matplotlib.pyplot as plt
from tqdm import tqdm

# 设备库
from gs200 import GS200Instrument
from lab_workflows.common import load_mapping
from lab_workflows.instrument_config import instrument_config_snapshot
from lab_workflows.experiment_runtime import (
    apply_runtime_params,
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
    HF2Instrument, DAQConfig, DAQResult,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)

# 数据分析库
# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
PURPOSE = "noise_spectroscopy"

print("所有库导入成功")

# %% Cell 4
# 加载物理量→仪器映射
MAPPING = load_mapping(project_root)

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
PURPOSE = "noise_spectroscopy"

# ---- 目标噪声谱频率扫描 ----
TARGET_NOISE_FREQ_START_HZ = 0.0
TARGET_NOISE_FREQ_STOP_HZ = 50000.0
TARGET_NOISE_FREQ_POINTS = 500
XY_SETTLE_TIME = 0.5       # 每点等待稳定时间 (s)
TEMP_SWITCH_ON_SETTLE_S = 2.0  # 恢复温控后等待时间 (s)

# ---- Pump 调制参数 ----
PUMP_MOD_FREQ = 10e3       # Pump 调制频率 (Hz)
PUMP_MOD_AMPLITUDE = 0.18  # 100MHz 正弦波幅度 (V)
PUMP_MOD_DUTY = 5          # 脉冲占空比 (%)
RF_GATE_AMPLITUDE = 5.0    # 脉冲门控幅度 (Vpp)
RF_GATE_OFFSET = 2.5       # 脉冲门控偏置 (V)

# ---- X/Y 交流控制信号 ----
# Bell-Bloom 原理: 原子以 Larmor 频率绕 B0 进动，XY 控制场须沿原子极化方向施加
#   XY_CTRL_PHASE:  XY 整体相对 Pump 调制信号的相位延迟 (deg)，需实验校准
#   XY_CTRL_QUAD:   X 与 Y 之间的正交相位差 (deg)，初始值 90°，后续由校准修正
#   XY_CALIB_ENVELOPE_V: 正弦载波整体相位校准时的恒定峰值电压
XY_CTRL_FREQ = PUMP_MOD_FREQ    # X/Y 交流控制频率 (Hz)，等于 Larmor 频率
XY_CTRL_PHASE = 90         # XY 控制场相对 Pump 调制信号的相位延迟 (deg)
XY_CTRL_QUAD = 90          # X 与 Y 之间的正交相位差 (deg)
XY_CALIB_ENVELOPE_V = 1.5
XY_PHASE_CAL_TOL_DEG = 1.0
XY_PHASE_CAL_MAX_ITER = 10
XY_PHASE_CAL_MIN_R_V = 1e-12
XY_PHASE_CAL_MIN_R_RATIO = 0.1

# 手动填写正弦峰值标定：Omega_ctrl = K * |V_peak| + B。
XY_CTRL_K_HZ_PER_V = 15075.784562638912
XY_CTRL_B_HZ = -218.47506313463893
XY_CALIBRATION_MIN_ENVELOPE_V = 0.0
XY_CALIBRATION_MAX_ENVELOPE_V = 4.0

XY_TRIGGER_FREQ = 100.0    # Time_sequence_2 固定基准方波频率 (Hz)
XY_TRIGGER_AMPLITUDE = 5.0 # Time_sequence_2 方波触发幅度 (Vpp)
XY_TRIGGER_OFFSET = 2.5    # Time_sequence_2 方波触发偏置 (V)
XY_TRIGGER_DUTY = 50.0     # Time_sequence_2 方波触发占空比 (%)
XY_TRIGGER_PHASE = 0.0     # Time_sequence_2 方波基准相位 (deg)

# ---- HF2 解调配置（相位校准用） ----
HF2_DEMOD_IDX = 0          # 解调器索引
HF2_OSC_FREQ = PUMP_MOD_FREQ    # 振荡器频率 (Hz)，与 Pump 调制频率一致
HF2_SIGNAL_RANGE = 2.0     # 信号输入量程 (V)
HF2_DEMOD_ORDER = 8        # 解调滤波器阶数
HF2_DEMOD_TC = 0.000692    # 解调时间常数 (s) —— 相位校准时使用，较低的带宽确保稳定相位
HF2_DEMOD_RATE = 100000    # 解调输出数据速率 (Sa/s)

# ---- HF2 DAQ 采集配置（噪声采集用） ----
HF2_DAQ_DURATION = 1.0     # DAQ 采集时长 (s)
HF2_DAQ_TC = 7.85e-07      # 解调时间常数 (s) —— 噪声采集时使用，更高的带宽保证频谱平坦
HF2_DAQ_RATE = 100000      # 解调输出数据速率 (Sa/s)，与 DEMOD_RATE 一致

# ---- 固定参数 ----
FIXED_PARAMS = {
    "Pump_laser_power": 0.1,         "Probe_laser_power": 0.1,
    "temperature": 100,              "Temp_Switch": 5.0,
    "main_magnetic_field": 1.03,
}

# ---- 运行目录命名 ----
RUN_TAG = "noise"

# 强类型模型是唯一 GUI/默认配置入口；大写名称仅作为旧实验主体的局部兼容别名。
PARAMS = load_runtime_params(NoiseSpectrumXYParams)
apply_runtime_params(globals(), PARAMS)

print("配置已加载")

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
    # Y 通道: 通过同一设备 CH2 控制
    devices["dg_comp"] = dg_comp

    # ---- DG4000: Z 磁场关闭；Time_sequence_2 为 dg_comp 共用外触发 ----
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep, _ = connect_signal_generator_routes(
        session,
        "dg_sweep",
        {
            "z": ("Z_magnetic_field", dg_sweep_cfg),
            "sequence_2": ("Time_sequence_2", MAPPING["Time_sequence_2"]),
        },
        logical_channels={"z": 1, "sequence_2": 2},
    )
    print(f"Z 场 DG4000 已连接: {dg_sweep.idn()}")
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    print(f"  -> Z 场输出: OFF (本实验不使用)")
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

    clock_sources = synchronize_connected_clocks(
        devices,
        MAPPING,
        {
            "dg_comp": "X_magnetic_field",
            "dg_sweep": "Z_magnetic_field",
            "dg_mod": "Pump_modulation",
            "dg_temp": "Temp_Switch",
            "dg_laser": "Pump_laser_power",
            "hf2": "lockin_r",
        },
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
        deadline = time.monotonic() + TEMP_SWITCH_ON_SETTLE_S
        while (remaining := deadline - time.monotonic()) > 0:
            check_cancelled()
            time.sleep(min(0.1, remaining))


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

# ---- 3. 主磁场 ----
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

# ---- 6. Z 磁场输出确认关闭 ----
validate_safety_limit("Z_magnetic_field", 0.0)
dg_sweep.setup_dc(0.0, channel=1)
dg_sweep.set_output(False, channel=1)
dg_sweep.set_output(False, channel=2)
print("Z 磁场: DC 0V, 输出 OFF (本实验不使用)")

# ---- 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# ---- 保存实验配置到运行目录 ----
instrument_snapshot = instrument_config_snapshot(project_root)
config = {
    "experiment_id": "noise-spectrum-xy",
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

# 启用 Pump 调制输出；dg_comp 外触发由 Time_sequence_2 CH2 经三通提供。
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

# 确认 X/Y 补偿磁场处于关闭状态（尚未配置）
print("确认 X/Y 补偿磁场关闭...")
dg_comp.set_output(False, channel=1)
dg_comp.set_output(False, channel=2)

# 配置信号输入
sig_in_cfg = SignalInputConfig(
    input_index=0, range=HF2_SIGNAL_RANGE,
    ac_coupling=True, diff=False, impedance=50,
)
demod.configure_signal_input(hfi, sig_in_cfg)
print(f"信号输入已配置: {HF2_SIGNAL_RANGE} V range, AC coupled")

# 配置振荡器
osc_cfg = OscillatorConfig(
    osc_index=0, frequency=HF2_OSC_FREQ, source="manual",
)
demod.configure_oscillator(hfi, osc_cfg)
print(f"振荡器已配置: {HF2_OSC_FREQ/1e3:.0f} kHz")

# 配置解调器
demod_cfg = DemodulatorConfig(
    demod_index=0, enable=True, rate=HF2_DEMOD_RATE,
    input_channel=0, osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER, phase=0.0,
)
actual_rate = demod.configure_demodulator(hfi, demod_cfg)
print(f"解调器 0 已配置: rate={actual_rate:.0f} Sa/s, TC={HF2_DEMOD_TC*1000:.3f} ms")

# 自动相位校准
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

# 读一组样本验证
sample = demod0_phase_result.after_sample
print(f"校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

time.sleep(0.5)

# 恢复温控
print("恢复温度开关...")
set_temp_switch(True, wait=True)

# %% Cell 11
# ---- X/Y 控制信号配置（Burst 模式，Time_sequence_2 外触发同步）----
# Bell-Bloom 磁力仪物理原理:
#   原子以 Larmor 频率绕主磁场 B0 进动，Pump 光脉冲与之同步。
#   X/Y 控制磁场必须沿原子极化方向施加，因此:
#     1. X 和 Y 相差 90° 相位（正交控制，沿极化方向旋转）
#     2. XY 整体需相位延迟 XY_CTRL_PHASE 与 Pump 调制对齐
# 同步机制:
#   Time_sequence_2 CH2 的 100 Hz 方波经三通 -> dg_comp CH1/CH2 Ext Trig。
#   触发方波保持常开，作为两路正弦 Burst 的共用相位基准。
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
    # 调幅可能改变实际响应相位；复用校相的重触发顺序，不重配正弦/Burst。
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
print(
    f"相位关系: Time_sequence_2 trigger={XY_TRIGGER_PHASE:.2f}° @ "
    f"{XY_TRIGGER_FREQ:.1f} Hz, sine burst phase={XY_CTRL_PHASE:.2f}°"
)

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
            print(
                f"  样本: R={sample_data['r']:.6f}, "
                f"X={sample_data['x']:.6f}, Y={sample_data['y']:.6f}"
            )
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
print(f"  XY_CTRL_QUAD = {XY_CTRL_QUAD:.2f}°")
print(f"  Y_CTRL_PHASE = {(XY_CTRL_PHASE + XY_CTRL_QUAD) % 360:.2f}°")

# 将最终 Burst 相位写回配置，便于复现实验。
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
config_saved["xy_ctrl"]["trigger_square_freq_Hz"] = float(XY_TRIGGER_FREQ)
config_saved["xy_ctrl"]["trigger_square_phase_deg"] = float(XY_TRIGGER_PHASE)
config_saved["xy_ctrl"]["trigger_source"] = (
    f"Time_sequence_2 CH2 {XY_TRIGGER_FREQ:g} Hz square -> "
    "splitter -> dg_comp CH1/CH2 Ext Trig"
)
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
print("最终 X/Y Burst 相位已写入 experiment_config.yaml")

# %% Cell 15
# ===== 逐点扫描目标噪声频率 + HF2 DAQ 采集 =====

print("=" * 60)
print("目标噪声谱频率扫描")
print("=" * 60)
print(
    f"目标范围: {TARGET_NOISE_FREQ_START_HZ:.1f} Hz -> "
    f"{TARGET_NOISE_FREQ_STOP_HZ:.1f} Hz, {TARGET_NOISE_FREQ_POINTS} 点"
)
print(
    f"反算 V_env: {XY_ENVELOPE_VOLTAGE_LIST_V[0]:.6f} V -> "
    f"{XY_ENVELOPE_VOLTAGE_LIST_V[-1]:.6f} V"
)
print(f"DAQ 采集: 时长 {HF2_DAQ_DURATION}s, TC={HF2_DAQ_TC*1e6:.2f}μs, rate={HF2_DAQ_RATE:.0f} Sa/s")
total_est = TARGET_NOISE_FREQ_POINTS * (
    XY_SETTLE_TIME + HF2_DAQ_DURATION + TEMP_SWITCH_ON_SETTLE_S + 0.3
)
print(f"预计扫描采集与等待耗时: {total_est:.0f}s ≈ {total_est/3600:.1f}h（不含初始化、校相、通信及分析）")
print()

np.savez(
    raw_dir / "control_scan_axes.npz",
    target_noise_frequency_Hz=TARGET_NOISE_FREQ_LIST_HZ,
    xy_envelope_voltage_V=XY_ENVELOPE_VOLTAGE_LIST_V,
    calibration_K_Hz_per_V=np.float64(XY_CTRL_K_HZ_PER_V),
    calibration_B_Hz=np.float64(XY_CTRL_B_HZ),
)

grid_cols = int(actual_rate * HF2_DAQ_DURATION)

# 切换到噪声采集用解调器参数（更短 TC，更高带宽）
print(f"切换到 DAQ 采集解调器参数: TC={HF2_DAQ_TC*1e6:.2f} μs, Rate={HF2_DAQ_RATE} Sa/s")
daq_demod_cfg = DemodulatorConfig(
    demod_index=0, enable=True,
    rate=HF2_DAQ_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DAQ_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
actual_rate_daq = demod.configure_demodulator(hfi, daq_demod_cfg)
grid_cols = int(actual_rate_daq * HF2_DAQ_DURATION)
time.sleep(0.2)

t_start = time.time()

# try/finally 确保异常时恢复温度开关
pbar = tqdm(total=TARGET_NOISE_FREQ_POINTS, desc="Scanning", unit="pt")


def safe_scan_outputs_off():
    """声明本实验需要关闭和保留的输出，交给共享安全步骤执行。"""
    return run_safety_shutdown(
        dg_channels=(
            DGChannelShutdown(dg_comp, 1, "X_magnetic_field", "X 正弦控制"),
            DGChannelShutdown(dg_comp, 2, "Y_magnetic_field", "Y 正弦控制"),
            DGChannelShutdown(dg_sweep, 1, "Z_magnetic_field", "Z 场"),
            DGChannelShutdown(dg_sweep, 2, "Time_sequence_2", "X/Y 共用触发"),
        ),
        temperature_switch=TemperatureSwitchRestore(dg_temp, 2),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


try:
    # 在安全恢复保护内校验硬件实际采样率，并保存派生段长，不作为可调参数。
    actual_welch = welch_settings(float(actual_rate_daq), HF2_DAQ_DURATION,
                                  PARAMS.analysis_bin_width_hz)
    config_saved["hf2_daq"].update(actual_rate_Sa_s=float(actual_rate_daq), **actual_welch)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
    print(f"Welch 实际分段: {actual_welch}")
    for i, (target_freq_hz, envelope_v) in enumerate(
        zip(
            TARGET_NOISE_FREQ_LIST_HZ,
            XY_ENVELOPE_VOLTAGE_LIST_V,
            strict=True,
        )
    ):
        check_cancelled()
        pbar.set_postfix_str(
            f"Omega={target_freq_hz/1e3:.2f}kHz, V={envelope_v:.3f}"
        )

        set_xy_sine_peak(envelope_v)
        time.sleep(0.3)

        # 关闭温度开关（消除温控磁场干扰）
        set_temp_switch(False)

        # 等待系统稳定
        time.sleep(XY_SETTLE_TIME)

        # 读取当前温度
        try:
            temp_now = tec.get_temperature(channel=1)
            pbar.set_postfix_str(
                f"Omega={target_freq_hz/1e3:.2f}kHz, "
                f"V={envelope_v:.3f}, T={temp_now:.1f}°C"
            )
        except:
            pbar.set_postfix_str(
                f"Omega={target_freq_hz/1e3:.2f}kHz, V={envelope_v:.3f}"
            )

        # 配置 HF2 DAQ 采集（每点独立配置）
        daq_cfg = DAQConfig(
            device=MAPPING["lockin_r"]["device_id"],
            trigger_type=0,                  # 连续模式
            duration=HF2_DAQ_DURATION,
            grid_cols=grid_cols,
            grid_rows=1,
            grid_mode=2,
            signal_paths=["sample.y"],       # 采集幅值信号
        )

        # 采集解调时域信号
        try:
            results = daq.acquire_data(
                hfi, daq_cfg, demod_idx=0,
                actual_rate=actual_rate_daq, timeout=HF2_DAQ_DURATION + 10.0,
            )
            waveform = results[0].values
        except Exception as e:
            tqdm.write(
                f"  [{i:4d}/{TARGET_NOISE_FREQ_POINTS}] "
                f"Omega={target_freq_hz:.3f}Hz, V_env={envelope_v:.6f}V: "
                f"DAQ 采集失败: {e}"
            )
            waveform = np.array([np.nan])

        # 保存原始波形（.npy 二进制格式）
        np.save(raw_dir / f"waveform_C{i:04d}.npy", waveform)

        # 恢复温度开关
        set_temp_switch(True, wait=True)

        pbar.update(1)

except Exception as e:
    print(f"\n❌ 扫描出错: {e}")
    print("正在执行异常安全恢复...")
    raise

finally:
    pbar.close()
    shutdown_report = safe_scan_outputs_off()
    if shutdown_report.errors:
        print("安全关闭警告: " + "；".join(shutdown_report.errors))
    else:
        print("安全关闭完成，温度开关已恢复 ON；Pump 载波和时序门控保持输出")

elapsed_total = time.time() - t_start
print(
    f"\n✅ 扫描完成！共 {TARGET_NOISE_FREQ_POINTS} 点, "
    f"用时 {elapsed_total:.0f}s"
)
print(f"原始波形保存在: {raw_dir}")

# 保存实际采样率和原始文件列表到 experiment_config.yaml
with open(config_path, encoding="utf-8") as f:
    config_saved = yaml.safe_load(f)
config_saved["hf2_daq"]["actual_rate_Sa_s"] = float(actual_rate_daq)
config_saved["data_files"] = [
    "raw/control_scan_axes.npz",
    *[
        f"raw/waveform_C{i:04d}.npy"
        for i in range(TARGET_NOISE_FREQ_POINTS)
    ],
]
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
print(f"实际 DAQ 采样率: {actual_rate_daq} Sa/s (已写入 experiment_config.yaml)")

# 恢复解调器至相位校准参数
print("恢复解调器 TC...")
restore_demod_cfg = DemodulatorConfig(
    demod_index=0, enable=True,
    rate=HF2_DEMOD_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
demod.configure_demodulator(hfi, restore_demod_cfg)
time.sleep(0.2)
print(f"解调器已恢复: TC={HF2_DEMOD_TC*1e6:.0f} μs, Rate={HF2_DEMOD_RATE} Sa/s")

print("其他设备保持连接")
