# %% [markdown] Cell 0
# # XY AW Offset 电压标定 — ConstXY 链路
#
# 在当前 ConstXY 实验链路下，通过 dg_am arbitrary offset 重新标定 X/Y AM 控制电压
# `XY_DC_VOLTAGE` 与实际响应中心频率 `f_peak` 的关系：
#
# ```text
# f_peak_Hz = K_eff * XY_DC_VOLTAGE_V + B_eff
# ```
#
# 标定结果用于后续 ConstXY 带宽测量直接由目标频率换算控制电压：
#
# ```text
# XY_DC_VOLTAGE_V = (TARGET_FREQ_Hz - B_eff) / K_eff
# ```
#
# **不**复用旧 AW 方案的 `A_ENV_K=13724 Hz/V` / `A_ENV_B=66 Hz` 作为结论，
# 仅作为历史参考（在 analysis.yaml 中单独标记）。
#
# ## 实验链路（与 RF_Field_Sensitivity_ConstXY_FreqSweep.py 一致）
# - `X_magnetic_field_AM` / `Y_magnetic_field_AM` (DG4E231500376 CH1/CH2)：
#   AM arbitrary offset = `XY_DC_VOLTAGE`（扫描变量，字段名为兼容旧分析保留）
# - `X_magnetic_field` / `Y_magnetic_field` (DG4E234902522 CH1/CH2)：
#   X/Y 90 kHz 载波 + **AM EXT**，AM depth 100%。
#   注意：这两个 mapping key 仅用于定位 dg_comp 载波发生器（通过 mapping.yaml
#   获取设备资源地址）；本实验不使用其直送 DC 模式。XY DC 控制由
#   `X_magnetic_field_AM` / `Y_magnetic_field_AM` 的 AM AW offset 包络实现。
# - `Z_magnetic_field` (DG4E242401288 CH1) 输出固定幅度正弦驱动，扫描其频率。
# - HF2 Demod 0 (90 kHz) 采集 `sample.y` 时域数据，作为**主分析数据**。
# - 硬件 Demod 3 **同时保留为验证**（`ACQUISITION_MODE = "both"`）：
#   Demod 0 Y → Aux Out 2 → 物理 BNC → Signal Input 2 (DC coupled) → Demod 3。
#   ⚠ `adcselect` **必须**指 `SIGIN2_INDEX` (=1, Signal Input 2)，
#   不要使用 `adcselect=2` 当作 Demod 0 Y 的内部路由。
#
# ## 校相对齐（关键：校相 OFF、扫描前 ON）
# 与 `RF_Field_Sensitivity_ConstXY_FreqSweep.py` 一致：校相时**关闭** ConstXY
# 链路，保证校相基线纯净（无 XY 磁场、Z 场、温控干扰）。校相完成后**重新打开**
# ConstXY 链路（carrier + AM 包络），进入扫描：
# - Phase 1 校相前：调用 `shutdown_xy_chain(dg_comp, dg_am)` 把 dg_comp CH1/CH2
#   与 dg_am CH1/CH2 output 都 OFF（波形/调制参数保留）。
# - Phase 1 校相后：调用 `configure_xy_carrier(dg_comp)` 重新配 90 kHz AM EXT
#   载波并打开 output；调用 `set_xy_am_dc(dg_am, PHASE_CAL_XY_DC_VOLTAGE_V, output=True)`
#   把 dg_am CH1/CH2 输出校相用 AW offset 包络。
# - `PHASE_CAL_XY_DC_VOLTAGE_V` 默认取 `XY_DC_VOLTAGE_LIST` 的中位值。
# - `PHASE_CAL_KEEP_XY_ON=True`：校相后保持 carrier 一直 ON，由 Phase 2 直接使用。
# - 扫描每个 XY_DC 电压点只调用 `set_xy_am_dc(xy_dc_v)`，不再重配 carrier；
#   每个点开始前确认 `dg_comp` CH1/CH2 output ON，并记录
#   `xy_carrier_expected_on=True`。
# - 扫描每个 XY_DC 电压点只调用 `set_xy_am_dc(xy_dc_v)`，不再重配 carrier；
#   每个点开始前确认 `dg_comp` CH1/CH2 output ON，并记录
#   `xy_carrier_expected_on=True`。
#
# ## 扫描策略
# - 粗扫 `Z_RF_FREQ` 从 500 Hz 到 6000 Hz，步进 100 Hz
# - 同一 `XY_DC_VOLTAGE` 内完成完整 Z 频率扫描，再切换到下一个电压点
# - 默认电压列表: `[0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20] V`
# - 每个频点采集 `POINT_DURATION_s = 1.0 s`
# - 采集模式默认 `"both"` 同时记录 offline y_V 与 hardware Demod 3。
#   若关心总时长，可临时改回 `"daq_fft_y"`，但 hardware Demod 3 通道会被跳过。
#
# ## 涉及设备
# | 设备 | 作用 |
# |------|------|
# | **dg_comp** (DG4E234902522) | CH1/CH2: X/Y 载波 (90 kHz), AM External |
# | **dg_am** (DG4E231500376) | CH1/CH2: AW offset 包络 = `XY_DC_VOLTAGE` |
# | **dg_sweep** (DG4E242401288) | CH1: Z 射频场 Burst 正弦 (频率扫描) |
# | **dg_mod** (DG4E222800868) | CH1: 100 MHz 正弦; CH2: 90 kHz 5% 脉冲门控 |
# | **dg_laser** (DG9Q280100002) | CH1/CH2: Pump/Probe 光功率 DC |
# | **dg_temp** (DG9Q271200104) | CH2: 温度开关 (5V ON / 0V OFF) |
# | **GS200** | 主磁场恒流源 (~9.3 mA) |
# | **TEC103** | 气室温度控制 |
# | **HF2** (dev18246) | Demod 0 (90 kHz): 主信号; Demod 3 (Z 驱动): 验证链路 |
#
# ## 输出
# - 目录: `data/XY_AW_Voltage_Calibration/<MMDD_HHMM_xy_dc_cal>/`
# - `experiment_config.yaml`：mapping/safety snapshot + 标定参数 + 链路说明
# - `raw/dc_v<N>_V<V>V/zfreq_<N>_<F>Hz.npz`：每个 (XY_DC, Z_freq) 组合一个 npz
# - `raw/dc_v<N>_V<V>V/frequency_index.json`：每个 XY_DC 子目录的频点索引
# - 全局 `raw/frequency_index.json`：所有电压点的总索引（便于 plot 读取）

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
from lab_workflows.devices import create_signal_generator
from tec_controller import TECInstrument
from sds_acquisition import SDSInstrument
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
    """检查数值是否在安全范围内，超出则报错.

    如果该物理量在 safety_limits.yaml 中不存在（例如 X/Y AM 控制电压），则
    **不**报错但打印 WARNING（脚本启动时集中提示），由操作者确认后继续。
    """
    lim = LIMITS.get(name)
    if lim is None:
        # 不抛错；启动期的统一 WARNING 会在下面打印
        return value
    lo, hi = lim["min"], lim["max"]
    if lo is not None and hi is not None:
        if value < lo or value > hi:
            raise ValueError(f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]")
    return value


# ========== 实验标识 ==========
EXPERIMENT_TYPE = "XY_AW_Voltage_Calibration"
RUN_TAG = "xy_aw_cal"

# ========== 采集模式 ==========
# "daq_fft_y"  — DAQ 采集 Demod 0 sample.y 时域数据（推荐，主分析）
# "demod_r"    — 仅采集 hardware Demod 3 R（验证用）
# "both"       — 两种都做（cross-check）；本实验默认采用，以便
#                offline y_V (DAQ) 与 hardware Demod 3 同时记录用于交叉对比。
#                若总时长过长，可临时改回 "daq_fft_y"。
ACQUISITION_MODE = "both"

# ========== XY AW offset 电压扫描参数 ==========
XY_DC_VOLTAGE_LIST = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]  # (V)
# XY_DC_VOLTAGE_LIST = [0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20]  # (V)
# dg_am 使用 arbitrary waveform 的 offset 施加常数 AM 电压。
# 兼容现有分析脚本，扫描变量名仍保留 XY_DC_VOLTAGE_LIST。
A_ENV_FREQ = 500.0
CONST_AW_POINTS = 1024
CONST_AW_VPP = 0.002
# 旧 AW 方案的历史参考系数（仅作为参考，不作为本实验结论）
A_ENV_K_LEGACY = 13724.0   # Hz/V
A_ENV_B_LEGACY = 66.0      # Hz

