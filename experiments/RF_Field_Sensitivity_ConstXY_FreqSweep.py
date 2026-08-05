# %% [markdown] Cell 0
# # 射频场频率响应测量 — XY 方向恒定 DC 场（重构版）
#
# 测量 Bell-Bloom 磁力仪对 Z 方向低频正弦扰动磁场的频率响应，
# 得到 -3 dB 带宽。沿用 [Z_Field_Bandwidth_Measurement.py](Z_Field_Bandwidth_Measurement.py)
# 的两路采集协议：
# - `daq_fft_y`：DAQ 采集 Demod 0 sample.y 时域，离线做 direct FFT
# - `demod_r`：硬件 Demod 3 通过**物理回环**读取 R（详见下）
# - `both`：两种都做，便于交叉对比
#
# ## 硬件 Demod 3 链路（关键 — 与 Z BW 实验一致）
# HF2 普通 Demod 3 不能直接选 Demod 0 Y 作为内部输入（`adcselect=2` 是 Aux Input 0，
# 不是 Demod 0 Y 内部路由）。因此 hardware demod_r 必须走**物理回环**：
#
# ```
# Demod 0 sample.y
#   -> Aux Out 2  (auxouts/1, scale=AUXOUT_SCALE, offset=AUXOUT_OFFSET)
#   -> 物理 BNC 同轴线
#   -> Signal Input 2  (sigins/1, range=SIGIN2_RANGE, DC coupling)
#   -> Demod 3  (adcselect=SIGIN2_INDEX, oscselect=DEMOD3_OSC_IDX)
# ```
#
# ⚠ Signal Input 2 **必须用 DC coupling**（`SIGIN2_AC_COUPLING=False`）。若使用 AC coupling
# 会像 Z 带宽实验早期 AC-run 一样压低低频响应，导致硬件曲线畸变。
#
# 该链路保证：direct FFT / offline lock-in / hardware demod_r 三组响应共用同一段
# Demod 0 Y 信号（其中 hardware 是物理回环 + 二次锁相），
# 便于在 -3 dB 带宽附近严格交叉对比。
#
# ## 与原 ConstXY 方案的关系
# - XY 方向包络从 `A(t)` 任意波改为 **DC 恒定值** `XY_DC_VOLTAGE`
# - 转换: `XY_DC_VOLTAGE = (LARMOR_FREQ_HZ - A_ENV_B) / A_ENV_K`
# - 不嵌套扫描 Z 射频场相位（理论上相位不影响 R）
# - 新增硬件 Demod 3 与 offline lock-in/direct FFT 的三组对照
#
# ## 物理方案
# - `dg_comp` (DG4E234902522) CH1/CH2: X/Y 载波 90 kHz, AM External
# - `dg_am` (DG4E231500376) CH1/CH2: DC 包络 = `XY_DC_VOLTAGE`
# - `dg_sweep` (DG4E242401288) CH1: Z 射频场 Burst 正弦 (频率扫描)
# - `dg_mod` (DG4E222800868) CH2 SYNC → `dg_sweep` CH1 Ext Trig
# - 基准频率 `LARMOR_FREQ_HZ` 顶部可调，对应施加静态沿某方向的等效旋转场
#
# ## 涉及设备
# | 设备 | 作用 |
# |------|------|
# | **dg_mod** (DG4E222800868) | CH1: 100MHz 正弦 → RF 开关 IN; CH2: 90kHz 5% 脉冲门控 + SYNC 触发 |
# | **dg_comp** (DG4E234902522) | CH1/CH2: X/Y 载波 (90kHz), AM external modulation |
# | **dg_am** (DG4E231500376) | CH1/CH2: 恒定 DC 包络 (`XY_DC_VOLTAGE`) → dg_comp MOD Input |
# | **dg_sweep** (DG4E242401288) | CH1: Z 射频场 Burst 正弦 (频率扫描); CH2: 触发直通/方波 |
# | **dg_laser** (DG9Q280100002) | CH1: Pump 光功率 DC; CH2: Probe 光功率 DC |
# | **dg_temp** (DG9Q271200104) | CH2: 温度开关 (5V ON / 0V OFF) |
# | **GS200** | 主磁场恒流源 (~9.3 mA) |
# | **TEC103** | 气室温度控制 |
# | **HF2** (dev18246) | Demod 0 (90 kHz): 主信号解调; Demod 3 (Z 驱动): 射频场解调 (走物理回环: Demod0 Y → AuxOut2 → SigIn2 DC → Demod3) |
#
# ## 注意事项（与本仓库约定一致）
# - **不要**碰 `rf_coil`（mapping.yaml 备注：共享 DG4E234902522 CH2）。
#   本实验已将该通道作为 Y 控制场载波，不会再调用 `rf_coil`。
# - 所有输出量设置前调用 `validate_safety_limit()`。
# - 扫描循环用 `try/finally` 包裹，异常时设备恢复安全状态。
# - 温度开关在每点采集前关、采集后开（嵌套 try/finally 保证一定恢复）。
# - 频率扫描按规范允许 `setup_sine()`，不要混用 `set_amplitude()`。

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
from lab_workflows.steps import configure_temperature_control
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
EXPERIMENT_TYPE = "RF_Field_Sensitivity_ConstXY_FreqSweep"
RUN_TAG = "constxy_freq"

# ========== 采集模式 ==========
# "daq_fft_y"  — 采集 Demod 0 sample.y 时域数据，离线 FFT 取幅值
# "demod_r"    — 配置额外 Demod 3（参考 = Z 驱动频率）直接读 R
# "both"       — 两种都做（用于与 hardware Demod 3 与 offline 软件锁相/direct FFT 三组对比）
ACQUISITION_MODE = "both"

