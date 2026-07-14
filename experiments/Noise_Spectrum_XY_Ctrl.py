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
from datetime import datetime
import matplotlib.pyplot as plt
from tqdm import tqdm

# 设备库
from gs200 import GS200Instrument
from signal_generator import DG4000Instrument
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
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
PURPOSE = "noise_spectroscopy"

# ---- 扫描参数 ----
XY_AMP_START = 0.01        # X/Y 幅度扫描起始 (V)
XY_AMP_STOP = 7.0          # X/Y 幅度扫描终止 (V)
XY_AMP_POINTS = 700        # 扫描点数
XY_SETTLE_TIME = 0.5      # 每点等待稳定时间 (s)

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
#   XY_CALIB_AMP:   正交校准时的 XY 幅值 (V)，使用较大幅值可获得更好的信噪比
XY_CTRL_FREQ = PUMP_MOD_FREQ    # X/Y 交流控制频率 (Hz)，等于 Larmor 频率
XY_CTRL_PHASE = 90         # XY 控制场相对 Pump 调制信号的相位延迟 (deg)
XY_CTRL_QUAD = 90          # X 与 Y 之间的正交相位差 (deg)
XY_CALIB_AMP = 0.5         # XY_CTRL_QUAD 校准时的 XY 幅值 (V)
XY_TRIGGER_FREQ = 100.0    # dg_am 固定基准方波频率 (Hz)
XY_TRIGGER_AMPLITUDE = 5.0 # dg_am 方波触发幅度 (Vpp)
XY_TRIGGER_OFFSET = 2.5    # dg_am 方波触发偏置 (V)
XY_TRIGGER_DUTY = 50.0     # dg_am 方波触发占空比 (%)
XY_TRIGGER_PHASE = 0.0     # dg_am 方波基准相位 (deg)，校准过程中保持不变

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
HF2_NPERSEG = 10000        # Welch PSD 每段点数

# ---- 固定参数 ----
FIXED_PARAMS = {
    "Pump_laser_power": 0.1,         "Probe_laser_power": 0.1,
    "temperature": 100,              "Temp_Switch": 5.0,
    "main_magnetic_field": 1.03,
}