# ========== 校相时的 XY DC 电压与状态保持 ==========
# 本实验在校相时必须保持 ConstXY 链路开启（carrier ON + dg_am 输出 AW offset 包络），
# 否则 Demod 0 的相位校准不对应实际测量态，校准后失去意义。
PHASE_CAL_XY_DC_VOLTAGE_V = (
    XY_DC_VOLTAGE_LIST[len(XY_DC_VOLTAGE_LIST) // 2]
    if XY_DC_VOLTAGE_LIST else 0.12
)   # 默认取 XY_DC_VOLTAGE_LIST 中位值 (≈0.12 V)；可显式覆盖。
PHASE_CAL_KEEP_XY_ON = True  # True: 校相后保持 carrier + dg_am ON 进入扫描循环。

# ========== X/Y 载波相位校准（移植自 RF_Field_Sensitivity.ipynb）==========
# 在 Phase 1 Demod 0 校相完成后、Phase 2 扫描之前，对 dg_comp 的 X/Y 载波做
# 分通道相位校准。通过 dg_am 输出 AM AW offset 包络 + dg_comp 90 kHz AM EXT 载波，
# 读取 HF2 Demod 0 theta，用 dg_comp.set_phase_adjust() 迭代修正。
ENABLE_XY_CARRIER_PHASE_CAL = True
XY_PHASE_CAL_MAX_ITER_X = 5       # CH1 校相最大迭代次数
XY_PHASE_CAL_MAX_ITER_Y = 15      # CH2 校相最大迭代次数
XY_PHASE_CAL_TOL_DEG = 0.5        # |theta°| < tol 即视为收敛
XY_PHASE_CAL_SETTLE_s = 0.5       # 每次 set_phase_adjust 后的等待时间
XY_PHASE_CAL_AM_DC_V = float(PHASE_CAL_XY_DC_VOLTAGE_V)  # 校相时 dg_am AW offset 包络

# ========== Z 射频场驱动参数（频率扫描变量）==========
Z_RF_FREQ_START_Hz = 500.0     # 起始频率 (Hz)
# Z_RF_FREQ_STOP_Hz = 6000.0     # 终止频率 (Hz)
Z_RF_FREQ_STOP_Hz = 35000.0     # 终止频率 (Hz)
Z_RF_FREQ_STEP_Hz = 500.0      # 步进 (Hz)
Z_RF_AMPLITUDE_Vpp = 0.01      # Z 射频场固定幅度 (Vpp)

# 粗扫频率列表（线性均匀）
Z_RF_FREQ_LIST = np.arange(
    Z_RF_FREQ_START_Hz,
    Z_RF_FREQ_STOP_Hz + 1e-9,
    Z_RF_FREQ_STEP_Hz,
)

# ========== 每频点采集参数 ==========
POINT_DURATION_s = 1.0
FREQ_SETTLE_TIME_s = 0.5
XY_DC_SETTLE_TIME_s = 0.3         # 切换 XY DC 电压后等待稳定
TEMP_SWITCH_OFF_LEAD_s = 0.1      # 采集前关温度开关后等待磁场残余消失 (s)
TEMP_SWITCH_ON_LAG_s = 1.0        # 采集后开温度开关后等待系统稳定 (s)

# ========== HF2 Demod 0 配置（主信号解调，参考 = Pump 调制频率）==========
HF2_DEMOD0_IDX = 0
HF2_DEMOD0_OSC_IDX = 0
HF2_DEMOD0_RATE = 50000           # 高速率，覆盖 Z 扫频带宽
HF2_DEMOD0_TC_s = 1e-6            # 测量时 TC（短 TC 保留高带宽）
HF2_DEMOD0_TC_CALIB_s = 1e-3      # 校相时 TC（长 TC 让读数稳定）
HF2_DEMOD0_ORDER = 4
HF2_DEMOD0_SIGNAL_RANGE_V = 2.0
HF2_DEMOD0_OSC_FREQ_Hz = 90e3     # 90 kHz = Pump 调制频率

# ========== HF2 Demod 3 配置（hardware Demod 3, 仅 both 模式）==========
DEMOD3_IDX = 3
DEMOD3_OSC_IDX = 1                # Osc 1 频率跟随 Z 驱动频率
DEMOD3_RATE = 4800
DEMOD3_TC_MIN_s = 0.01
DEMOD3_TC_PER_PERIOD_FRAC = 0.05
DEMOD3_ORDER = 8
DEMOD3_N_AVG = 50
DEMOD3_AVG_INTERVAL_s = 0.01
DEMOD3_READ_SETTLE_TIME_s = 0.2

# ========== 物理回环链路参数 (Demod 0 Y → AuxOut2 → SigIn2 → Demod 3) ==========
CONFIGURE_AUXOUT2_TO_DEMOD0_Y = True
AUXOUT_INDEX = 1                  # 0 = Aux Out 1, 1 = Aux Out 2
AUXOUT_SOURCE_DEMOD_IDX = 0       # Demod 0
AUXOUT_SOURCE_SELECT = 1          # 0=X, 1=Y, 2=R, 3=Theta
AUXOUT_SCALE = 1.0
AUXOUT_OFFSET = 0.0

SIGIN2_INDEX = 1                  # 0 = Signal Input 1, 1 = Signal Input 2
SIGIN2_RANGE_V = 1.0
SIGIN2_AC_COUPLING = False        # 关键：DC 耦合
SIGIN2_IMPEDANCE_OHM = 50

# Demod 3 的 adcselect 必须指 SIGIN2_INDEX (=1)，不要用 2
DEMOD3_ADC_SELECT = SIGIN2_INDEX

# ========== X/Y 载波参数（dg_comp AM EXT）==========
XY_CARRIER_FREQ_Hz = 90e3
X_CARRIER_AMPLITUDE_Vpp = 6.0
Y_CARRIER_AMPLITUDE_Vpp = 6.0
X_CARRIER_PHASE_deg = 0.0            # CH1 载波相位 (deg)；相位校准后会更新
Y_CARRIER_PHASE_deg = 0.0            # CH2 载波相位 (deg)；相位校准后会更新
TARGET_XY_PHASE_DIFF_deg = 90.0      # 目标 X→Y 相位差 (旋转场)
X_CARRIER_PHASE_INITIAL_deg = float(X_CARRIER_PHASE_deg)
Y_CARRIER_PHASE_INITIAL_deg = 90.0   # 默认 90° 相位差
X_AM_DEPTH_pct = 100
Y_AM_DEPTH_pct = 100

# ========== Z 射频场幅度转换系数 ==========
Z_V_TO_NT = 3517 / 2              # V → nT
Z_V_TO_FT = Z_V_TO_NT * 1e6       # V → fT

# ========== Pump 调制参数 ==========
PUMP_MOD_FREQ_Hz = 90e3
PUMP_MOD_AMPLITUDE_Vpp = 0.18
PUMP_MOD_DUTY_pct = 5

RF_GATE_AMPLITUDE_Vpp = 5.0
RF_GATE_OFFSET_V = 2.5

# ========== 固定参数（沿用 ConstXY 带宽实验）==========
FIXED_PARAMS = {
    "Pump_laser_power": 0.2,
    "Probe_laser_power": 0.1,
    "main_magnetic_field": 9.31,
    "temperature": 100,
    "Temp_Switch": 5.0,
    "Time_sequence": 5.0,
    "Time_sequence_2": 0.0,
}

# ========== 安全校验 ==========
print("=" * 60)
print("安全限值检查")
print("=" * 60)
for k, v in FIXED_PARAMS.items():
    validate_safety_limit(k, v)
validate_safety_limit("Z_magnetic_field", Z_RF_AMPLITUDE_Vpp)
validate_safety_limit("rf_coil", 0.0)
# Pump 调制
validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE_Vpp)
validate_safety_limit("Time_sequence", 5.0)

# ---- X_magnetic_field_AM / Y_magnetic_field_AM 限值检查 ----
# 这两个物理量在当前 safety_limits.yaml 中可能没有定义。
# 不静默跳过，必须给出明确 WARNING，并建议补充限值。
print()
for am_key in ("X_magnetic_field_AM", "Y_magnetic_field_AM"):
    if am_key not in LIMITS:
        print(f"⚠ WARNING: safety_limits.yaml 中未定义 {am_key} 的安全限值。")
        print(f"   本实验将对所有 XY_DC_VOLTAGE_LIST 中的电压点跳过数值拦截，")
        print(f"   完全依赖操作者人工确认。建议在 params/safety_limits.yaml 中补充:")
        print(f"     {am_key}:")
        print(f"       min: <建议下界 V>")
        print(f"       max: <建议上界 V>")
        print(f"       ramp_rate: null")
        print(f"       output_off_on_error: true")
        print(f"       description: \"DG4E231500376 CH1/CH2 X/Y AM AW offset 包络\"")
    else:
        lim = LIMITS[am_key]
        print(f"  {am_key}: [{lim['min']}, {lim['max']}] V")

# ---- 对每个 XY DC 电压做一次物理量级别校验 ----
for v_dc in XY_DC_VOLTAGE_LIST:
    if "X_magnetic_field_AM" in LIMITS:
        validate_safety_limit("X_magnetic_field_AM", v_dc)
    if "Y_magnetic_field_AM" in LIMITS:
        validate_safety_limit("Y_magnetic_field_AM", v_dc)
print()
print("=" * 60)

# ---- X/Y 载波幅度 (Vpp) 安全校验 ----
# X/Y 载波由 dg_comp (DG4E234902522 CH1/CH2) 输出正弦波，幅度为
# X_CARRIER_AMPLITUDE_Vpp (=Y_CARRIER_AMPLITUDE_Vpp = 6.0 Vpp)。
# 检查 safety_limits.yaml 中是否存在 X_magnetic_field_carrier /
# Y_magnetic_field_carrier；若存在则拦截校验，否则打印 WARNING。
CARRIER_SAFETY_CHECK = {}   # 用于 experiment_config.yaml 记录
for carrier_key, carrier_amp, carrier_label in [
    ("X_magnetic_field_carrier", X_CARRIER_AMPLITUDE_Vpp, "X_CARRIER_AMPLITUDE_Vpp"),
    ("Y_magnetic_field_carrier", Y_CARRIER_AMPLITUDE_Vpp, "Y_CARRIER_AMPLITUDE_Vpp"),
]:
    if carrier_key in LIMITS:
        validate_safety_limit(carrier_key, carrier_amp)
        CARRIER_SAFETY_CHECK[carrier_key] = {
            "amplitude_Vpp": carrier_amp,
            "limit_found": True,
            "passed": True,
            "limit_min": LIMITS[carrier_key]["min"],
            "limit_max": LIMITS[carrier_key]["max"],
        }
        print(f"  {carrier_key} ({carrier_label}): {carrier_amp} Vpp, "
              f"limit=[{LIMITS[carrier_key]['min']}, {LIMITS[carrier_key]['max']}] Vpp — OK")
    else:
        print(f"⚠ WARNING: safety_limits.yaml 中未定义 {carrier_key} 的安全限值。")
        print(f"   本实验将跳过 X/Y 载波幅度 {carrier_label}={carrier_amp} Vpp 的数值拦截，")
        print(f"   完全依赖操作者人工确认。建议在 params/safety_limits.yaml 中补充:")
        print(f"     {carrier_key}:")
        print(f"       min: <建议下界 Vpp>")
        print(f"       max: <建议上界 Vpp>")
        print(f"       ramp_rate: null")
        print(f"       output_off_on_error: true")
        print(f"       description: \"DG4E234902522 CH1/CH2 X/Y 载波幅度 Vpp\"")
        CARRIER_SAFETY_CHECK[carrier_key] = {
            "amplitude_Vpp": carrier_amp,
            "limit_found": False,
            "passed": None,
            "warning": "No safety limit defined in safety_limits.yaml",
        }
print()
print("=" * 60)

print(f"\n实验类型: {EXPERIMENT_TYPE}")
print(f"运行标签: {RUN_TAG}")
print(f"采集模式: {ACQUISITION_MODE}")
print(f"XY_AW offset 电压扫描: {len(XY_DC_VOLTAGE_LIST)} 点 "
      f"[{min(XY_DC_VOLTAGE_LIST):.2f}, {max(XY_DC_VOLTAGE_LIST):.2f}] V")
