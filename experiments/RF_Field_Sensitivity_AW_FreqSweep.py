# %% [markdown] Cell 0
# # 射频场频率响应测量（AM 外部调制方案 — 任意波控制）
#
# 利用 Bell-Bloom 磁力仪测量系统对 Z 方向射频场的频率响应。
# **固定 Z 射频场幅度**，嵌套扫描 Z 射频场频率和 Demod 3 解调相位，
# 采集每个 (频率, 相位) 组合下的 Demod 3 R/X/Y 或 Demod 0 Y 时域数据，
# 获得系统的幅频和相频特性。
#
# ## 扫描模式
# - 外循环: Z 射频场频率 (freq_list)
# - 内循环: Demod 3 解调参考相位 (phase_list, 0~360°)
# - 每个 (频率, 相位) 点按 ACQUISITION_MODE 采集:
#   - "yfft" — 仅采集 Demod 0 sample.y 时域数据
#   - "demod_rxy" — 仅读取 Demod 3 R/X/Y/theta 多次平均
#   - "both" — 两者同时采集
#
# ## 物理 Demod3 回环链路
#   Demod 0 Y → HF2 Aux Out 2 → 物理线 → HF2 Signal Input 2 DC → Demod 3
#   (不再使用 adcselect=2 内部路由)
#
# ## 涉及设备
# | 设备 | 作用 |
# |------|------|
# | **dg_mod** (DG4E222800868) | CH1: 100MHz 正弦 → RF 开关 IN; CH2: 脉冲门控 (90kHz, 5% 占空比) |
# | **dg_comp** (DG4E234902522) | CH1/CH2: X/Y 载波 (90kHz), AM 外部调制模式 |
# | **dg_am** (DG4E231500376) | CH1/CH2: X/Y AM 物理电压包络（由 Ω_ctrl 按单一标定坐标转换） |
# | **dg_sweep** (DG4E242401288) | CH1: Z 射频场 Burst 正弦 (频率扫描); CH2: 触发直通 → dg_am Ext Trig |
# | **dg_laser** (DG9Q280100002) | CH1: Pump 光功率 DC; CH2: Probe 光功率 DC |
# | **dg_temp** (DG9Q271200104) | CH2: 温度开关 (5V ON / 0V OFF) |
# | **GS200** | 主磁场恒流源 (~9.3 mA) |
# | **TEC103** | 气室温度控制 |
# | **HF2** (dev18246) | Demod 0: 主信号解调; Aux Out 2 → Sig In 2 → Demod 3: 射频场解调物理回环 |
# | **SDS 示波器** | (可选) C2(X), C3(Y) 监测 AM 调制输出波形 |
#
# ## 实验流程
# - **环境准备**: 库导入 → 参数配置 → 安全校验 → 设备连接
# - **初始值设置**: 光功率/主磁场/温度设定 → Pump 调制启动 → 创建运行目录
# - **Phase 1**:
#   - HF2 Demod 0 校准 → 物理回环链路(AuxOut2 + SigIn2 + Demod3)配置与读回
#   - Demod 0 自动校相 → 切测量 TC
# - **Phase 2**: X/Y 载波相位校准 (校相期间 dg_am 用 DC 模式, 记录收敛状态, 完成后再切回 A(t) 任意波)
# - **Phase 3**: 嵌套扫描 → 按 ACQUISITION_MODE 采集 → 逐频点保存
# - **安全关闭**: 仅断开 TEC，其余设备保持连接

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
from lab_workflows.common import load_mapping
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
MAPPING = load_mapping(project_root)

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
EXPERIMENT_TYPE = "RF_Field_Sensitivity_AW_FreqSweep"
RUN_TAG = "freq_resp"

# ========== 采集模式 ==========
# "yfft"       — 仅采集 Demod 0 sample.y 时域数据，离线 FFT
# "demod_rxy"  — 配置 Demod 3（参考=Z 驱动频率），直接读 R/X/Y/theta 平均
# "both"       — 两种都采集
ACQUISITION_MODE = "both"

# ========== Z 射频场频率扫描参数 ==========
Z_RF_FREQ_START = 0.0              # 起始频率 (Hz)，0 为基线点
Z_RF_FREQ_STOP = 10000            # 终止频率 (Hz)
Z_RF_FREQ_POINTS = 101             # 频率扫描点数
Z_RF_FREQ_LOG_SPACED = False       # True: 对数均匀; False: 线性均匀

# ---- Z 射频场固定幅度 ----
Z_RF_AMPLITUDE = 0.05              # Z 射频场固定幅度 (Vpp)

# ---- 每频点测量参数 ----
FREQ_SETTLE_TIME = 0.5            # 切换频率后等待稳定时间 (s)
POINT_DURATION_s = 1.0             # yfft 模式下每频点 DAQ 采集时长 (s)

# ---- 相位扫描参数（嵌套在频率扫描内部）----
PHASE_START = 0                   # 起始相位 (deg)
PHASE_STOP = 360                  # 终止相位 (deg)
PHASE_POINTS = 10                 # 相位点数
PHASE_SETTLE_TIME = 0.1           # 切换相位后等待稳定时间 (s)

# ---- 包络 A(t) 参数（任意波方案）----
A_ENV_FREQ = 500                 # A(t) 基频 (Hz)
A_ENV_K = 16319.281020056458     # Ω → AM 物理电压转换斜率 (Hz/V), 来自 XY_DC_Voltage_Calibration
A_ENV_B = 2121.8563579769193      # Ω → AM 物理电压转换截距 (Hz), 已包含综合零点
A_ENV_K_X = A_ENV_K
A_ENV_B_X = A_ENV_B
A_ENV_K_Y = A_ENV_K
A_ENV_B_Y = A_ENV_B
ARB_WAVEFORM_FILE = "control_waveformAW.csv"  # Ω_ctrl(t) 波形 CSV

# AM 电压换算模式：
#   linear_voltage: V_AM = (Ω_ctrl - B_eff) / K_eff，默认且推荐；不要再额外叠加 MOD_OFFSET。
#   zero_centered: V_AM = V_zero + Ω_ctrl / K_eff，用独立零点电压表示。
AM_VOLTAGE_CONVERSION_MODE = "linear_voltage"
AM_ZERO_V_X = -0.1487540739959558  # 历史 MOD 零点，仅 zero_centered 模式使用
AM_ZERO_V_Y = -0.12523177770966548

# 历史 MOD 偏置常量：保留用于配置记录/对照，不在 linear_voltage 模式中叠加。
MOD_OFFSET_X = AM_ZERO_V_X
MOD_OFFSET_Y = AM_ZERO_V_Y

# ---- X/Y 载波参数（AM 外部调制方案）----
XY_CARRIER_FREQ = 90e3            # X/Y 载波频率 (Hz)
X_CARRIER_AMPLITUDE = 6.0         # X 载波幅值 (Vpp)
Y_CARRIER_AMPLITUDE = 6.0         # Y 载波幅值 (Vpp)
X_CARRIER_PHASE = 0               # CH1 载波初始相位 (deg)，校相后更新
Y_CARRIER_PHASE = 90              # CH2 载波初始相位 (deg)，校相后更新
X_AM_DEPTH = 100                  # X 通道 AM 调制深度 (%)
Y_AM_DEPTH = 100                  # Y 通道 AM 调制深度 (%)

# ========== X/Y 载波相位校准参数（Phase 2）==========
ENABLE_XY_CARRIER_PHASE_CAL = True
XY_PHASE_CAL_DC_X = 1.0           # X 校相时 dg_am CH1 DC 包络 (V)，提高校相信号幅度
XY_PHASE_CAL_DC_Y = 1.0           # Y 校相时 dg_am CH2 DC 包络 (V)，提高校相信号幅度
XY_PHASE_CAL_MAX_ITER_X = 5       # CH1 校相最大迭代次数
XY_PHASE_CAL_MAX_ITER_Y = 15      # CH2 校相最大迭代次数
XY_PHASE_CAL_TOL_DEG = 0.5        # |theta°| < tol 即视为收敛
XY_PHASE_CAL_SETTLE_s = 0.5       # 每次 set_phase_adjust 后的等待时间