# ========== XY 方向恒定场参数（顶部可调）==========
LARMOR_FREQ_HZ = 7000             # XY 方向恒定场对应的 Larmor 进动频率 (Hz)
# A_ENV_K = 13724                   # Ω → 电压转换斜率 (Hz/V)
# A_ENV_B = 66                      # Ω → 电压转换截距 (Hz)
A_ENV_K = 16319                   # Ω → 电压转换斜率 (Hz/V)
A_ENV_B = 2121                      # Ω → 电压转换截距 (Hz)
XY_DC_VOLTAGE = (LARMOR_FREQ_HZ - A_ENV_B) / A_ENV_K
print(f"XY DC 电压: {XY_DC_VOLTAGE:.4f} V (对应 Larmor 频率 = {LARMOR_FREQ_HZ} Hz)")

# ========== Z 射频场驱动参数（频率扫描变量）==========
Z_RF_FREQ_START = 0.0            # 起始频率 (Hz)
Z_RF_FREQ_STOP = 10000.0          # 终止频率 (Hz)
Z_RF_FREQ_POINTS = 101            # 频率扫描点数
Z_RF_FREQ_LOG_SPACED = False      # True: 对数均匀; False: 线性均匀
Z_RF_AMPLITUDE = 0.01             # Z 射频场固定幅度 (Vpp)

if Z_RF_FREQ_LOG_SPACED:
    Z_RF_FREQ_LIST = np.logspace(
        np.log10(Z_RF_FREQ_START), np.log10(Z_RF_FREQ_STOP), Z_RF_FREQ_POINTS
    )
else:
    Z_RF_FREQ_LIST = np.linspace(
        Z_RF_FREQ_START, Z_RF_FREQ_STOP, Z_RF_FREQ_POINTS
    )

# ========== 每频点采集参数 ==========
POINT_DURATION_s = 1.0            # 每个频点采集时长 (s)
FREQ_SETTLE_TIME = 0.5            # 切换频率后等待稳定时间 (s)
TEMP_SWITCH_OFF_LEAD = 0.1        # 采集前关温度开关后等待磁场残余消失 (s)
TEMP_SWITCH_ON_LAG = 1.0          # 采集后开温度开关后等待系统稳定 (s)

# ========== HF2 Demod 0 配置（主信号解调，参考 = Pump 调制频率）==========
HF2_DEMOD0_IDX = 0
HF2_DEMOD0_OSC_IDX = 0
HF2_DEMOD0_RATE = 50000           # 高速率，用于覆盖 Z 扫频带宽
HF2_DEMOD0_TC = 1e-6              # 测量时 TC（短 TC 保留高带宽）
HF2_DEMOD0_TC_CALIB = 1e-3        # 校相时 TC（长 TC 让读数稳定）
HF2_DEMOD0_ORDER = 4
HF2_DEMOD0_SIGNAL_RANGE = 2.0
HF2_DEMOD0_OSC_FREQ = 90e3        # 90 kHz = Pump 调制频率 = Larmor 频率

# ========== HF2 Demod 3 配置（射频场解调，参考 = Z 驱动频率）==========
# Demod 3 通过物理回环接收 Demod 0 Y：
#   Demod 0 Y -> Aux Out 2 -> 物理线 -> Signal Input 2 -> Demod 3
DEMOD3_IDX = 3                    # Demod 3 作 Z 驱动解调
DEMOD3_OSC_IDX = 1                # Osc 1 频率跟随 Z 驱动频率
DEMOD3_RATE = 4800                # Demod 3 输出速率
DEMOD3_TC_MIN_s = 0.01            # TC 下限，避免过低引入低频噪声
DEMOD3_TC_PERIOD_FRACTION = 0.05  # TC = 周期 × 该系数（确保 < 周期/2π）
DEMOD3_ORDER = 8
DEMOD3_READ_SETTLE_TIME_s = 0.2   # 切换频率后等待 Demod 3 稳定的时间 (s)
DEMOD3_N_AVG = 50                 # demod_r 模式下每频点平均次数
DEMOD3_AVG_INTERVAL_s = 0.01      # demod_r 模式下读取间隔 (s)

# ========== 物理回环链路参数 (Demod 0 Y → AuxOut2 → SigIn2 → Demod 3) ==========
# ⚠ 不可以再用 `DEMOD3_ADC_SELECT = 2`：HF2 普通 Demod 3 没有 "Demod 0 Y 内部路由" 选项，
#    adcselect=2 实际是 Aux Input 0 而不是 Demod 0 Y。下面参数走 Z_BW 已验证的物理回环。
CONFIGURE_AUXOUT2_TO_DEMOD0_Y = True
AUXOUT_INDEX = 1                  # 0 = Aux Out 1, 1 = Aux Out 2 (HF2 后面板)
AUXOUT_SOURCE_DEMOD_IDX = 0       # Demod 0 (主信号解调)
AUXOUT_SOURCE_SELECT = 1          # 0=X, 1=Y, 2=R, 3=Theta → 取 Y
AUXOUT_SCALE = 1.0                # 输出电压缩放系数
AUXOUT_OFFSET = 0.0               # 输出电压偏置 (V)

# Signal Input 2 (HF2 后面板) 接收 Aux Out 2 的物理输出
SIGIN2_INDEX = 1                  # 0 = Signal Input 1, 1 = Signal Input 2
SIGIN2_RANGE = 1.0                # 信号输入量程 (V)
SIGIN2_AC_COUPLING = False        # 关键：必须 DC coupling，
                                  #      AC coupling 会压低低频响应并畸变硬件曲线