print(f"Z 射频场频率扫描: {len(Z_RF_FREQ_LIST)} 点, "
      f"{Z_RF_FREQ_LIST[0]:.1f} ~ {Z_RF_FREQ_LIST[-1]:.1f} Hz, 步进 {Z_RF_FREQ_STEP_Hz} Hz")
print(f"Z 驱动幅度: {Z_RF_AMPLITUDE_Vpp} Vpp = {Z_RF_AMPLITUDE_Vpp * Z_V_TO_NT:.2f} nT p-p")
print(f"每频点采集时长: {POINT_DURATION_s} s")
print(f"总计扫描点数: {len(XY_DC_VOLTAGE_LIST) * len(Z_RF_FREQ_LIST)} "
      f"({len(XY_DC_VOLTAGE_LIST)} × {len(Z_RF_FREQ_LIST)})")

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
    dg_sweep = create_signal_generator(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"dg_sweep (Z 场) 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    devices["dg_sweep"] = dg_sweep

    # ---- DG4000: X/Y 载波 (dg_comp, AM EXT) ----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"dg_comp 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)
    devices["dg_comp"] = dg_comp

    # ---- DG4000: X/Y AM AW offset 包络 (dg_am) ----
    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = create_signal_generator(dg_am_cfg["resource"], channel=1)
    dg_am.connect()
    print(f"dg_am 已连接: {dg_am.idn()}")
    dg_am.set_ref_clock_source("EXTernal")
    # 初始: DC 0V, burst OFF, mod OFF, output OFF
    dg_am.set_burst_state(False, channel=1)
    dg_am.set_mod_state(False, channel=1)
    dg_am.set_burst_state(False, channel=2)
    dg_am.set_mod_state(False, channel=2)
    dg_am.setup_dc(0.0, channel=1)
    dg_am.setup_dc(0.0, channel=2)
    dg_am.set_output(False, channel=1)
    dg_am.set_output(False, channel=2)
    devices["dg_am"] = dg_am

    # ---- DG4000: Pump 调制 + 时序门控 (dg_mod) ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = create_signal_generator(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    print(f"dg_mod 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # ---- DG900: Pump/Probe 光功率 (DC, dg_laser) ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = create_signal_generator(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    print(f"dg_laser 已连接: {dg_laser.idn()}")
    devices["dg_laser"] = dg_laser

    # ---- DG900: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg["resource"], channel=2)
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

    # ---- SDS 示波器（可选监测）----
    scope_cfg = MAPPING.get("scope_waveform", {})
    try:
        scope = SDSInstrument(scope_cfg.get("resource", ""))
        scope.connect()
        print(f"示波器已连接: {scope.idn()}")
        devices["scope"] = scope
    except Exception as e:
        print(f"  示波器连接失败（可忽略）: {e}")

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
# ========== 初始值设置（温度等待稳定 ±1°C）==========
hfi = devices["hf2"]
tec = devices["tec"]
gs = devices["gs200"]
dg_laser = devices["dg_laser"]
dg_comp = devices["dg_comp"]
dg_sweep = devices["dg_sweep"]
dg_am = devices["dg_am"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]


# ============================================================
# 辅助函数：ConstXY 链路配置
#   - X_magnetic_field / Y_magnetic_field (dg_comp CH1/CH2):
#       90 kHz AM EXT 载波，AM depth 100%。
#   - X_magnetic_field_AM / Y_magnetic_field_AM (dg_am CH1/CH2):
#       AW offset 包络 = XY_DC_VOLTAGE_V，由 dg_am CH1/CH2 输出至 dg_comp AM 输入。
#
# 注意：X_magnetic_field / Y_magnetic_field 的 mapping key 仅用于定位
# dg_comp 载波发生器；本实验不使用其直送 DC 模式。XY DC 控制由
# X_magnetic_field_AM / Y_magnetic_field_AM 的 AM AW offset 包络实现。
# ============================================================


def configure_xy_carrier(dg_comp_inst):
    """把 dg_comp CH1/CH2 配成 90 kHz AM EXT 载波并打开 output.

    顺序：
      1) mod_state OFF（避免残留调制模式干扰）
      2) setup_sine(90kHz, amp=carrier, phase=...)
      3) set_mod_type("AM")
      4) set_mod_am_source("EXT")
      5) set_mod_am_depth(100%)
      6) set_mod_state(True)
      7) set_output(True)

    Returns:
        dict: 读回的 output 状态（便于 sanity check）。
    """
    carrier_cfg_ch1 = {
        "freq_Hz": XY_CARRIER_FREQ_Hz,
        "amp_Vpp": X_CARRIER_AMPLITUDE_Vpp,
        "phase_deg": X_CARRIER_PHASE_deg,
        "am_depth_pct": X_AM_DEPTH_pct,
    }
    carrier_cfg_ch2 = {
        "freq_Hz": XY_CARRIER_FREQ_Hz,
        "amp_Vpp": Y_CARRIER_AMPLITUDE_Vpp,
        "phase_deg": Y_CARRIER_PHASE_deg,
        "am_depth_pct": Y_AM_DEPTH_pct,
    }

    for ch, cfg in [(1, carrier_cfg_ch1), (2, carrier_cfg_ch2)]:
        # 安全校验：carrier amplitude (若 safety_limits.yaml 中定义了 carrier 限值)
        # NOTE: 暂不在 safety_limits.yaml 里强制要求 carrier 限值，未定义时仅返回。
        try:
            validate_safety_limit(
                f"X_magnetic_field_carrier" if ch == 1
                else "Y_magnetic_field_carrier", cfg["amp_Vpp"])
        except ValueError:
            raise
        # 1) 复位 mod state
        dg_comp_inst.set_mod_state(False, channel=ch)
        # 2) 配 sine carrier
        dg_comp_inst.setup_sine(freq=cfg["freq_Hz"], amplitude=cfg["amp_Vpp"],
                               phase=cfg["phase_deg"], channel=ch)
        # 3) 调制类型 = AM
        dg_comp_inst.set_mod_type("AM", channel=ch)
        # 4) AM source = EXT
        dg_comp_inst.set_mod_am_source("EXT", channel=ch)
        # 5) AM depth
        dg_comp_inst.set_mod_am_depth(cfg["am_depth_pct"], channel=ch)
        # 6) mod ON
        dg_comp_inst.set_mod_state(True, channel=ch)
        # 7) output ON
        dg_comp_inst.set_output(True, channel=ch)

    print(f"  dg_comp CH1 (X carrier): {XY_CARRIER_FREQ_Hz/1e3:.0f} kHz, "
          f"{X_CARRIER_AMPLITUDE_Vpp} Vpp, AM EXT depth {X_AM_DEPTH_pct}%, output ON")
    print(f"  dg_comp CH2 (Y carrier): {XY_CARRIER_FREQ_Hz/1e3:.0f} kHz, "
          f"{Y_CARRIER_AMPLITUDE_Vpp} Vpp, AM EXT depth {Y_AM_DEPTH_pct}%, output ON")
    return {
        "ch1_carrier_ok": True,
        "ch2_carrier_ok": True,
    }


def set_xy_am_dc(dg_am_inst, v_dc, output=True):
    """用 arbitrary waveform offset 设置 X/Y AM 电压。

    函数名保留 set_xy_am_dc 是为了兼容原扫描流程；本 AW 版本不调用
    setup_dc(v_dc)，而是上传全零 arbitrary waveform，并把 offset 设为 v_dc。
    实际输出近似为 offset，微小 Vpp 仅用于保证走 AW 输出路径。
    """
    v_dc = float(v_dc)
    half_v = 0.5 * float(CONST_AW_VPP)
    validate_safety_limit("X_magnetic_field_AM", v_dc - half_v)
    validate_safety_limit("X_magnetic_field_AM", v_dc)
    validate_safety_limit("X_magnetic_field_AM", v_dc + half_v)
    validate_safety_limit("Y_magnetic_field_AM", v_dc - half_v)
    validate_safety_limit("Y_magnetic_field_AM", v_dc)
    validate_safety_limit("Y_magnetic_field_AM", v_dc + half_v)

    y = np.zeros(CONST_AW_POINTS, dtype=float)
    for ch in (1, 2):
        dg_am_inst.set_burst_state(False, channel=ch)
        dg_am_inst.set_mod_state(False, channel=ch)
        dg_am_inst.setup_arbitrary(
            y.copy(),
            freq=A_ENV_FREQ,
            amplitude=CONST_AW_VPP,
            offset=v_dc,
            channel=ch,
        )
        dg_am_inst.set_output(bool(output), channel=ch)

    state = "ON" if output else "OFF"
    print(
        f"  dg_am CH1/CH2: AW offset={v_dc:.4f} V, "
        f"Vpp={CONST_AW_VPP:.4f} V, output={state} (→ dg_comp AM EXT 包络)"
    )
    return v_dc

def shutdown_xy_chain(dg_comp_inst, dg_am_inst):
    """Phase 1 校相前关闭 ConstXY 链路，保证校相基线纯净。

    关闭 dg_comp CH1/CH2 (90 kHz AM EXT 载波) 输出，
    关闭 dg_am CH1/CH2 (AM AW offset 包络) 输出，但不修改它们的波形/参数。
    """
    # 关 dg_am
    try:
        for ch in (1, 2):
            dg_am_inst.set_output(False, channel=ch)
        print("  dg_am CH1/CH2: output OFF (校相基线)")
    except Exception as e:
        print(f"  dg_am 关闭失败: {e}")
    # 关 dg_comp (mod 状态保留，仅关 output)
    try:
        for ch in (1, 2):
            dg_comp_inst.set_output(False, channel=ch)
        print("  dg_comp CH1/CH2: output OFF (校相基线)")
    except Exception as e:
        print(f"  dg_comp 关闭失败: {e}")


# ============================================================
# 辅助函数：X/Y 载波相位校准（移植自 RF_Field_Sensitivity.ipynb 的 Phase 2）
# ============================================================


def configure_xy_carrier_for_phase_cal(dg_comp_inst, x_phase_deg, y_phase_deg):
    """把 dg_comp CH1/CH2 配成 90 kHz sine + AM EXT，准备相位校准。

    不会主动打开 output；output 由 calibrate_xy_carrier_phase()
    在分通道校相时按需打开/关闭。
    """
    for ch in (1, 2):
        dg_comp_inst.set_mod_state(False, channel=ch)

    # CH1: X 载波
    dg_comp_inst.setup_sine(freq=XY_CARRIER_FREQ_Hz,
                            amplitude=X_CARRIER_AMPLITUDE_Vpp,
                            phase=float(x_phase_deg), channel=1)
    dg_comp_inst.set_mod_type("AM", channel=1)
    dg_comp_inst.set_mod_am_source("EXT", channel=1)
    dg_comp_inst.set_mod_am_depth(X_AM_DEPTH_pct, channel=1)
    dg_comp_inst.set_mod_state(True, channel=1)

    # CH2: Y 载波
    dg_comp_inst.setup_sine(freq=XY_CARRIER_FREQ_Hz,
                            amplitude=Y_CARRIER_AMPLITUDE_Vpp,
                            phase=float(y_phase_deg), channel=2)
    dg_comp_inst.set_mod_type("AM", channel=2)
    dg_comp_inst.set_mod_am_source("EXT", channel=2)
    dg_comp_inst.set_mod_am_depth(Y_AM_DEPTH_pct, channel=2)
    dg_comp_inst.set_mod_state(True, channel=2)

    print(f"  dg_comp CH1 (X phase cal): {XY_CARRIER_FREQ_Hz/1e3:.0f} kHz, "
          f"{X_CARRIER_AMPLITUDE_Vpp} Vpp, AM EXT depth {X_AM_DEPTH_pct}%, "
          f"phase={x_phase_deg:.2f}°")
    print(f"  dg_comp CH2 (Y phase cal): {XY_CARRIER_FREQ_Hz/1e3:.0f} kHz, "
          f"{Y_CARRIER_AMPLITUDE_Vpp} Vpp, AM EXT depth {Y_AM_DEPTH_pct}%, "
          f"phase={y_phase_deg:.2f}°")


def set_xy_phase_cal_am_dc(dg_am_inst, v_dc):
    """用 arbitrary waveform offset 设置校相用 dg_am 包络。"""
    v_dc = float(v_dc)
    half_v = 0.5 * float(CONST_AW_VPP)
    validate_safety_limit("X_magnetic_field_AM", v_dc - half_v)
    validate_safety_limit("X_magnetic_field_AM", v_dc)
    validate_safety_limit("X_magnetic_field_AM", v_dc + half_v)
    validate_safety_limit("Y_magnetic_field_AM", v_dc - half_v)
    validate_safety_limit("Y_magnetic_field_AM", v_dc)
    validate_safety_limit("Y_magnetic_field_AM", v_dc + half_v)

    y = np.zeros(CONST_AW_POINTS, dtype=float)
    for ch in (1, 2):
        dg_am_inst.set_burst_state(False, channel=ch)
        dg_am_inst.set_mod_state(False, channel=ch)
        dg_am_inst.setup_arbitrary(
            y.copy(),
            freq=A_ENV_FREQ,
            amplitude=CONST_AW_VPP,
            offset=v_dc,
            channel=ch,
        )
    print(
        f"  dg_am CH1/CH2 校相 AM AW offset = {v_dc:.4f} V, "
        f"Vpp={CONST_AW_VPP:.4f} V (→ dg_comp AM EXT 包络)"
    )
    return v_dc

def calibrate_xy_carrier_phase(dg_comp_inst, dg_am_inst, hfi_inst,
                                initial_x_phase, initial_y_phase, am_dc_v,
                                max_iter_x=5, max_iter_y=15,
                                tol_deg=0.5, settle_s=0.5,
                                demod0_calibrated_phase_deg=0.0):
    """分通道迭代校准 X/Y 载波相位（移植自 RF_Field_Sensitivity.ipynb）。

    CH1 校相：仅开 CH1，迭代读 Demod 0 theta → set_phase_adjust(ch=1)
    CH2 校相：CH1 已校基础上开启 CH2，迭代读 Demod 0 theta →
              set_phase_adjust(ch=2)
    """
    import math as _math

    result = {
        "x_carrier_phase_calibrated_deg": float(initial_x_phase) % 360,
        "y_carrier_phase_calibrated_deg": float(initial_y_phase) % 360,
        "xy_phase_diff_deg": (float(initial_y_phase) - float(initial_x_phase)) % 360,
        "phase_cal_success_x": False,
        "phase_cal_success_y": False,
        "n_iter_x": 0,
        "n_iter_y": 0,
    }

    x_phase = float(initial_x_phase) % 360
    y_phase = float(initial_y_phase) % 360

    # ---- Demod 0 切到校相 TC（保证 theta 测量稳定）----
    demod0_phase_cal_cfg = DemodulatorConfig(
        demod_index=HF2_DEMOD0_IDX, enable=True,
        rate=HF2_DEMOD0_RATE, input_channel=0,
        osc_select=HF2_DEMOD0_OSC_IDX, harmonic=1,
        time_constant=HF2_DEMOD0_TC_CALIB_s,
        order=HF2_DEMOD0_ORDER,
        phase=float(demod0_calibrated_phase_deg),
    )
    demod.configure_demodulator(hfi_inst, demod0_phase_cal_cfg)
    print(f"  Demod 0: TC={HF2_DEMOD0_TC_CALIB_s*1000:.1f} ms (校相 TC)")

    # ---- AM AW offset + 载波配置 ----
    set_xy_phase_cal_am_dc(dg_am_inst, am_dc_v)
    configure_xy_carrier_for_phase_cal(dg_comp_inst, x_phase, y_phase)

    # ---- CH1 校相 ----
    print("\n  校准 CH1 相位 (仅开 CH1)...")
    dg_comp_inst.set_output(False, channel=2)
    dg_am_inst.set_output(False, channel=2)
    dg_comp_inst.set_output(True, channel=1)
    dg_am_inst.set_output(True, channel=1)
    time.sleep(settle_s)

    for i in range(max_iter_x):
        sample = demod.read_demod_sample(hfi_inst, demod_idx=HF2_DEMOD0_IDX)
        theta_deg = _math.degrees(sample["theta"])
        result["n_iter_x"] = i + 1
        print(f"    CH1 迭代 {i+1}: theta = {theta_deg:.2f}°")
        if abs(theta_deg) < tol_deg:
            result["phase_cal_success_x"] = True
            print(f"    CH1 相位已收敛 (< {tol_deg}°)")
            break
        x_phase = (x_phase - theta_deg) % 360
        dg_comp_inst.set_phase_adjust(x_phase, channel=1)
        time.sleep(settle_s)

    result["x_carrier_phase_calibrated_deg"] = x_phase
    print(f"    CH1 最终载波相位 = {x_phase:.2f}°")

    # ---- CH2 校相 ----
    print("\n  校准 CH2 相位 (CH1 + CH2 都开)...")
    dg_comp_inst.set_output(True, channel=2)
    dg_am_inst.set_output(True, channel=2)
    dg_comp_inst.set_phase_adjust(y_phase, channel=2)
    time.sleep(settle_s * 0.6)

    for i in range(max_iter_y):
        sample = demod.read_demod_sample(hfi_inst, demod_idx=HF2_DEMOD0_IDX)
        theta_deg = _math.degrees(sample["theta"])
        result["n_iter_y"] = i + 1
        print(f"    CH2 迭代 {i+1}: theta = {theta_deg:.2f}°")
        if abs(theta_deg) < tol_deg:
            result["phase_cal_success_y"] = True
            print(f"    CH2 相位已收敛 (< {tol_deg}°)")
            break
        y_phase = (y_phase - theta_deg) % 360
        dg_comp_inst.set_phase_adjust(y_phase, channel=2)
        time.sleep(settle_s)

    result["y_carrier_phase_calibrated_deg"] = y_phase
    result["xy_phase_diff_deg"] = (y_phase - x_phase) % 360
    print(f"    CH2 最终载波相位 = {y_phase:.2f}°")
    print(f"    实际相位差 = {result['xy_phase_diff_deg']:.2f}° "
          f"(target {TARGET_XY_PHASE_DIFF_deg:.1f}°)")
    return result


# ---- 0. 复位 Z 场通道的 burst/mod 状态 ----
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

# ---- 3. 主磁场 (GS200, 电流模式) ----
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# ---- 4. 温度控制（等待稳定 ±1°C）----
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

# ---- 5. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 6. dg_am 准备：DC 0V, 输出 OFF（等 Phase 1 set_xy_am_dc() 启动）----
dg_am.set_burst_state(False, channel=1)
dg_am.set_mod_state(False, channel=1)
dg_am.set_burst_state(False, channel=2)
dg_am.set_mod_state(False, channel=2)
dg_am.setup_dc(0.0, channel=1)
dg_am.setup_dc(0.0, channel=2)
dg_am.set_output(False, channel=1)
dg_am.set_output(False, channel=2)
print(f"dg_am CH1(X AM): DC=0.000V, burst OFF, mod OFF, 输出 OFF")
print(f"dg_am CH2(Y AM): DC=0.000V, burst OFF, mod OFF, 输出 OFF")

# ---- 7. dg_comp 载波 + AM 包络配置 --------
# X_magnetic_field / Y_magnetic_field 的 mapping key 仅用于定位 dg_comp
# 载波发生器；真正产生 XY 磁场的链路是 dg_comp (90kHz sine + AM EXT) +
# dg_am (AW offset 包络)。在 Phase 1 校相时，ConstXY 链路必须 OFF（基线纯净）；
# 校相后才会调用 configure_xy_carrier() + set_xy_am_dc() 重新打开链路。
# 这里先确保 dg_comp mod 状态为 OFF（避免残留调制模式），但**不动 output**：
dg_comp.set_mod_state(False, channel=1)
dg_comp.set_mod_state(False, channel=2)
print("dg_comp 载波 (mod OFF)；Phase 1 校相前 shutdown，校相后 configure_xy_carrier + set_xy_am_dc 打开")

# ---- 8. Pump 调制配置 (RF 开关方案) ----
print("\n--- Pump 调制配置 (RF 开关方案) ---")
dg_mod.set_burst_state(False, channel=1)
dg_mod.set_mod_state(False, channel=1)
dg_mod.set_burst_state(False, channel=2)
dg_mod.set_mod_state(False, channel=2)

# CH1: 100 MHz 连续正弦 → RF 开关 IN
validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE_Vpp)
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE_Vpp,
                  offset=0.0, phase=0.0, channel=1)