# ---- Z 射频场幅度转换系数 ----
Z_V_TO_NT = 3517 / 2              # V → nT 转换系数

# ---- Pump 调制参数 ----
PUMP_MOD_FREQ = 90000             # Pump 调制频率 (Hz)
PUMP_MOD_DUTY = 5                 # 脉冲占空比 (%)
PUMP_MOD_AMPLITUDE = 0.18         # 100MHz 载波幅度 (Vpp)

# ---- HF2 解调器 0 配置（主信号解调）----
DEMOD0_IDX = 0
DEMOD0_OSC_IDX = 0
DEMOD0_OSC_FREQ = 90000
DEMOD0_SIGNAL_RANGE = 2.0
DEMOD0_ORDER = 4
DEMOD0_TC_CALIB = 0.001           # 校相时使用 (s)
DEMOD0_TC_MEAS = 1e-6             # 采集数据时使用 (s)
DEMOD0_RATE = 100000               # 采样率 (Sa/s)

# ========== HF2 解调器 3 配置（射频场解调，物理回环）==========
# Demod 3 通过物理回环接收 Demod 0 Y：
#   Demod 0 Y -> Aux Out 2 -> 物理线 -> Signal Input 2 -> Demod 3
# ⚠ 不再使用 DEMOD3_ADC_SELECT = 2（HF2 普通 Demod 3 没有内部路由选项）
DEMOD3_IDX = 3
DEMOD3_OSC_IDX = 1
DEMOD3_TC_MIN_s = 0.01            # TC 下限，避免过低引入低频噪声
DEMOD3_TC_PERIOD_FRACTION = 0.05  # TC = 周期 × 该系数
DEMOD3_ORDER = 8
DEMOD3_RATE = 4800                # Demod 3 输出采样率 (Sa/s)
DEMOD3_READ_SETTLE_TIME_s = 0.2   # 切换频率后等待 Demod 3 稳定的时间
DEMOD3_N_AVG = 50                 # demod_rxy 模式下每频点平均次数
DEMOD3_AVG_INTERVAL_s = 0.01      # demod_rxy 模式下读取间隔 (s)

# ========== 物理回环链路参数 (Demod 0 Y → AuxOut2 → SigIn2 → Demod 3) ==========
CONFIGURE_AUXOUT2_TO_DEMOD0_Y = True
AUXOUT_INDEX = 1                  # 0 = Aux Out 1, 1 = Aux Out 2 (HF2 后面板)
AUXOUT_SOURCE_DEMOD_IDX = 0       # Demod 0 (主信号解调)
AUXOUT_SOURCE_SELECT = 1          # 0=X, 1=Y, 2=R, 3=Theta → 取 Y
AUXOUT_SCALE = 1.0                # 输出电压缩放系数
AUXOUT_OFFSET = 0.0               # 输出电压偏置 (V)

# Signal Input 2 (HF2 后面板) 接收 Aux Out 2 的物理输出
SIGIN2_INDEX = 1                  # 0 = Signal Input 1, 1 = Signal Input 2
SIGIN2_RANGE = 1.0                # 信号输入量程 (V)
SIGIN2_AC_COUPLING = False        # 关键：必须 DC coupling
SIGIN2_IMPEDANCE = 50             # 输入阻抗 (Ω)

# Demod 3 的 adcselect 必须指 SIGIN2_INDEX = 1（Signal Input 2），不是 2
DEMOD3_ADC_SELECT = SIGIN2_INDEX

# ---- 温控开关参数 ----
TEMP_SWITCH_OFF_LEAD = 0.1        # 采集前关温度开关后等待磁场残余消失 (s)
TEMP_SWITCH_ON_LAG = 1.0          # 采集后开温度开关后等待系统稳定 (s)

# ========== 固定参数（沿用 Static_Magnetic_Field_Sensitivity）==========
FIXED_PARAMS = {
    "Pump_laser_power": 0.2,
    "Probe_laser_power": 0.1,
    "main_magnetic_field": 9.31,
    "temperature": 100,
    "Temp_Switch": 5.0,
}

# ---- 安全校验 ----
for k, v in FIXED_PARAMS.items():
    validate_safety_limit(k, v)
validate_safety_limit("Z_magnetic_field", Z_RF_AMPLITUDE)
# 显式声明本实验不占用 rf_coil（与 Y_magnetic_field 共用 DG4E234902522 CH2）
validate_safety_limit("rf_coil", 0.0)

# ---- 生成频率扫描列表 ----
if Z_RF_FREQ_LOG_SPACED:
    freq_list = np.logspace(
        np.log10(max(Z_RF_FREQ_START, 1)),  # logspace 要求下限 > 0
        np.log10(Z_RF_FREQ_STOP), Z_RF_FREQ_POINTS
    )
else:
    freq_list = np.linspace(Z_RF_FREQ_START, Z_RF_FREQ_STOP, Z_RF_FREQ_POINTS)+50

has_baseline = (Z_RF_FREQ_START == 0.0)

print(f"实验类型: {EXPERIMENT_TYPE}")
print(f"运行标签: {RUN_TAG}")
print(f"采集模式: {ACQUISITION_MODE}")
print(f"Z 射频场幅度: {Z_RF_AMPLITUDE} Vpp = {Z_RF_AMPLITUDE * Z_V_TO_NT:.2f} nT p-p")
print(f"频率扫描: {freq_list[0]:.1f} ~ {freq_list[-1]:.1f} Hz, {len(freq_list)} 点")
if has_baseline:
    print(f"  包含 0 Hz 基线点")

# ---- 生成相位扫描列表 ----
phase_list = np.linspace(PHASE_START, PHASE_STOP, PHASE_POINTS)
print(f"相位扫描列表 ({PHASE_POINTS} 点):")
print(f"  范围: {phase_list[0]:.1f} ~ {phase_list[-1]:.1f} deg")
print("配置已加载")

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

    # ---- DG4000: X/Y 补偿磁场（AM 载波输出）----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    # 初始关闭
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)
    devices["dg_comp"] = dg_comp

    # ---- DG4000: X/Y AM A(t) 独立控制 (dg_am) ----
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

    # ---- DG4000: Z 射频场（dg_sweep）----
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = create_signal_generator(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"dg_sweep DG4000 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    devices["dg_sweep"] = dg_sweep

    # ---- DG4000: Pump 调制 ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = create_signal_generator(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    print(f"调制 DG4000 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # ---- DG900: 温度开关 (ConstXY 已确认是 DG900，不是 DG4000) ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    print(f"温控 DG900 已连接: {dg_temp.idn()}")
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

    # ---- DG900: Pump/Probe 光功率 (dg_laser) ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = create_signal_generator(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    print(f"光功率 DG900 已连接: {dg_laser.idn()}")
    devices["dg_laser"] = dg_laser

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

    # ---- SDS 示波器（可选监测，连接失败不终止实验）----
    scope = None
    try:
        scope_cfg = MAPPING.get("scope_waveform", {})
        scope = SDSInstrument(scope_cfg.get("resource", ""))
        scope.connect()
        print(f"示波器已连接: {scope.idn()}")
    except Exception as e:
        print(f"示波器连接失败 (可选设备，继续): {e}")
        scope = None
    devices["scope"] = scope

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

# ---- 1. Pump 光功率 (dg_laser CH1) ----
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
print(f"Pump 光功率: {FIXED_PARAMS['Pump_laser_power']} V DC")

# ---- 2. Probe 光功率 (dg_laser CH2) ----
dg_laser.setup_dc(FIXED_PARAMS["Probe_laser_power"], channel=2)
print(f"Probe 光功率: {FIXED_PARAMS['Probe_laser_power']} V DC")

# ---- 3. 主磁场 (GS200) ----
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)  # mA → A
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# ---- 4. 温度开关 ON ----
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 5. 温度控制（等待稳定 ±1°C）----
configure_temperature_control(
    tec, FIXED_PARAMS["temperature"], tolerance_c=1.0, stable_reads=1
)

