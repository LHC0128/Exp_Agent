# %% [markdown] Cell 0
# # 热态投影噪声标定 (PSD 法)
#
# **实验目的**：在同一实验条件下先后测量纯光噪声（主磁场偏置）和热态噪声（主磁场正常），通过 Welch PSD 频域分析剔除技术噪声后积分得方差，计算等效耦合强度 $\tilde{\kappa}^2$ 并反推投影噪声极限 PNL。
#
# **原理**：热态下 $\langle J \rangle = 0$，不存在条件压缩/反压缩效应，测到的原子噪声是纯投影噪声。两阶段测量使用完全相同的 Probe 功率、解调器设置和采集参数，确保 Var(X^light) 和 Var(X^thermal) 的可比性。
#
# **两阶段测量**：
# | 阶段 | 主磁场 | Pump 光 | 测量量 |
# |------|--------|:---:|------|
# | Phase 1 (Cell 5) | **偏置** (5 mA) | OFF | 纯光噪声 $\mathrm{Var}(X^{\text{light}})$ |
# | Phase 2 (Cell 6) | **正常** (9.40 mA) | OFF | 热态噪声 $\mathrm{Var}(X^{\text{thermal}})$ |
#
# **核心公式**：
# $$
# \tilde{\kappa}^2 = \frac{\mathrm{Var}(X^{\text{thermal}}) - \mathrm{Var}(X^{\text{light}})}{\mathrm{Var}(X^{\text{light}})} \times 0.8 \times \frac{5}{6}
# $$
#
# **数据处理链**：
# 1. **Phase 1**（Cell 5）：主磁场偏置 → DAQ 采集 X/Y 波形 → 保存 `waveforms_phase1_light.npz`
# 2. **Phase 2**（Cell 6）：主磁场正常 → DAQ 采集 X/Y 波形 → 保存 `waveforms_thermal.npz`
# 3. **分析**（Cell 7-8）：加载两阶段波形 → Welch PSD → 工频陷波 → 频段积分 → $\tilde{\kappa}^2$ → 对称性验证
#
# **涉及设备**：
# - GS200：主磁场（偏置 5 mA / 正常 9.40 mA）
# - DG912 Pro (dg_laser)：Pump 光功率 (CH1, 关断)、Probe 光功率 (CH2, 工作值)
# - DG4000 (dg_temp)：温度开关
# - TEC103：气室温度控制
# - HF2：锁相放大器 + DAQ 采集 X/Y 时间序列
#
# **参数概览**：
# - 温度：100 °C，Probe 功率：0.3 V
# - 每阶段采集时长：1 s，重复 40 次
# - PSD: Welch nperseg=10000
# - 积分范围：[0.5, 2000] Hz，陷波频率：[50, 100, 150] Hz

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
from scipy import signal as scipy_signal

# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Projection_noise"
PURPOSE = "thermal_projection_noise_calibration"

print("所有库导入成功")

# %% Cell 2
# ========== 加载配置 ==========

# 加载物理量→仪器映射
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 实验参数 ==========
# ---- 探测光 ----
PROBE_POWER = 0.03               # 探测光功率 (V)

# ---- 主磁场（两阶段切换值）----
MAIN_FIELD_NORMAL_mA = 9.30     # Phase 2 正常工作主磁场 (mA)，Ω_L/2π ≈ 90 kHz
MAIN_FIELD_OFFSET_mA = 5.0      # Phase 1 偏置主磁场 (mA)，Ω_L 移出 LIA 带宽

# ---- 采集参数（两阶段共用）----
ACQ_DURATION = 1.0              # DAQ 单次采集时长 (s)
ACQ_REPEATS = 100                # 每阶段重复采集次数

# ---- PSD 计算参数 ----
NPERSEG = 5000                 # Welch PSD 每段点数

# ---- PSD 积分范围 ----
PSD_INTEG_FMIN = 0.5            # PSD 积分下限 (Hz)，排除 DC 附近漂移
PSD_INTEG_FMAX = 5000           # PSD 积分上限 (Hz)，LIA LPF 带宽