print(f"  CH1: 100 MHz 正弦, {PUMP_MOD_AMPLITUDE_Vpp*1000:.0f} mVpp → RF 开关 IN")

# CH2: 90 kHz 5% 脉冲门控
pulse_width = (PUMP_MOD_DUTY_pct / 100.0) / PUMP_MOD_FREQ_Hz
validate_safety_limit("Time_sequence", 5.0)
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ_Hz, amplitude=RF_GATE_AMPLITUDE_Vpp,
                   offset=RF_GATE_OFFSET_V, width=pulse_width, channel=2)
dg_mod.set_pulse_dcycle(PUMP_MOD_DUTY_pct, channel=2)
print(f"  CH2: 脉冲 {PUMP_MOD_FREQ_Hz/1e3:.0f} kHz, {RF_GATE_AMPLITUDE_Vpp:.1f} Vpp, "
      f"offset {RF_GATE_OFFSET_V:.1f}V, duty {PUMP_MOD_DUTY_pct}% → RF 开关 CTRL")

# CH2 SYNC → 触发 dg_sweep Ext Trig
dg_mod.set_sync_state(True, channel=2)
print(f"  CH2 SYNC: ON (→ dg_sweep CH1 Ext Trig)")

# ---- 9. 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"\n运行目录: {run_dir}")