# ---- 6. dg_am 初始值（DC 偏置，等待后续任意波配置）----
dg_am.setup_dc(0.0, channel=1)
dg_am.setup_dc(0.0, channel=2)
dg_am.set_output(False, channel=1)
dg_am.set_output(False, channel=2)
print("dg_am CH1(X AM): standby DC=0.000V, 输出 OFF")
print("dg_am CH2(Y AM): standby DC=0.000V, 输出 OFF")

# ---- 7. Pump 调制配置（RF 开关方案）----
print("\n--- Pump 调制配置 ---")

# [经验] 配置前先重置 burst/mod 状态
dg_mod.set_burst_state(False, channel=1)
dg_mod.set_mod_state(False, channel=1)
dg_mod.set_burst_state(False, channel=2)
dg_mod.set_mod_state(False, channel=2)

# CH1: 100MHz 连续正弦波 → RF 开关 IN
validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE)
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE,
                  offset=0.0, phase=0.0, channel=1)
print(f"CH1: 100MHz 正弦, {PUMP_MOD_AMPLITUDE*1000:.0f} mVpp → RF 开关 IN")

# CH2: 脉冲门控
validate_safety_limit("Time_sequence", 5.0)
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=5.0,
                   offset=2.5, channel=2)
dg_mod.set_pulse_dcycle(PUMP_MOD_DUTY, channel=2)
print(f"CH2: 脉冲 {PUMP_MOD_FREQ/1000:.0f} kHz, 占空比 {PUMP_MOD_DUTY}%, 5Vpp+2.5V offset")

# CH2 SYNC ON → 作为外部触发源
dg_mod.set_sync_state(True, channel=2)
print(f"CH2 SYNC: ON (→ dg_sweep Ext Trig)")

# ---- 8. 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"\n运行目录: {run_dir}")

# ---- 9. 保存实验配置 ----
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": "RF field frequency response (AW modulation)",
    "timestamp": timestamp,
    "run_tag": RUN_TAG,
    "acquisition_mode": ACQUISITION_MODE,
    "freq_sweep": {
        "Z_RF_FREQ_START_Hz": Z_RF_FREQ_START,
        "Z_RF_FREQ_STOP_Hz": Z_RF_FREQ_STOP,
        "Z_RF_FREQ_POINTS": Z_RF_FREQ_POINTS,
        "Z_RF_FREQ_LOG_SPACED": Z_RF_FREQ_LOG_SPACED,
        "freq_list_Hz": freq_list.tolist(),
        "Z_RF_AMPLITUDE_Vpp": Z_RF_AMPLITUDE,
        "FREQ_SETTLE_TIME_s": FREQ_SETTLE_TIME,
        "POINT_DURATION_s": POINT_DURATION_s,
        "RESPONSE_N_AVG": DEMOD3_N_AVG,
        "has_baseline": has_baseline,
    },
    "phase_sweep": {
        "PHASE_START_deg": PHASE_START,
        "PHASE_STOP_deg": PHASE_STOP,
        "PHASE_POINTS": PHASE_POINTS,
        "phase_list_deg": phase_list.tolist(),
        "PHASE_SETTLE_TIME_s": PHASE_SETTLE_TIME,
    },
    "envelope": {
        "A_ENV_FREQ_Hz": A_ENV_FREQ,
        "AM_VOLTAGE_CONVERSION_MODE": AM_VOLTAGE_CONVERSION_MODE,
        "A_ENV_K_Hz_per_V": A_ENV_K,
        "A_ENV_B_Hz": A_ENV_B,
        "A_ENV_K_X_Hz_per_V": A_ENV_K_X,
        "A_ENV_B_X_Hz": A_ENV_B_X,
        "A_ENV_K_Y_Hz_per_V": A_ENV_K_Y,
        "A_ENV_B_Y_Hz": A_ENV_B_Y,
        "ARB_WAVEFORM_FILE": ARB_WAVEFORM_FILE,
        "AM_ZERO_V_X": AM_ZERO_V_X,
        "AM_ZERO_V_Y": AM_ZERO_V_Y,
        "MOD_OFFSET_X_V_legacy_not_applied": MOD_OFFSET_X,
        "MOD_OFFSET_Y_V_legacy_not_applied": MOD_OFFSET_Y,
        "offset_double_compensation_guard": "linear_voltage mode already includes the AM zero in B_eff; do not add MOD_OFFSET",
    },
    "xy_carrier": {
        "XY_CARRIER_FREQ_Hz": XY_CARRIER_FREQ,
        "X_CARRIER_AMPLITUDE_Vpp": X_CARRIER_AMPLITUDE,
        "Y_CARRIER_AMPLITUDE_Vpp": Y_CARRIER_AMPLITUDE,
        "X_AM_DEPTH_pct": X_AM_DEPTH,
        "Y_AM_DEPTH_pct": Y_AM_DEPTH,
        "XY_PHASE_CAL_DC_X_V": XY_PHASE_CAL_DC_X,
        "XY_PHASE_CAL_DC_Y_V": XY_PHASE_CAL_DC_Y,
    },
    "hf2_demod0": {
        "demod_idx": DEMOD0_IDX,
        "osc_idx": DEMOD0_OSC_IDX,
        "osc_freq_Hz": DEMOD0_OSC_FREQ,
        "signal_range_V": DEMOD0_SIGNAL_RANGE,
        "rate_Sa_s": DEMOD0_RATE,
        "TC_calib_s": DEMOD0_TC_CALIB,
        "TC_meas_s": DEMOD0_TC_MEAS,
        "order": DEMOD0_ORDER,
    },
    "hf2_demod3": {
        "demod_idx": DEMOD3_IDX,
        "osc_idx": DEMOD3_OSC_IDX,
        "adcselect": DEMOD3_ADC_SELECT,
        "signal_source": "physical_loopback_auxout2_demod0_y_to_signal_input2",
        "physical_link": "AuxOut2 → physical cable → Signal Input 2 DC → Demod3",
        "TC_min_s": DEMOD3_TC_MIN_s,
        "TC_period_fraction": DEMOD3_TC_PERIOD_FRACTION,
        "rate_Sa_s": DEMOD3_RATE,
        "order": DEMOD3_ORDER,
        "N_AVG": DEMOD3_N_AVG,
        "AVG_INTERVAL_s": DEMOD3_AVG_INTERVAL_s,
        "READ_SETTLE_TIME_s": DEMOD3_READ_SETTLE_TIME_s,
    },
    "physical_demod_chain": {
        "auxout_index": AUXOUT_INDEX,
        "auxout_demod_source": AUXOUT_SOURCE_DEMOD_IDX,
        "auxout_signal_select": {0: "X", 1: "Y", 2: "R", 3: "Theta"}[AUXOUT_SOURCE_SELECT],
        "auxout_scale": AUXOUT_SCALE,
        "auxout_offset": AUXOUT_OFFSET,
        "sigin2_index": SIGIN2_INDEX,
        "sigin2_DC_coupling": not SIGIN2_AC_COUPLING,
        "sigin2_range_V": SIGIN2_RANGE,
        "note": "Legacy adcselect=2 is NOT usable. Physical loopback is required.",
    },
    "pump_modulation": {
        "PUMP_MOD_FREQ_Hz": PUMP_MOD_FREQ,
        "PUMP_MOD_DUTY_pct": PUMP_MOD_DUTY,
        "PUMP_MOD_AMPLITUDE_Vpp": PUMP_MOD_AMPLITUDE,
    },
    "fixed_params": FIXED_PARAMS,
    "Z_V_to_nT": Z_V_TO_NT,
    "rf_coil_shared_channel_note": (
        "RF coil and Y_magnetic_field share instrument DG4E234902522 CH2. "
        "When Z RF field output is ON, Y compensation field output MUST be OFF."
    ),
    "mapping_snapshot": {
        key: {k: v for k, v in cfg.items() if k != "display"}
        for key, cfg in MAPPING.items()
        if key in ("main_magnetic_field", "X_magnetic_field", "Y_magnetic_field",
                   "X_magnetic_field_AM", "Y_magnetic_field_AM", "Z_magnetic_field",
                   "Pump_modulation", "Time_sequence", "Temp_Switch", "lockin_r",
                   "scope_waveform")
    },
    "safety_limits_snapshot": {
        key: LIMITS[key] for key in LIMITS
        if key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field",
                   "X_magnetic_field_AM", "Y_magnetic_field_AM", "rf_coil",
                   "main_magnetic_field", "Pump_modulation", "Time_sequence",
                   "Temp_Switch", "temperature")
    },
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print(f"实验配置已保存: {config_path}")
print("\n初始值设置完成")

