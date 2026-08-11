# %% [markdown] Cell 0
# # X/Y 噪声谱控制实验 v2 采集脚本
#
# 从 Noise_Spectrum_XY_Ctrl_v2.ipynb 拆分出来的采集部分。
# PSD 计算、拟合和绘图请运行 Noise_Spectrum_XY_Ctrl_v2_plot.py。

# %% Cell 3
# ========== 交互式绘图模式 ==========
# 启用后图表支持鼠标拖拽平移、滚轮缩放、框选放大
# 如遇到显示问题，可尝试 %matplotlib inline 切换回静态模式

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
from sds_acquisition import SDSInstrument, SDSAcquisition, AcquisitionConfig, ChannelConfig
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

# ---- 扫描参数（AM DC 电压扫描）----
# [经验] AM 外部调制线性响应: Out = Carrier × (V_DC / 1.3V)
#       扫描 dg_sweep CH2 DC 电压 0→1.3V 即扫描有效控制场强度
AM_DC_START = 0.0          # AM DC 控制电压扫描起始 (V)
AM_DC_STOP = 2.5         # AM DC 控制电压扫描终止 (V)，MOD Input 满量程
AM_DC_POINTS = 251         # 扫描点数
AM_DC_SETTLE_TIME = 0.5    # 每点等待稳定时间 (s)

# ---- Pump 调制参数 ----
PUMP_MOD_FREQ = 10e3       # Pump 调制频率 (Hz)
PUMP_MOD_AMPLITUDE = 0.18  # 100MHz 正弦波幅度 (V)
PUMP_MOD_DUTY = 5          # 脉冲占空比 (%)
RF_GATE_AMPLITUDE = 5.0    # 脉冲门控幅度 (Vpp)
RF_GATE_OFFSET = 2.5       # 脉冲门控偏置 (V)

# ---- X/Y 载波参数（AM 外部调制方案，可分别设置）----
# 载波幅度可分别设置，有效 XY 场强由 dg_sweep CH2 DC 电压通过 AM 调制控制
XY_CARRIER_FREQ = PUMP_MOD_FREQ        # X/Y 载波频率 (Hz)，等于 Larmor 频率
X_CARRIER_AMPLITUDE = 6    # X 载波幅值 (Vpp)
Y_CARRIER_AMPLITUDE = 6    # Y 载波幅值 (Vpp)
X_CARRIER_PHASE = 0           # CH1 载波初始相位 (deg)，校相后确定
Y_CARRIER_PHASE = 90          # CH2 载波初始相位 (deg)，校相后确定
XY_AM_DEPTH_X = 100           # X 通道 AM 调制深度 (%)
XY_AM_DEPTH_Y = 100           # Y 通道 AM 调制深度 (%)

# ★ MOD 偏置补偿: X/Y 零输出点不同, CH2 加偏置弥合差距
MOD_OFFSET_X = -0.15          # X 通道 MOD DC 偏置 (V)
MOD_OFFSET_Y = -0.15        # Y 通道 MOD DC 偏置 (V), +0.015V 补偿 -0.015V 差异

# ---- HF2 解调配置（相位校准用） ----
HF2_DEMOD_IDX = 0          # 解调器索引
HF2_OSC_FREQ = PUMP_MOD_FREQ    # 振荡器频率 (Hz)，与 Pump 调制频率一致
HF2_SIGNAL_RANGE = 2.0     # 信号输入量程 (V)
HF2_DEMOD_ORDER = 8        # 解调滤波器阶数
HF2_DEMOD_TC = 0.000692    # 解调时间常数 (s) —— 相位校准时使用
HF2_DEMOD_RATE = 100000    # 解调输出数据速率 (Sa/s)

# ---- HF2 DAQ 采集配置（噪声采集用） ----
HF2_DAQ_DURATION = 1.0     # DAQ 采集时长 (s)
HF2_DAQ_TC = 7.85e-07      # 解调时间常数 (s) —— 噪声采集时使用
HF2_DAQ_RATE = 100000      # 解调输出数据速率 (Sa/s)，与 DEMOD_RATE 一致
HF2_NPERSEG = 10000        # Welch PSD 每段点数

# ---- 固定参数 ----
FIXED_PARAMS = {
    "Pump_laser_power": 0.1,         "Probe_laser_power": 0.1,
    "temperature": 100,              "Temp_Switch": 5.0,
    "main_magnetic_field": 1.03,
}

# ---- 运行目录命名 ----
RUN_TAG = "noise_v2"

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