# %% Cell 5
# ============================================================
# Phase 1: HF2 配置 + Demod 0 相位校准（与 ConstXY 带宽实验一致）
# ============================================================
print("=" * 60)
print("Phase 1: HF2 配置 + Demod 0 相位校准")
print("=" * 60)

# 校相：关闭 ConstXY 链路 (dg_comp + dg_am) 与其它会引入干扰的输出，
# 保证校相基线纯净 (与 RF_Field_Sensitivity_ConstXY_FreqSweep.py 一致)。
# 校相完成后会在下方再次调用 configure_xy_carrier() + set_xy_am_dc()
# 重新打开链路，进入扫描。
print("校相前: 关闭 ConstXY 链路 (dg_comp + dg_am), 关闭 Z 场与温度开关...")
shutdown_xy_chain(dg_comp, dg_am)
dg_temp.set_output(False, channel=2)
dg_sweep.set_output(False, channel=1)
time.sleep(0.3)
print(f"  X/Y 载波 (dg_comp CH1/CH2): OFF (校相基线)")
print(f"  X/Y AM 包络 (dg_am CH1/CH2): OFF (校相基线)")
print(f"  Z 射频场 (dg_sweep CH1): OFF")
print(f"  温度开关 (dg_temp CH2): OFF")

# ---- 配置 Signal Input 0 ----
sig_in_cfg = SignalInputConfig(
    input_index=0,
    range=HF2_DEMOD0_SIGNAL_RANGE_V,
    ac_coupling=True,
    diff=False,
    impedance=50,
)
demod.configure_signal_input(hfi, sig_in_cfg)
print(f"信号输入 0: range={HF2_DEMOD0_SIGNAL_RANGE_V} V, AC 耦合")

# ---- 配置 Osc 0 (Pump 调制 90 kHz) ----
osc0_cfg = OscillatorConfig(
    osc_index=HF2_DEMOD0_OSC_IDX,
    frequency=HF2_DEMOD0_OSC_FREQ_Hz,
)
demod.configure_oscillator(hfi, osc0_cfg)
print(f"振荡器 {HF2_DEMOD0_OSC_IDX}: {HF2_DEMOD0_OSC_FREQ_Hz/1e3:.0f} kHz")

# ---- 配置 Osc 1 (Z 驱动频率，初值 1 kHz 占位) ----
osc1_initial_freq = 1000.0
osc1_cfg = OscillatorConfig(
    osc_index=DEMOD3_OSC_IDX,
    frequency=osc1_initial_freq,
)
demod.configure_oscillator(hfi, osc1_cfg)
print(f"振荡器 {DEMOD3_OSC_IDX}: {osc1_initial_freq:.0f} Hz (初值)")

# ---- 配置 Demod 0 (校相用长 TC) ----
demod0_calib_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD0_IDX,
    enable=True,
    rate=HF2_DEMOD0_RATE,
    input_channel=0,
    osc_select=HF2_DEMOD0_OSC_IDX,
    harmonic=1,
    time_constant=HF2_DEMOD0_TC_CALIB_s,
    order=HF2_DEMOD0_ORDER,
    phase=0.0,
)
actual_rate_d0 = demod.configure_demodulator(hfi, demod0_calib_cfg)
print(f"Demod 0 (校相): rate={actual_rate_d0:.0f} Sa/s, "
      f"TC={HF2_DEMOD0_TC_CALIB_s*1000:.1f} ms, order={HF2_DEMOD0_ORDER}")

# ---- 配置 Signal Input 2 (DC 耦合) ----
sig_in2_cfg = SignalInputConfig(
    input_index=SIGIN2_INDEX,
    range=SIGIN2_RANGE_V,
    ac_coupling=SIGIN2_AC_COUPLING,
    diff=False,
    impedance=SIGIN2_IMPEDANCE_OHM,
)
demod.configure_signal_input(hfi, sig_in2_cfg)
print(f"信号输入 {SIGIN2_INDEX} (Signal Input 2): "
      f"range={SIGIN2_RANGE_V} V, "
      f"{'AC' if SIGIN2_AC_COUPLING else 'DC'} 耦合, "
      f"imp={SIGIN2_IMPEDANCE_OHM}Ω")

# ---- 配置 Aux Out 2 → Demod 0 Y (物理回环发送端) ----
if CONFIGURE_AUXOUT2_TO_DEMOD0_Y:
    auxout_path = hfi.aux_out_path(AUXOUT_INDEX)
    # HF2 LabOne: outputselect = demod_index * 4 + signal_type
    auxout_outputselect = AUXOUT_SOURCE_DEMOD_IDX * 4 + AUXOUT_SOURCE_SELECT
    hfi.set_int(f"{auxout_path}/outputselect", auxout_outputselect)
    hfi.set_double(f"{auxout_path}/scale", AUXOUT_SCALE)
    hfi.set_double(f"{auxout_path}/offset", AUXOUT_OFFSET)
    hfi.sync()
    sig_type_str = {0: "X", 1: "Y", 2: "R", 3: "Theta"}[AUXOUT_SOURCE_SELECT]
    print(f"Aux Out {AUXOUT_INDEX+1} (auxouts/{AUXOUT_INDEX}): "
          f"Demod{AUXOUT_SOURCE_DEMOD_IDX} {sig_type_str}, "
          f"scale={AUXOUT_SCALE}, offset={AUXOUT_OFFSET} V")

    auxout_actual_outputselect = hfi.get_int(f"{auxout_path}/outputselect")
    auxout_actual_scale = hfi.get_double(f"{auxout_path}/scale")
    auxout_actual_offset = hfi.get_double(f"{auxout_path}/offset")
    auxout_actual_demod = auxout_actual_outputselect // 4
    auxout_actual_sig = auxout_actual_outputselect % 4
    auxout_actual_sig_str = {0: "X", 1: "Y", 2: "R", 3: "Theta"}[auxout_actual_sig]
else:
    auxout_actual_outputselect = None
    auxout_actual_scale = None
    auxout_actual_offset = None
    auxout_actual_demod = None
    auxout_actual_sig_str = None

# ---- 配置 Demod 3 (射频场解调，初值 1 kHz) ----
demod3_initial_cfg = DemodulatorConfig(
    demod_index=DEMOD3_IDX,
    enable=True,
    rate=DEMOD3_RATE,
    input_channel=DEMOD3_ADC_SELECT,
    osc_select=DEMOD3_OSC_IDX,
    harmonic=1,
    time_constant=max(DEMOD3_TC_MIN_s,
                      DEMOD3_TC_PER_PERIOD_FRAC / osc1_initial_freq),
    order=DEMOD3_ORDER,
    phase=0.0,
)
actual_rate_d3 = demod.configure_demodulator(hfi, demod3_initial_cfg)
print(f"Demod {DEMOD3_IDX} (射频场, 初值): rate={actual_rate_d3:.0f} Sa/s, "
      f"TC={demod3_initial_cfg.time_constant*1e3:.2f} ms, "
      f"adcselect={DEMOD3_ADC_SELECT} (= Signal Input 2)")