# ---- 技术噪声剔除 ----
NOTCH_FREQS = [50, 100, 150]    # 需剔除的工频谐波 (Hz)
NOTCH_WIDTH = 2                 # 剔除窗口半宽 (Hz)

# ---- 自适应窄带峰去除 ----
# [经验] 即使非工频位置也可能出现窄带干扰峰（如开关电源 ~3.6 kHz），
# 用中位数阈值法自动检测并剔除，避免污染积分方差
PEAK_THRESHOLD = 5.0            # 窄带峰检测阈值，PSD 超出积分频段中位数 N 倍的 bin 标记为峰
PEAK_DILATE = 1                 # 标记峰两侧各扩展 bin 数，处理频谱泄漏

# ---- F=1 修正因子 ----
F1_CORRECTION = 0.833           # 5/6，远失谐等耦合近似；有 repump 时设为 1.0
PNL_THERMAL_RATIO = 0.8         # 0.8 = 4/5，PNL/热态方差比

# ---- HF2 解调参数 ----
HF2_DEMOD_IDX = 0               # 解调器索引
HF2_OSC_FREQ = 90000            # 振荡器频率 (Hz)
HF2_SIGNAL_RANGE = 2.0          # 信号输入量程 (V)
HF2_DEMOD_ORDER = 4             # 解调滤波器阶数
HF2_DEMOD_TC = 7.85e-07         # 解调时间常数 (s)，高带宽噪声采集
HF2_DEMOD_RATE = 30000          # 解调输出速率 (Sa/s)

# ========== 固定参数 ==========
FIXED_PARAMS = {
    "temperature": 100.0,       # 气室温度 (°C)
    "Pump_laser_power": 0.0,    # Pump 光功率 (V)，全程关闭
    "Temp_Switch": 5.0,         # 温度开关常开 (V)
}

# ========== 运行目录命名 ==========
RUN_TAG = "thermal_pnl"

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
print(f"  两阶段测量: Phase1(偏置{MAIN_FIELD_OFFSET_mA}mA) → Phase2(正常{MAIN_FIELD_NORMAL_mA}mA)")
print(f"  F1_CORRECTION = {F1_CORRECTION} {'(有 repump)' if F1_CORRECTION == 1.0 else '(无 repump，远失谐等耦合近似)'}")

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
# ========== 设置初始值并创建运行目录 ==========

# ---- 1. Pump 光功率 (DC, 关闭) ----
# [经验] 热态噪声测量需完全关闭 Pump 光，确保无原子极化
validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
dg_laser.set_output(False, channel=1)  # 直接关闭输出确保隔离度
print(f"Pump 光功率: CH1 关闭 (输出 OFF)")

# ---- 2. Probe 光功率 (DC, 工作值) ----
validate_safety_limit("Probe_laser_power", PROBE_POWER)
dg_laser.setup_dc(PROBE_POWER, channel=2)
dg_laser.set_output(True, channel=2)
print(f"Probe 光功率: CH2={PROBE_POWER} V (输出 ON)")

# ---- 3. 主磁场（初始设为偏置安全值）----
validate_safety_limit("main_magnetic_field", MAIN_FIELD_OFFSET_mA)
gs.set_current(MAIN_FIELD_OFFSET_mA / 1000.0)
gs.set_output(True)
print(f"主磁场: {MAIN_FIELD_OFFSET_mA} mA (初始安全值)")

# ---- 4. 温度控制 ----
validate_safety_limit("temperature", FIXED_PARAMS["temperature"])
tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
tec.set_enable(True, channel=1)
temp_now = tec.get_temperature(channel=1)
print(f"温度设定: {FIXED_PARAMS['temperature']} °C, 当前: {temp_now:.1f} °C")
# [经验] 采集前需确保温度已稳定，避免原子数漂移引入额外噪声
print("等待温度稳定...")
while True:
    time.sleep(5)
    t = tec.get_temperature(channel=1)
    print(f"  当前温度: {t:.2f} °C")
    if abs(t - FIXED_PARAMS['temperature']) < 1:
        print(f"温度已稳定: {t:.2f} °C")
        break