SIGIN2_IMPEDANCE = 50             # 输入阻抗 (Ω)

# Demod 3 的 adcselect 必须指 SIGIN2_INDEX = 1（Signal Input 2），不是 2
DEMOD3_ADC_SELECT = SIGIN2_INDEX

if SIGIN2_AC_COUPLING:
    print("⚠ WARNING: Signal Input 2 is AC-coupled; "
          "hardware demod_r frequency response may be distorted "
          "(low-frequency content will be attenuated by the AC high-pass filter).")

# ========== X/Y 载波参数（dg_comp AM 外部调制）==========
XY_CARRIER_FREQ = 90e3            # X/Y 载波频率 (Hz)
X_CARRIER_AMPLITUDE = 6.0         # X 载波幅值 (Vpp)
Y_CARRIER_AMPLITUDE = 6.0         # Y 载波幅值 (Vpp)
X_CARRIER_PHASE = 0.0             # CH1 载波相位 (deg)；Phase 2 校准后会被覆盖
Y_CARRIER_PHASE = 0.0             # CH2 载波相位 (deg)；Phase 2 校准后会被覆盖
X_AM_DEPTH = 100                  # X 通道 AM 调制深度 (%)
Y_AM_DEPTH = 100                  # Y 通道 AM 调制深度 (%)

# ========== X/Y 载波相位校准（Phase 2 新增）==========
# 移植自 experiments/RF_Field_Sensitivity.ipynb 的 "Phase 2: X/Y 载波相位校准"。
# 通过 dg_am 输出 AM DC 包络 + dg_comp 90 kHz AM EXT 载波，读取 HF2 Demod 0
# theta 后用 dg_comp.set_phase_adjust() 迭代修正 X/Y 载波相位。
ENABLE_XY_CARRIER_PHASE_CAL = True
XY_PHASE_CAL_MAX_ITER_X = 5       # CH1 校相最大迭代次数
XY_PHASE_CAL_MAX_ITER_Y = 15      # CH2 校相最大迭代次数
XY_PHASE_CAL_TOL_DEG = 0.5        # |theta°| < tol 即视为收敛
XY_PHASE_CAL_SETTLE_s = 0.5       # 每次 set_phase_adjust 后的等待时间
XY_PHASE_CAL_AM_DC_V = XY_DC_VOLTAGE   # 校相时 dg_am 输出的 AM DC 包络（=XY_DC_VOLTAGE）
X_CARRIER_PHASE_INITIAL_deg = float(X_CARRIER_PHASE)
Y_CARRIER_PHASE_INITIAL_deg = 90.0     # 与 X 通道默认 90° 相位差
TARGET_XY_PHASE_DIFF_deg = 90.0        # 目标 X→Y 相位差 (旋转场)

# ========== Z 射频场幅度转换系数 ==========
Z_V_TO_NT = 3517 / 2              # V → nT 转换系数
Z_V_TO_FT = Z_V_TO_NT * 1e6       # V → fT 转换系数

# ========== Pump 调制参数 ==========
PUMP_MOD_FREQ = 90e3              # Pump 调制重复频率 (Hz)
PUMP_MOD_AMPLITUDE = 0.18         # 100 MHz 载波幅度 (Vpp)
PUMP_MOD_DUTY = 5                 # 脉冲占空比 (%)

# RF 开关门控参数
RF_GATE_AMPLITUDE = 5.0           # CH2 门控脉冲幅度 (Vpp)
RF_GATE_OFFSET = 2.5              # CH2 门控脉冲偏置 (V)

# ========== 固定参数（沿用 Static_Magnetic_Field_Sensitivity）==========
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
for k, v in FIXED_PARAMS.items():
    validate_safety_limit(k, v)
validate_safety_limit("Z_magnetic_field", Z_RF_AMPLITUDE)
# 显式声明本实验不占用 rf_coil（共享 DG4E234902522 CH2）
validate_safety_limit("rf_coil", 0.0)

print(f"实验类型: {EXPERIMENT_TYPE}")
print(f"运行标签: {RUN_TAG}")
print(f"采集模式: {ACQUISITION_MODE}")
print(f"Larmor:    {LARMOR_FREQ_HZ} Hz (XY_DC_VOLTAGE = {XY_DC_VOLTAGE:.4f} V)")
print(f"Z 驱动幅度: {Z_RF_AMPLITUDE} Vpp = {Z_RF_AMPLITUDE * Z_V_TO_NT:.2f} nT p-p")
print(f"频率扫描: {Z_RF_FREQ_LIST[0]:.1f} ~ {Z_RF_FREQ_LIST[-1]:.1f} Hz, "
      f"{len(Z_RF_FREQ_LIST)} 点 ({'log' if Z_RF_FREQ_LOG_SPACED else 'lin'} spacing)")