# ---- 自动校相 (Demod 0) ----
print("正在进行 Demod 0 相位校准...")
calibrated_phase = demod.auto_calibrate_phase(
    hfi, demod_idx=HF2_DEMOD0_IDX,
    tolerance_deg=1.0, max_attempts=5, settle_time=0.2,
)
print(f"Demod 0 校准完成: phaseshift = {calibrated_phase:.2f}°")
sample = demod.read_demod_sample(hfi, demod_idx=HF2_DEMOD0_IDX)
print(f"  校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

# ---- 重新打开 ConstXY 链路 + (可选) X/Y 载波相位校准 ----
# 校相时为纯净基线，链路上 OFF；进入扫描前必须再打开。
# 后续 Phase 2 每个 XY_DC 电压点只调用 set_xy_am_dc() 切换包络电压，
# 不再触碰 dg_comp carrier。
print("校相后: 重新打开 ConstXY 链路")
validate_safety_limit("X_magnetic_field_AM", PHASE_CAL_XY_DC_VOLTAGE_V)
validate_safety_limit("Y_magnetic_field_AM", PHASE_CAL_XY_DC_VOLTAGE_V)

if ENABLE_XY_CARRIER_PHASE_CAL:
    # 载波相位校准（移植自 RF_Field_Sensitivity.ipynb）
    # calibrate_xy_carrier_phase() 内部会完成：
    #   1. Demod 0 切回校相 TC
    #   2. configure_xy_carrier_for_phase_cal (90 kHz AM EXT)
    #   3. set_xy_phase_cal_am_dc (AM AW offset 包络)
    #   4. 分通道迭代 set_phase_adjust → 读出最终 X/Y 相位
    # 校准完成后 carrier 保持 ON，Demod 0 在下方再切到测量 TC。
    print("启用载波相位校准 (ENABLE_XY_CARRIER_PHASE_CAL=True)")
    dg_temp.set_output(False, channel=2)  # temp OFF (避免引入噪声)
    time.sleep(0.3)

    xy_cal_result = calibrate_xy_carrier_phase(
        dg_comp_inst=dg_comp,
        dg_am_inst=dg_am,
        hfi_inst=hfi,
        initial_x_phase=X_CARRIER_PHASE_INITIAL_deg,
        initial_y_phase=Y_CARRIER_PHASE_INITIAL_deg,
        am_dc_v=XY_PHASE_CAL_AM_DC_V,
        max_iter_x=XY_PHASE_CAL_MAX_ITER_X,
        max_iter_y=XY_PHASE_CAL_MAX_ITER_Y,
        tol_deg=XY_PHASE_CAL_TOL_DEG,
        settle_s=XY_PHASE_CAL_SETTLE_s,
        demod0_calibrated_phase_deg=float(calibrated_phase),
    )

    # 更新全局 X/Y 相位为校准结果
    X_CARRIER_PHASE_deg = xy_cal_result["x_carrier_phase_calibrated_deg"]
    Y_CARRIER_PHASE_deg = xy_cal_result["y_carrier_phase_calibrated_deg"]

    dg_temp.set_output(True, channel=2)  # temp 恢复

    print(f"\nX/Y 载波相位校准完成:")
    print(f"  X_CARRIER_PHASE = {X_CARRIER_PHASE_deg:.2f}° (收敛={xy_cal_result['phase_cal_success_x']})")
    print(f"  Y_CARRIER_PHASE = {Y_CARRIER_PHASE_deg:.2f}° (收敛={xy_cal_result['phase_cal_success_y']})")
    print(f"  实际相位差 = {xy_cal_result['xy_phase_diff_deg']:.2f}° (target {TARGET_XY_PHASE_DIFF_deg:.1f}°)")
else:
    print("载波相位校准已禁用 (ENABLE_XY_CARRIER_PHASE_CAL=False), 使用固定相位")
    configure_xy_carrier(dg_comp)
    set_xy_am_dc(dg_am, PHASE_CAL_XY_DC_VOLTAGE_V, output=True)
    xy_cal_result = {
        "x_carrier_phase_calibrated_deg": float(X_CARRIER_PHASE_deg),
        "y_carrier_phase_calibrated_deg": float(Y_CARRIER_PHASE_deg),
        "xy_phase_diff_deg": (float(Y_CARRIER_PHASE_deg) - float(X_CARRIER_PHASE_deg)) % 360,
        "phase_cal_success_x": False,
        "phase_cal_success_y": False,
        "n_iter_x": 0,
        "n_iter_y": 0,
    }

# ---- 恢复温控 (如果上面校准临时关了) ----
dg_temp.set_output(True, channel=2)
print("温度开关已恢复")

# ---- 切换 Demod 0 至测量 TC ----
demod0_meas_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD0_IDX, enable=True,
    rate=HF2_DEMOD0_RATE,
    input_channel=0,
    osc_select=HF2_DEMOD0_OSC_IDX, harmonic=1,
    time_constant=HF2_DEMOD0_TC_s,
    order=HF2_DEMOD0_ORDER,
    phase=calibrated_phase,
)
actual_rate_d0_meas = demod.configure_demodulator(hfi, demod0_meas_cfg)
print(f"Demod 0 (测量): rate={actual_rate_d0_meas:.0f} Sa/s, "
      f"TC={HF2_DEMOD0_TC_s*1e6:.0f} μs, phase={calibrated_phase:.2f}°")

# ---- 读回 Signal Input 2 / Demod 3 实际配置 ----
sigin2_actual_path = hfi.sig_in_path(SIGIN2_INDEX)
sigin2_actual_range = hfi.get_double(f"{sigin2_actual_path}/range")
sigin2_actual_ac = hfi.get_int(f"{sigin2_actual_path}/ac")
sigin2_actual_imp50 = hfi.get_int(f"{sigin2_actual_path}/imp50")

demod_r_actual_path = hfi.demod_path(DEMOD3_IDX)
demod_r_actual_adcselect = hfi.get_int(f"{demod_r_actual_path}/adcselect")
demod_r_actual_rate = hfi.get_double(f"{demod_r_actual_path}/rate")
demod_r_actual_tc = hfi.get_double(f"{demod_r_actual_path}/timeconstant")
demod_r_actual_order = hfi.get_int(f"{demod_r_actual_path}/order")
demod_r_actual_osc = hfi.get_int(f"{demod_r_actual_path}/oscselect")
print(f"读回 Demod {DEMOD3_IDX}: "
      f"adcselect={demod_r_actual_adcselect} "
      f"(期望 {DEMOD3_ADC_SELECT} = Signal Input 2), "
      f"oscselect={demod_r_actual_osc}, "
      f"rate={demod_r_actual_rate:.0f} Sa/s, "
      f"TC={demod_r_actual_tc*1e3:.2f} ms, order={demod_r_actual_order}")
print(f"读回 Signal Input {SIGIN2_INDEX}: range={sigin2_actual_range} V, "
      f"AC={'ON' if sigin2_actual_ac else 'OFF'} (期望 {'AC' if SIGIN2_AC_COUPLING else 'DC'})")

# ---- 保存实验配置 ----
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "timestamp": timestamp,
    "run_tag": RUN_TAG,
    "acquisition_mode": ACQUISITION_MODE,
    "purpose": (
        "在当前 ConstXY 链路下，通过 dg_am arbitrary offset 重新标定 X/Y AM 电压 XY_DC_VOLTAGE 与"
        "响应中心频率 f_peak 的关系：f_peak = K_eff * XY_DC_VOLTAGE + B_eff"
    ),
    "legacy_reference_only_NOT_result": {
        "A_ENV_K_Hz_per_V_LEGACY": A_ENV_K_LEGACY,
        "A_ENV_B_Hz_LEGACY": A_ENV_B_LEGACY,
        "note": (
            "旧 AW 方案的历史系数，仅作参考；本实验的 K_eff/B_eff "
            "必须由本次测量数据独立拟合得出。"
        ),
    },
    "xy_dc_voltage_scan": {
        "XY_DC_VOLTAGE_LIST_V": list(XY_DC_VOLTAGE_LIST),
        "n_points": len(XY_DC_VOLTAGE_LIST),
        "applied_by": "dg_am.setup_arbitrary(zeros, freq=A_ENV_FREQ, amplitude=CONST_AW_VPP, offset=XY_DC_VOLTAGE)",
        "A_ENV_FREQ_Hz": A_ENV_FREQ,
        "CONST_AW_POINTS": CONST_AW_POINTS,
        "CONST_AW_VPP": CONST_AW_VPP,
        "field_name_compatibility_note": (
            "raw field xy_dc_voltage_V is kept for XY_DC_Voltage_Calibration_plot "
            "compatibility; value is AW offset voltage in this experiment."
        ),
    },
    "z_freq_sweep": {
        "Z_RF_FREQ_START_Hz": float(Z_RF_FREQ_START_Hz),
        "Z_RF_FREQ_STOP_Hz": float(Z_RF_FREQ_STOP_Hz),
        "Z_RF_FREQ_STEP_Hz": float(Z_RF_FREQ_STEP_Hz),
        "freq_list_Hz": Z_RF_FREQ_LIST.tolist(),
        "Z_RF_AMPLITUDE_Vpp": Z_RF_AMPLITUDE_Vpp,
        "POINT_DURATION_s": POINT_DURATION_s,
        "FREQ_SETTLE_TIME_s": FREQ_SETTLE_TIME_s,
        "XY_DC_SETTLE_TIME_s": XY_DC_SETTLE_TIME_s,
        "is_baseline_handling": (
            "未设置 0 Hz baseline 点；每个 XY_DC 下都做完整 Z 频率扫描。"
        ),
    },
    "hf2_demod0": {
        "demod_idx": HF2_DEMOD0_IDX,
        "osc_idx": HF2_DEMOD0_OSC_IDX,
        "osc_freq_Hz": HF2_DEMOD0_OSC_FREQ_Hz,
        "signal_range_V": HF2_DEMOD0_SIGNAL_RANGE_V,
        "rate_Sa_s_requested": HF2_DEMOD0_RATE,
        "rate_Sa_s_actual": float(actual_rate_d0_meas),
        "TC_calib_s": HF2_DEMOD0_TC_CALIB_s,
        "TC_meas_s": HF2_DEMOD0_TC_s,
        "order": HF2_DEMOD0_ORDER,
        "calibrated_phase_deg": float(calibrated_phase),
    },
    "hf2_demod_r": {
        "demod_idx": DEMOD3_IDX,
        "osc_idx": DEMOD3_OSC_IDX,
        "adcselect_requested": DEMOD3_ADC_SELECT,
        "adcselect_actual": int(demod_r_actual_adcselect),
        "oscselect_actual": int(demod_r_actual_osc),
        "rate_Sa_s_requested": DEMOD3_RATE,
        "rate_Sa_s_actual": float(demod_r_actual_rate),
        "TC_min_s": DEMOD3_TC_MIN_s,
        "TC_per_period_fraction": DEMOD3_TC_PER_PERIOD_FRAC,
        "TC_initial_s": float(demod_r_actual_tc),
        "order_requested": DEMOD3_ORDER,
        "order_actual": int(demod_r_actual_order),
        "n_avg": DEMOD3_N_AVG,
        "avg_interval_s": DEMOD3_AVG_INTERVAL_s,
        "read_settle_time_s": DEMOD3_READ_SETTLE_TIME_s,
    },
    "hf2_signal_input_2": {
        "input_index": SIGIN2_INDEX,
        "range_V_requested": SIGIN2_RANGE_V,
        "range_V_actual": float(sigin2_actual_range),
        "ac_coupling_requested": SIGIN2_AC_COUPLING,
        "ac_coupling_actual": bool(sigin2_actual_ac == 1),
        "imp50_actual": bool(sigin2_actual_imp50 == 1),
        "impedance_requested_ohm": SIGIN2_IMPEDANCE_OHM,
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
            "-> physical cable -> Signal Input 2 (sigins/1, DC coupled) "
            "-> Demod 3 (adcselect=1)"
        ),
    },
    "xy_carrier": {
        "XY_CARRIER_FREQ_Hz": XY_CARRIER_FREQ_Hz,
        "X_CARRIER_AMPLITUDE_Vpp": X_CARRIER_AMPLITUDE_Vpp,
        "Y_CARRIER_AMPLITUDE_Vpp": Y_CARRIER_AMPLITUDE_Vpp,
        "X_CARRIER_PHASE_deg": X_CARRIER_PHASE_deg,
        "Y_CARRIER_PHASE_deg": Y_CARRIER_PHASE_deg,
        "X_AM_DEPTH_pct": X_AM_DEPTH_pct,
        "Y_AM_DEPTH_pct": Y_AM_DEPTH_pct,
        "xy_carrier_mapping_keys": ["X_magnetic_field", "Y_magnetic_field"],
        "xy_carrier_device": "dg_comp (DG4E234902522 CH1/CH2)",
        "xy_am_dc_mapping_keys": ["X_magnetic_field_AM", "Y_magnetic_field_AM"],
        "xy_am_dc_device": "dg_am (DG4E231500376 CH1/CH2)",
        "note": (
            "X_magnetic_field / Y_magnetic_field 的 mapping key 仅用于定位 "
            "dg_comp 载波发生器；本实验不使用其直送 DC 模式。XY DC 控制由 "
            "X_magnetic_field_AM / Y_magnetic_field_AM 的 AM AW offset 包络实现。"
        ),
    },
    # ---- Phase 1 校相对齐 (关键: 校相时链路 OFF，校相后 ON 进入扫描) ----
    "phase_calibration": {
        "xy_dc_voltage_V": float(PHASE_CAL_XY_DC_VOLTAGE_V),
        "keep_xy_on": bool(PHASE_CAL_KEEP_XY_ON),
        "carrier_off_during_phase_calibration": True,
        "carrier_reconfigured_and_opened_after_phase_calibration": True,
        "xy_carrier_kept_on_after_phase_calibration": True,
        "note": (
            "校相前关闭 ConstXY 链路 (shutdown_xy_chain -> dg_comp/dg_am OFF)，"
            "保证校相基线纯净。校相完成后调用 configure_xy_carrier() 把 dg_comp "
            "CH1/CH2 配成 90 kHz AM EXT 载波并打开 output；调用 set_xy_am_dc() "
            "把 dg_am CH1/CH2 输出 PHASE_CAL_XY_DC_VOLTAGE_V AW offset 包络。"
            "进入 Phase 2 扫描时 carrier 持续 ON，每个 XY_DC 电压点只切换 "
            "dg_am 包络电压，不重配 dg_comp。"
        ),
    },
    # ---- X/Y 载波相位校准 (Demod 0 校相后, Phase 2 扫描前) ----
    "xy_carrier_phase_calibration": {
        "enabled": bool(ENABLE_XY_CARRIER_PHASE_CAL),
        "target_xy_phase_diff_deg": float(TARGET_XY_PHASE_DIFF_deg),
        "am_dc_V": float(XY_PHASE_CAL_AM_DC_V),
        "x_carrier_phase_initial_deg": float(X_CARRIER_PHASE_INITIAL_deg),
        "y_carrier_phase_initial_deg": float(Y_CARRIER_PHASE_INITIAL_deg),
        "x_carrier_phase_calibrated_deg": float(
            xy_cal_result["x_carrier_phase_calibrated_deg"]),
        "y_carrier_phase_calibrated_deg": float(
            xy_cal_result["y_carrier_phase_calibrated_deg"]),
        "xy_phase_diff_calibrated_deg": float(
            xy_cal_result["xy_phase_diff_deg"]),
        "phase_cal_success_x": bool(xy_cal_result["phase_cal_success_x"]),
        "phase_cal_success_y": bool(xy_cal_result["phase_cal_success_y"]),
        "phase_cal_method": (
            "RF_Field_Sensitivity.ipynb style: "
            "Demod0 theta feedback + dg_comp.set_phase_adjust()"
        ),
    },
    # ---- 信号链路说明 ----
    "signal_chain_note": (
        "X_magnetic_field / Y_magnetic_field 是 dg_comp (DG4E234902522 CH1/CH2) "
        "的 90 kHz AM EXT 载波 mapping key（不是直送 DC 通道）；"
        "X_magnetic_field_AM / Y_magnetic_field_AM 是 dg_am (DG4E231500376 "
        "CH1/CH2) 的 AM AW offset 包络，由 dg_comp AM 输入端接收。真正产生 XY 旋转场的是 "
        "dg_comp 载波被 dg_am AW offset 包络调幅后的输出。"
    ),
    "pump_modulation": {
        "PUMP_MOD_FREQ_Hz": PUMP_MOD_FREQ_Hz,
        "PUMP_MOD_DUTY_pct": PUMP_MOD_DUTY_pct,
        "PUMP_MOD_AMPLITUDE_Vpp": PUMP_MOD_AMPLITUDE_Vpp,
        "RF_GATE_AMPLITUDE_Vpp": RF_GATE_AMPLITUDE_Vpp,
        "RF_GATE_OFFSET_V": RF_GATE_OFFSET_V,
    },
    "fixed_params": FIXED_PARAMS,
    "Z_V_to_nT": Z_V_TO_NT,
    "Z_V_to_fT": Z_V_TO_FT,
    "mapping_snapshot": MAPPING,
    "safety_limits_snapshot": LIMITS,
    "warning_missing_am_limits": (
        "如果 safety_limits_snapshot 中未包含 X_magnetic_field_AM / "
        "Y_magnetic_field_AM，则本实验跳过这两个量的数值拦截；"
        "请在 params/safety_limits.yaml 中补充。"
    ),
    "xy_carrier_safety_check": CARRIER_SAFETY_CHECK,
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print(f"实验配置已保存: {config_path}")