# ---- 5. 温度开关 (ON) ----
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
dg_temp.set_output(True, channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# ---- 6. 配置 HF2 解调器 ----
# [经验] 噪声标定需使用高带宽（低 TC），确保采集到完整白噪声谱
demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True,
    rate=HF2_DEMOD_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
)
actual_rate = demod.configure_demodulator(hfi, demod_cfg)
print(f"HF2 解调器 {HF2_DEMOD_IDX} 已配置: Rate={actual_rate:.0f} Sa/s, TC={HF2_DEMOD_TC*1e6:.2f} μs")

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
    "two_phase_measurement": True,
    "probe_power": PROBE_POWER,
    "phases": {
        "main_field_mA": MAIN_FIELD_NORMAL_mA,
        "pump_power": 0.0,
        "description": "主磁场正常，Ω_L 在 LIA 带宽内，测量热态噪声"
    },
    "acquisition_params": {
        "duration": ACQ_DURATION,
        "repeats": ACQ_REPEATS,
    },
    "psd_params": {
        "nperseg": NPERSEG,
        "integ_fmin": PSD_INTEG_FMIN,
        "integ_fmax": PSD_INTEG_FMAX,
        "notch_freqs": NOTCH_FREQS,
        "notch_width": NOTCH_WIDTH,
    },
    "correction_factors": {
        "f1_correction": F1_CORRECTION,
        "pnl_thermal_ratio": PNL_THERMAL_RATIO,
    },
    "fixed_params": FIXED_PARAMS,
    "hf2_demod": {
        "demod_idx": HF2_DEMOD_IDX,
        "osc_freq": HF2_OSC_FREQ,
        "signal_range": HF2_SIGNAL_RANGE,
        "order": HF2_DEMOD_ORDER,
        "tc": HF2_DEMOD_TC,
        "rate": HF2_DEMOD_RATE,
        "actual_rate": actual_rate,
    },
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print(f"实验配置已保存: {config_path}")

print("\n初始值设置完成")

# %% Cell 5
# =====================================================================
# Phase 1: 纯光噪声测量
# =====================================================================
# 主磁场偏置, Omega_L 移出 LIA 带宽, LIA 仅看到散粒噪声 + 电噪声
# [经验] 采集时关闭温度开关，消除温控 PWM 磁场干扰
# [经验] 两阶段使用完全相同的 DAQ 参数和采集次数

print("=" * 60)
print("Phase 1: 纯光噪声测量")
print(f"  主磁场: {MAIN_FIELD_OFFSET_mA} mA (偏置)")
print(f"  Pump 光: OFF")
print(f"  Probe 光: {PROBE_POWER} V (CW)")
print(f"  采集: {ACQ_REPEATS} 次 x {ACQ_DURATION} s")
print("=" * 60)

validate_safety_limit("main_magnetic_field", MAIN_FIELD_OFFSET_mA)
gs.set_current(MAIN_FIELD_OFFSET_mA / 1000.0)
time.sleep(0.5)
print(f"主磁场已设为偏置值: {MAIN_FIELD_OFFSET_mA} mA")

grid_cols = int(actual_rate * ACQ_DURATION)
print(f"DAQ grid_cols: {grid_cols} (rate={actual_rate:.0f} Sa/s x {ACQ_DURATION} s)")

phase1_waveforms = []
t_start = time.time()

try:
    for rep in tqdm(range(ACQ_REPEATS), desc="Phase 1 - Light Noise", unit="rep"):
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
            tqdm.write(f"  [rep={rep}] DAQ error: {e}")
            x_data = np.full(grid_cols, np.nan)
            y_data = np.full(grid_cols, np.nan)

        dg_temp.set_output(True, channel=2)
        time.sleep(2)

        phase1_waveforms.append({"x": x_data, "y": y_data})

except Exception as e:
    print(f"\nPhase 1 error: {e}")
    dg_temp.set_output(True, channel=2)
    raise

finally:
    dg_temp.set_output(True, channel=2)
    print("temp switch restored ON")

elapsed_p1 = time.time() - t_start
print(f"Phase 1 done: {elapsed_p1 / 60:.1f} min")