print(f"每频点采集时长: {POINT_DURATION_s} s")

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

    # ---- DG4000: Z 磁场（频率扫描 + 触发源）----
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = create_signal_generator(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"dg_sweep (Z 场) 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    devices["dg_sweep"] = dg_sweep

    # ---- DG4000: X/Y 补偿磁场 (AM 载波, dg_comp) ----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"dg_comp 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)
    devices["dg_comp"] = dg_comp

    # ---- DG4000: X/Y AM DC 包络控制 (dg_am) ----
    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = create_signal_generator(dg_am_cfg["resource"], channel=1)
    dg_am.connect()
    print(f"dg_am 已连接: {dg_am.idn()}")
    dg_am.set_ref_clock_source("EXTernal")
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
    try:
        tec.connect()
        print("TEC103 已连接")
    except Exception as exc:
        print(f"[警告] TEC103 连接失败：{exc}。实验继续，由外部软件负责温控。")
        tec = None
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
# ========== 初始值设置 ==========
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
# 辅助函数：X/Y 载波相位校准（移植自 RF_Field_Sensitivity.ipynb 的 Phase 2）
#   - dg_am 输出 AM DC 包络 (XY_DC_VOLTAGE 量级)
#   - dg_comp 90 kHz sine + AM EXT
#   - 读 HF2 Demod 0 theta，用 dg_comp.set_phase_adjust() 迭代修正
# ============================================================


def configure_xy_carrier_for_phase_cal(dg_comp_inst, x_phase_deg, y_phase_deg):
    """把 dg_comp CH1/CH2 配成 90 kHz sine + AM EXT，准备相位校准。

    不会主动打开 output；output 由 calibrate_xy_carrier_phase() 在分通道校
    相时按需打开/关闭。
    """
    for ch in (1, 2):
        dg_comp_inst.set_mod_state(False, channel=ch)

    # CH1: X 载波
    dg_comp_inst.setup_sine(freq=XY_CARRIER_FREQ, amplitude=X_CARRIER_AMPLITUDE,
                            phase=float(x_phase_deg), channel=1)
    dg_comp_inst.set_mod_type("AM", channel=1)
    dg_comp_inst.set_mod_am_source("EXT", channel=1)
    dg_comp_inst.set_mod_am_depth(X_AM_DEPTH, channel=1)
    dg_comp_inst.set_mod_state(True, channel=1)

    # CH2: Y 载波
    dg_comp_inst.setup_sine(freq=XY_CARRIER_FREQ, amplitude=Y_CARRIER_AMPLITUDE,
                            phase=float(y_phase_deg), channel=2)
    dg_comp_inst.set_mod_type("AM", channel=2)
    dg_comp_inst.set_mod_am_source("EXT", channel=2)
    dg_comp_inst.set_mod_am_depth(Y_AM_DEPTH, channel=2)
    dg_comp_inst.set_mod_state(True, channel=2)

    print(f"  dg_comp CH1: {XY_CARRIER_FREQ/1e3:.0f} kHz, "
          f"{X_CARRIER_AMPLITUDE} Vpp, AM EXT depth {X_AM_DEPTH}%, "
          f"phase={x_phase_deg:.2f}°")
    print(f"  dg_comp CH2: {XY_CARRIER_FREQ/1e3:.0f} kHz, "
          f"{Y_CARRIER_AMPLITUDE} Vpp, AM EXT depth {Y_AM_DEPTH}%, "
          f"phase={y_phase_deg:.2f}°")


def set_xy_phase_cal_am_dc(dg_am_inst, v_dc):
    """设置 dg_am CH1/CH2 的 DC 包络，用于 X/Y 载波相位校准。

    顺序：
      1) burst OFF、mod OFF（复位残留状态）
      2) setup_dc(v_dc, channel=1/2)

    注意：本函数不主动 set_output；由 calibrate_xy_carrier_phase() 在
    分通道校相时按需控制。
    """
    v_dc = float(v_dc)
    # 安全校验：若 *_AM 限值未定义则不报错
    validate_safety_limit("X_magnetic_field_AM", v_dc)
    validate_safety_limit("Y_magnetic_field_AM", v_dc)
    for ch in (1, 2):
        dg_am_inst.set_burst_state(False, channel=ch)
        dg_am_inst.set_mod_state(False, channel=ch)
        dg_am_inst.setup_dc(v_dc, channel=ch)
    print(f"  dg_am CH1/CH2 校相 AM DC = {v_dc:.4f} V "
          f"(→ dg_comp AM EXT 包络)")
    return v_dc


def calibrate_xy_carrier_phase(dg_comp_inst, dg_am_inst, hfi_inst,
                                initial_x_phase, initial_y_phase, am_dc_v,
                                max_iter_x=5, max_iter_y=15,
                                tol_deg=0.5, settle_s=0.5,
                                demod0_calibrated_phase_deg=0.0):
    """分通道迭代校准 X/Y 载波相位（移植自 RF_Field_Sensitivity.ipynb）。

    CH1 校相：
      - 仅打开 dg_comp CH1 + dg_am CH1；CH2 完全 OFF
      - 迭代读 Demod 0 theta（弧度 → 度），用 set_phase_adjust 修正

    CH2 校相：
      - 在 CH1 已校准基础上，打开 CH2
      - 同样迭代修正 Y_CARRIER_PHASE，使 theta→0（X+Y 矢量贡献的相位）

    返回 dict 含 calib 后的 X/Y 相位、相位差、收敛标志与迭代次数。
    """
    import math as _math   # 局部 import，保持函数自包含

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
        time_constant=HF2_DEMOD0_TC_CALIB,
        order=HF2_DEMOD0_ORDER,
        phase=float(demod0_calibrated_phase_deg),
    )
    demod.configure_demodulator(hfi_inst, demod0_phase_cal_cfg)
    print(f"  Demod 0: TC={HF2_DEMOD0_TC_CALIB*1000:.1f} ms (校相 TC)")

    # ---- AM DC + 载波配置 (CH1/CH2 都准备好，分通道切 output) ----
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

# ---- 3. 主磁场 (GS200, 电流模式) ----
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)  # mA → A
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# ---- 4. 温度控制（等待稳定 ±1°C）----
validate_safety_limit("temperature", FIXED_PARAMS["temperature"])
configure_temperature_control(
    tec, FIXED_PARAMS["temperature"], tolerance_c=1.0, stable_reads=1
)