# %% Cell 6
# ============================================================
# Phase 2: 双层扫描循环
#   外层 XY_DC_VOLTAGE (每个电压点完成完整 Z 频率扫描)
#   内层 Z_RF_FREQ_LIST
#   try/finally 保证异常/正常结束都关闭 Z 输出 + 归零 dg_am + 恢复温控
# ============================================================
print("\n" + "=" * 60)
print("Phase 2: XY DC 电压标定扫描")
print("=" * 60)
print(f"XY AW offset 电压点数: {len(XY_DC_VOLTAGE_LIST)}")
print(f"每个 XY 电压下 Z 频点数: {len(Z_RF_FREQ_LIST)}")
print(f"总计: {len(XY_DC_VOLTAGE_LIST) * len(Z_RF_FREQ_LIST)} 个测量点")

# ---- 数据容器 ----
# 每个 XY_DC_VOLTAGE 子目录保存自己的 frequency_index，全局再汇总一份
global_index_records = []   # [(v_idx, xy_dc, z_idx, z_freq, file_path), ...]

try:
    # ============== 外层循环：XY_DC_VOLTAGE ==============
    # 注意：dg_comp carrier 在 Phase 1 之后必须保持 ON。
    # 每个 xy_dc_v 点只切换 dg_am 的 AW offset 包络，不重配 dg_comp。
    # 若检测到 dg_comp output OFF，自动调用 configure_xy_carrier() 恢复。
    for v_idx, xy_dc_v in enumerate(tqdm(XY_DC_VOLTAGE_LIST,
                                         desc="XY AW offset 电压扫描")):
        # ---- Sanity check: dg_comp CH1/CH2 output 应该 ON ----
        xy_carrier_expected_on = True
        try:
            dg_comp_ch1_on = bool(dg_comp.get_output(channel=1))
            dg_comp_ch2_on = bool(dg_comp.get_output(channel=2))
            if not (dg_comp_ch1_on and dg_comp_ch2_on):
                print(f"  ⚠ dg_comp output 异常 (CH1={dg_comp_ch1_on}, "
                      f"CH2={dg_comp_ch2_on}), 调用 configure_xy_carrier() 恢复")
                configure_xy_carrier(dg_comp)
        except Exception as e:
            # get_output 不可用时退回到"信任状态", 不做强制
            print(f"  (无法读回 dg_comp output 状态: {e}; 信任 Phase 1 配置)")

        # ---- 切换 XY DC 电压（只改 dg_am AW offset 包络，carrier 保持 ON）----
        set_xy_am_dc(dg_am, float(xy_dc_v), output=True)
        time.sleep(XY_DC_SETTLE_TIME_s)
        print(f"\n[XY AW offset] v_idx={v_idx:02d}, XY_DC_VOLTAGE={xy_dc_v:.4f} V "
              f"(旧 A_ENV 预测: f={(xy_dc_v * A_ENV_K_LEGACY + A_ENV_B_LEGACY):.1f} Hz), "
              f"carrier 保持 ON")

        # ---- 子目录 ----
        v_tag = f"dc_v{v_idx:02d}_{xy_dc_v:.4f}V"
        v_dir = raw_dir / v_tag
        v_dir.mkdir(exist_ok=True)

        v_index_records = []  # 本电压点的 frequency_index 记录

        # ============== 内层循环：Z_RF_FREQ ==============
        for z_idx, z_freq in enumerate(Z_RF_FREQ_LIST):
            is_baseline = False   # 本实验不做 0 Hz 基线（每个 XY_DC 都是扫描）
            record = {
                "v_idx": v_idx,
                "xy_dc_voltage_V": float(xy_dc_v),
                "z_idx": z_idx,
                "z_freq_Hz": float(z_freq),
                "is_baseline": bool(is_baseline),
                "z_drive_amplitude_Vpp": Z_RF_AMPLITUDE_Vpp,
                "acquisition_mode": ACQUISITION_MODE,
                # ---- 记录 XY carrier 与 AM 状态 ----
                "xy_carrier_expected_on": bool(xy_carrier_expected_on),
                "phase_cal_xy_dc_voltage_V": float(PHASE_CAL_XY_DC_VOLTAGE_V),
            }

            # ---- 1. 设置 Z 场驱动 ----
            dg_sweep.setup_sine(freq=float(z_freq), amplitude=Z_RF_AMPLITUDE_Vpp,
                                offset=0.0, phase=0.0, channel=1)
            dg_sweep.set_burst_state(True, channel=1)
            dg_sweep.set_burst_mode("INFinity", channel=1)
            dg_sweep.set_burst_trigger_source("EXTernal", channel=1)
            dg_sweep.set_output(True, channel=1)
            time.sleep(FREQ_SETTLE_TIME_s)

            # ---- 2. (demod_r / both 模式) 同步 Osc 1 和 Demod 3 ----
            if ACQUISITION_MODE in ("demod_r", "both"):
                osc1_cfg = OscillatorConfig(
                    osc_index=DEMOD3_OSC_IDX, frequency=float(z_freq),
                )
                demod.configure_oscillator(hfi, osc1_cfg)
                tc = max(DEMOD3_TC_MIN_s,
                         DEMOD3_TC_PER_PERIOD_FRAC / float(z_freq))
                actual_demod3_rate = min(DEMOD3_RATE,
                                         int(max(float(z_freq), 1.0) * 50))
                demod3_cfg = DemodulatorConfig(
                    demod_index=DEMOD3_IDX, enable=True,
                    rate=max(100, actual_demod3_rate),
                    input_channel=DEMOD3_ADC_SELECT,
                    osc_select=DEMOD3_OSC_IDX, harmonic=1,
                    time_constant=tc, order=DEMOD3_ORDER,
                    phase=0.0,
                )
                demod.configure_demodulator(hfi, demod3_cfg)
                point_rate = hfi.get_double(f"{demod_r_actual_path}/rate")
                point_tc = hfi.get_double(f"{demod_r_actual_path}/timeconstant")
                point_adcselect = hfi.get_int(
                    f"{demod_r_actual_path}/adcselect")
                record["demod_r_actual_tc_s"] = float(point_tc)
                record["demod_r_actual_rate_Sa_s"] = float(point_rate)
                record["demod_r_adcselect_actual"] = int(point_adcselect)

            # ---- 3. 关温度开关（消除温控磁场干扰）----
            dg_temp.set_output(False, channel=2)
            time.sleep(TEMP_SWITCH_OFF_LEAD_s)

            # ---- 4. 采集 HF2 数据 ----
            try:
                if ACQUISITION_MODE in ("daq_fft_y", "both"):
                    daq_cfg = DAQConfig(
                        device=MAPPING["lockin_r"]["device_id"],
                        trigger_type=0,
                        duration=POINT_DURATION_s,
                        grid_cols=int(actual_rate_d0_meas * POINT_DURATION_s),
                        grid_rows=1,
                        grid_mode=2,
                        signal_paths=["sample.y"],
                    )
                    daq_results = daq.acquire_data(
                        hfi, config=daq_cfg,
                        demod_idx=HF2_DEMOD0_IDX,
                        actual_rate=actual_rate_d0_meas,
                        timeout=POINT_DURATION_s + 5.0,
                    )
                    y_signal = next(
                        (r.values for r in daq_results
                         if r.signal_name == "sample.y"),
                        None,
                    )
                    t_signal = (daq_results[0].time
                                if daq_results else np.array([]))
                    actual_rate = actual_rate_d0_meas
                    if y_signal is not None and len(y_signal) > 0:
                        record["time_s"] = t_signal
                        record["y_V"] = y_signal
                        record["actual_rate_Sa_s"] = float(actual_rate)

                if ACQUISITION_MODE in ("demod_r", "both"):
                    time.sleep(DEMOD3_READ_SETTLE_TIME_s)
                    x_list, y_list, r_list, theta_list = [], [], [], []
                    for _ in range(DEMOD3_N_AVG):
                        s = demod.read_demod_sample(hfi, demod_idx=DEMOD3_IDX)
                        x_list.append(s["x"])
                        y_list.append(s["y"])
                        r_list.append(s["r"])
                        theta_list.append(s["theta"])
                        time.sleep(DEMOD3_AVG_INTERVAL_s)
                    x_arr = np.asarray(x_list, dtype=float)
                    y_arr = np.asarray(y_list, dtype=float)
                    r_arr = np.asarray(r_list, dtype=float)
                    theta_arr = np.asarray(theta_list, dtype=float)

                    x_mean = float(np.mean(x_arr))
                    y_mean = float(np.mean(y_arr))
                    r_vec = float(math.hypot(x_mean, y_mean))
                    phase_vec_rad = float(math.atan2(y_mean, x_mean))
                    phase_vec_deg = float(math.degrees(phase_vec_rad))
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
                # 恢复温度开关
                dg_temp.set_output(True, channel=2)
                time.sleep(TEMP_SWITCH_ON_LAG_s)

            # ---- 5. 保存当前频点 ----
            fname = f"zfreq_{z_idx:04d}_{z_freq:.3f}Hz.npz"
            save_dict = {
                "xy_dc_voltage_V": np.float64(xy_dc_v),
                "z_freq_Hz": np.float64(z_freq),
                "is_baseline": np.uint8(int(is_baseline)),
                "z_drive_amplitude_Vpp": np.float64(Z_RF_AMPLITUDE_Vpp),
                "acquisition_mode": ACQUISITION_MODE,
                "actual_rate_Sa_s": np.float64(
                    record.get("actual_rate_Sa_s", 0.0)),
            }
            if "y_V" in record:
                save_dict["time_s"] = record["time_s"]
                save_dict["y_V"] = record["y_V"]
            if "demod_r_r_vector_mean_V" in record:
                save_dict["demod_r_x_values_V"] = record["demod_r_x_values_V"]
                save_dict["demod_r_y_values_V"] = record["demod_r_y_values_V"]
                save_dict["demod_r_r_values_V"] = record["demod_r_r_values_V"]
                save_dict["demod_r_theta_values_rad"] = record[
                    "demod_r_theta_values_rad"]
                save_dict["demod_r_theta_values_deg"] = record[
                    "demod_r_theta_values_deg"]
                save_dict["demod_r_x_mean_V"] = np.float64(
                    record["demod_r_x_mean_V"])
                save_dict["demod_r_y_mean_V"] = np.float64(
                    record["demod_r_y_mean_V"])
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
                if "demod_r_actual_tc_s" in record:
                    save_dict["demod_r_actual_tc_s"] = np.float64(
                        record["demod_r_actual_tc_s"])
                    save_dict["demod_r_actual_rate_Sa_s"] = np.float64(
                        record["demod_r_actual_rate_Sa_s"])
                    save_dict["demod_r_adcselect_actual"] = np.int32(
                        record["demod_r_adcselect_actual"])

            fpath = v_dir / fname
            np.savez(fpath, **save_dict)

            # ---- 子目录 frequency_index ----
            rec_entry = {
                "v_idx": v_idx,
                "xy_dc_voltage_V": float(xy_dc_v),
                "z_idx": z_idx,
                "z_freq_Hz": float(z_freq),
                "is_baseline": bool(is_baseline),
                "file": fname,
            }
            v_index_records.append(rec_entry)
            # 全局索引（子目录相对路径）
            global_index_records.append({
                "v_idx": v_idx,
                "xy_dc_voltage_V": float(xy_dc_v),
                "z_idx": z_idx,
                "z_freq_Hz": float(z_freq),
                "is_baseline": bool(is_baseline),
                "subdir": v_tag,
                "file": fname,
            })

            # ---- 进度打印 ----
            y_n = len(record.get("y_V", []))
            extra = f", y_n={y_n}"
            if "demod_r_r_vector_mean_V" in record:
                extra += (f", HW vec={record['demod_r_r_vector_mean_V']:.4e} V, "
                          f"scalar={record['demod_r_r_mean_scalar_V']:.4e}±"
                          f"{record['demod_r_r_std_scalar_V']:.1e} V, "
                          f"φ={record['demod_r_phase_vector_deg']:.1f}°")
            if (z_idx + 1) % 10 == 0 or z_idx == 0 or z_idx == len(Z_RF_FREQ_LIST) - 1:
                print(f"  [{z_idx+1}/{len(Z_RF_FREQ_LIST)}] "
                      f"f_z={z_freq:.1f} Hz{extra}")

        # ---- 本电压点扫描完成，关闭 dg_am 输出（内层 try/finally 之外的清理）----
        # 注意：内层 try/finally 已经保证 Z 场、温度开关恢复。
        # 这里不立刻关闭 dg_am，让下一个 XY_DC_VOLTAGE 时由循环头部重置。
        # 但在 v_idx 完成时先 print 一行小结：
        print(f"  ✔ XY_AW_offset={xy_dc_v:.4f} V: "
              f"扫完 {len(v_index_records)} 个 Z 频点")

        # ---- 保存本电压子目录的 frequency_index ----
        np.savez(v_dir / "frequency_index.npz",
                 **{"records": np.array(v_index_records, dtype=object)})
        with open(v_dir / "frequency_index.json", "w", encoding="utf-8") as f:
            json.dump(v_index_records, f, indent=2, ensure_ascii=False)

    # ============================================================
    # 全局 frequency_index（汇总所有 XY_DC）
    # ============================================================
    np.savez(raw_dir / "frequency_index.npz",
             **{"records": np.array(global_index_records, dtype=object)})
    with open(raw_dir / "frequency_index.json", "w", encoding="utf-8") as f:
        json.dump(global_index_records, f, indent=2, ensure_ascii=False)
    print(f"\n全局频点索引已保存: {raw_dir / 'frequency_index.json'}")
    print(f"共 {len(global_index_records)} 个测量点 "
          f"({len(XY_DC_VOLTAGE_LIST)} × {len(Z_RF_FREQ_LIST)})")

    print("\n" + "=" * 60)
    print("XY AW offset 标定扫描完成")
    print("=" * 60)