# %% Cell 5
# ============================================================
# Phase 1: HF2 配置 — Demod 0 校相 + 物理回环链路配置
# ============================================================
print("=" * 60)
print("Phase 1: HF2 配置 — Demod 0 校相 + Demod 0 Y → AuxOut2 → SigIn2 → Demod 3")
print("=" * 60)

# ---- 1a. 关闭所有磁场 ----
print("关闭所有磁场...")
dg_temp.set_output(False, channel=2)
print("  温度开关: OFF")
dg_comp.set_output(False, channel=1)
dg_comp.set_output(False, channel=2)
print("  X/Y 场: OFF")
dg_am.set_output(False, channel=1)
dg_am.set_output(False, channel=2)
print("  X/Y AM A(t): OFF")
dg_sweep.set_output(False, channel=1)
print("  Z 场: OFF")
time.sleep(0.5)

# ---- 1b. 配置信号输入 0 (Sig In 1, 主信号) ----
sig_cfg = SignalInputConfig(
    input_index=0,
    range=DEMOD0_SIGNAL_RANGE,
    ac_coupling=True,
    diff=False,
    impedance=50,
)
demod.configure_signal_input(hfi, sig_cfg)
print(f"信号输入 0: range={DEMOD0_SIGNAL_RANGE} V, AC 耦合")

# ---- 1c. 配置振荡器 0 ----
osc0_cfg = OscillatorConfig(
    osc_index=DEMOD0_OSC_IDX,
    frequency=DEMOD0_OSC_FREQ,
)
demod.configure_oscillator(hfi, osc0_cfg)
print(f"振荡器 0: {DEMOD0_OSC_FREQ} Hz")

# ---- 1d. 配置 Demod 0 (校相 TC) ----
demod0_cfg = DemodulatorConfig(
    demod_index=DEMOD0_IDX,
    enable=True,
    rate=DEMOD0_RATE,
    input_channel=0,
    osc_select=DEMOD0_OSC_IDX,
    harmonic=1,
    time_constant=DEMOD0_TC_CALIB,
    order=DEMOD0_ORDER,
    phase=0.0,
)
actual_rate_d0 = demod.configure_demodulator(hfi, demod0_cfg)
print(f"Demod 0: TC={DEMOD0_TC_CALIB:.0e} s (校相), rate={actual_rate_d0:.0f} Sa/s")

# ---- 1e. 配置 Signal Input 2 (DC 耦合，物理回环接收端) ----
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

# ---- 1f. 配置 Aux Out 2 → Demod 0 Y (物理回环发送端) ----
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
    print(f"  读回: outputselect={auxout_actual_outputselect} "
          f"(Demod{auxout_actual_demod} {auxout_actual_sig_str}), "
          f"scale={auxout_actual_scale}, offset={auxout_actual_offset}")
else:
    auxout_actual_outputselect = None
    auxout_actual_scale = None
    auxout_actual_offset = None
    auxout_actual_demod = None
    auxout_actual_sig_str = None

# ---- 1g. 配置 Demod 3 (射频场解调，初值 1 kHz) ----
osc3_initial_freq = 1000.0
osc1_initial_cfg = OscillatorConfig(
    osc_index=DEMOD3_OSC_IDX,
    frequency=osc3_initial_freq,
)
demod.configure_oscillator(hfi, osc1_initial_cfg)
print(f"振荡器 1 (Demod 3 参考): 初值 {osc3_initial_freq} Hz")

# adcselect = SIGIN2_INDEX = 1：Demod 3 输入信号来自 Signal Input 2
demod3_initial_tc = max(DEMOD3_TC_MIN_s,
                         DEMOD3_TC_PERIOD_FRACTION / osc3_initial_freq)
demod3_initial_cfg = DemodulatorConfig(
    demod_index=DEMOD3_IDX,
    enable=True,
    rate=DEMOD3_RATE,
    input_channel=DEMOD3_ADC_SELECT,
    osc_select=DEMOD3_OSC_IDX,
    harmonic=1,
    time_constant=demod3_initial_tc,
    order=DEMOD3_ORDER,
    phase=0.0,
)
actual_rate_d3 = demod.configure_demodulator(hfi, demod3_initial_cfg)
print(f"Demod {DEMOD3_IDX} (射频场, 初值): rate={actual_rate_d3:.0f} Sa/s, "
      f"TC={demod3_initial_tc*1e3:.2f} ms, "
      f"adcselect={DEMOD3_ADC_SELECT} (= Signal Input 2, 物理回环)")

# ---- 1h. 自动校相 (Demod 0) ----
print("\n正在进行 Demod 0 相位校准...")
time.sleep(0.5)
calibrated_phase_0 = demod.auto_calibrate_phase(
    hfi, demod_idx=DEMOD0_IDX,
    tolerance_deg=1.0, max_attempts=5, settle_time=0.2,
)
print(f"Demod 0 校准完成: phaseshift = {calibrated_phase_0:.2f}°")
sample = demod.read_demod_sample(hfi, demod_idx=DEMOD0_IDX)
print(f"  校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")
time.sleep(2)

# ---- 1i. 恢复温控 ----
dg_temp.set_output(True, channel=2)
print("温度开关已恢复")

# ---- 1j. 切换 Demod 0 至测量 TC ----
demod0_meas_cfg = DemodulatorConfig(
    demod_index=DEMOD0_IDX, enable=True,
    rate=DEMOD0_RATE,
    input_channel=0,
    osc_select=DEMOD0_OSC_IDX, harmonic=1,
    time_constant=DEMOD0_TC_MEAS,
    order=DEMOD0_ORDER,
    phase=calibrated_phase_0,
)
actual_rate_d0_meas = demod.configure_demodulator(hfi, demod0_meas_cfg)
print(f"Demod 0 (测量): rate={actual_rate_d0_meas:.0f} Sa/s, "
      f"TC={DEMOD0_TC_MEAS*1e6:.0f} μs, phase={calibrated_phase_0:.2f}°")

# ---- 1k. 读回 Signal Input 2 / Demod 3 实际配置 (用于 config 快照) ----
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

# ---- 保存 HF2 实际配置到 config ----
hf2_actual = {
    "demod0": {
        "rate_Sa_s": float(actual_rate_d0_meas),
        "phase_deg": float(calibrated_phase_0),
    },
    "demod3": {
        "adcselect": int(demod_r_actual_adcselect),
        "oscselect": int(demod_r_actual_osc),
        "rate_Sa_s": float(demod_r_actual_rate),
        "TC_s": float(demod_r_actual_tc),
        "order": int(demod_r_actual_order),
    },
    "sigin2": {
        "range_V": float(sigin2_actual_range),
        "ac_coupling": bool(sigin2_actual_ac),
        "imp50": bool(sigin2_actual_imp50),
    },
    "auxout2": {
        "outputselect": int(auxout_actual_outputselect) if auxout_actual_outputselect is not None else None,
        "scale": float(auxout_actual_scale) if auxout_actual_scale is not None else None,
        "offset": float(auxout_actual_offset) if auxout_actual_offset is not None else None,
        "demod_source": int(auxout_actual_demod) if auxout_actual_demod is not None else None,
        "signal_type": str(auxout_actual_sig_str) if auxout_actual_sig_str is not None else None,
    },
}
config["hf2_actual_config"] = hf2_actual
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print("HF2 实际配置已保存到 experiment_config.yaml")
# %%
# ============================================================
# Phase 2: X/Y 载波相位校准（函数化，记录收敛状态）
# ============================================================
print("\n" + "=" * 60)
print("Phase 2: X/Y 载波相位校准")
print("=" * 60)