# ---- 运行目录命名 ----
RUN_TAG = "noise"

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

    # ---- DG4000: X/Y 补偿磁场（Burst 载波输出） ----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = DG4000Instrument(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    print(f"补偿场 DG4000 已连接: {dg_comp.idn()}")
    dg_comp.set_ref_clock_source("EXTernal")
    # Y 通道: 通过同一设备 CH2 控制
    devices["dg_comp"] = dg_comp

    # ---- DG4000: dg_comp 外触发方波基准 (dg_am CH1/CH2 同相) ----
    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = DG4000Instrument(dg_am_cfg["resource"], channel=1)
    dg_am.connect()
    print(f"dg_am 已连接: {dg_am.idn()}")
    dg_am.set_ref_clock_source("EXTernal")
    for ch in (1, 2):
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
        dg_am.setup_dc(0.0, channel=ch)
        dg_am.set_output(False, channel=ch)
    devices["dg_am"] = dg_am

    # ---- DG4000: Z 磁场扫描（本实验用不到，仅连接并关闭输出）----
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = DG4000Instrument(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    print(f"Z 场 DG4000 已连接: {dg_sweep.idn()}")
    dg_sweep.set_ref_clock_source("EXTernal")
    dg_sweep.setup_dc(0.0, channel=1)
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_output(False, channel=2)
    print(f"  -> Z 场输出: OFF (本实验不使用)")
    devices["dg_sweep"] = dg_sweep

    # ---- DG4000: Pump 调制 ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = DG4000Instrument(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    print(f"调制 DG4000 已连接: {dg_mod.idn()}")
    devices["dg_mod"] = dg_mod

    # ---- DG4000: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = DG4000Instrument(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    print(f"温控 DG4000 已连接: {dg_temp.idn()}")
    devices["dg_temp"] = dg_temp

    # ---- TEC103: 温度控制器 ----
    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
    print(f"TEC103 已连接")
    devices["tec"] = tec

    # ---- DG4000: Pump/Probe 光功率 ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = DG4000Instrument(dg_laser_cfg["resource"], channel=1)
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
dg_am = devices["dg_am"]
dg_sweep = devices["dg_sweep"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]

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
tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
tec.set_enable(True, channel=1)
temp_now = tec.get_temperature(channel=1)
print(f"温度设定: {FIXED_PARAMS['temperature']} °C, 当前: {temp_now:.1f} °C")
print("等待温度稳定...")
while True:
    time.sleep(5)
    t = tec.get_temperature(channel=1)
    print(f"  当前温度: {t:.2f} °C")
    try:
        t2 = tec.get_temperature(channel=1)
        if abs(t2 - FIXED_PARAMS['temperature']) < 1:
            print(f"温度已稳定: {t:.2f} °C")
            break
    except:
        pass

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
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": PURPOSE,
    "timestamp": timestamp,
    "scan_params": {
        "XY_AMP_START_V": XY_AMP_START,
        "XY_AMP_STOP_V": XY_AMP_STOP,
        "XY_AMP_POINTS": XY_AMP_POINTS,
        "XY_SETTLE_TIME_s": XY_SETTLE_TIME,
    },
    "xy_ctrl": {
        "XY_CTRL_FREQ_Hz": XY_CTRL_FREQ,
        "XY_CTRL_PHASE_deg": XY_CTRL_PHASE,
        "XY_CTRL_QUAD_deg": XY_CTRL_QUAD,
        "XY_CALIB_AMP_V": XY_CALIB_AMP,
        "XY_TRIGGER_FREQ_Hz": XY_TRIGGER_FREQ,
        "XY_TRIGGER_AMPLITUDE_Vpp": XY_TRIGGER_AMPLITUDE,
        "XY_TRIGGER_OFFSET_V": XY_TRIGGER_OFFSET,
        "XY_TRIGGER_DUTY_pct": XY_TRIGGER_DUTY,
        "XY_TRIGGER_PHASE_deg": XY_TRIGGER_PHASE,
        "trigger_source": "dg_am CH1/CH2 fixed-phase 100 Hz square -> dg_comp Ext Trig",
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

# 启用 Pump 调制输出；dg_comp 外触发改由 dg_am CH1/CH2 同相 100 Hz 方波提供。
dg_mod.set_output(True, channel=1)
dg_mod.set_output(True, channel=2)
dg_mod.set_sync_state(False, channel=2)
print("  dg_mod 输出: CH1(100MHz) ON, CH2(脉冲) ON")
print("  dg_mod CH2 SYNC 未作为 dg_comp 触发源")

XY_TRIGGER_CHANNELS = (1, 2)

def validate_trigger_square_levels():
    """检查 dg_am 两路方波触发电平是否在 AM 安全范围内。"""
    half_amp = XY_TRIGGER_AMPLITUDE / 2.0
    low_level = XY_TRIGGER_OFFSET - half_amp
    high_level = XY_TRIGGER_OFFSET + half_amp
    validate_safety_limit("X_magnetic_field_AM", low_level)
    validate_safety_limit("X_magnetic_field_AM", high_level)
    validate_safety_limit("Y_magnetic_field_AM", low_level)
    validate_safety_limit("Y_magnetic_field_AM", high_level)


def configure_dg_am_reference_trigger():
    """设置 dg_am CH1/CH2 为 100 Hz 同相固定方波基准；校准过程中不再改变。"""
    validate_trigger_square_levels()
    trigger_phase = XY_TRIGGER_PHASE % 360
    for ch in XY_TRIGGER_CHANNELS:
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)
        dg_am.setup_square(
            freq=XY_TRIGGER_FREQ,
            amplitude=XY_TRIGGER_AMPLITUDE,
            offset=XY_TRIGGER_OFFSET,
            dcycle=XY_TRIGGER_DUTY,
            phase=trigger_phase,
            channel=ch,
        )
        dg_am.set_output(True, channel=ch)

    # 使用 DG4000 相位初始化功能，让两路方波使用同一个相位基准。
    for ch in XY_TRIGGER_CHANNELS:
        dg_am.phase_init(channel=ch)

    print(
        f"  dg_am CH1/CH2: fixed SQUARE {XY_TRIGGER_FREQ} Hz, "
        f"phase={trigger_phase:.2f}°, outputs ON -> dg_comp Ext Trig reference"
    )


configure_dg_am_reference_trigger()

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

time.sleep(0.5)

# 恢复温控
print("恢复温度开关...")
dg_temp.set_output(True, channel=2)
time.sleep(0.5)

# %% Cell 11
# ---- X/Y 控制信号配置（Burst 模式，dg_am 外触发同步）----
# Bell-Bloom 磁力仪物理原理:
#   原子以 Larmor 频率绕主磁场 B0 进动，Pump 光脉冲与之同步。
#   X/Y 控制磁场必须沿原子极化方向施加，因此:
#     1. X 和 Y 相差 90° 相位（正交控制，沿极化方向旋转）
#     2. XY 整体需相位延迟 XY_CTRL_PHASE 与 Pump 调制对齐
# 同步机制:
#   dg_am CH1/CH2 同相 100 Hz 方波 -> dg_comp Ext Trig（需硬件连接）
#   dg_am 两路方波保持 100 Hz 同相常开，作为 dg_comp 外触发相位基准。
print("配置 X/Y 交流控制信号（Burst 模式，dg_am 固定基准外触发）...")
XY_CALIB_AMP = 1.5


def update_xy_burst_phase(phase_deg, quad_deg, rearm=False):
    """更新 dg_comp X/Y Burst 相位，必要时重新武装外触发 Burst。"""
    x_phase = phase_deg % 360
    y_phase = (phase_deg + quad_deg) % 360
    if rearm:
        dg_comp.set_burst_state(False, channel=1)
        dg_comp.set_burst_state(False, channel=2)
        dg_comp.set_output(False, channel=1)
        dg_comp.set_output(False, channel=2)
        dg_comp.set_burst_state(True, channel=1)
        dg_comp.set_burst_state(True, channel=2)
    dg_comp.set_burst_phase(x_phase, channel=1)
    dg_comp.set_burst_phase(y_phase, channel=2)
    return x_phase, y_phase


# ---- CH1: X 通道 ----
# setup_sine 只用于初始化载波；后续幅度扫描只用 set_amplitude()。
dg_comp.set_burst_state(False, channel=1)
dg_comp.set_mod_state(False, channel=1)
dg_comp.setup_sine(freq=XY_CTRL_FREQ, amplitude=XY_CALIB_AMP,
                   offset=0.0, phase=0.0, channel=1)
dg_comp.set_output(False, channel=1)
dg_comp.set_burst_state(True, channel=1)
dg_comp.set_burst_mode("INFinity", channel=1)
dg_comp.set_burst_ncycles(50000, channel=1)
dg_comp.set_burst_trigger_source("EXTernal", channel=1)
dg_comp.set_burst_trigger_slope("POSitive", channel=1)

# ---- CH2: Y 通道 ----
dg_comp.set_burst_state(False, channel=2)
dg_comp.set_mod_state(False, channel=2)
dg_comp.setup_sine(freq=XY_CTRL_FREQ, amplitude=XY_CALIB_AMP,
                   offset=0.0, phase=0.0, channel=2)
dg_comp.set_output(False, channel=2)
dg_comp.set_burst_state(True, channel=2)
dg_comp.set_burst_mode("INFinity", channel=2)
dg_comp.set_burst_ncycles(50000, channel=2)
dg_comp.set_burst_trigger_source("EXTernal", channel=2)
dg_comp.set_burst_trigger_slope("POSitive", channel=2)
# dg_am 已在 RF 配置阶段固定相位并保持常开；这里只设置 dg_comp 相对触发基准的 Burst 相位。
x_phase, y_phase = update_xy_burst_phase(XY_CTRL_PHASE, XY_CTRL_QUAD)

print(f"  X CH1: Burst 模式, {XY_CTRL_FREQ} Hz, 幅度 {XY_CALIB_AMP} V, 相位 {x_phase:.2f}°")
print(f"  Y CH2: Burst 模式, {XY_CTRL_FREQ} Hz, 幅度 {XY_CALIB_AMP} V, 相位 {y_phase:.2f}° (= X + {XY_CTRL_QUAD}°)")
print("  X/Y 输出 OFF (等待固定相位 100 Hz dg_am 方波外触发启动 Burst)")
print("硬件同步要求: dg_am CH1/CH2 同相 100 Hz 固定方波 -> dg_comp Ext Trig 需物理连接")
print(f"相位关系: dg_am trigger={XY_TRIGGER_PHASE:.2f}° @ {XY_TRIGGER_FREQ:.1f} Hz, dg_comp X={XY_CTRL_PHASE:.2f}°, Y=X+{XY_CTRL_QUAD}°={y_phase:.2f}°")
print(f"幅值: {XY_CALIB_AMP} V（校准用，扫描起始 {XY_AMP_START} V）")

# %% Cell 12
# ============================================================
# XY_CTRL_PHASE 校准：保持 dg_am 基准不变，迭代 dg_comp Burst 触发相位
# ============================================================
print("=" * 60)
print("校准 XY_CTRL_PHASE（dg_am 固定基准 + dg_comp Burst 相位迭代）")
print("=" * 60)

PHASE_CAL_TOL_DEG = 1.0
MAX_CALIB_ITER = 10


def read_lockin_phase_offset_deg(hfi_inst, demod_idx=0):
    """读取 HF2 解调器 X/Y 相位偏移。"""
    sample_data = demod.read_demod_sample(hfi_inst, demod_idx=demod_idx)
    phase_deg = float(np.degrees(np.arctan2(sample_data["y"], sample_data["x"])))
    return phase_deg, sample_data


try:
    for calib_iter in range(1, MAX_CALIB_ITER + 1):
        print(f"\n--- 第 {calib_iter} 次校准迭代 ---")
        update_xy_burst_phase(XY_CTRL_PHASE, XY_CTRL_QUAD, rearm=True)
        dg_comp.set_output(True, channel=1)
        dg_comp.set_output(True, channel=2)

        dg_temp.set_output(False, channel=2)
        time.sleep(1.0)

        phase_offset, sample = read_lockin_phase_offset_deg(hfi, demod_idx=0)
        print(f"  相位偏移: {phase_offset:+.2f}°")
        print(f"  样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

        dg_temp.set_output(True, channel=2)
        time.sleep(1.0)

        if abs(phase_offset) < PHASE_CAL_TOL_DEG:
            print(f"  相位已收敛 (|{phase_offset:.2f}°| < {PHASE_CAL_TOL_DEG}°)")
            break

        old_phase = XY_CTRL_PHASE
        XY_CTRL_PHASE = (XY_CTRL_PHASE - phase_offset) % 360
        print(f"  修正: {old_phase:.2f}° -> {XY_CTRL_PHASE:.2f}°")
        update_xy_burst_phase(XY_CTRL_PHASE, XY_CTRL_QUAD, rearm=True)
        dg_comp.set_output(False, channel=1)
        dg_comp.set_output(False, channel=2)
    else:
        print(f"\n达到最大迭代次数 {MAX_CALIB_ITER}，校准可能未完全收敛")

finally:
    dg_temp.set_output(True, channel=2)
    update_xy_burst_phase(XY_CTRL_PHASE, XY_CTRL_QUAD, rearm=True)
    dg_comp.set_output(True, channel=1)
    dg_comp.set_output(True, channel=2)
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
config_saved["xy_ctrl"]["phase_cal_tolerance_deg"] = float(PHASE_CAL_TOL_DEG)
config_saved["xy_ctrl"]["trigger_square_freq_Hz"] = float(XY_TRIGGER_FREQ)
config_saved["xy_ctrl"]["trigger_square_phase_deg"] = float(XY_TRIGGER_PHASE)
config_saved["xy_ctrl"]["trigger_source"] = "dg_am CH1/CH2 fixed-phase 100 Hz square -> dg_comp Ext Trig"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(config_saved, f, allow_unicode=True, sort_keys=False)
print("最终 X/Y Burst 相位已写入 experiment_config.yaml")

# %% Cell 15
# ===== 逐点扫描 X/Y 幅度 + HF2 DAQ 采集 =====

amplitudes = np.linspace(XY_AMP_START, XY_AMP_STOP, XY_AMP_POINTS)
print("=" * 60)
print(f"X/Y 幅度扫描噪声谱测量")
print("=" * 60)
print(f"控制频率: {XY_CTRL_FREQ} Hz")
print(f"扫描范围: {XY_AMP_START} V → {XY_AMP_STOP} V, {XY_AMP_POINTS} 点")
print(f"DAQ 采集: 时长 {HF2_DAQ_DURATION}s, TC={HF2_DAQ_TC*1e6:.2f}μs, rate={HF2_DAQ_RATE:.0f} Sa/s")
total_est = XY_AMP_POINTS * (XY_SETTLE_TIME + HF2_DAQ_DURATION + 2.0)
print(f"预计耗时: {total_est:.0f}s ≈ {total_est/3600:.1f}h")
print()

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
pbar = tqdm(total=XY_AMP_POINTS, desc="Scanning", unit="pt")
try:
    for i, amp in enumerate(amplitudes):
        pbar.set_postfix_str(f"A={amp:.3f}V")

        # 只修改 Burst 载波幅度；随后重新武装外触发以保持相位对齐。
        dg_comp.set_amplitude(amp, channel=1)
        dg_comp.set_amplitude(amp, channel=2)
        update_xy_burst_phase(XY_CTRL_PHASE, XY_CTRL_QUAD, rearm=True)
        dg_comp.set_output(True, channel=1)
        dg_comp.set_output(True, channel=2)
        time.sleep(0.3)

        # 关闭温度开关（消除温控磁场干扰）
        dg_temp.set_output(False, channel=2)

        # 等待系统稳定
        time.sleep(XY_SETTLE_TIME)

        # 读取当前温度
        try:
            temp_now = tec.get_temperature(channel=1)
            pbar.set_postfix_str(f"A={amp:.3f}V, T={temp_now:.1f}°C")
        except:
            pbar.set_postfix_str(f"A={amp:.3f}V")

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
            tqdm.write(f"  [{i:4d}/{XY_AMP_POINTS}] amp={amp:.4f}V: DAQ 采集失败: {e}")
            waveform = np.array([np.nan])

        # 保存原始波形（.npy 二进制格式）
        np.save(raw_dir / f"waveform_C{i:04d}.npy", waveform)

        # 恢复温度开关
        dg_temp.set_output(True, channel=2)
        time.sleep(2)

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
print(f"\n✅ 扫描完成！共 {XY_AMP_POINTS} 点, 用时 {elapsed_total:.0f}s")
print(f"原始波形保存在: {raw_dir}")

# 保存实际采样率和原始文件列表到 experiment_config.yaml
with open(config_path, encoding="utf-8") as f:
    config_saved = yaml.safe_load(f)
config_saved["hf2_daq"]["actual_rate_Sa_s"] = float(actual_rate_daq)
config_saved["data_files"] = [
    f"raw/waveform_C{i:04d}.npy" for i in range(XY_AMP_POINTS)
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

# %% Cell 16
# ========== 安全断开（仅断开 TEC，其余设备保持连接） ==========
print("正在断开 TEC...")
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")
print("其他设备保持连接")
