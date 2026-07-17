# %% [markdown] Cell 0
# # 光散粒噪声标定 (ASD 底噪法)
#
# **实验目的**：扫描探测光功率，从原始时域波形离线计算 Welch PSD → ASD，取平坦区中位数作为底噪，线性拟合 $\mathrm{ASD}_{\mathrm{noise}} = \alpha' P_{\mathrm{probe}} + \beta'$，单位为 $\mathrm{V/\sqrt{Hz}}$。
#
# **原理**：将主磁场大幅偏置使原子 Larmor 频率移出 LIA 探测带宽，此时 LIA 输出仅含光散粒噪声和电噪声。
#
# **数据处理链**：
# 1. **采集阶段**（cell-5）：DAQ 采集 X/Y 时域波形，完整保存为 `waveforms_P*.npz`
# 2. **分析阶段**（cell-6）：加载波形 → Welch PSD → 截取平坦区(f < 0.1fs，避开数字抽取滤波器滚降) → sqrt 得 ASD → median 底噪 → 线性拟合
#
# **方法优势**：
# - ASD (V/√Hz) 是谱密度，物理意义比积分方差更 fundamental（散粒噪声公式 $S_0 \propto P_{\mathrm{probe}}$ 直接对应）
# - 中位数天然抗尖峰干扰，比积分更鲁棒
# - 频率截断避开 LPF 滚降区，只取白噪声平台
# - 原始数据完整保存，PSD/ASD 参数可随时调整重算
#
# **涉及设备**：
# - GS200：主磁场（偏置到 5 mA，远离正常工作点 9.305 mA）
# - DG912 Pro (dg_laser)：Pump 光功率 (CH1, 关断)、Probe 光功率 (CH2, 扫描变量)
# - DG4000 (dg_temp)：温度开关
# - TEC103：气室温度控制
# - HF2：锁相放大器 + DAQ 采集 X/Y 时间序列
#
# **参数概览**：
# - 扫描变量：Probe_laser_power (0 → 0.1 V, 11 点)
# - 温度：100 °C，主磁场：5 mA
# - 每点采集时长：1 s，重复 20 次
# - PSD: Welch nperseg=4096
# - ASD: flat band (0, 0.3fs] Hz, median of sqrt(PSD)

# %% Cell 1
# ========== 交互式绘图模式 ==========

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
from datetime import datetime
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from tqdm import tqdm

# 设备库
from gs200 import GS200Instrument
from lab_workflows.devices import create_signal_generator
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig,
    DemodulatorConfig, demod, daq,
)

# 数据分析库
from scipy.optimize import curve_fit
from scipy import signal as scipy_signal

# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Photon_shot_noise"
PURPOSE = "shot_noise_calibration"

print("所有库导入成功")

# %% Cell 2
# ========== 加载配置 ==========

# 加载物理量→仪器映射
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 扫描参数 ==========
PROBE_POWER_START = 0.0       # 探测光功率起始 (V)
PROBE_POWER_STOP = 0.3        # 探测光功率终止 (V)
PROBE_POWER_POINTS = 31        # 扫描点数
PROBE_SETTLE_TIME = 0.5       # 每点设置后等待稳定时间 (s)

# ========== 采集参数 ==========
ACQ_DURATION = 1.0             # 每点 DAQ 采集时长 (s)
ACQ_REPEATS = 20               # 每点重复采集次数

# ========== PSD 计算参数 ==========
PSD_NPERSEG = 4096             # Welch PSD 每段点数

# ========== 固定参数 ==========
FIXED_PARAMS = {
    "temperature": 100.0,               # 气室温度 (°C)
    "SHOT_NOISE_FIELD_mA": 5.0,         # 散粒噪声测量用主磁场 (mA)，远离 9.305 mA 正常点
    "Pump_laser_power": 0.0,            # Pump 光功率，关闭
    "Temp_Switch": 5.0,                 # 温度开关常开
}

# ========== HF2 解调参数 ==========
HF2_DEMOD_IDX = 0              # 解调器索引
HF2_OSC_FREQ = 90000           # 振荡器频率 (Hz)
HF2_SIGNAL_RANGE = 2.0         # 信号输入量程 (V)
HF2_DEMOD_ORDER = 4            # 解调滤波器阶数
HF2_DEMOD_TC = 7e-7       # 解调时间常数 (s)
HF2_DEMOD_RATE = 50000         # 解调输出速率 (Sa/s)