# ---- 6. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 7. dg_am 准备输出 DC 包络（XY_DC_VOLTAGE，先关闭输出等 Phase 启动）----
dg_am.setup_dc(0.0, channel=1)
dg_am.setup_dc(0.0, channel=2)
dg_am.set_output(False, channel=1)
dg_am.set_output(False, channel=2)
print(f"dg_am CH1(X AM): DC=0.000V, 输出 OFF (待 XY_DC_VOLTAGE={XY_DC_VOLTAGE:.4f}V)")
print(f"dg_am CH2(Y AM): DC=0.000V, 输出 OFF")

# ---- 8. Pump 调制配置 (RF 开关方案) ----
print("\n--- Pump 调制配置 (RF 开关方案) ---")
# [经验] 配置前先重置 burst/mod 状态
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
validate_safety_limit("Time_sequence", 5.0)
pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                   offset=RF_GATE_OFFSET, width=pulse_width, channel=2)
dg_mod.set_pulse_dcycle(PUMP_MOD_DUTY, channel=2)
print(f"  CH2: 脉冲 {PUMP_MOD_FREQ/1e3:.0f} kHz, {RF_GATE_AMPLITUDE:.1f} Vpp, "
      f"offset {RF_GATE_OFFSET:.1f}V, 占空比 {PUMP_MOD_DUTY}% → RF 开关 CTRL")

# CH2 SYNC ON → 作为外部触发源 (→ dg_sweep Ext Trig)
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
# Phase 1: HF2 配置 + Demod 0 相位校准
# ============================================================
print("=" * 60)
print("Phase 1: HF2 配置 + Demod 0 相位校准")
print("=" * 60)

# ---- 关闭所有磁场（包括 XY 载波、AM 包络、Z 射频场、温度开关）----
# [经验] 校相必须基线归零，否则 XY 控制场、Z 射频场会扰乱相位判定
print("关闭所有磁场与温控 (校相期间基线归零)...")
dg_temp.set_output(False, channel=2)
time.sleep(0.5)
dg_comp.set_output(False, channel=1)
dg_comp.set_output(False, channel=2)
dg_am.set_output(False, channel=1)
dg_am.set_output(False, channel=2)
dg_sweep.set_output(False, channel=1)
print("  X/Y 载波 (dg_comp CH1/CH2): OFF")
print("  X/Y AM 包络 (dg_am CH1/CH2): OFF")
print("  Z 射频场 (dg_sweep CH1): OFF")
print("  温度开关 (dg_temp CH2): OFF")
time.sleep(0.3)

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
    osc_index=DEMOD3_OSC_IDX,
    frequency=osc1_initial_freq,
)
demod.configure_oscillator(hfi, osc1_cfg)
print(f"振荡器 {DEMOD3_OSC_IDX}: {osc1_initial_freq:.0f} Hz (初值，扫描时更新)")

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

# ---- 配置 Signal Input 2 (DC 耦合，作为 AuxOut2 的物理接收端) ----
sig_in2_cfg = SignalInputConfig(
    input_index=SIGIN2_INDEX,
    range=SIGIN2_RANGE,
    ac_coupling=SIGIN2_AC_COUPLING,
    diff=False,
    impedance=SIGIN2_IMPEDANCE,
)
demod.configure_signal_input(hfi, sig_in2_cfg)
print(f"信号输入 {SIGIN2_INDEX} (Signal Input 2, 物理回环接收端): "
      f"range={SIGIN2_RANGE} V, "
      f"{'AC' if SIGIN2_AC_COUPLING else 'DC'} 耦合, "
      f"imp={SIGIN2_IMPEDANCE}Ω")

# ---- 配置 Aux Out 2 → Demod 0 Y (物理回环发送端) ----
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

    # 读回 Aux Out 2 的实际值
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
# adcselect = SIGIN2_INDEX = 1：Demod 3 输入信号来自 Signal Input 2
demod3_initial_cfg = DemodulatorConfig(
    demod_index=DEMOD3_IDX,
    enable=True,
    rate=DEMOD3_RATE,
    input_channel=DEMOD3_ADC_SELECT,
    osc_select=DEMOD3_OSC_IDX,
    harmonic=1,
    time_constant=max(DEMOD3_TC_MIN_s,
                      DEMOD3_TC_PERIOD_FRACTION / osc1_initial_freq),
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

# ---- 恢复温控 ----
dg_temp.set_output(True, channel=2)
print("温度开关已恢复")

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

# ---- 读回 Signal Input 2 / Demod 3 实际配置 (用于 config 快照) ----
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
      f"AC={'ON' if sigin2_actual_ac else 'OFF'} (期望 {'AC' if SIGIN2_AC_COUPLING else 'DC'}), "
      f"imp50={'ON' if sigin2_actual_imp50 else 'OFF'}")