finally:
    # ---- 异常或正常结束：安全清理 ----
    print("\n[安全清理] 正在关闭 Z 场输出、dg_am、dg_comp、恢复温控...")
    # 1) Z 场
    try:
        dg_sweep.setup_dc(0.0, channel=1)
        dg_sweep.set_burst_state(False, channel=1)
        dg_sweep.set_output(False, channel=1)
        print("  Z 场 (dg_sweep CH1): DC 0V, burst OFF, output OFF")
    except Exception as e:
        print(f"  Z 场关闭失败: {e}")
    # 2) 归零 + 关闭 dg_am (X/Y AM AW offset 包络)
    try:
        dg_am.setup_dc(0.0, channel=1)
        dg_am.setup_dc(0.0, channel=2)
        dg_am.set_burst_state(False, channel=1)
        dg_am.set_burst_state(False, channel=2)
        dg_am.set_mod_state(False, channel=1)
        dg_am.set_mod_state(False, channel=2)
        dg_am.set_output(False, channel=1)
        dg_am.set_output(False, channel=2)
        print("  dg_am CH1/CH2: DC 0V, burst OFF, mod OFF, output OFF")
    except Exception as e:
        print(f"  dg_am 关闭失败: {e}")
    # 3) 关闭 dg_comp (X/Y 90 kHz AM EXT 载波)
    try:
        for ch in (1, 2):
            dg_comp.set_mod_state(False, channel=ch)
        dg_comp.set_output(False, channel=1)
        dg_comp.set_output(False, channel=2)
        print("  dg_comp CH1/CH2: mod OFF, output OFF")
    except Exception as e:
        print(f"  dg_comp 关闭失败: {e}")
    # 4) 温度开关恢复 ON
    try:
        dg_temp.set_output(True, channel=2)
        print("  温度开关 (dg_temp CH2): ON")
    except Exception as e:
        print(f"  温度开关恢复失败: {e}")

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