# ---- Step A: 加载 Ω_ctrl(t) → A(t) → 计算参数（先不写入 dg_am）----
print("\n从 CSV 加载 Ω_ctrl(t) 并计算 A(t) 任意波参数...")

def omega_to_am_voltage(omega_ctrl_hz, channel):
    """把 Ω_ctrl(Hz) 转成 dg_am 外部 AM 物理电压。"""
    omega_ctrl_hz = np.asarray(omega_ctrl_hz, dtype=float)
    ch = str(channel).lower()
    if ch in ("x", "1", "ch1"):
        k_eff, b_eff, v_zero = A_ENV_K_X, A_ENV_B_X, AM_ZERO_V_X
    elif ch in ("y", "2", "ch2"):
        k_eff, b_eff, v_zero = A_ENV_K_Y, A_ENV_B_Y, AM_ZERO_V_Y
    else:
        raise ValueError(f"未知 AM 通道: {channel!r}")

    if AM_VOLTAGE_CONVERSION_MODE == "linear_voltage":
        return (omega_ctrl_hz - b_eff) / k_eff
    if AM_VOLTAGE_CONVERSION_MODE == "zero_centered":
        return v_zero + omega_ctrl_hz / k_eff
    raise ValueError(
        "AM_VOLTAGE_CONVERSION_MODE 必须为 'linear_voltage' 或 'zero_centered'; "
        "不要使用 (Ω-B)/K + MOD_OFFSET 的混合补偿"
    )


def build_aw_params(v_waveform):
    """把物理电压波形转换为 DG arbitrary 的 normalized/Vpp/offset 参数。"""
    v_waveform = np.asarray(v_waveform, dtype=float)
    v_min = float(np.min(v_waveform))
    v_max = float(np.max(v_waveform))
    v_center = (v_max + v_min) / 2.0
    v_half_range = (v_max - v_min) / 2.0
    if v_half_range <= 0:
        normalized = np.zeros_like(v_waveform)
        vpp = 0.002
    else:
        normalized = (v_waveform - v_center) / v_half_range
        vpp = 2.0 * v_half_range
    return {
        "waveform_V": v_waveform,
        "normalized": normalized,
        "vpp": float(vpp),
        "offset": float(v_center),
        "min": float(v_min),
        "max": float(v_max),
    }


waveform_path = project_root / "experiments" / ARB_WAVEFORM_FILE
waveform_data = np.loadtxt(waveform_path, delimiter=",", skiprows=1)
omega_ctrl = waveform_data[:, 1]  # Ω_ctrl (Hz)

# 推荐坐标：linear_voltage 直接使用 V_AM=(Ω_ctrl-B_eff)/K_eff，不再额外加 MOD_OFFSET。
x_aw = build_aw_params(omega_to_am_voltage(omega_ctrl, "x"))
y_aw = build_aw_params(omega_to_am_voltage(omega_ctrl, "y"))

# 兼容旧变量名（用于后续少量打印/配置）：以 X 通道为代表。
a_waveform = x_aw["waveform_V"]
a_min = x_aw["min"]
a_max = x_aw["max"]
a_center = x_aw["offset"]
a_normalized = x_aw["normalized"]
arb_vpp = x_aw["vpp"]

# 校相期间使用较大的独立 DC 包络；这是物理 AM 电压，不再叠加 MOD_OFFSET。
CALIB_DC_X = float(XY_PHASE_CAL_DC_X)
CALIB_DC_Y = float(XY_PHASE_CAL_DC_Y)
validate_safety_limit("X_magnetic_field_AM", CALIB_DC_X)
validate_safety_limit("Y_magnetic_field_AM", CALIB_DC_Y)

x_offset_final = x_aw["offset"]
y_offset_final = y_aw["offset"]
x_min_out = x_aw["min"]
x_max_out = x_aw["max"]
y_min_out = y_aw["min"]
y_max_out = y_aw["max"]

print(f"  AM conversion mode: {AM_VOLTAGE_CONVERSION_MODE}")
print(f"  Ω_ctrl 范围: [{np.min(omega_ctrl):.2f}, {np.max(omega_ctrl):.2f}] Hz, mean={np.mean(omega_ctrl):.2f} Hz")
print(f"  CH1 AM 电压范围: [{x_min_out:.3f},{x_max_out:.3f}] V, offset={x_offset_final:.3f}V, Vpp={x_aw['vpp']:.3f}V")
print(f"  CH2 AM 电压范围: [{y_min_out:.3f},{y_max_out:.3f}] V, offset={y_offset_final:.3f}V, Vpp={y_aw['vpp']:.3f}V")
print(f"  等效均值 Ω: X={np.mean(x_aw['waveform_V']) * A_ENV_K_X + A_ENV_B_X:.1f} Hz, "
      f"Y={np.mean(y_aw['waveform_V']) * A_ENV_K_Y + A_ENV_B_Y:.1f} Hz")
print(f"  校相 DC 值: X={CALIB_DC_X:.3f}V, Y={CALIB_DC_Y:.3f}V")

# 安全校验任意波输出范围
for ch_name, v_min, v_max in [("X_magnetic_field_AM", x_min_out, x_max_out),
                                ("Y_magnetic_field_AM", y_min_out, y_max_out)]:
    validate_safety_limit(ch_name, v_min)
    validate_safety_limit(ch_name, v_max)
print("  ✅ 任意波输出电压范围安全校验通过")
print("  (任意波配置将在校相完成后再写入 dg_am)")

# %%
# ---- 辅助函数: 配置 dg_comp CH1/CH2 AM 外部调制 ----
def configure_xy_carrier_for_phase_cal(dg_comp_inst, x_phase_deg, y_phase_deg):
    """把 dg_comp CH1/CH2 配成 90 kHz sine + AM EXT，准备相位校准。"""
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


def apply_z_rf_burst_phase(dg_sweep_inst, phase_deg):
    """设置 Z 射频 Burst 起始相位，并重新开关输出使相位生效。"""
    dg_sweep_inst.set_output(False, channel=1)
    dg_sweep_inst.set_burst_state(False, channel=1)
    dg_sweep_inst.set_burst_phase(float(phase_deg), channel=1)
    dg_sweep_inst.set_burst_state(True, channel=1)
    dg_sweep_inst.set_output(True, channel=1)


def set_xy_phase_cal_am_dc(dg_am_inst, v_dc_x, v_dc_y):
    """设置 dg_am CH1/CH2 的 DC 包络，用于 X/Y 载波相位校准。"""
    validate_safety_limit("X_magnetic_field_AM", v_dc_x)
    validate_safety_limit("Y_magnetic_field_AM", v_dc_y)
    for ch in (1, 2):
        dg_am_inst.set_burst_state(False, channel=ch)
        dg_am_inst.set_mod_state(False, channel=ch)
    dg_am_inst.setup_dc(v_dc_x, channel=1)
    dg_am_inst.setup_dc(v_dc_y, channel=2)
    print(f"  dg_am CH1(X): DC = {v_dc_x:.4f} V")
    print(f"  dg_am CH2(Y): DC = {v_dc_y:.4f} V")
    return v_dc_x, v_dc_y