# ---- 保存实验配置 ----
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "timestamp": timestamp,
    "run_tag": RUN_TAG,
    "acquisition_mode": ACQUISITION_MODE,
    "xy_const_field": {
        "LARMOR_FREQ_Hz": LARMOR_FREQ_HZ,
        "XY_DC_VOLTAGE_V": XY_DC_VOLTAGE,
        "A_ENV_K_Hz_per_V": A_ENV_K,
        "A_ENV_B_Hz": A_ENV_B,
        "conversion": "XY_DC_VOLTAGE = (LARMOR_FREQ_Hz - A_ENV_B) / A_ENV_K",
    },
    "freq_sweep": {
        "Z_RF_FREQ_START_Hz": Z_RF_FREQ_START,
        "Z_RF_FREQ_STOP_Hz": Z_RF_FREQ_STOP,
        "Z_RF_FREQ_POINTS": Z_RF_FREQ_POINTS,
        "Z_RF_FREQ_LOG_SPACED": Z_RF_FREQ_LOG_SPACED,
        "freq_list_Hz": Z_RF_FREQ_LIST.tolist(),
        "Z_RF_AMPLITUDE_Vpp": Z_RF_AMPLITUDE,
        "FREQ_SETTLE_TIME_s": FREQ_SETTLE_TIME,
        "POINT_DURATION_s": POINT_DURATION_s,
        "is_baseline_handling": (
            "freq == 0 时 Z 场 DC 0V + output OFF + dg_am/dg_comp OFF，"
            "采集 1 s Y 作为基线；不参与带宽/峰值计算"
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
        "demod_idx": DEMOD3_IDX,
        "osc_idx": DEMOD3_OSC_IDX,
        "adcselect_requested": DEMOD3_ADC_SELECT,
        "adcselect_actual": int(demod_r_actual_adcselect),
        "oscselect_actual": int(demod_r_actual_osc),
        "rate_Sa_s_requested": DEMOD3_RATE,
        "rate_Sa_s_actual": float(demod_r_actual_rate),
        "TC_min_s": DEMOD3_TC_MIN_s,
        "TC_per_period_fraction": DEMOD3_TC_PERIOD_FRACTION,
        "TC_initial_s": float(demod_r_actual_tc),
        "order_requested": DEMOD3_ORDER,
        "order_actual": int(demod_r_actual_order),
        "n_avg": DEMOD3_N_AVG,
        "avg_interval_s": DEMOD3_AVG_INTERVAL_s,
        "read_settle_time_s": DEMOD3_READ_SETTLE_TIME_s,
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
            "-> physical cable -> Signal Input 2 (sigins/1, DC coupled) "
            "-> Demod 3 (adcselect=1)"
        ),
    },
    "xy_carrier": {
        "XY_CARRIER_FREQ_Hz": XY_CARRIER_FREQ,
        "X_CARRIER_AMPLITUDE_Vpp": X_CARRIER_AMPLITUDE,
        "Y_CARRIER_AMPLITUDE_Vpp": Y_CARRIER_AMPLITUDE,
        "X_CARRIER_PHASE_deg": X_CARRIER_PHASE,
        "Y_CARRIER_PHASE_deg": Y_CARRIER_PHASE,
        "X_AM_DEPTH_pct": X_AM_DEPTH,
        "Y_AM_DEPTH_pct": Y_AM_DEPTH,
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
    "note_rf_coil": (
        "实验未占用 rf_coil 物理通道；DG4E234902522 CH2 "
        "在本实验中作为 Y 控制场载波。"
    ),
}
# ---- 先保存配置快照（Phase 1 内容），Phase 2 校准后再补充字段 ----
config_path = run_dir / "experiment_config.yaml"

# %% Cell 6a
# ============================================================
# Phase 2: X/Y 载波相位校准（移植自 RF_Field_Sensitivity.ipynb）
#
# 通过 dg_am 输出 AM DC 包络 + dg_comp 90 kHz AM EXT 载波，
# 读取 HF2 Demod 0 theta，用 dg_comp.set_phase_adjust() 迭代修正。
# 校准后 dg_comp CH1/CH2 保持 ON，Phase 3 只改变 dg_am 包络。
# ============================================================
print("\n" + "=" * 60)
print("Phase 2: X/Y 载波相位校准")
print("=" * 60)

if ENABLE_XY_CARRIER_PHASE_CAL:
    print("启用载波相位校准 (ENABLE_XY_CARRIER_PHASE_CAL=True)")

    # temp_switch OFF (避免引入干扰)
    dg_temp.set_output(False, channel=2)
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
    X_CARRIER_PHASE = xy_cal_result["x_carrier_phase_calibrated_deg"]
    Y_CARRIER_PHASE = xy_cal_result["y_carrier_phase_calibrated_deg"]

    # temp_switch 恢复
    dg_temp.set_output(True, channel=2)

    print(f"\nX/Y 载波相位校准完成:")
    print(f"  X_CARRIER_PHASE = {X_CARRIER_PHASE:.2f}° (收敛={xy_cal_result['phase_cal_success_x']})")
    print(f"  Y_CARRIER_PHASE = {Y_CARRIER_PHASE:.2f}° (收敛={xy_cal_result['phase_cal_success_y']})")
    print(f"  实际相位差 = {xy_cal_result['xy_phase_diff_deg']:.2f}° (target {TARGET_XY_PHASE_DIFF_deg:.1f}°)")