# ========== 运行目录命名 ==========
RUN_TAG = "shot_noise_cal"

# 安全边界检查函数
def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错"""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if value is not None and (value < lo or value > hi):
        raise ValueError(
            f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]"
        )
    return value

print("配置加载完成")

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

    # ---- DG912 Pro: Pump/Probe 光功率 ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = create_signal_generator(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    print(f"光功率 DG912 Pro 已连接: {dg_laser.idn()}")
    dg_laser.set_ref_clock_source("EXTernal")
    devices["dg_laser"] = dg_laser

    # ---- DG4000: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = create_signal_generator(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    print(f"温控 DG4000 已连接: {dg_temp.idn()}")
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
    raise

print(f"\n所有设备连接完成，共 {len(devices)} 个设备")

# %% Cell 4
# ========== 设置初始值 ==========

# ---- 1. Pump 光功率 (DC, 关闭) ----
# [经验] 散粒噪声测量需完全关闭 Pump 光，确保无原子极化
validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
dg_laser.set_output(False, channel=1)  # 直接关闭输出确保隔离度
print(f"Pump 光功率: CH1 关闭 (输出 OFF)")

# ---- 2. Probe 光功率 (DC, 初始值 0) ----
validate_safety_limit("Probe_laser_power", PROBE_POWER_START)
dg_laser.setup_dc(PROBE_POWER_START, channel=2)
dg_laser.set_output(True, channel=2)
print(f"Probe 光功率: CH2={PROBE_POWER_START} V (输出 ON)")

# ---- 3. 主磁场（偏置到远离正常点）----
# [经验] 将主磁场从正常值 9.305 mA 大幅偏置，使 Ω_L 移出 LIA 探测带宽
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["SHOT_NOISE_FIELD_mA"])
gs.set_current(FIXED_PARAMS["SHOT_NOISE_FIELD_mA"] / 1000.0)
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['SHOT_NOISE_FIELD_mA']} mA (偏置，远离正常点 9.305 mA)")

# ---- 4. 温度控制 ----
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
            print(f"温度已稳定: {t2:.2f} °C")
            break
    except:
        pass

# ---- 5. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 6. 配置 HF2 解调器 ----

demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True,
    rate=HF2_DEMOD_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
)
actual_rate = demod.configure_demodulator(hfi, demod_cfg)
print(f"HF2 解调器 {HF2_DEMOD_IDX} 已配置: Rate={HF2_DEMOD_RATE} Sa/s, TC={HF2_DEMOD_TC*1e6:.0f} μs")

# ========== 创建运行目录 ==========
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
raw_dir = run_dir / "raw"
raw_dir.mkdir(exist_ok=True)
results_dir = run_dir / "results"
results_dir.mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# ========== 保存实验配置 ==========
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "purpose": PURPOSE,
    "timestamp": timestamp,
    "scan_params": {
        "variable": "Probe_laser_power",
        "start": PROBE_POWER_START,
        "stop": PROBE_POWER_STOP,
        "points": PROBE_POWER_POINTS,
        "settle_time": PROBE_SETTLE_TIME,
    },
    "acquisition_params": {
        "duration": ACQ_DURATION,
        "repeats": ACQ_REPEATS,
    },
    "psd_params": {
        "nperseg": PSD_NPERSEG,
    },
    "fixed_params": FIXED_PARAMS,
    "hf2_demod": {
        "demod_idx": HF2_DEMOD_IDX,
        "osc_freq": HF2_OSC_FREQ,
        "signal_range": HF2_SIGNAL_RANGE,
        "order": HF2_DEMOD_ORDER,
        "tc": HF2_DEMOD_TC,
        "rate": HF2_DEMOD_RATE,
    },
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print(f"实验配置已保存: {config_path}")

print("\n初始值设置完成")

# %% Cell 5
# ========== 数据采集：扫描 Probe 光功率 + DAQ 采集 X/Y 时间序列 ==========
# [经验] 采集时关闭温度开关，消除温控 PWM 磁场干扰
# [经验] 原始时域数据完整保存，PSD 计算和拟合在分析 Cell 中进行
# [经验] 高功率点若偏离线性，可能是探测器饱和，需检查 PD 工作点

probe_powers = np.linspace(PROBE_POWER_START, PROBE_POWER_STOP, PROBE_POWER_POINTS)
print("=" * 60)
print(f"光散粒噪声标定 — 探测光功率扫描")
print(f"主磁场 (偏置): {FIXED_PARAMS['SHOT_NOISE_FIELD_mA']} mA")
print(f"探测光功率: {PROBE_POWER_START} → {PROBE_POWER_STOP} V, {PROBE_POWER_POINTS} 点")
print(f"每点采集 {ACQ_REPEATS} 次 × {ACQ_DURATION} s")
print("=" * 60)

grid_cols = int(actual_rate * ACQ_DURATION)
print(f"DAQ grid_cols: {grid_cols} (rate={actual_rate:.0f} Sa/s × {ACQ_DURATION} s)")

t_start = time.time()

pbar = tqdm(total=PROBE_POWER_POINTS, desc="Probe Power", unit="pt")
try:
    for i, pwr in enumerate(probe_powers):
        validate_safety_limit("Probe_laser_power", pwr)

        dg_laser.setup_dc(pwr, channel=2)
        time.sleep(PROBE_SETTLE_TIME)

        power_waveforms = []

        for rep in range(ACQ_REPEATS):
            dg_temp.set_output(False, channel=2)
            time.sleep(0.1)
            daq_cfg = DAQConfig(
                device=MAPPING["lockin_r"]["device_id"],
                trigger_type=0,
                duration=ACQ_DURATION,
                grid_cols=grid_cols,
                grid_rows=1,
                grid_mode=2,
                signal_paths=["sample.x", "sample.y"],
            )

            try:
                results = daq.acquire_data(
                    hfi, daq_cfg, demod_idx=HF2_DEMOD_IDX,
                    actual_rate=actual_rate, timeout=ACQ_DURATION + 10.0,
                )
                x_data = results[0].values
                y_data = results[1].values
            except Exception as e:
                tqdm.write(f"  [P={pwr:.3f}V, rep={rep}] DAQ 采集失败: {e}")
                x_data = np.full(grid_cols, np.nan)
                y_data = np.full(grid_cols, np.nan)

            dg_temp.set_output(True, channel=2)
            time.sleep(2)

            power_waveforms.append({"x": x_data, "y": y_data})

        # 保存原始波形（供后续 PSD 分析使用）
        np.savez(
            raw_dir / f"waveforms_P{i:03d}.npz",
            probe_power=pwr,
            probe_power_index=i,
            x_waveforms=np.array([w["x"] for w in power_waveforms]),
            y_waveforms=np.array([w["y"] for w in power_waveforms]),
            actual_rate=actual_rate,
        )

        # 快速方差估算（仅用于进度显示，不代表最终结果）
        quick_var_x = np.mean([np.var(w["x"]) for w in power_waveforms])
        quick_var_y = np.mean([np.var(w["y"]) for w in power_waveforms])
        pbar.set_postfix_str(
            f"P={pwr:.3f}V, Var(X)≈{quick_var_x:.3e}, Var(Y)≈{quick_var_y:.3e}"
        )
        pbar.update(1)

except Exception as e:
    print(f"\n扫描出错: {e}")
    print("紧急恢复温度开关...")
    dg_temp.set_output(True, channel=2)
    raise

finally:
    pbar.close()
    dg_temp.set_output(True, channel=2)
    print("温度开关已恢复 ON")

# 保存扫描元数据
np.savez(
    raw_dir / "scan_meta.npz",
    probe_powers=probe_powers,
    acq_duration=ACQ_DURATION,
    acq_repeats=ACQ_REPEATS,
    actual_rate=actual_rate,
)
print(f"扫描元数据已保存: {raw_dir / 'scan_meta.npz'}")

elapsed_total = time.time() - t_start
print(f"\n数据采集完成，总耗时: {elapsed_total / 60:.1f} 分钟")

# %% Cell 6
# ========== 安全断开（仅断开 TEC） ==========

print("正在断开 TEC...")
tec_dev = devices.get("tec")
if tec_dev and hasattr(tec_dev, "disconnect"):
    try:
        tec_dev.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")

print("其他设备保持连接，输出状态不变")