def calibrate_xy_carrier_phase(dg_comp_inst, dg_am_inst, hfi_inst,
                                initial_x_phase, initial_y_phase,
                                calib_dc_x, calib_dc_y,
                                max_iter_x=5, max_iter_y=15,
                                tol_deg=0.5, settle_s=0.5,
                                calibrated_demod0_phase_deg=0.0):
    """分通道迭代校准 X/Y 载波相位，返回 dict 含校准结果与收敛状态。"""
    import math as _math

    result = {
        "x_carrier_phase_calibrated_deg": float(initial_x_phase) % 360,
        "y_carrier_phase_calibrated_deg": float(initial_y_phase) % 360,
        "xy_phase_diff_deg": (float(initial_y_phase) - float(initial_x_phase)) % 360,
        "phase_cal_success_x": False,
        "phase_cal_success_y": False,
        "n_iter_x": 0,
        "n_iter_y": 0,
        "calib_dc_x_V": float(calib_dc_x),
        "calib_dc_y_V": float(calib_dc_y),
    }

    x_phase = float(initial_x_phase) % 360
    y_phase = float(initial_y_phase) % 360

    # ---- Demod 0 切到校相 TC ----
    demod0_phase_cal_cfg = DemodulatorConfig(
        demod_index=DEMOD0_IDX, enable=True,
        rate=DEMOD0_RATE,
        input_channel=0, osc_select=DEMOD0_OSC_IDX, harmonic=1,
        time_constant=DEMOD0_TC_CALIB,
        order=DEMOD0_ORDER,
        phase=float(calibrated_demod0_phase_deg),
    )
    demod.configure_demodulator(hfi_inst, demod0_phase_cal_cfg)
    print(f"  Demod 0: TC={DEMOD0_TC_CALIB:.0e} s (校相 TC)")

    # ---- AM DC + 载波配置 ----
    set_xy_phase_cal_am_dc(dg_am_inst, calib_dc_x, calib_dc_y)
    configure_xy_carrier_for_phase_cal(dg_comp_inst, x_phase, y_phase)

    # ---- CH1 校相 ----
    print("\n  校准 CH1 相位 (仅开 CH1)...")
    dg_comp_inst.set_output(False, channel=2)
    dg_am_inst.set_output(False, channel=2)
    dg_comp_inst.set_output(True, channel=1)
    dg_am_inst.set_output(True, channel=1)
    time.sleep(settle_s)

    for i in range(max_iter_x):
        sample = demod.read_demod_sample(hfi_inst, demod_idx=DEMOD0_IDX)
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
        sample = demod.read_demod_sample(hfi_inst, demod_idx=DEMOD0_IDX)
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
    print(f"    实际相位差 = {result['xy_phase_diff_deg']:.2f}°")
    return result


# ---- Step B: 配置 dg_sweep CH2 触发直通 ----
print("\n配置 dg_sweep CH2 触发直通...")
dg_sweep.setup_square(freq=10, amplitude=5.0, offset=2.5, channel=2)
print(f"dg_sweep CH2: 方波 10Hz, 外部触发 → dg_am Ext Trig")

# ---- Step C: 执行校相 ----
if ENABLE_XY_CARRIER_PHASE_CAL:
    calib_result = calibrate_xy_carrier_phase(
        dg_comp, dg_am, hfi,
        initial_x_phase=X_CARRIER_PHASE,
        initial_y_phase=Y_CARRIER_PHASE,
        calib_dc_x=CALIB_DC_X,
        calib_dc_y=CALIB_DC_Y,
        max_iter_x=XY_PHASE_CAL_MAX_ITER_X,
        max_iter_y=XY_PHASE_CAL_MAX_ITER_Y,
        tol_deg=XY_PHASE_CAL_TOL_DEG,
        settle_s=XY_PHASE_CAL_SETTLE_s,
        calibrated_demod0_phase_deg=calibrated_phase_0,
    )
    # 更新全局校相结果
    X_CARRIER_PHASE = float(calib_result["x_carrier_phase_calibrated_deg"])
    Y_CARRIER_PHASE = float(calib_result["y_carrier_phase_calibrated_deg"])
else:
    print("Phase 2: X/Y 载波相位校准已禁用 (ENABLE_XY_CARRIER_PHASE_CAL=False)")
    calib_result = {
        "x_carrier_phase_calibrated_deg": float(X_CARRIER_PHASE) % 360,
        "y_carrier_phase_calibrated_deg": float(Y_CARRIER_PHASE) % 360,
        "xy_phase_diff_deg": (float(Y_CARRIER_PHASE) - float(X_CARRIER_PHASE)) % 360,
        "phase_cal_success_x": False,
        "phase_cal_success_y": False,
        "n_iter_x": 0, "n_iter_y": 0,
        "calib_dc_x_V": float(CALIB_DC_X),
        "calib_dc_y_V": float(CALIB_DC_Y),
    }

print(f"\n✅ 分通道校相完成: X={X_CARRIER_PHASE:.2f}°, Y={Y_CARRIER_PHASE:.2f}°, "
      f"Δ={(Y_CARRIER_PHASE - X_CARRIER_PHASE) % 360:.2f}°")
# %%
# ---- Step D: 校相完成，dg_am 切回 A(t) 任意波 + Burst 模式 ----
print("\n校相完成，dg_am 切回 A(t) 任意波模式...")

# 安全校验任意波输出电压范围（确认无越界）
validate_safety_limit("X_magnetic_field_AM", x_offset_final)
validate_safety_limit("X_magnetic_field_AM", x_min_out)
validate_safety_limit("X_magnetic_field_AM", x_max_out)
validate_safety_limit("Y_magnetic_field_AM", y_offset_final)
validate_safety_limit("Y_magnetic_field_AM", y_min_out)
validate_safety_limit("Y_magnetic_field_AM", y_max_out)

# CH1 (X): 物理 AM 电压波形
dg_am.setup_arbitrary(
    x_aw["normalized"].copy(), freq=A_ENV_FREQ,
    amplitude=x_aw["vpp"], offset=x_aw["offset"], phase=0.0, channel=1,
)
print(f"  CH1 (X): 任意波 {A_ENV_FREQ}Hz, Vpp={x_aw['vpp']:.3f}V, offset={x_aw['offset']:.3f}V")

# CH2 (Y): 物理 AM 电压波形
dg_am.setup_arbitrary(
    y_aw["normalized"].copy(), freq=A_ENV_FREQ,
    amplitude=y_aw["vpp"], offset=y_aw["offset"], phase=0.0, channel=2,
)
print(f"  CH2 (Y): 任意波 {A_ENV_FREQ}Hz, Vpp={y_aw['vpp']:.3f}V, offset={y_aw['offset']:.3f}V")

# Burst 模式 + 外部触发
for ch in [1, 2]:
    dg_am.set_burst_state(True, channel=ch)
    dg_am.set_burst_mode("INFinity", channel=ch)
    dg_am.set_burst_trigger_source("EXTernal", channel=ch)
    dg_am.set_burst_phase(0, channel=ch)
print("  CH1/CH2 Burst INFinity, Ext Trig")

# ---- Step E: 切换 Demod 0 至测量 TC ----
demod0_meas_cfg = DemodulatorConfig(
    demod_index=DEMOD0_IDX, enable=True, rate=DEMOD0_RATE,
    input_channel=0, osc_select=DEMOD0_OSC_IDX, harmonic=1,
    time_constant=DEMOD0_TC_MEAS, order=DEMOD0_ORDER,
    phase=calibrated_phase_0,
)
actual_rate_d0_meas = demod.configure_demodulator(hfi, demod0_meas_cfg)
print(f"\nDemod 0: TC={DEMOD0_TC_MEAS*1000:.1f} ms (数据采集), rate={actual_rate_d0_meas:.0f} Sa/s")

# ---- 保存校准结果 ----
calib_results = {
    "calibrated_phase_0_deg": float(calibrated_phase_0),
    "X_CARRIER_PHASE_deg": float(X_CARRIER_PHASE),
    "Y_CARRIER_PHASE_deg": float(Y_CARRIER_PHASE),
    "xy_phase_diff_deg": float(calib_result["xy_phase_diff_deg"]),
    "phase_cal_success_x": calib_result["phase_cal_success_x"],
    "phase_cal_success_y": calib_result["phase_cal_success_y"],
    "n_iter_x": calib_result["n_iter_x"],
    "n_iter_y": calib_result["n_iter_y"],
    "calib_dc_x_V": float(CALIB_DC_X),
    "calib_dc_y_V": float(CALIB_DC_Y),
}
config["calibration"] = calib_results
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print(f"\n校准结果已保存到配置")