else:
    print("⚠ 载波相位校准已禁用 (ENABLE_XY_CARRIER_PHASE_CAL=False), 使用固定相位")
    # 直接用固定相位配置 carrier（旧行为）
    dg_comp.set_mod_state(False, channel=1)
    dg_comp.set_mod_state(False, channel=2)
    dg_comp.setup_sine(freq=XY_CARRIER_FREQ, amplitude=X_CARRIER_AMPLITUDE,
                       phase=X_CARRIER_PHASE, channel=1)
    dg_comp.set_mod_type("AM", channel=1)
    dg_comp.set_mod_am_source("EXT", channel=1)
    dg_comp.set_mod_am_depth(X_AM_DEPTH, channel=1)
    dg_comp.set_mod_state(True, channel=1)
    dg_comp.setup_sine(freq=XY_CARRIER_FREQ, amplitude=Y_CARRIER_AMPLITUDE,
                       phase=Y_CARRIER_PHASE, channel=2)
    dg_comp.set_mod_type("AM", channel=2)
    dg_comp.set_mod_am_source("EXT", channel=2)
    dg_comp.set_mod_am_depth(Y_AM_DEPTH, channel=2)
    dg_comp.set_mod_state(True, channel=2)
    dg_comp.set_output(True, channel=1)
    dg_comp.set_output(True, channel=2)
    print(f"  CH1: X 载波 {XY_CARRIER_FREQ/1000:.0f} kHz, {X_CARRIER_AMPLITUDE} Vpp, "
          f"AM EXT depth {X_AM_DEPTH}%, phase={X_CARRIER_PHASE:.2f}°")
    print(f"  CH2: Y 载波 {XY_CARRIER_FREQ/1000:.0f} kHz, {Y_CARRIER_AMPLITUDE} Vpp, "
          f"AM EXT depth {Y_AM_DEPTH}%, phase={Y_CARRIER_PHASE:.2f}°")
    xy_cal_result = {
        "x_carrier_phase_calibrated_deg": X_CARRIER_PHASE,
        "y_carrier_phase_calibrated_deg": Y_CARRIER_PHASE,
        "xy_phase_diff_deg": (Y_CARRIER_PHASE - X_CARRIER_PHASE) % 360,
        "phase_cal_success_x": False,
        "phase_cal_success_y": False,
        "n_iter_x": 0,
        "n_iter_y": 0,
    }

# ---- dg_sweep CH2 触发直通（Burst 触发源）----
dg_sweep.setup_square(freq=10, amplitude=5.0, offset=2.5, channel=2)
print(f"dg_sweep CH2: 方波 10Hz, 外部触发 → dg_sweep CH1 Ext Trig")

# ---- dg_am 输出 DC 包络（XY_DC_VOLTAGE）----
# 注意：如果启用了 ENABLE_XY_CARRIER_PHASE_CAL，calibrate_xy_carrier_phase()
# 已经把 dg_am 配成 am_dc_v 的 DC 并 output ON。这里再确认一次 XY_DC_VOLTAGE。
print(f"\ndg_am 输出 DC 包络: XY_DC_VOLTAGE = {XY_DC_VOLTAGE:.4f} V")
dg_am.set_burst_state(False, channel=1)
dg_am.set_mod_state(False, channel=1)
dg_am.set_burst_state(False, channel=2)
dg_am.set_mod_state(False, channel=2)
dg_am.setup_dc(XY_DC_VOLTAGE, channel=1)
dg_am.setup_dc(XY_DC_VOLTAGE, channel=2)
dg_am.set_output(True, channel=1)
dg_am.set_output(True, channel=2)
print(f"  CH1 (X): DC = {XY_DC_VOLTAGE:.4f} V → 输出 ON")
print(f"  CH2 (Y): DC = {XY_DC_VOLTAGE:.4f} V → 输出 ON")

# ---- 补充 experiment_config.yaml 中的 carrier phase cal 字段 ----
config["xy_carrier_phase_calibration"] = {
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
}
config["xy_carrier"]["X_CARRIER_PHASE_deg"] = float(X_CARRIER_PHASE)
config["xy_carrier"]["Y_CARRIER_PHASE_deg"] = float(Y_CARRIER_PHASE)
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print(f"实验配置已保存: {config_path}")

# 切换到测量 TC
demod0_meas_cfg_before_scan = DemodulatorConfig(
    demod_index=HF2_DEMOD0_IDX, enable=True,
    rate=HF2_DEMOD0_RATE,
    input_channel=0,
    osc_select=HF2_DEMOD0_OSC_IDX, harmonic=1,
    time_constant=HF2_DEMOD0_TC,
    order=HF2_DEMOD0_ORDER,
    phase=calibrated_phase,
)
actual_rate_d0_meas = demod.configure_demodulator(hfi, demod0_meas_cfg_before_scan)
print(f"Demod 0 (测量): rate={actual_rate_d0_meas:.0f} Sa/s, "
      f"TC={HF2_DEMOD0_TC*1e6:.0f} μs")

# =============================================================================
# Phase 3: 单层 Z 射频场频率扫描（保留 burst + Ext Trigger 触发链路）
# =============================================================================
print("\n" + "=" * 60)
print("Phase 3: Z 射频场频率扫描（burst + 外部触发）")
print("=" * 60)
print(f"XY 恒定场 Larmor 频率: {LARMOR_FREQ_HZ} Hz "
      f"(XY_DC_VOLTAGE = {XY_DC_VOLTAGE:.4f} V)")
print(f"固定 Z 射频场幅度:     {Z_RF_AMPLITUDE} Vpp")
print(f"频率扫描: {Z_RF_FREQ_LIST[0]:.1f} ~ {Z_RF_FREQ_LIST[-1]:.1f} Hz, "
      f"{len(Z_RF_FREQ_LIST)} 点")
print(f"采集模式: {ACQUISITION_MODE}")
print(f"总计: {len(Z_RF_FREQ_LIST)} 个测量点")

# ---- 数据容器 ----
freq_index_records = []   # [(idx, freq_Hz, is_baseline, file_path), ...]