np.savez(
    raw_dir / "waveforms_phase1_light.npz",
    phase="light_noise",
    main_field_mA=MAIN_FIELD_OFFSET_mA,
    probe_power=PROBE_POWER,
    x_waveforms=np.array([w["x"] for w in phase1_waveforms]),
    y_waveforms=np.array([w["y"] for w in phase1_waveforms]),
    actual_rate=actual_rate,
    acq_duration=ACQ_DURATION,
)
print(f"Phase 1 saved: {raw_dir / 'waveforms_phase1_light.npz'}")

# %% Cell 6
# =====================================================================
# 热态噪声测量
# =====================================================================
# 主磁场恢复到正常值 9.30 mA → Ω_L 回到 LIA 带宽内 → LIA 看到光噪声 + 原子自旋噪声

print("=" * 60)
print("热态噪声测量")
print(f"  主磁场: {MAIN_FIELD_NORMAL_mA} mA (正常，Ω_L 在 LIA 带宽内)")
print(f"  Pump 光: OFF (热态，⟨J⟩=0)")
print(f"  Probe 光: {PROBE_POWER} V (CW)")
print(f"  采集: {ACQ_REPEATS} 次 × {ACQ_DURATION} s")
print("=" * 60)

# 恢复主磁场到正常值
validate_safety_limit("main_magnetic_field", MAIN_FIELD_NORMAL_mA)
gs.set_current(MAIN_FIELD_NORMAL_mA / 1000.0)
print(f"主磁场已恢复到正常值: {MAIN_FIELD_NORMAL_mA} mA")

# [经验] 等待原子回到热平衡（≥ 5×T₁），确保 Pump 关闭后极化完全弛豫
thermal_settle_time = 0.2  # s，T₁ ~ 30 ms，等待 ~7×T₁
print(f"等待热平衡 ({thermal_settle_time} s, ~7×T₁)...")
time.sleep(thermal_settle_time)

grid_cols = int(actual_rate * ACQ_DURATION)
print(f"DAQ grid_cols: {grid_cols} (rate={actual_rate:.0f} Sa/s × {ACQ_DURATION} s)")

thermal_waveforms = []  # 存储每段采集的 X/Y 波形

t_start = time.time()

try:
    for rep in tqdm(range(ACQ_REPEATS), desc="Thermal Noise", unit="rep"):
        # 关闭温度开关
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
            tqdm.write(f"  [rep={rep}] DAQ 采集失败: {e}")
            x_data = np.full(grid_cols, np.nan)
            y_data = np.full(grid_cols, np.nan)

        # 立即恢复温度开关
        dg_temp.set_output(True, channel=2)
        time.sleep(2)

        thermal_waveforms.append({"x": x_data, "y": y_data})

except Exception as e:
    print(f"\n热态采集出错: {e}")
    print("紧急恢复温度开关...")
    dg_temp.set_output(True, channel=2)
    raise

finally:
    dg_temp.set_output(True, channel=2)
    print("温度开关已恢复 ON")

elapsed = time.time() - t_start
print(f"热态采集完成，耗时: {elapsed / 60:.1f} 分钟")

# 保存热态原始波形
np.savez(
    raw_dir / "waveforms_thermal.npz",
    main_field_mA=MAIN_FIELD_NORMAL_mA,
    probe_power=PROBE_POWER,
    x_waveforms=np.array([w["x"] for w in thermal_waveforms]),
    y_waveforms=np.array([w["y"] for w in thermal_waveforms]),
    actual_rate=actual_rate,
    acq_duration=ACQ_DURATION,
)
print(f"热态数据已保存: {raw_dir / 'waveforms_thermal.npz'}")

# %% Cell 7
# ========== 安全断开（仅断开 TEC） ==========

print("正在断开 TEC...")
tec_dev = devices.get("tec")
if tec_dev and hasattr(tec_dev, "disconnect"):
    try:
        tec_dev.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")

# 主磁场恢复到偏置安全值
try:
    gs.set_current(MAIN_FIELD_OFFSET_mA / 1000.0)
    print(f"  主磁场已恢复到偏置值: {MAIN_FIELD_OFFSET_mA} mA")
except Exception as e:
    print(f"  恢复磁场失败: {e}")

print("其他设备保持连接，输出状态不变")