# %% Cell 6
# ============================================================
# Phase 3: 嵌套扫描 — 频率外循环 × 相位内循环
#   采集模式按 ACQUISITION_MODE:
#     "yfft"     — Demod 0 sample.y 时域数据
#     "demod_rxy" — Demod 3 X/Y/R/theta 多次采样
#     "both"     — 两者同时采集
#   逐频点保存，相位点内 try/finally 保护温度开关
# ============================================================

print("\n" + "=" * 60)
print("Phase 3: 频率 × 相位 嵌套扫描")
print("=" * 60)
print(f"采集模式: {ACQUISITION_MODE}")
print(f"固定 Z 射频场幅度: {Z_RF_AMPLITUDE} Vpp")
print(f"频率扫描: {freq_list[0]:.1f} ~ {freq_list[-1]:.1f} Hz, {len(freq_list)} 点")
if has_baseline:
    print(f"  包含 0 Hz 基线点")
print(f"相位扫描: {phase_list[0]:.1f} ~ {phase_list[-1]:.1f} deg, {len(phase_list)} 点")
print(f"总计: {len(freq_list) * len(phase_list)} 个测量点")

# 数据容器：兼容旧字段
r_matrix = np.full((len(freq_list), len(phase_list)), np.nan)
x_matrix = np.full((len(freq_list), len(phase_list)), np.nan)
y_matrix = np.full((len(freq_list), len(phase_list)), np.nan)
theta_matrix = np.full((len(freq_list), len(phase_list)), np.nan)

# 频点索引记录
freq_index_records = []

try:
    for fi, z_rf_freq in enumerate(tqdm(freq_list, desc="频率扫描")):
        is_baseline = (z_rf_freq == 0.0)
        record = {
            "idx": fi,
            "freq_Hz": float(z_rf_freq),
            "is_baseline": bool(is_baseline),
            "z_drive_amplitude_Vpp": Z_RF_AMPLITUDE,
            "acquisition_mode": ACQUISITION_MODE,
        }

        # ---- 1. 设置 Z 场驱动 ----
        if is_baseline:
            validate_safety_limit("Z_magnetic_field", 0.0)
            dg_sweep.setup_dc(0.0, channel=1)
            dg_sweep.set_burst_state(False, channel=1)
            dg_sweep.set_output(False, channel=1)
        else:
            dg_sweep.setup_sine(freq=z_rf_freq, amplitude=Z_RF_AMPLITUDE,
                                offset=0.0, phase=0.0, channel=1)
            dg_sweep.set_burst_phase(0.0, channel=1)
            dg_sweep.set_burst_state(True, channel=1)
            dg_sweep.set_burst_mode("INFinity", channel=1)
            dg_sweep.set_burst_trigger_source("EXTernal", channel=1)
            dg_sweep.set_output(True, channel=1)

        # ---- 2. 更新 Osc 1 和 Demod 3 跟随 Z 驱动频率 ----
        if ACQUISITION_MODE in ("demod_rxy", "both") and not is_baseline:
            osc1_cfg = OscillatorConfig(
                osc_index=DEMOD3_OSC_IDX, frequency=z_rf_freq,
            )
            demod.configure_oscillator(hfi, osc1_cfg)
            tc = max(DEMOD3_TC_MIN_s,
                     DEMOD3_TC_PERIOD_FRACTION / max(z_rf_freq, 1.0))
            actual_demod3_rate = min(DEMOD3_RATE, int(max(z_rf_freq, 1.0) * 50))
            demod3_cfg = DemodulatorConfig(
                demod_index=DEMOD3_IDX, enable=True,
                rate=max(100, actual_demod3_rate),
                input_channel=DEMOD3_ADC_SELECT,
                osc_select=DEMOD3_OSC_IDX, harmonic=1,
                time_constant=tc, order=DEMOD3_ORDER,
                phase=0.0,
            )
            demod.configure_demodulator(hfi, demod3_cfg)
            # 读回实际配置
            point_rate = hfi.get_double(f"{demod_r_actual_path}/rate")
            point_tc = hfi.get_double(f"{demod_r_actual_path}/timeconstant")
            point_adcselect = hfi.get_int(f"{demod_r_actual_path}/adcselect")
            record["demod_r_actual_tc_s"] = float(point_tc)
            record["demod_r_actual_rate_Sa_s"] = float(point_rate)
            record["demod_r_adcselect_actual"] = int(point_adcselect)

        # ---- 3. 等待频率切换稳定 ----
        time.sleep(FREQ_SETTLE_TIME)

        # ---- 4. 相位内循环 ----
        for pi, phase_deg in enumerate(phase_list):

            # ---- 4a. 关闭温控（消除磁场干扰），内层 try/finally ----
            dg_temp.set_output(False, channel=2)
            time.sleep(TEMP_SWITCH_OFF_LEAD)

            try:
                # 设置 Z 射频场 Burst 起始相位，并重新开关输出使下一次触发按新相位开始。
                if not is_baseline:
                    apply_z_rf_burst_phase(dg_sweep, phase_deg)
                    time.sleep(PHASE_SETTLE_TIME)

                # ---- 5. 按 ACQUISITION_MODE 采集 ----
                # 5a. yfft 模式：DAQ 采集 Demod 0 sample.y
                if ACQUISITION_MODE in ("yfft", "both"):
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
                        demod_idx=DEMOD0_IDX,
                        actual_rate=actual_rate_d0_meas,
                        timeout=POINT_DURATION_s + 5.0,
                    )
                    y_signal = next(
                        (r.values for r in daq_results if r.signal_name == "sample.y"),
                        None,
                    )
                    t_signal = daq_results[0].time if daq_results else np.array([])
                    if y_signal is not None and len(y_signal) > 0:
                        record["time_s"] = t_signal
                        record["y_V"] = y_signal
                        record["actual_rate_Sa_s"] = float(actual_rate_d0_meas)

                # 5b. demod_rxy 模式：多次读取 Demod 3 X/Y/R/theta
                if ACQUISITION_MODE in ("demod_rxy", "both") and not is_baseline:
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

                    # Vector mean
                    x_mean = float(np.mean(x_arr))
                    y_mean = float(np.mean(y_arr))
                    r_vec = float(math.hypot(x_mean, y_mean))
                    phase_vec_rad = float(math.atan2(y_mean, x_mean))
                    phase_vec_deg = float(math.degrees(phase_vec_rad))
                    # Scalar mean
                    r_scalar_mean = float(np.mean(r_arr))
                    r_scalar_std = float(np.std(r_arr))

                    r_matrix[fi, pi] = r_vec        # vector R → r_matrix
                    x_matrix[fi, pi] = x_mean
                    y_matrix[fi, pi] = y_mean
                    theta_matrix[fi, pi] = phase_vec_deg

                    record["demod_r_x_values_V"] = x_arr
                    record["demod_r_y_values_V"] = y_arr
                    record["demod_r_r_values_V"] = r_arr
                    record["demod_r_theta_values_rad"] = theta_arr
                    record["demod_r_theta_values_deg"] = np.degrees(theta_arr)
                    record["demod_r_x_mean_V"] = x_mean
                    record["demod_r_y_mean_V"] = y_mean
                    record["demod_r_r_vector_mean_V"] = r_vec
                    record["demod_r_r_mean_scalar_V"] = r_scalar_mean
                    record["demod_r_r_std_scalar_V"] = r_scalar_std
                    record["demod_r_phase_vector_rad"] = phase_vec_rad
                    record["demod_r_phase_vector_deg"] = phase_vec_deg

            finally:
                # 恢复温控（无论采集是否成功）
                dg_temp.set_output(True, channel=2)
                time.sleep(TEMP_SWITCH_ON_LAG)

        # ---- 6. 保存当前频点 ----
        fname = f"freq_{fi:04d}_{z_rf_freq:.3f}Hz"
        if is_baseline:
            fname = f"freq_{fi:04d}_0Hz_BASELINE"
        save_dict = {
            "freq_Hz": np.float64(z_rf_freq),
            "is_baseline": np.uint8(int(is_baseline)),
            "z_drive_amplitude_Vpp": np.float64(Z_RF_AMPLITUDE),
            "acquisition_mode": ACQUISITION_MODE,
        }
        if "y_V" in record:
            save_dict["time_s"] = record["time_s"]
            save_dict["y_V"] = record["y_V"]
            save_dict["actual_rate_Sa_s"] = np.float64(record["actual_rate_Sa_s"])
        if "demod_r_r_vector_mean_V" in record:
            save_dict["demod_r_x_values_V"] = record["demod_r_x_values_V"]
            save_dict["demod_r_y_values_V"] = record["demod_r_y_values_V"]
            save_dict["demod_r_r_values_V"] = record["demod_r_r_values_V"]
            save_dict["demod_r_theta_values_rad"] = record["demod_r_theta_values_rad"]
            save_dict["demod_r_theta_values_deg"] = record["demod_r_theta_values_deg"]
            save_dict["demod_r_x_mean_V"] = np.float64(record["demod_r_x_mean_V"])
            save_dict["demod_r_y_mean_V"] = np.float64(record["demod_r_y_mean_V"])
            save_dict["demod_r_r_vector_mean_V"] = np.float64(record["demod_r_r_vector_mean_V"])
            save_dict["demod_r_r_mean_scalar_V"] = np.float64(record["demod_r_r_mean_scalar_V"])
            save_dict["demod_r_r_std_scalar_V"] = np.float64(record["demod_r_r_std_scalar_V"])
            save_dict["demod_r_phase_vector_rad"] = np.float64(record["demod_r_phase_vector_rad"])
            save_dict["demod_r_phase_vector_deg"] = np.float64(record["demod_r_phase_vector_deg"])
            if "demod_r_actual_tc_s" in record:
                save_dict["demod_r_actual_tc_s"] = np.float64(record["demod_r_actual_tc_s"])
                save_dict["demod_r_actual_rate_Sa_s"] = np.float64(record["demod_r_actual_rate_Sa_s"])
                save_dict["demod_r_adcselect_actual"] = np.int32(record["demod_r_adcselect_actual"])
        fpath = raw_dir / f"{fname}.npz"
        np.savez(fpath, **save_dict)
        freq_index_records.append({
            "idx": fi, "freq_Hz": float(z_rf_freq),
            "is_baseline": bool(is_baseline),
            "file": fpath.name,
        })

        # ---- 进度打印 ----
        if (fi + 1) % 10 == 0 or fi == 0 or fi == len(freq_list) - 1:
            extra = ""
            if ACQUISITION_MODE in ("demod_rxy", "both") and not is_baseline:
                r_vec_val = r_matrix[fi, :]
                if not np.all(np.isnan(r_vec_val)):
                    extra = (f", R_vec={np.nanmean(r_vec_val):.4e} V, "
                             f"φ={np.nanmean(theta_matrix[fi, :]):.1f}°")
            y_n = len(record.get("y_V", []))
            extra += f", y_n={y_n}"
            tag = " (BASELINE)" if is_baseline else ""
            print(f"  [{fi+1}/{len(freq_list)}] f={z_rf_freq:.2f} Hz{tag}{extra}")

    # ---- 7. 保存 frequency_index ----
    np.savez(raw_dir / "frequency_index.npz",
             **{"records": np.array(freq_index_records, dtype=object)})
    print(f"\n频点索引已保存: {raw_dir / 'frequency_index.npz'}")

    # JSON 格式便于人工阅读
    with open(raw_dir / "frequency_index.json", "w", encoding="utf-8") as f:
        json.dump(freq_index_records, f, indent=2, ensure_ascii=False)
    print(f"频点索引 (JSON): {raw_dir / 'frequency_index.json'}")

    # ---- 8. 保存最终兼容数据（保留旧字段，追加新字段）----
    freq_resp_data = {
        "freq_Hz": freq_list,
        "phase_deg": phase_list,
        "r_matrix_V": r_matrix,
        "x_matrix_V": x_matrix,
        "y_matrix_V": y_matrix,
        "theta_matrix_deg": theta_matrix,
    }
    np.savez(raw_dir / "freq_phase_scan.npz", **freq_resp_data)
    print(f"数据已保存: {raw_dir / 'freq_phase_scan.npz'}")
    print(f"  频率: {len(freq_list)} 点, 相位: {len(phase_list)} 点")
    print(f"  R 矩阵形状: {r_matrix.shape}")
    print(f"  R 范围: [{np.nanmin(r_matrix):.4e}, {np.nanmax(r_matrix):.4e}] V")

    # ---- 9. 更新配置 ----
    config["freq_sweep"]["actual_freq_list_Hz"] = freq_list.tolist()
    config["phase_sweep"]["actual_phase_list_deg"] = phase_list.tolist()
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print("\n✅ 频率 × 相位 嵌套扫描完成")

