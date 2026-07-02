# %% [markdown] Cell 0
# # Z 方向磁场频率响应 / 带宽测量
#
# 测量 Bell-Bloom 磁力仪对 Z 方向低频正弦扰动磁场的频率响应，
# 根据幅频响应曲线计算 -3 dB 带宽。
#
# ## 实验思路
# - **固定 Z 场幅度**（小信号近似，假定系统线性），扫描 Z 场正弦频率
# - 沿用 [Static_Magnetic_Field_Sensitivity.py](Static_Magnetic_Field_Sensitivity.py) 的
#   磁场/光功率/温度/Pump 调制/HF2 基础配置
# - Z 场驱动使用 mapping.yaml 中 `Z_magnetic_field` 对应的 DG4000 CH1
# - HF2 采集 Demod 0 的 Y 信号，**支持两种采集模式**以便交叉对比：
#   - `daq_fft_y`：采集 sample.y 时域数据，离线 FFT/Welch 取驱动频率处幅值
#   - `demod_r`：配置额外 Demod 3（参考 = Z 驱动频率，输入 = Demod 0 Y）直接读 R
#   - `both`：两种都做
# - 默认模式 `both`，同时给出 0 Hz 基线（关闭 Z 场输出）作为背景
#
# ## 信号链路
# - `dg_sweep` CH1（Z_magnetic_field）→ Z 线圈
# - `dg_laser` CH1/CH2 → Pump/Probe 光功率 DC
# - `dg_mod` CH1 + CH2 → RF 开关方案 Pump 调制（100 MHz + 90 kHz 5% 门控）
# - `dg_comp` CH1/CH2 → X/Y 补偿磁场
# - `dg_temp` CH2 → 温度开关（采集期间 OFF，采完恢复 ON）
# - GS200 → 主磁场恒流源
# - TEC103 → 气室温度控制
# - HF2 Demod 0 (90 kHz) → 主信号解调；Demod 3 (Z 驱动频率) → 射频场解调
#
# ## 注意事项
# - 0 Hz 不作为真正正弦驱动点：DC 0V + 输出 OFF，采集 1 s Y 作为背景/基线
# - 每次切换 Z 场频率后等待 `FREQ_SETTLE_TIME` 让信号稳定
# - 每次采集前关闭温度开关避免温控磁场干扰锁相读数
# - `setup_sine(freq=...)` 重绘波形会在每次频率变化时重新触发；这是**频率**扫描，
#   按仓库规范允许使用 `setup_sine`，不要混用 `set_amplitude`

# %% Cell 1
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
import json
from datetime import datetime
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from tqdm import tqdm

# 设备库
from gs200 import GS200Instrument
from signal_generator import DG4000Instrument, DG900Instrument
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)

print("所有库导入成功")

# %% Cell 2
# ========== 加载配置 ==========
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]