try:
    for fi, freq in enumerate(tqdm(Z_RF_FREQ_LIST, desc="频率扫描")):
        is_baseline = (freq == 0.0)
        record = {
            "idx": fi,
            "freq_Hz": float(freq),
            "is_baseline": bool(is_baseline),
            "z_drive_amplitude_Vpp": Z_RF_AMPLITUDE,
            "acquisition_mode": ACQUISITION_MODE,
        }

        # ---- 1. 设置 Z 场驱动 ----
        if is_baseline:
            # 0 Hz: DC 0V + 输出关闭，仅作为背景/基线
            validate_safety_limit("Z_magnetic_field", 0.0)
            dg_sweep.setup_dc(0.0, channel=1)
            dg_sweep.set_output(False, channel=1)
            dg_sweep.set_burst_state(False, channel=1)
        else:
            # 正频率: 固定幅度正弦，频率变化 (BURSt INFINITY + 外部触发)
            dg_sweep.setup_sine(freq=freq, amplitude=Z_RF_AMPLITUDE,
                                offset=0.0, phase=0.0, channel=1)
            dg_sweep.set_burst_state(True, channel=1)
            dg_sweep.set_burst_mode("INFinity", channel=1)
            dg_sweep.set_burst_trigger_source("EXTernal", channel=1)
            dg_sweep.set_output(True, channel=1)
            # 等待频率切换稳定
            time.sleep(FREQ_SETTLE_TIME)

        # ---- 2. (demod_r 模式) 更新 Osc 1 和 Demod 3 跟随 Z 驱动频率 ----
        if ACQUISITION_MODE in ("demod_r", "both") and not is_baseline:
            osc1_cfg = OscillatorConfig(
                osc_index=DEMOD3_OSC_IDX, frequency=freq,
            )
            demod.configure_oscillator(hfi, osc1_cfg)
            tc = max(DEMOD3_TC_MIN_s, DEMOD3_TC_PERIOD_FRACTION / freq)
            # 高频段 TC 极小，需要更高的 rate 才能让低通收敛
            actual_demod3_rate = min(DEMOD3_RATE, int(max(freq, 1.0) * 50))
            demod3_cfg = DemodulatorConfig(
                demod_index=DEMOD3_IDX, enable=True,
                rate=max(100, actual_demod3_rate),
                input_channel=DEMOD3_ADC_SELECT,
                osc_select=DEMOD3_OSC_IDX, harmonic=1,
                time_constant=tc, order=DEMOD3_ORDER,
                phase=0.0,
            )
            demod.configure_demodulator(hfi, demod3_cfg)
            # 读回实际 TC / rate / adcselect (便于排查)
            point_rate = hfi.get_double(f"{demod_r_actual_path}/rate")
            point_tc = hfi.get_double(f"{demod_r_actual_path}/timeconstant")
            point_adcselect = hfi.get_int(
                f"{demod_r_actual_path}/adcselect")
            record["demod_r_actual_tc_s"] = float(point_tc)
            record["demod_r_actual_rate_Sa_s"] = float(point_rate)
            record["demod_r_adcselect_actual"] = int(point_adcselect)

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
                time.sleep(DEMOD3_READ_SETTLE_TIME_s)
                # 多次读取 Demod 3 X/Y/R/theta
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
            "z_drive_amplitude_Vpp": np.float64(Z_RF_AMPLITUDE),
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
            # 每点的 demod_r 诊断字段（便于排查 TC/rate/adcselect）
            if "demod_r_actual_tc_s" in record:
                save_dict["demod_r_actual_tc_s"] = np.float64(
                    record["demod_r_actual_tc_s"])
                save_dict["demod_r_actual_rate_Sa_s"] = np.float64(
                    record["demod_r_actual_rate_Sa_s"])
                save_dict["demod_r_adcselect_actual"] = np.int32(
                    record["demod_r_adcselect_actual"])
        fpath = raw_dir / f"{fname}.npz"
        np.savez(fpath, **save_dict)
        freq_index_records.append({
            "idx": fi, "freq_Hz": float(freq),
            "is_baseline": bool(is_baseline),
            "file": fpath.name,
        })

        # ---- 进度打印 ----
        if (fi + 1) % 20 == 0 or fi == 0 or fi == len(Z_RF_FREQ_LIST) - 1:
            extra = ""
            if "demod_r_r_vector_mean_V" in record:
                extra = (f", HW vec={record['demod_r_r_vector_mean_V']:.4e} V, "
                         f"scalar={record['demod_r_r_mean_scalar_V']:.4e}±"
                         f"{record['demod_r_r_std_scalar_V']:.1e} V, "
                         f"φ={record['demod_r_phase_vector_deg']:.1f}°")
            y_n = len(record.get("y_V", []))
            extra += f", y_n={y_n}"
            tag = " (BASELINE)" if is_baseline else ""
            print(f"  [{fi+1}/{len(Z_RF_FREQ_LIST)}] f={freq:.2f} Hz{tag}{extra}")

    # ---- 6. 保存 frequency_index ----
    np.savez(raw_dir / "frequency_index.npz",
             **{"records": np.array(freq_index_records, dtype=object)})
    print(f"\n频点索引已保存: {raw_dir / 'frequency_index.npz'}")

    # 兼容 JSON 格式便于人工阅读
    with open(raw_dir / "frequency_index.json", "w", encoding="utf-8") as f:
        json.dump(freq_index_records, f, indent=2, ensure_ascii=False)
    print(f"频点索引 (JSON): {raw_dir / 'frequency_index.json'}")

    # ---- 更新配置 ----
    config["freq_sweep"]["actual_freq_list_Hz"] = Z_RF_FREQ_LIST.tolist()
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print("\n" + "=" * 60)
    print("频率扫描完成")
    print("=" * 60)

finally:
    # ---- 异常或正常结束时：关闭 Z 场输出，恢复温度开关 ----
    try:
        dg_sweep.setup_dc(0.0, channel=1)
        dg_sweep.set_burst_state(False, channel=1)
        dg_sweep.set_output(False, channel=1)
        print("[安全清理] Z 场输出已关闭 (DC 0V, burst OFF, OFF)")
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