finally:
    # ========== 全局安全恢复 ==========
    print("\n--- 全局安全恢复 ---")

    # 1) 恢复温度开关 ON
    try:
        dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
        dg_temp.set_output(True, channel=2)
        print("  温度开关: 已恢复 ON")
    except Exception as e:
        print(f"  温度开关恢复失败: {e}")

    # 2) dg_sweep: burst OFF → DC 0V → output OFF
    for ch in [1, 2]:
        try:
            dg_sweep.set_burst_state(False, channel=ch)
            dg_sweep.setup_dc(0.0, channel=ch)
            dg_sweep.set_output(False, channel=ch)
        except Exception as e:
            print(f"  dg_sweep CH{ch} 关闭失败: {e}")
    print("  Z 射频场: 已关闭 (DC 0V, burst OFF, output OFF)")

    # 3) dg_am: burst OFF → DC 0V → output OFF
    for ch in [1, 2]:
        try:
            dg_am.set_burst_state(False, channel=ch)
            dg_am.setup_dc(0.0, channel=ch)
            dg_am.set_output(False, channel=ch)
        except Exception as e:
            print(f"  dg_am CH{ch} 关闭失败: {e}")
    print("  dg_am: 已关闭 (DC 0V, burst OFF, output OFF)")

    # 4) dg_comp: 关闭 AM 调制 + output OFF
    for ch in [1, 2]:
        try:
            dg_comp.set_mod_state(False, channel=ch)
            dg_comp.set_output(False, channel=ch)
        except Exception as e:
            print(f"  dg_comp CH{ch} 关闭失败: {e}")
    print("  dg_comp: 已关闭")

# %% Cell 7
# ========== 安全关闭 ==========
print("\n" + "=" * 60)
print("安全关闭 - 逐一断开设备")
print("=" * 60)

# 先关闭所有信号输出
for name in ["dg_am", "dg_sweep", "dg_comp"]:
    try:
        dev = devices.get(name)
        if dev is None:
            continue
        for ch in [1, 2]:
            try:
                if hasattr(dev, "set_burst_state"):
                    dev.set_burst_state(False, channel=ch)
                if hasattr(dev, "setup_dc"):
                    dev.setup_dc(0.0, channel=ch)
                dev.set_output(False, channel=ch)
            except Exception:
                pass
    except Exception as e:
        print(f"  {name} 关闭失败: {e}")

# 恢复温度开关
try:
    dg_temp = devices.get("dg_temp")
    if dg_temp and hasattr(dg_temp, "setup_dc"):
        dg_temp.setup_dc(5.0, channel=2)
        dg_temp.set_output(True, channel=2)
        print("  温度开关: ON (5V)")
except Exception as e:
    print(f"  温度开关恢复失败: {e}")

# 断开 TEC
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")

print("其他设备保持连接（供电不中断）")