print("安全校验函数已定义")

# %% Cell 6
devices = {}

try:
    # ---- GS200: 主磁场 ----
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = GS200Instrument(gs_cfg["resource"])
    gs.connect()
    print(f"GS200 已连接: {gs.idn()}")
    gs.set_source_function(gs_cfg["source_function"])
    # [经验] GS200 需硬件级电流保护
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    # ---- DG4000: X/Y 补偿磁场（AM 载波输出）----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = create_signal_generator(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    # Y 通道: 通过同一设备 CH2 控制
    devices["dg_comp"] = dg_comp

    # ---- DG4000: X/Y AM DC 独立控制 (dg_am) ----
    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = create_signal_generator(dg_am_cfg["resource"], channel=1)
    dg_am.connect()
    print(f"dg_am 已连接: {dg_am.idn()}")
    dg_am.set_ref_clock_source("EXTernal")
    # CH1 → X MOD, CH2 → Y MOD (独立!)
    dg_am.setup_dc(0.0, channel=1)
    dg_am.setup_dc(0.0, channel=2)
    dg_am.set_output(False, channel=1)
    dg_am.set_output(False, channel=2)
    devices["dg_am"] = dg_am

    # ---- DG4000: Z 磁场 + AM DC 控制（dg_sweep）----
    # v2 方案: CH1=Z 场(本实验关闭), CH2=AM DC 电压(控制 XY 场强)
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = create_signal_generator(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"dg_sweep DG4000 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    # CH1: Z 磁场关闭; CH2: 初始 DC 0V（后续在校相和扫描中设置）
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    print(f"  -> CH1 (Z 场): DC 0V, 输出 OFF")
    print(f"  -> CH2 (AM DC): 0V, 输出 OFF (将在 XY 配置中开启)")
    devices["dg_sweep"] = dg_sweep

    # ---- DG4000: Pump 调制 ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = create_signal_generator(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    print(f"调制 DG4000 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # ---- DG4000: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    print(f"温控 DG4000 已连接: {dg_temp.idn()}")
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

    # ---- DG4000: Pump/Probe 光功率 ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = create_signal_generator(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    print(f"光功率 DG4000 已连接: {dg_laser.idn()}")
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

    # ---- SDS 示波器（监测 X/Y AM 调制输出）----
    scope_cfg = MAPPING.get("scope_waveform", {})
    scope_resource = scope_cfg.get("resource", "")
    scope = SDSInstrument(scope_resource)
    scope.connect()
    print(f"示波器已连接: {scope.idn()}")
    devices["scope"] = scope

except Exception as e:
    print(f"设备连接失败: {e}")
    raise

print(f"\n所有设备连接完成，共 {len(devices)} 个设备")

# %% Cell 7
hfi = devices["hf2"]
tec = devices["tec"]
gs = devices["gs200"]
dg_laser = devices["dg_laser"]
dg_comp = devices["dg_comp"]
dg_sweep = devices["dg_sweep"]
dg_am = devices["dg_am"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]
scope = devices["scope"]

# ---- 1. Pump 光功率 ----
validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
print(f"Pump 光功率: {FIXED_PARAMS['Pump_laser_power']} V DC")

# ---- 2. Probe 光功率 ----
validate_safety_limit("Probe_laser_power", FIXED_PARAMS["Probe_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Probe_laser_power"], channel=2)
print(f"Probe 光功率: {FIXED_PARAMS['Probe_laser_power']} V DC")

# ---- 3. 主磁场 ----
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# ---- 4. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 5. 温度控制 ----
validate_safety_limit("temperature", FIXED_PARAMS["temperature"])
configure_temperature_control(
    tec, FIXED_PARAMS["temperature"], tolerance_c=1.0, stable_reads=1
)

# ---- 6. Z 磁场关闭 ----
validate_safety_limit("Z_magnetic_field", 0.0)
dg_sweep.setup_dc(0.0, channel=1)
dg_sweep.set_output(False, channel=1)
dg_sweep.set_output(False, channel=2)
print("Z 磁场: DC 0V, 输出 OFF (本实验不使用)")
print("dg_sweep: CH1/CH2 输出 OFF")

# ---- 7. AM DC 独立控制 (dg_am CH1→X MOD, CH2→Y MOD) ----
# CH1 → dg_comp CH1 MOD Input (X), 含偏置补偿
# CH2 → dg_comp CH2 MOD Input (Y), 含偏置补偿
dg_am.setup_dc(MOD_OFFSET_X, channel=1)
dg_am.setup_dc(MOD_OFFSET_Y, channel=2)
dg_am.set_output(False, channel=1)
dg_am.set_output(False, channel=2)
print(f"dg_am CH1(X MOD): DC={MOD_OFFSET_X:.3f}V (偏置补偿), 输出 OFF (将在 XY 配置中开启)")
print(f"dg_am CH2(Y MOD): DC={MOD_OFFSET_Y:.3f}V (偏置补偿), 输出 OFF (将在 XY 配置中开启)")

# ---- 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# ---- 保存实验配置到运行目录 ----
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": PURPOSE,
    "timestamp": timestamp,
    "scan_params": {
        "AM_DC_START_V": AM_DC_START,
        "AM_DC_STOP_V": AM_DC_STOP,
        "AM_DC_POINTS": AM_DC_POINTS,
        "AM_DC_SETTLE_TIME_s": AM_DC_SETTLE_TIME,
    },
    "xy_carrier_am": {
        "XY_CARRIER_FREQ_Hz": XY_CARRIER_FREQ,
        "X_CARRIER_AMPLITUDE_Vpp": X_CARRIER_AMPLITUDE,
        "Y_CARRIER_AMPLITUDE_Vpp": Y_CARRIER_AMPLITUDE,
        "X_CARRIER_PHASE_deg": X_CARRIER_PHASE,
        "Y_CARRIER_PHASE_deg": Y_CARRIER_PHASE,
        "XY_AM_DEPTH_X_pct": XY_AM_DEPTH_X,
        "XY_AM_DEPTH_Y_pct": XY_AM_DEPTH_Y,
        "MOD_OFFSET_X_V": MOD_OFFSET_X,
        "MOD_OFFSET_Y_V": MOD_OFFSET_Y,
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
        "nperseg": HF2_NPERSEG,
    },
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
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

# 启用 Pump 调制输出及 SYNC 信号
# dg_mod CH2 SYNC → dg_comp Ext Trig（用作 XY Burst 外部触发同步）
dg_mod.set_output(True, channel=1)
dg_mod.set_output(True, channel=2)
dg_mod.set_sync_state(True, channel=2)
print("  dg_mod 输出: CH1(100MHz) ON, CH2(脉冲) ON")
print("  dg_mod CH2 SYNC → dg_comp Ext Trig")

print("\n✅ RF 开关方案配置完成")

# ---- HF2 解调器相位校准（设置 XY 磁场前执行） ----
# 参考: Static_Magnetic_Field_Sensitivity.ipynb 相位校准流程
# 校准时 XYZ 磁场均处于关闭状态，仅主磁场 B0 保持稳定
print("\n关闭温度开关（相位校准前，消除温控磁场干扰）...")
dg_temp.set_output(False, channel=2)

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
calibrated_phase = demod.auto_calibrate_phase(
    hfi, demod_idx=0, tolerance_deg=1.0, max_attempts=5, settle_time=0.2
)
print(f"相位校准完成: {calibrated_phase:.2f}°")

# 读一组样本验证
sample = demod.read_demod_sample(hfi, demod_idx=0)
print(f"校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

time.sleep(2)

# 恢复温控
print("恢复温度开关...")
dg_temp.set_output(True, channel=2)
time.sleep(0.5)

# %% Cell 11
# ---- X/Y 控制信号配置（AM 外部调制 — dg_am 两路独立 DC 控制）----
# [经验] dg_am CH1 → X MOD (含 MOD_OFFSET_X), CH2 → Y MOD (含 MOD_OFFSET_Y)
#        AM 调制线性响应: Out = Carrier × (V_DC / 1.3V)
#        V_DC=0V → 载波抑制（XY 场 OFF）
#        V_DC=1.3V → 载波满幅度（XY 场 ON）
#        扫描 DC 电压即扫描有效控制场强度 Ω_Ctrl
# [经验] CH1 和 CH2 载波幅度/深度可分别设置，相位差 90° 通过 set_phase_adjust() 独立设置

print("配置 X/Y 交流控制信号（AM 外部调制 — dg_am 独立 DC 控制）...")

# Step A: 设置 dg_am CH1/CH2 DC 电压（初始偏置值，扫描循环中逐点改变）
print("\n设置 dg_am CH1/CH2 DC 控制电压...")
validate_safety_limit("Z_magnetic_field", 0.0)  # AM DC 电压经 MOD Input 控制
dg_am.setup_dc(MOD_OFFSET_X, channel=1)
dg_am.setup_dc(MOD_OFFSET_Y, channel=2)
dg_am.set_output(True, channel=1)
dg_am.set_output(True, channel=2)
print(f"dg_am CH1(X MOD): DC={MOD_OFFSET_X:.3f}V (偏置补偿, 扫描循环中逐点改变)")
print(f"dg_am CH2(Y MOD): DC={MOD_OFFSET_Y:.3f}V (偏置补偿, 扫描循环中逐点改变)")
# Step B: 配置 dg_comp CH1/CH2 AM 外部调制（载波幅度可分别设置）
print("\n配置 X/Y AM 调制载波...")

# CH1: X 载波
dg_comp.setup_sine(freq=XY_CARRIER_FREQ, amplitude=X_CARRIER_AMPLITUDE,
                   phase=0.0, channel=1)
dg_comp.set_mod_type("AM", channel=1)
dg_comp.set_mod_am_source("EXT", channel=1)
dg_comp.set_mod_am_depth(XY_AM_DEPTH_X, channel=1)
dg_comp.set_mod_state(True, channel=1)
print(f"CH1: X 载波 {XY_CARRIER_FREQ/1000:.0f} kHz, {X_CARRIER_AMPLITUDE} Vpp, "
      f"AM EXT, 深度 {XY_AM_DEPTH_X}%")

# CH2: Y 载波（可独立设置幅度和深度）
dg_comp.setup_sine(freq=XY_CARRIER_FREQ, amplitude=Y_CARRIER_AMPLITUDE,
                   phase=0.0, channel=2)
dg_comp.set_mod_type("AM", channel=2)
dg_comp.set_mod_am_source("EXT", channel=2)
dg_comp.set_mod_am_depth(XY_AM_DEPTH_Y, channel=2)
dg_comp.set_mod_state(True, channel=2)
print(f"CH2: Y 载波 {XY_CARRIER_FREQ/1000:.0f} kHz, {Y_CARRIER_AMPLITUDE} Vpp, "
      f"AM EXT, 深度 {XY_AM_DEPTH_Y}%")

print(f"\n  DC 电压控制效果:")
print(f"    X: V_DC = 偏置+0V → X 场 OFF | 偏置+1.3V → X 场 ON")
print(f"    Y: V_DC = 偏置+0V → Y 场 OFF | 偏置+1.3V → Y 场 ON")
print()
print("硬件连接要求: dg_am CH1 → dg_comp CH1 MOD Input | dg_am CH2 → dg_comp CH2 MOD Input")
print(f"相位关系: X={X_CARRIER_PHASE}°, Y=X+{(Y_CARRIER_PHASE - X_CARRIER_PHASE) % 360}°={Y_CARRIER_PHASE}°")
print(f"载波幅度: X={X_CARRIER_AMPLITUDE} Vpp, Y={Y_CARRIER_AMPLITUDE} Vpp")
print(f"AM 深度: X={XY_AM_DEPTH_X}%, Y={XY_AM_DEPTH_Y}%")
print(f"MOD 偏置: X={MOD_OFFSET_X:.3f}V, Y={MOD_OFFSET_Y:.3f}V")
print(f"DC 电压扫描范围: {AM_DC_START} ~ {AM_DC_STOP} V")

# %% Cell 12
# ============================================================
# 分通道校相：X/Y 载波相位校准
# [经验] 校相时设置 DC 电压为中间值（0.65V），使 XY 场处于半强度
# [经验] 逐通道独立校相，关闭温控以消除磁场干扰
# [经验] dg_am CH1→X MOD, CH2→Y MOD，校相时分别控制
# ============================================================
print("=" * 60)
print("分通道校相：X/Y 载波相位校准")
print("=" * 60)

import math  # noqa: F811

# 设置 DC 电压至中间值（0.65V + 偏置补偿 = 50% 载波幅度），用于校相
CALIB_DC_VOLTAGE = 0.5
dg_am.setup_dc(CALIB_DC_VOLTAGE + MOD_OFFSET_X, channel=1)
dg_am.setup_dc(CALIB_DC_VOLTAGE + MOD_OFFSET_Y, channel=2)
time.sleep(0.3)
print(f"AM DC 电压: {CALIB_DC_VOLTAGE}V + 偏置(X={MOD_OFFSET_X:.3f}V, Y={MOD_OFFSET_Y:.3f}V) (50% 载波幅度，用于校相)")

# 关闭温控（消除温控线圈磁场对校相的干扰）
dg_temp.set_output(False, channel=2)
print("温度开关: OFF")

# ---- 校准 CH1 相位 ----
# 暂时关闭 CH2（dg_comp CH2 输出 + dg_am CH2），只开 CH1 读 HF2 相位
print("\n校准 CH1 相位...")
dg_comp.set_output(False, channel=2)
dg_am.set_output(False, channel=2)
dg_comp.set_output(True, channel=1)
time.sleep(0.5)

for iteration in range(5):
    sample = demod.read_demod_sample(hfi, demod_idx=0)
    phase_offset = math.degrees(sample["theta"])  # atan2(y,x)，解调矢量角度
    print(f"  CH1 迭代 {iteration+1}: theta = {phase_offset:.2f}°")
    if abs(phase_offset) < 0.1:
        print(f"  CH1 相位已收敛 (<0.1°)")
        break
    X_CARRIER_PHASE = (X_CARRIER_PHASE - phase_offset) % 360
    dg_comp.set_phase_adjust(X_CARRIER_PHASE, channel=1)
    time.sleep(0.5)

print(f"\n  CH1 最终载波相位 = {X_CARRIER_PHASE:.2f}°")

# ---- 在 CH1 相位已校准的基础上，开启 CH2 并修正其单独相位 ----
# CH2 独立校相，使用参数 Y_CARRIER_PHASE 作为起始值
print("\n校准 CH2 相位...")
dg_comp.set_output(True, channel=2)
dg_am.set_output(True, channel=2)
dg_comp.set_phase_adjust(Y_CARRIER_PHASE, channel=2)
time.sleep(0.3)

for iteration in range(15):
    sample = demod.read_demod_sample(hfi, demod_idx=0)
    phase_offset = math.degrees(sample["theta"])
    print(f"  CH2 迭代 {iteration+1}: theta = {phase_offset:.2f}°")
    if abs(phase_offset) < 0.1:
        print(f"  CH2 相位已收敛 (<0.1°)")
        break
    Y_CARRIER_PHASE = (Y_CARRIER_PHASE - phase_offset) % 360
    dg_comp.set_phase_adjust(Y_CARRIER_PHASE, channel=2)
    time.sleep(0.5)

print(f"\n  CH2 最终载波相位 = {Y_CARRIER_PHASE:.2f}°")
print(f"  实际相位差 = {(Y_CARRIER_PHASE - X_CARRIER_PHASE) % 360:.2f}°")

# 恢复温控
dg_temp.set_output(True, channel=2)
print("\n温度开关: ON (已恢复)")

print(f"\n✅ 分通道校相完成")
print(f"  X_CARRIER_PHASE = {X_CARRIER_PHASE:.2f}°")
print(f"  Y_CARRIER_PHASE = {Y_CARRIER_PHASE:.2f}°")

# %% Cell 16
# ===== 逐点扫描 AM DC 电压 + HF2 DAQ 采集 =====
# [经验] AM 外部调制模式下，dg_am CH1/CH2 DC 电压独立控制 X/Y 有效场强度。
#       扫描 DC 电压 0→1.3V 即扫描有效控制场强度 Ω_Ctrl。
#       载波幅度可分别设置，CH1/CH2 分别加 MOD_OFFSET_X / MOD_OFFSET_Y 偏置补偿。

amplitudes = np.linspace(AM_DC_START, AM_DC_STOP, AM_DC_POINTS)
print("=" * 60)
print(f"AM DC 电压扫描噪声谱测量")
print("=" * 60)
print(f"载波频率: {XY_CARRIER_FREQ/1000:.0f} kHz (Larmor, X={X_CARRIER_AMPLITUDE}Vpp, Y={Y_CARRIER_AMPLITUDE}Vpp)")
print(f"DC 电压扫描: {AM_DC_START} V → {AM_DC_STOP} V, {AM_DC_POINTS} 点")
print(f"  → Out = Carrier × (V_DC / 1.3V), Ω_Ctrl ∝ V_DC")
print(f"MOD 偏置补偿: X={MOD_OFFSET_X:.3f}V, Y={MOD_OFFSET_Y:.3f}V (独立!)")
print(f"DAQ 采集: 时长 {HF2_DAQ_DURATION}s, TC={HF2_DAQ_TC*1e6:.2f}μs, rate={HF2_DAQ_RATE:.0f} Sa/s")
total_est = AM_DC_POINTS * (AM_DC_SETTLE_TIME + HF2_DAQ_DURATION + 2.0)
print(f"预计耗时: {total_est:.0f}s ≈ {total_est/3600:.1f}h")
print()

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
pbar = tqdm(total=AM_DC_POINTS, desc="Scanning", unit="pt")
try:
    for i, dc_v in enumerate(amplitudes):
        # [经验] AM 模式下修改 dg_am CH1/CH2 DC 电压（含独立偏置补偿）
        #       DC 电压范围 0~1.3V，对应 AM 输出 0~100% 载波幅度
        actual_dc_x = dc_v + MOD_OFFSET_X
        actual_dc_y = dc_v + MOD_OFFSET_Y
        pbar.set_postfix_str(f"V_DC={dc_v:.4f}V (X={actual_dc_x:.4f}, Y={actual_dc_y:.4f})")

        validate_safety_limit("X_magnetic_field_AM", float(actual_dc_x))
        validate_safety_limit("Y_magnetic_field_AM", float(actual_dc_y))
        dg_am.setup_dc(actual_dc_x, channel=1)
        dg_am.setup_dc(actual_dc_y, channel=2)
        # 关闭温度开关（消除温控磁场干扰）
        dg_temp.set_output(False, channel=2)

        # 读取当前温度
        try:
            temp_now = tec.get_temperature(channel=1)
            pbar.set_postfix_str(f"V_DC={dc_v:.4f}V, T={temp_now:.1f}°C")
        except:
            pbar.set_postfix_str(f"V_DC={dc_v:.4f}V")

        # 配置 HF2 DAQ 采集（每点独立配置）
        daq_cfg = DAQConfig(
            device=MAPPING["lockin_r"]["device_id"],
            trigger_type=0,                  # 连续模式
            duration=HF2_DAQ_DURATION,
            grid_cols=grid_cols,
            grid_rows=1,
            grid_mode=2,
            signal_paths=["sample.y"],       # 采集 Y 信号
        )
        # 等待系统稳定
        time.sleep(0.1)
        # 采集解调时域信号
        try:
            results = daq.acquire_data(
                hfi, daq_cfg, demod_idx=0,
                actual_rate=actual_rate_daq, timeout=HF2_DAQ_DURATION + 10.0,
            )
            waveform = results[0].values
        except Exception as e:
            tqdm.write(f"  [{i:4d}/{AM_DC_POINTS}] V_DC={dc_v:.4f}V: DAQ 采集失败: {e}")
            waveform = np.array([np.nan])

        # 保存原始波形（.npy 二进制格式）
        np.save(raw_dir / f"waveform_C{i:04d}.npy", waveform)

        # 恢复温度开关
        dg_temp.set_output(True, channel=2)
        time.sleep(3)

        pbar.update(1)

except Exception as e:
    print(f"\n❌ 扫描出错: {e}")
    # 紧急恢复温度开关
    print("紧急恢复温度开关...")
    dg_temp.set_output(True, channel=2)
    raise

finally:
    pbar.close()
    # [经验] 确保异常退出时温控恢复
    dg_temp.set_output(True, channel=2)
    print("温度开关已恢复 ON")

elapsed_total = time.time() - t_start
print(f"\n✅ 扫描完成！共 {AM_DC_POINTS} 点, 用时 {elapsed_total:.0f}s")
print(f"原始波形保存在: {raw_dir}")

# 保存实际采样率到 experiment_config.yaml（硬件实际值可能不同于配置值）
with open(config_path, encoding="utf-8") as f:
    config_saved = yaml.safe_load(f)
config_saved["hf2_daq"]["actual_rate_Sa_s"] = float(actual_rate_daq)
config_saved['data_files'] = [
    f'raw/waveform_C{i:04d}.npy' for i in range(AM_DC_POINTS)
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

print(f"解调器已恢复: TC={HF2_DEMOD_TC*1e6:.0f} μs, Rate={HF2_DEMOD_RATE} Sa/s")
time.sleep(0.2)
demod.configure_demodulator(hfi, restore_demod_cfg)

# %% Cell 17
# ========== 安全断开（仅断开 TEC，其余设备保持连接） ==========
print('正在断开 TEC...')
tec = devices.get('tec')
if tec and hasattr(tec, 'disconnect'):
    try:
        tec.disconnect()
        print('  TEC 已断开')
    except Exception as e:
        print(f'  TEC 断开失败: {e}')
print('其他设备保持连接')