def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错。"""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if lo is not None and hi is not None:
        if value < lo or value > hi:
            raise ValueError(f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]")
    return value


# ========== 实验标识 ==========
EXPERIMENT_TYPE = "Z_Field_Bandwidth_Measurement"
RUN_TAG = "z_bw"

# ========== Z 场驱动参数 ==========
# 固定幅度正弦波，扫描频率
Z_DRIVE_AMPLITUDE = 0.01        # Vpp；小信号近似；设置前必须 validate_safety_limit

Z_FREQ_START = 0.0              # 起始频率 (Hz)，含 0 Hz 作为基线
Z_FREQ_STOP = 1000.0           # 终止频率 (Hz)
Z_FREQ_POINTS = 101             # 频率扫描点数
Z_FREQ_LIST = np.linspace(Z_FREQ_START, Z_FREQ_STOP, Z_FREQ_POINTS)

# ========== 每频点采集参数 ==========
POINT_DURATION = 1.0            # 每个频点采集时长 (s)
FREQ_SETTLE_TIME = 0.5          # 切换频率后等待稳定时间 (s)
TEMP_SWITCH_OFF_LEAD = 0.1      # 采集前关温度开关后等待磁场残余消失 (s)
TEMP_SWITCH_ON_LAG = 1.0        # 采集后开温度开关后等待系统稳定 (s)

# ========== 采集模式 ==========
# "daq_fft_y"  — 采集 Demod 0 sample.y 时域数据，离线 FFT 取幅值
# "demod_r"    — 配置额外 Demod 3（参考 = Z 驱动频率）直接读 R
# "both"       — 两种都做
ACQUISITION_MODE = "both"

# ========== HF2 Demod 0 配置（主信号解调，参考 = Pump 调制频率）==========
HF2_DEMOD0_IDX = 0
HF2_DEMOD0_OSC_IDX = 0
HF2_DEMOD0_RATE = 50000         # 高速率，用于覆盖 ~10 kHz 频段
HF2_DEMOD0_TC = 1e-6            # 测量时 TC（短 TC 保留高带宽）
HF2_DEMOD0_TC_CALIB = 1e-3      # 校相时 TC（长 TC 让读数稳定）
HF2_DEMOD0_ORDER = 4
HF2_DEMOD0_SIGNAL_RANGE = 2.0
HF2_DEMOD0_OSC_FREQ = 90e3      # 90 kHz = Pump 调制频率

# ========== HF2 Demod 3 配置（射频场解调，参考 = Z 驱动频率）==========
DEMOD_R_IDX = 3                 # Demod 3 作 Z 驱动解调
DEMOD_R_OSC_IDX = 1             # Osc 1 频率跟随 Z 驱动频率
DEMOD_R_RATE = 4800             # 解调输出速率
DEMOD_R_TC_MIN = 0.01         # TC 下限，避免过低引入低频噪声
DEMOD_R_TC_PER_PERIOD = 0.05    # TC = 周期 × 该系数（确保 < 周期/2π）
DEMOD_R_ORDER = 8
DEMOD_R_N_AVG = 50              # demod_r 模式下每频点平均次数
DEMOD_R_AVG_INTERVAL = 0.01     # demod_r 模式下读取间隔 (s)
DEMOD_R_READ_SETTLE_TIME = 0.2  # 切换频率后等待 Demod 3 稳定的时间 (s)

# ========== HF2 物理链路: AuxOut2 → SigIn2 → Demod3 ==========
# 链路: Demod0 sample.y -> Aux Output 2 -> 物理线 -> Signal Input 2 -> Demod 3
# 用于诊断 hardware Demod 3 与 offline 软件锁相 / 直接 FFT 的差异
CONFIGURE_AUXOUT2_TO_DEMOD0_Y = True
AUXOUT_INDEX = 1                # 0=Aux Out 1, 1=Aux Out 2 (HF2 后面板)
AUXOUT_SOURCE_DEMOD_IDX = 0     # Demod 0
AUXOUT_SOURCE_SELECT = 1        # 1=Demod Y (0=X, 1=Y, 2=R, 3=Theta)
AUXOUT_SCALE = 1.0
AUXOUT_OFFSET = 0.0
SIGIN2_INDEX = 1                # 0=Signal In 1, 1=Signal In 2 (HF2 后面板)
SIGIN2_RANGE = 1.0
SIGIN2_AC_COUPLING = False      # 默认 DC 耦合（与 SigIn 0 的 AC 耦合不同）
SIGIN2_IMPEDANCE = 50
# Demod 3 的输入选择 Signal Input 2（adcselect = SIGIN2_INDEX）
DEMOD_R_ADC_SELECT = SIGIN2_INDEX

# ========== 固定实验参数（沿用 Static_Magnetic_Field_Sensitivity）==========
PUMP_MOD_FREQ = 90e3            # Pump 调制重复频率 (Hz)
PUMP_MOD_AMPLITUDE = 0.18       # 100 MHz 载波幅度 (Vpp)
PUMP_MOD_DUTY = 5               # 脉冲占空比 (%)

# RF 开关门控参数
RF_GATE_AMPLITUDE = 5.0         # CH2 门控脉冲幅度 (Vpp)
RF_GATE_OFFSET = 2.5            # CH2 门控脉冲偏置 (V)
RF_GATE_DELAY = 0.0

# 固定参数
FIXED_PARAMS = {
    "Pump_laser_power": 0.1,
    "Probe_laser_power": 0.1,
    "temperature": 100,
    "Temp_Switch": 5.0,
    "Time_sequence": 10.0,
    "main_magnetic_field": 9.31,
    "X_magnetic_field": 0,
    "Y_magnetic_field": 0,
    "Time_sequence_2": 0.0,
}

# 磁场校准系数
Z_V_TO_NT = 3517
Z_V_TO_FT = Z_V_TO_NT * 1e6

# ========== 安全校验 ==========
for k, v in FIXED_PARAMS.items():
    validate_safety_limit(k, v)
validate_safety_limit("Z_magnetic_field", Z_DRIVE_AMPLITUDE)

print(f"实验类型: {EXPERIMENT_TYPE}")
print(f"运行标签: {RUN_TAG}")
print(f"采集模式: {ACQUISITION_MODE}")
print(f"Z 驱动幅度: {Z_DRIVE_AMPLITUDE} Vpp = {Z_DRIVE_AMPLITUDE * Z_V_TO_NT:.2f} nT p-p")
print(f"频率扫描: {Z_FREQ_LIST[0]:.1f} ~ {Z_FREQ_LIST[-1]:.1f} Hz, {len(Z_FREQ_LIST)} 点")
print(f"每频点采集时长: {POINT_DURATION} s")

# %% Cell 3
# ========== 连接设备 ==========
devices = {}

try:
    # ---- GS200: 主磁场 ----
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = GS200Instrument(gs_cfg["resource"])
    gs.connect()
    print(f"GS200 已连接: {gs.idn()}")
    gs.set_source_function(gs_cfg["source_function"])
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    # ---- DG4000: Z 磁场（频率扫描）----
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = DG4000Instrument(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"dg_sweep (Z 场) 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    devices["dg_sweep"] = dg_sweep

    # ---- DG912 Pro: Pump/Probe 光功率 (DC) ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = DG900Instrument(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    print(f"dg_laser 已连接: {dg_laser.idn()}")
    devices["dg_laser"] = dg_laser

    # ---- DG4000: X/Y 补偿磁场 ----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = DG4000Instrument(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"dg_comp 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)
    devices["dg_comp"] = dg_comp

    # ---- DG4000: Pump 调制 + 时序门控 ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = DG4000Instrument(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    print(f"dg_mod 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # ---- DG912 Pro: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = DG900Instrument(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    print(f"dg_temp 已连接: {dg_temp.idn()}")
    devices["dg_temp"] = dg_temp

    # ---- TEC103: 温度控制器 ----
    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
    print(f"TEC103 已连接")
    devices["tec"] = tec

    # ---- HF2: 锁相放大器 ----
    hf2_cfg = MAPPING["lockin_r"]
    hfi = HF2Instrument(
        host=hf2_cfg.get("host", "127.0.0.1"),
        port=hf2_cfg.get("port", 8005),
        api_level=1,
        device_id=hf2_cfg["device_id"],
    )
    hfi.connect()
    print(f"HF2 已连接: {hfi.idn}")
    hfi.set_extclk(True)
    print(f"  -> HF2 时钟源: 外部")
    devices["hf2"] = hfi

except Exception as e:
    print(f"设备连接失败: {e}")
    for name, dev in devices.items():
        try:
            if hasattr(dev, "disconnect"):
                dev.disconnect()
        except Exception:
            pass
    raise

print(f"\n所有设备连接完成，共 {len(devices)} 个设备")

# %% Cell 4
# ========== 初始值设置 ==========
hfi = devices["hf2"]
tec = devices["tec"]
gs = devices["gs200"]
dg_laser = devices["dg_laser"]
dg_comp = devices["dg_comp"]
dg_sweep = devices["dg_sweep"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]

# ---- 0. 复位 Z 场通道的 burst/mod 状态（避免残留模式干扰）----
dg_sweep.set_burst_state(False, channel=1)
dg_sweep.set_mod_state(False, channel=1)
dg_sweep.setup_dc(0.0, channel=1)
dg_sweep.set_output(False, channel=1)
print("Z 场通道已复位: DC 0V, burst OFF, mod OFF, output OFF")

# ---- 1. Pump 光功率 (dg_laser CH1) ----
validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
print(f"Pump 光功率: {FIXED_PARAMS['Pump_laser_power']} V DC")

# ---- 2. Probe 光功率 (dg_laser CH2) ----
validate_safety_limit("Probe_laser_power", FIXED_PARAMS["Probe_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Probe_laser_power"], channel=2)
print(f"Probe 光功率: {FIXED_PARAMS['Probe_laser_power']} V DC")

# ---- 3. X/Y 补偿磁场（0 时关闭输出）----
validate_safety_limit("X_magnetic_field", FIXED_PARAMS["X_magnetic_field"])
validate_safety_limit("Y_magnetic_field", FIXED_PARAMS["Y_magnetic_field"])
dg_comp.setup_dc(FIXED_PARAMS["X_magnetic_field"], channel=1)
dg_comp.setup_dc(FIXED_PARAMS["Y_magnetic_field"], channel=2)
dg_comp.set_output(False, channel=1)
dg_comp.set_output(False, channel=2)
print(f"X 补偿磁场: {FIXED_PARAMS['X_magnetic_field']} V (OFF)")
print(f"Y 补偿磁场: {FIXED_PARAMS['Y_magnetic_field']} V (OFF)")

# ---- 4. 主磁场 (GS200, 电流模式) ----
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)  # mA → A
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# ---- 5. 温度控制（等待稳定 ±1°C）----
validate_safety_limit("temperature", FIXED_PARAMS["temperature"])
tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
tec.set_enable(True, channel=1)
temp_now = tec.get_temperature(channel=1)
print(f"温度设定: {FIXED_PARAMS['temperature']} °C, 当前: {temp_now:.1f} °C")
print("等待温度稳定...")
while True:
    time.sleep(5)
    t = tec.get_temperature(channel=1)
    print(f"  当前温度: {t:.2f} °C")
    if abs(t - FIXED_PARAMS["temperature"]) < 1:
        print(f"温度已稳定: {t:.2f} °C")
        break

# ---- 6. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 7. 时序信号 10Hz 方波 (dg_sweep CH2) ----
validate_safety_limit("Time_sequence", FIXED_PARAMS["Time_sequence"])
dg_sweep.setup_square(freq=10.0, amplitude=FIXED_PARAMS["Time_sequence"],
                      offset=0.0, dcycle=50.0, channel=2)
dg_sweep.set_output(True, channel=2)
print(f"时序信号: 10 Hz 方波, {FIXED_PARAMS['Time_sequence']} V (由 dg_sweep CH2 提供)")

# ---- 8. Pump 调制配置 (RF 开关方案) ----
print("\n--- Pump 调制配置 (RF 开关方案) ---")
# 先复位 burst/mod
dg_mod.set_burst_state(False, channel=1)
dg_mod.set_mod_state(False, channel=1)
dg_mod.set_burst_state(False, channel=2)
dg_mod.set_mod_state(False, channel=2)

# CH1: 100 MHz 连续正弦 → RF 开关 IN
validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE)
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE,
                  offset=0.0, phase=0.0, channel=1)
print(f"  CH1: 100 MHz 正弦, {PUMP_MOD_AMPLITUDE*1000:.0f} mVpp → RF 开关 IN")

# CH2: 脉冲门控
pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                   offset=RF_GATE_OFFSET, width=pulse_width, channel=2)
print(f"  CH2: 脉冲 {PUMP_MOD_FREQ/1e3:.0f} kHz, {RF_GATE_AMPLITUDE:.1f} Vpp, "
      f"offset {RF_GATE_OFFSET:.1f}V, 占空比 {PUMP_MOD_DUTY}% → RF 开关 CTRL")

# ---- 9. 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"\n运行目录: {run_dir}")

# %% Cell 5
# ============================================================
# Phase 1: HF2 配置 + Demod 0 相位校准
# ============================================================
print("=" * 60)
print("Phase 1: HF2 配置 + Demod 0 相位校准")
print("=" * 60)

# ---- 关闭温控（避免温控磁场干扰锁相读数）----
print("关闭温度开关 (校相期间)...")
dg_temp.set_output(False, channel=2)
time.sleep(0.5)

# ---- 配置信号输入 0 ----
sig_in_cfg = SignalInputConfig(
    input_index=0,
    range=HF2_DEMOD0_SIGNAL_RANGE,
    ac_coupling=True,
    diff=False,
    impedance=50,
)
demod.configure_signal_input(hfi, sig_in_cfg)
print(f"信号输入 0: range={HF2_DEMOD0_SIGNAL_RANGE} V, AC 耦合")

# ---- 配置 Osc 0 (Pump 调制频率 90 kHz) ----
osc0_cfg = OscillatorConfig(
    osc_index=HF2_DEMOD0_OSC_IDX,
    frequency=HF2_DEMOD0_OSC_FREQ,
)
demod.configure_oscillator(hfi, osc0_cfg)
print(f"振荡器 {HF2_DEMOD0_OSC_IDX}: {HF2_DEMOD0_OSC_FREQ/1e3:.0f} kHz")

# ---- 配置 Osc 1 (Z 驱动频率，初值 = 1 kHz 占位) ----
osc1_initial_freq = 1000.0
osc1_cfg = OscillatorConfig(
    osc_index=DEMOD_R_OSC_IDX,
    frequency=osc1_initial_freq,
)
demod.configure_oscillator(hfi, osc1_cfg)
print(f"振荡器 {DEMOD_R_OSC_IDX}: {osc1_initial_freq:.0f} Hz (初值，扫描时更新)")

# ---- 配置 Demod 0 (校相时用长 TC) ----
demod0_calib_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD0_IDX,
    enable=True,
    rate=HF2_DEMOD0_RATE,
    input_channel=0,
    osc_select=HF2_DEMOD0_OSC_IDX,
    harmonic=1,
    time_constant=HF2_DEMOD0_TC_CALIB,
    order=HF2_DEMOD0_ORDER,
    phase=0.0,
)
actual_rate_d0 = demod.configure_demodulator(hfi, demod0_calib_cfg)
print(f"Demod 0 (校相): rate={actual_rate_d0:.0f} Sa/s, "
      f"TC={HF2_DEMOD0_TC_CALIB*1000:.1f} ms, order={HF2_DEMOD0_ORDER}")

# ---- 配置 Demod 3 (射频场解调，初值 1 kHz) ----
demod_r_initial_cfg = DemodulatorConfig(
    demod_index=DEMOD_R_IDX,
    enable=True,
    rate=DEMOD_R_RATE,
    input_channel=DEMOD_R_ADC_SELECT,
    osc_select=DEMOD_R_OSC_IDX,
    harmonic=1,
    time_constant=max(DEMOD_R_TC_MIN,
                      DEMOD_R_TC_PER_PERIOD / osc1_initial_freq),
    order=DEMOD_R_ORDER,
    phase=0.0,
)
actual_rate_dR = demod.configure_demodulator(hfi, demod_r_initial_cfg)
print(f"Demod {DEMOD_R_IDX} (射频场, 初值): rate={actual_rate_dR:.0f} Sa/s, "
      f"TC={demod_r_initial_cfg.time_constant*1e3:.2f} ms, "
      f"adcselect={DEMOD_R_ADC_SELECT}")

# ---- 配置 Signal Input 2 (DC 耦合) ----
sig_in2_cfg = SignalInputConfig(
    input_index=SIGIN2_INDEX,
    range=SIGIN2_RANGE,
    ac_coupling=SIGIN2_AC_COUPLING,
    diff=False,
    impedance=SIGIN2_IMPEDANCE,
)
demod.configure_signal_input(hfi, sig_in2_cfg)
print(f"信号输入 {SIGIN2_INDEX} (Signal Input 2): "
      f"range={SIGIN2_RANGE} V, "
      f"{'AC' if SIGIN2_AC_COUPLING else 'DC'} 耦合, "
      f"imp={SIGIN2_IMPEDANCE}Ω")

# ---- 配置 Aux Output 2 → Demod 0 Y ----
if CONFIGURE_AUXOUT2_TO_DEMOD0_Y:
    auxout_path = hfi.aux_out_path(AUXOUT_INDEX)
    # HF2 LabOne: outputselect = demod_index * 4 + signal_type
    #   signal_type: 0=X, 1=Y, 2=R, 3=Theta
    auxout_outputselect = AUXOUT_SOURCE_DEMOD_IDX * 4 + AUXOUT_SOURCE_SELECT
    hfi.set_int(f"{auxout_path}/outputselect", auxout_outputselect)
    hfi.set_double(f"{auxout_path}/scale", AUXOUT_SCALE)
    hfi.set_double(f"{auxout_path}/offset", AUXOUT_OFFSET)
    hfi.sync()
    sig_type_str = {0: "X", 1: "Y", 2: "R", 3: "Theta"}[AUXOUT_SOURCE_SELECT]
    print(f"Aux Out {AUXOUT_INDEX+1} (auxouts/{AUXOUT_INDEX}): "
          f"Demod{AUXOUT_SOURCE_DEMOD_IDX} {sig_type_str}, "
          f"scale={AUXOUT_SCALE}, offset={AUXOUT_OFFSET} V")

    # 读回实际值用于 config 快照
    auxout_actual_outputselect = hfi.get_int(f"{auxout_path}/outputselect")
    auxout_actual_scale = hfi.get_double(f"{auxout_path}/scale")
    auxout_actual_offset = hfi.get_double(f"{auxout_path}/offset")
    # signal_type = outputselect % 4, demod_idx = outputselect // 4
    auxout_actual_demod = auxout_actual_outputselect // 4
    auxout_actual_sig = auxout_actual_outputselect % 4
    auxout_actual_sig_str = {0: "X", 1: "Y", 2: "R", 3: "Theta"}[auxout_actual_sig]
else:
    auxout_actual_outputselect = None
    auxout_actual_scale = None
    auxout_actual_offset = None
    auxout_actual_demod = None
    auxout_actual_sig_str = None

# 读回 Signal Input 2 实际配置
sigin2_actual_path = hfi.sig_in_path(SIGIN2_INDEX)
sigin2_actual_range = hfi.get_double(f"{sigin2_actual_path}/range")
sigin2_actual_ac = hfi.get_int(f"{sigin2_actual_path}/ac")
sigin2_actual_imp50 = hfi.get_int(f"{sigin2_actual_path}/imp50")

# 读回 Demod 3 实际配置
demod_r_actual_path = hfi.demod_path(DEMOD_R_IDX)
demod_r_actual_adcselect = hfi.get_int(f"{demod_r_actual_path}/adcselect")
demod_r_actual_rate = hfi.get_double(f"{demod_r_actual_path}/rate")
demod_r_actual_tc = hfi.get_double(f"{demod_r_actual_path}/timeconstant")
demod_r_actual_order = hfi.get_int(f"{demod_r_actual_path}/order")
demod_r_actual_osc = hfi.get_int(f"{demod_r_actual_path}/oscselect")
print(f"读回 Demod {DEMOD_R_IDX}: "
      f"adcselect={demod_r_actual_adcselect}, "
      f"oscselect={demod_r_actual_osc}, "
      f"rate={demod_r_actual_rate:.0f} Sa/s, "
      f"TC={demod_r_actual_tc*1e3:.2f} ms, order={demod_r_actual_order}")
# %% Cell 5b
# ---- 自动校相 (Demod 0) ----
print("正在进行 Demod 0 相位校准...")
calibrated_phase = demod.auto_calibrate_phase(
    hfi, demod_idx=HF2_DEMOD0_IDX,
    tolerance_deg=1.0, max_attempts=5, settle_time=0.2,
)
print(f"Demod 0 校准完成: phaseshift = {calibrated_phase:.2f}°")

# ---- 验证 ----
sample = demod.read_demod_sample(hfi, demod_idx=HF2_DEMOD0_IDX)
print(f"  校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

# ---- 恢复温控 ----
dg_temp.set_output(True, channel=2)
print("温度开关已恢复")
# %% Cell 5c
# ---- 切换 Demod 0 至测量 TC（短 TC 保留高带宽）----
demod0_meas_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD0_IDX, enable=True,
    rate=HF2_DEMOD0_RATE,
    input_channel=0,
    osc_select=HF2_DEMOD0_OSC_IDX, harmonic=1,
    time_constant=HF2_DEMOD0_TC,
    order=HF2_DEMOD0_ORDER,
    phase=calibrated_phase,
)
actual_rate_d0_meas = demod.configure_demodulator(hfi, demod0_meas_cfg)
print(f"Demod 0 (测量): rate={actual_rate_d0_meas:.0f} Sa/s, "
      f"TC={HF2_DEMOD0_TC*1e6:.0f} μs, phase={calibrated_phase:.2f}°")
# %% Cell 5d
# ---- 保存实验配置 ----
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "timestamp": timestamp,
    "run_tag": RUN_TAG,
    "acquisition_mode": ACQUISITION_MODE,
    "freq_sweep": {
        "Z_FREQ_START_Hz": Z_FREQ_START,
        "Z_FREQ_STOP_Hz": Z_FREQ_STOP,
        "Z_FREQ_POINTS": Z_FREQ_POINTS,
        "freq_list_Hz": Z_FREQ_LIST.tolist(),
        "Z_DRIVE_AMPLITUDE_Vpp": Z_DRIVE_AMPLITUDE,
        "FREQ_SETTLE_TIME_s": FREQ_SETTLE_TIME,
        "POINT_DURATION_s": POINT_DURATION,
        "is_baseline_handling": (
            "f == 0 时 Z 场 DC 0V + output OFF，"
            "采集 1 s Y 作为基线；不参与带宽计算"
        ),
    },
    "hf2_demod0": {
        "demod_idx": HF2_DEMOD0_IDX,
        "osc_idx": HF2_DEMOD0_OSC_IDX,
        "osc_freq_Hz": HF2_DEMOD0_OSC_FREQ,
        "signal_range_V": HF2_DEMOD0_SIGNAL_RANGE,
        "rate_Sa_s_requested": HF2_DEMOD0_RATE,
        "rate_Sa_s_actual": float(actual_rate_d0_meas),
        "TC_calib_s": HF2_DEMOD0_TC_CALIB,
        "TC_meas_s": HF2_DEMOD0_TC,
        "order": HF2_DEMOD0_ORDER,
        "calibrated_phase_deg": float(calibrated_phase),
    },
    "hf2_demod_r": {
        "demod_idx": DEMOD_R_IDX,
        "osc_idx": DEMOD_R_OSC_IDX,
        "adcselect_requested": DEMOD_R_ADC_SELECT,
        "adcselect_actual": int(demod_r_actual_adcselect),
        "oscselect_actual": int(demod_r_actual_osc),
        "rate_Sa_s_requested": DEMOD_R_RATE,
        "rate_Sa_s_actual": float(demod_r_actual_rate),
        "TC_min_s": DEMOD_R_TC_MIN,
        "TC_per_period": DEMOD_R_TC_PER_PERIOD,
        "TC_initial_s": float(demod_r_actual_tc),
        "order_requested": DEMOD_R_ORDER,
        "order_actual": int(demod_r_actual_order),
        "n_avg": DEMOD_R_N_AVG,
        "avg_interval_s": DEMOD_R_AVG_INTERVAL,
        "read_settle_time_s": DEMOD_R_READ_SETTLE_TIME,
    },
    "hf2_signal_input_2": {
        "input_index": SIGIN2_INDEX,
        "range_V_requested": SIGIN2_RANGE,
        "range_V_actual": float(sigin2_actual_range),
        "ac_coupling_requested": SIGIN2_AC_COUPLING,
        "ac_coupling_actual": bool(sigin2_actual_ac == 1),
        "imp50_actual": bool(sigin2_actual_imp50 == 1),
        "impedance_requested_ohm": SIGIN2_IMPEDANCE,
    },
    "hf2_aux_out_2": {
        "auxout_index": AUXOUT_INDEX,
        "enabled": CONFIGURE_AUXOUT2_TO_DEMOD0_Y,
        "source_demod_requested": AUXOUT_SOURCE_DEMOD_IDX,
        "source_select_requested": AUXOUT_SOURCE_SELECT,
        "scale_requested": AUXOUT_SCALE,
        "offset_requested": AUXOUT_OFFSET,
        "outputselect_actual": (int(auxout_actual_outputselect)
                                if auxout_actual_outputselect is not None else None),
        "source_demod_actual": auxout_actual_demod,
        "source_signal_actual": auxout_actual_sig_str,
        "scale_actual": (float(auxout_actual_scale)
                          if auxout_actual_scale is not None else None),
        "offset_actual": (float(auxout_actual_offset)
                           if auxout_actual_offset is not None else None),
        "signal_chain": (
            "Demod0 sample.y -> Aux Out 2 (auxouts/1, Y signal) "
            "-> 物理线 -> Signal Input 2 (sigins/1, DC coupled) "
            "-> Demod 3 (adcselect=1)"
        ),
    },
    "pump_modulation": {
        "PUMP_MOD_FREQ_Hz": PUMP_MOD_FREQ,
        "PUMP_MOD_DUTY_pct": PUMP_MOD_DUTY,
        "PUMP_MOD_AMPLITUDE_Vpp": PUMP_MOD_AMPLITUDE,
        "RF_GATE_AMPLITUDE_Vpp": RF_GATE_AMPLITUDE,
        "RF_GATE_OFFSET_V": RF_GATE_OFFSET,
    },
    "fixed_params": FIXED_PARAMS,
    "Z_V_to_nT": Z_V_TO_NT,
    "Z_V_to_fT": Z_V_TO_FT,
    "mapping_snapshot": MAPPING,
    "safety_limits_snapshot": LIMITS,
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print(f"实验配置已保存: {config_path}")

# %% Cell 6
# ============================================================
# Phase 2: Z 场频率扫描 (try/finally 确保异常时安全关闭)
# ============================================================
print("\n" + "=" * 60)
print("Phase 2: Z 场频率扫描")
print("=" * 60)
print(f"频率点数: {len(Z_FREQ_LIST)}")
print(f"固定幅度: {Z_DRIVE_AMPLITUDE} Vpp")
print(f"采集模式: {ACQUISITION_MODE}")

# ---- 数据容器 ----
freq_index_records = []   # [(idx, freq_Hz, is_baseline, file_path), ...]

try:
    for fi, freq in enumerate(tqdm(Z_FREQ_LIST, desc="频率扫描")):
        is_baseline = (freq == 0.0)
        record = {
            "idx": fi,
            "freq_Hz": float(freq),
            "is_baseline": bool(is_baseline),
            "z_drive_amplitude_Vpp": Z_DRIVE_AMPLITUDE,
            "acquisition_mode": ACQUISITION_MODE,
        }

        # ---- 1. 设置 Z 场驱动 ----
        if is_baseline:
            # 0 Hz: DC 0V + 输出关闭，仅作为背景/基线
            validate_safety_limit("Z_magnetic_field", 0.0)
            dg_sweep.setup_dc(0.0, channel=1)
            dg_sweep.set_output(False, channel=1)
        else:
            # 正频率: 固定幅度正弦，频率变化
            dg_sweep.setup_sine(freq=freq, amplitude=Z_DRIVE_AMPLITUDE,
                                offset=0.0, phase=0.0, channel=1)
            dg_sweep.set_output(True, channel=1)
            # 等待频率切换稳定
            time.sleep(FREQ_SETTLE_TIME)

        # ---- 2. (demod_r 模式) 更新 Osc 1 和 Demod 3 跟随 Z 驱动频率 ----
        if ACQUISITION_MODE in ("demod_r", "both") and not is_baseline:
            osc1_cfg = OscillatorConfig(
                osc_index=DEMOD_R_OSC_IDX, frequency=freq,
            )
            demod.configure_oscillator(hfi, osc1_cfg)
            tc = max(DEMOD_R_TC_MIN, DEMOD_R_TC_PER_PERIOD / freq)
            demod_r_cfg = DemodulatorConfig(
                demod_index=DEMOD_R_IDX, enable=True,
                rate=DEMOD_R_RATE,
                input_channel=DEMOD_R_ADC_SELECT,
                osc_select=DEMOD_R_OSC_IDX, harmonic=1,
                time_constant=tc, order=DEMOD_R_ORDER,
                phase=0.0,
            )
            demod.configure_demodulator(hfi, demod_r_cfg)

        # ---- 3. 关闭温度开关（消除温控磁场对锁相读数的干扰）----
        dg_temp.set_output(False, channel=2)
        time.sleep(TEMP_SWITCH_OFF_LEAD)

        # ---- 4. 采集 HF2 数据 ----
        try:
            if ACQUISITION_MODE in ("daq_fft_y", "both"):
                # DAQ 采集 sample.y (Demod 0)
                daq_cfg = DAQConfig(
                    device=MAPPING["lockin_r"]["device_id"],
                    trigger_type=0,
                    duration=POINT_DURATION,
                    grid_cols=int(actual_rate_d0_meas * POINT_DURATION),
                    grid_rows=1,
                    grid_mode=2,
                    signal_paths=["sample.y"],
                )
                daq_results = daq.acquire_data(
                    hfi, config=daq_cfg,
                    demod_idx=HF2_DEMOD0_IDX,
                    actual_rate=actual_rate_d0_meas,
                    timeout=POINT_DURATION + 5.0,
                )
                y_signal = next(
                    (r.values for r in daq_results if r.signal_name == "sample.y"),
                    None,
                )
                t_signal = daq_results[0].time if daq_results else np.array([])
                actual_rate = actual_rate_d0_meas
                if y_signal is not None and len(y_signal) > 0:
                    record["time_s"] = t_signal
                    record["y_V"] = y_signal
                    record["actual_rate_Sa_s"] = float(actual_rate)

            if ACQUISITION_MODE in ("demod_r", "both") and not is_baseline:
                # 等待 Demod 3 稳定（窄带 TC 收敛）
                time.sleep(DEMOD_R_READ_SETTLE_TIME)
                # 多次读取 Demod 3 X/Y/R/theta
                x_list = []
                y_list = []
                r_list = []
                theta_list = []
                for _ in range(DEMOD_R_N_AVG):
                    s = demod.read_demod_sample(hfi, demod_idx=DEMOD_R_IDX)
                    x_list.append(s["x"])
                    y_list.append(s["y"])
                    r_list.append(s["r"])
                    theta_list.append(s["theta"])
                    time.sleep(DEMOD_R_AVG_INTERVAL)
                x_arr = np.asarray(x_list, dtype=float)
                y_arr = np.asarray(y_list, dtype=float)
                r_arr = np.asarray(r_list, dtype=float)
                theta_arr = np.asarray(theta_list, dtype=float)

                # Vector mean: 先平均 X 和 Y，再合成 R_vec
                x_mean = float(np.mean(x_arr))
                y_mean = float(np.mean(y_arr))
                r_vec = float(math.hypot(x_mean, y_mean))
                phase_vec_rad = float(math.atan2(y_mean, x_mean))
                phase_vec_deg = float(math.degrees(phase_vec_rad))
                # Scalar mean: 先 |Z| 再平均 R
                r_scalar_mean = float(np.mean(r_arr))
                r_scalar_std = float(np.std(r_arr))

                record["demod_r_x_values_V"] = x_arr
                record["demod_r_y_values_V"] = y_arr
                record["demod_r_r_values_V"] = r_arr
                record["demod_r_theta_values_rad"] = theta_arr
                record["demod_r_x_mean_V"] = x_mean
                record["demod_r_y_mean_V"] = y_mean
                record["demod_r_r_vector_mean_V"] = r_vec
                record["demod_r_r_mean_scalar_V"] = r_scalar_mean
                record["demod_r_r_std_scalar_V"] = r_scalar_std
                record["demod_r_phase_vector_rad"] = phase_vec_rad
                record["demod_r_phase_vector_deg"] = phase_vec_deg
                record["demod_r_theta_values_deg"] = np.degrees(theta_arr)

        finally:
            # 无论采集是否成功，都恢复温度开关
            dg_temp.set_output(True, channel=2)
            time.sleep(TEMP_SWITCH_ON_LAG)

        # ---- 5. 保存当前频点 ----
        fname = f"freq_{fi:04d}_{freq:.3f}Hz"
        if is_baseline:
            fname = f"freq_{fi:04d}_0Hz_BASELINE"
        save_dict = {
            "freq_Hz": np.float64(freq),
            "is_baseline": np.uint8(int(is_baseline)),
            "z_drive_amplitude_Vpp": np.float64(Z_DRIVE_AMPLITUDE),
            "acquisition_mode": ACQUISITION_MODE,
            "actual_rate_Sa_s": np.float64(record.get("actual_rate_Sa_s", 0.0)),
        }
        if "y_V" in record:
            save_dict["time_s"] = record["time_s"]
            save_dict["y_V"] = record["y_V"]
        if "demod_r_r_vector_mean_V" in record:
            save_dict["demod_r_x_values_V"] = record["demod_r_x_values_V"]
            save_dict["demod_r_y_values_V"] = record["demod_r_y_values_V"]
            save_dict["demod_r_r_values_V"] = record["demod_r_r_values_V"]
            save_dict["demod_r_theta_values_rad"] = record["demod_r_theta_values_rad"]
            save_dict["demod_r_theta_values_deg"] = record["demod_r_theta_values_deg"]
            save_dict["demod_r_x_mean_V"] = np.float64(record["demod_r_x_mean_V"])
            save_dict["demod_r_y_mean_V"] = np.float64(record["demod_r_y_mean_V"])
            save_dict["demod_r_r_vector_mean_V"] = np.float64(
                record["demod_r_r_vector_mean_V"])
            save_dict["demod_r_r_mean_scalar_V"] = np.float64(
                record["demod_r_r_mean_scalar_V"])
            save_dict["demod_r_r_std_scalar_V"] = np.float64(
                record["demod_r_r_std_scalar_V"])
            save_dict["demod_r_phase_vector_rad"] = np.float64(
                record["demod_r_phase_vector_rad"])
            save_dict["demod_r_phase_vector_deg"] = np.float64(
                record["demod_r_phase_vector_deg"])
        fpath = raw_dir / f"{fname}.npz"
        np.savez(fpath, **save_dict)
        freq_index_records.append({
            "idx": fi, "freq_Hz": float(freq),
            "is_baseline": bool(is_baseline),
            "file": fpath.name,
        })

        # ---- 进度打印 ----
        if (fi + 1) % 20 == 0 or fi == 0 or fi == len(Z_FREQ_LIST) - 1:
            extra = ""
            if "demod_r_r_vector_mean_V" in record:
                extra = (f", HW vec={record['demod_r_r_vector_mean_V']:.4e} V, "
                         f"scalar={record['demod_r_r_mean_scalar_V']:.4e}±"
                         f"{record['demod_r_r_std_scalar_V']:.1e} V, "
                         f"φ={record['demod_r_phase_vector_deg']:.1f}°")
            y_n = len(record.get("y_V", []))
            extra += f", y_n={y_n}"
            tag = " (BASELINE)" if is_baseline else ""
            print(f"  [{fi+1}/{len(Z_FREQ_LIST)}] f={freq:.2f} Hz{tag}"
                  f"{extra}")

    # ---- 6. 保存 frequency_index ----
    np.savez(raw_dir / "frequency_index.npz",
             **{"records": np.array(freq_index_records, dtype=object)})
    print(f"\n频点索引已保存: {raw_dir / 'frequency_index.npz'}")

    # 兼容 JSON 格式便于人工阅读
    with open(raw_dir / "frequency_index.json", "w", encoding="utf-8") as f:
        json.dump(freq_index_records, f, indent=2, ensure_ascii=False)
    print(f"频点索引 (JSON): {raw_dir / 'frequency_index.json'}")

    print("\n" + "=" * 60)
    print("频率扫描完成")
    print("=" * 60)

finally:
    # ---- 异常或正常结束时：关闭 Z 场输出，恢复温度开关 ----
    try:
        dg_sweep.setup_dc(0.0, channel=1)
        dg_sweep.set_output(False, channel=1)
        print("[安全清理] Z 场输出已关闭 (DC 0V, OFF)")
    except Exception as e:
        print(f"[安全清理] Z 场关闭失败: {e}")
    try:
        dg_temp.set_output(True, channel=2)
        print("[安全清理] 温度开关已恢复 ON")
    except Exception as e:
        print(f"[安全清理] 温度开关恢复失败: {e}")

# %% Cell 7
# ========== 安全断开（仅断开 TEC，其余设备保持连接） ==========
print("\n正在断开 TEC...")
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")
print("其他设备保持连接")

# %%
