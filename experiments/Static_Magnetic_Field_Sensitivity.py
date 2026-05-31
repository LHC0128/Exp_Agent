# %% [markdown] Cell 0
# # 静磁场灵敏度测量
#
# 测量静磁场的色散线形，根据色散斜率和功率谱密度计算磁场灵敏度。
#
# ## 硬件连接说明
# - **Pump 调制**: DG4000 (DG4E222800868) CH1 输出 100MHz 正弦波 → RF 开关 IN
# - **RF 开关门控**: DG4000 (DG4E222800868) CH2 输出脉冲方波 → RF 开关 CTRL
# - **RF 开关输出**: RF 开关 OUT → AOM（Pump 光调制）
# - **10Hz 时序信号**: DG4000 (DG4E242401288) CH2 输出（由 DG4000_Z_field 的 CH2 提供）
# - **SYNC 触发**: DG4000 (DG4E242401288) CH1 SYNC → HF2 辅助输入
#
# ## 注意事项
# - 数据采集前需关闭 Temp_Switch (0V)，避免温控磁场干扰锁相读数
# - 采集结束后立即打开温控，等待温度稳定
# - RF 开关输出端需串 0.1μF 隔直电容再接 AOM

# %% Cell 1
from pathlib import Path
import sys
# 自动定位项目根目录
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import time
from datetime import datetime
import matplotlib.pyplot as plt

# 设备库
from gs200 import GS200Instrument
from signal_generator import DG4000Instrument, DG900Instrument
from tec_controller import TECInstrument
from lockin_amplifier import (
    HF2Instrument, DAQConfig, DAQResult,
    SignalInputConfig, OscillatorConfig, DemodulatorConfig,
    demod, daq,
)

print("库导入完成")

# %% Cell 2
# 加载物理量→仪器映射
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 实验参数 ==========
EXPERIMENT_TYPE = "Static_Magnetic_Field_Sensitivity"
PURPOSE = "dispersion_and_sensitivity"

SCAN_VARIABLE = "Z_magnetic_field"
RAMP_LOW = -0.5
RAMP_HIGH = 0.5
RAMP_FREQ = 1.0
RAMP_SYMMETRY = 20
DAQ_DURATION = 1

FIXED_PARAMS = {
    "Pump_laser_power": 0.2,         "Probe_laser_power": 0.2,
    "temperature": 100,              "Temp_Switch": 5.0,
    "Time_sequence": 10.0,           "main_magnetic_field": 9.3,
    "X_magnetic_field": 0,         "Y_magnetic_field": 0,
    "Time_sequence_2": 0.0,         
}

PUMP_MOD_FREQ = 90e3                # Pump 调制重复频率 (Hz)
PUMP_MOD_AMPLITUDE = 0.18           # 100MHz 载波幅度 (Vpp)
PUMP_MOD_DUTY = 5                   # 脉冲占空比 (%), 与任意波方案一致

# RF 开关控制信号参数
RF_GATE_AMPLITUDE = 5.0             # CH2 门控脉冲幅度 (Vpp), 适配 TTL
RF_GATE_OFFSET = 2.5                # CH2 门控脉冲偏置 (V), 0~5V TTL
RF_GATE_DELAY = 0.0                 # CH2 门控脉冲延迟 (s)

# ========== HF2 配置 ==========
HF2_DEMOD_IDX = 0
HF2_OSC_FREQ = PUMP_MOD_FREQ
HF2_DEMOD_ORDER = 4
HF2_SIGNAL_RANGE = 2.0
HF2_DEMOD_RATE = 1000
HF2_DEMOD_TC = 0.001
HF2_NOISE_RATE = 50000
HF2_NOISE_TC = 1e-6

# ========== 噪声采集参数 ==========
NOISE_N_AVG = 5            # 噪声采集次数，增大可降低频谱涨落
NOISE_DURATION = 1.0        # 每次采集时长 (s)

# ========== 磁场校准 ==========
Z_V_TO_NT = 3517
Z_V_TO_FT = Z_V_TO_NT * 1000000

# ========== DAQ 触发 ==========
DAQ_TRIGGER_CHANNEL = 0
DAQ_TRIGGER_LEVEL = 1
DAQ_TRIGGER_SLOPE = 0

# ========== 运行目录命名 ==========
# 格式: MMDD_HHMM_短标签，如 "0517_1120_sens"
# 在此处修改短标签即可自定义目录名
RUN_TAG = "sens"

print("配置已加载")

# %% Cell 3
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


devices = {}

try:
    # ---- GS200: 主磁场 ----
    gs_cfg = MAPPING["main_magnetic_field"]
    gs = GS200Instrument(gs_cfg["resource"])
    gs.connect()
    gs.set_source_function(gs_cfg["source_function"])
    # safety_limits.yaml 存储单位: mA, set_current_limit 单位: A
    gs.set_current_limit(LIMITS["main_magnetic_field"]["max"] / 1000.0)
    devices["gs200"] = gs

    # ---- DG4000: Z 磁场扫描 (共 2 通道) ----
    dg_sweep_cfg = MAPPING["Z_magnetic_field"]
    dg_sweep = DG4000Instrument(dg_sweep_cfg["resource"], channel=1)
    dg_sweep.connect()
    dg_sweep.set_ref_clock_source("EXTernal")
    devices["dg_sweep"] = dg_sweep

    # ---- DG912 Pro: Pump/Probe 光功率 (DC) ----
    dg_laser_cfg = MAPPING["Pump_laser_power"]
    dg_laser = DG900Instrument(dg_laser_cfg["resource"], channel=1)
    dg_laser.connect()
    devices["dg_laser"] = dg_laser

    # ---- DG4000: X/Y 补偿磁场 ----
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = DG4000Instrument(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    dg_comp.set_ref_clock_source("EXTernal")
    devices["dg_comp"] = dg_comp

    # ---- DG4000: Pump 调制 + 时序 ----
    dg_mod_cfg = MAPPING["Pump_modulation"]
    dg_mod = DG4000Instrument(dg_mod_cfg["resource"], channel=1)
    dg_mod.connect()
    devices["dg_mod"] = dg_mod

    # ---- DG912 Pro: 温度开关 ----
    dg_temp_cfg = MAPPING["Temp_Switch"]
    dg_temp = DG900Instrument(dg_temp_cfg["resource"], channel=2)
    dg_temp.connect()
    devices["dg_temp"] = dg_temp

    # ---- TEC103: 温度控制器 ----
    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
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
    hfi.set_extclk(True)
    devices["hf2"] = hfi

except Exception as e:
    print(f"设备连接失败: {e}")
    raise

print(f"设备连接完成: GS200, DG4000×3, DG912×2, TEC103, HF2 (共 {len(devices)})")

# %% Cell 4
hfi = devices["hf2"]
tec = devices["tec"]
gs = devices["gs200"]
dg_laser = devices["dg_laser"]
dg_comp = devices["dg_comp"]
dg_sweep = devices["dg_sweep"]
dg_mod = devices["dg_mod"]
dg_temp = devices["dg_temp"]

# ---- 设置固定参数 ----

# 1. Pump 光功率 (DC 电平)
validate_safety_limit("Pump_laser_power", FIXED_PARAMS["Pump_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Pump_laser_power"], channel=1)
print(f"Pump 光功率: {FIXED_PARAMS['Pump_laser_power']} V DC")

# 2. Probe 光功率 (DC 电平)
validate_safety_limit("Probe_laser_power", FIXED_PARAMS["Probe_laser_power"])
dg_laser.setup_dc(FIXED_PARAMS["Probe_laser_power"], channel=2)
print(f"Probe 光功率: {FIXED_PARAMS['Probe_laser_power']} V DC")

# 3. X/Y 补偿磁场 — 非零时自动打开输出，0 时关闭
validate_safety_limit("X_magnetic_field", FIXED_PARAMS["X_magnetic_field"])
validate_safety_limit("Y_magnetic_field", FIXED_PARAMS["Y_magnetic_field"])
dg_comp.setup_dc(FIXED_PARAMS["X_magnetic_field"], channel=1)
dg_comp.setup_dc(FIXED_PARAMS["Y_magnetic_field"], channel=2)
x_on = abs(FIXED_PARAMS["X_magnetic_field"]) > 0
y_on = abs(FIXED_PARAMS["Y_magnetic_field"]) > 0
dg_comp.set_output(x_on, channel=1)
dg_comp.set_output(y_on, channel=2)
x_label = f"{FIXED_PARAMS['X_magnetic_field']} V {'(ON)' if x_on else '(OFF)'}"
y_label = f"{FIXED_PARAMS['Y_magnetic_field']} V {'(ON)' if y_on else '(OFF)'}"
print(f"X 补偿磁场: {x_label}")
print(f"Y 补偿磁场: {y_label}")

# 4. 主磁场 (GS200, 电流模式)
validate_safety_limit("main_magnetic_field", FIXED_PARAMS["main_magnetic_field"])
gs.set_current(FIXED_PARAMS["main_magnetic_field"] / 1000.0)  # mA -> A
gs.set_output(True)
print(f"主磁场: {FIXED_PARAMS['main_magnetic_field']} mA")

# 5. 温度控制
validate_safety_limit("temperature", FIXED_PARAMS["temperature"])
tec.set_target_temperature(FIXED_PARAMS["temperature"], channel=1)
tec.set_enable(True, channel=1)
temp_now = tec.get_temperature(channel=1)
print(f"温度设定: {FIXED_PARAMS['temperature']} °C, 当前: {temp_now:.1f} °C")

# 6. 温度开关 (ON)
validate_safety_limit("Temp_Switch", FIXED_PARAMS["Temp_Switch"])
dg_temp.setup_dc(FIXED_PARAMS["Temp_Switch"], channel=2)
print(f"温度开关: ON ({FIXED_PARAMS['Temp_Switch']} V)")

# 7. 时序信号 (10Hz 方波) → 移到 dg_sweep CH2
validate_safety_limit("Time_sequence", FIXED_PARAMS["Time_sequence"])
dg_sweep.setup_square(freq=10.0, amplitude=FIXED_PARAMS["Time_sequence"],
                      offset=0.0, dcycle=50.0, channel=2)
dg_sweep.set_output(True, channel=2)
print(f"时序信号: 10 Hz 方波, {FIXED_PARAMS['Time_sequence']} V (由 dg_sweep CH2 提供)")

# 8. dg_mod 不再使用 CH2 输出时序, 后续用于 RF 开关门控
#    先预设置 CH2 为脉冲模式 (后面 Pump 调制设置 Cell 中具体配置)

# ---- 创建本次运行目录 (简洁命名: MMDD_HHMM_tag) ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# %% Cell 5
# ---- 配置 RF 开关方案（替代任意波） ----
# 方案说明:
#   dg_mod CH1: 100MHz 连续正弦波 (RF 载波)
#   dg_mod CH2: 脉冲方波 (RF 开关门控, 5% 占空比)
#   RF 开关 OUT → AOM: 只在门控高电平时输出 100MHz
#
# 优点:
#   - 100MHz 是纯净正弦波, 非 DAC 合成, 无采样失真
#   - RF 开关隔离度 >50dB, 无漏光
#   - 各频率下的 100MHz 载波品质一致
# =====================================================

# ---- 1) DG4000 CH1: 100MHz 连续正弦波 ----
dg_mod.setup_sine(freq=100e6, amplitude=PUMP_MOD_AMPLITUDE,
                  offset=0.0, phase=0.0, channel=1)
print(f"  100MHz, 幅度 {PUMP_MOD_AMPLITUDE*1000:.0f} mVpp → RF 开关 IN")

# ---- 2) DG4000 CH2: 脉冲门控信号 ----
#    频率 = PUMP_MOD_FREQ (90 kHz), 脉宽 = duty% × 周期
pulse_width = (PUMP_MOD_DUTY / 100.0) / PUMP_MOD_FREQ
print(f"\nCH2: 配置 {PUMP_MOD_FREQ/1e3:.0f} kHz 脉冲门控...")
print(f"  脉宽: {pulse_width*1e6:.2f} μs ({PUMP_MOD_DUTY}% duty)")
dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, amplitude=RF_GATE_AMPLITUDE,
                   offset=RF_GATE_OFFSET, width=pulse_width, channel=2)
print(f"  {PUMP_MOD_FREQ/1e3:.0f} kHz, {RF_GATE_AMPLITUDE:.1f} Vpp, "
      f"offset {RF_GATE_OFFSET:.1f}V → RF 开关 CTRL")

print("\n✅ RF 开关方案配置完成")

# %% Cell 6
# ---- HF2 解调器 0 相位校准 ----
# 校准应在 Pump 光打开、静态磁场条件下进行
# (此时 Z 场输出关闭，不扫场)
# 校准前关闭温控开关，避免温控磁场干扰

# 关闭温控
print("关闭温度开关...")
dg_temp.set_output(False, channel=2)

# 关闭 Z 磁场扫场输出 (DC 0V + 输出关闭)
print("关闭 Z 磁场 (DC 0V, 输出 OFF)...")
dg_sweep.setup_dc(0.0, channel=1)
dg_sweep.set_output(False, channel=1)

# 配置信号输入
sig_in_cfg = SignalInputConfig(
    input_index=0,
    range=HF2_SIGNAL_RANGE,
    ac_coupling=True,
    diff=False,
    impedance=50,
)
demod.configure_signal_input(hfi, sig_in_cfg)
print(f"信号输入已配置: {HF2_SIGNAL_RANGE} V range, AC coupled")

# 配置振荡器 (90 kHz, 匹配 Pump 调制频率)
osc_cfg = OscillatorConfig(
    osc_index=0,
    frequency=HF2_OSC_FREQ,
    source="manual",
)
demod.configure_oscillator(hfi, osc_cfg)
print(f"振荡器已配置: {HF2_OSC_FREQ/1e3:.0f} kHz")

# 配置解调器 0
demod_cfg = DemodulatorConfig(
    demod_index=0,
    enable=True,
    rate=HF2_DEMOD_RATE,
    input_channel=0,
    osc_select=0,
    harmonic=1,
    time_constant=HF2_DEMOD_TC,
    order=HF2_DEMOD_ORDER,
    phase=0.0,
)
actual_rate = demod.configure_demodulator(hfi, demod_cfg)
print(f"解调器 0 已配置: rate={actual_rate:.0f} Sa/s, TC={HF2_DEMOD_TC*1000:.1f} ms")

# 自动相位校准
print("正在进行相位校准...")
calibrated_phase = demod.auto_calibrate_phase(
    hfi, demod_idx=0, tolerance_deg=1.0, max_attempts=5, settle_time=0.2
)
print(f"相位校准完成: {calibrated_phase:.2f}°")

# 验证: 读一组样本查看 X/Y 比值
sample = demod.read_demod_sample(hfi, demod_idx=0)
print(f"校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

time.sleep(1)  # 等待系统稳定

# 恢复温控
print("恢复温度开关...")
dg_temp.set_output(True, channel=2)

# %% Cell 7
# ===== 数据采集: Z 场连续扫场 + HF2 DAQ (触发同步) =====
# 硬件连接: DG4000 CH1 [SYNC] → HF2 辅助输入 1 (auxins/0)
# 流程:
#   1. 等待相位校准后的系统稳定 (气室温度、磁场)
#   2. 启动 Z 场 RAMP 扫场
#   3. APPLy 完成后开启 SYNC 输出
#   4. 关闭温控, 等待磁场干扰消退
#   5. HF2 DAQ 触发采集
#   6. 停止扫场, 恢复温控
# =====================================================

print("=" * 50)
print("开始数据采集 (触发同步)")
print("=" * 50)

try:

    # Step 2: 启动 Z 场 RAMP 扫场 (APPLy 会重置 SYNC, 所以先不开)
    print("[2/6] 启动 Z 场斜坡扫场...")
    dg_sweep.setup_ramp(freq=RAMP_FREQ, amplitude=(RAMP_HIGH - RAMP_LOW),
                        offset=0.0, symmetry=RAMP_SYMMETRY, channel=1)

    # Step 3: APPLy 完成后开启 SYNC 输出
    print("[3/6] 开启 Z 场 DG4000 SYNC 输出...")
    dg_sweep.set_sync_state(True, channel=1)
    time.sleep(0.5)

    # Step 4: 关闭温度开关, 等待温控磁场残余消失
    print("[4/6] 关闭温度开关 (等 2 s 消残余磁场)...")
    dg_temp.set_output(False, channel=2)
    time.sleep(1.0)

    # Step 5: HF2 DAQ 触发采集 (等待 SYNC 上升沿启动)
    print(f"[5/6] HF2 DAQ 触发采集 (aux{DAQ_TRIGGER_CHANNEL}, {DAQ_TRIGGER_LEVEL}V)...")
    aux_path = f"/{MAPPING['lockin_r']['device_id']}/auxins/{DAQ_TRIGGER_CHANNEL}/sample.AuxIn{DAQ_TRIGGER_CHANNEL}"
    daq_cfg = DAQConfig(
        device=MAPPING["lockin_r"]["device_id"],
        trigger_type=1,
        trigger_channel=DAQ_TRIGGER_CHANNEL,
        trigger_level=DAQ_TRIGGER_LEVEL,
        trigger_slope=DAQ_TRIGGER_SLOPE,
        trigger_delay=0.0,
        duration=DAQ_DURATION,
        # 使用 actual_rate 而非硬编码常量，确保网格大小与解调器实际速率匹配
        grid_cols=int(actual_rate * DAQ_DURATION),
        grid_rows=1,
        grid_mode=2,
        signal_paths=["sample.r", "sample.x", "sample.y"],
        extra_paths=[aux_path],
    )

    daq_results = daq.acquire_data(
        hfi, config=daq_cfg, demod_idx=0,
        actual_rate=actual_rate, timeout=DAQ_DURATION + 10.0,
    )
    print(f"    DAQ 采集完成, 共 {len(daq_results)} 路信号")

    for result in daq_results:
        print(f"    {result.signal_name}: {len(result.values)} 点")

    # Step 6: 停止扫场, 恢复温控
    print("[6/6] 停止扫场, 恢复温度开关...")
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_sync_state(False, channel=1)
    dg_temp.set_output(True, channel=2)
    time.sleep(2)

    print("=" * 50)
    print("数据采集完成")
    print("=" * 50)

except Exception as e:
    print(f"采集中断: {e}")
    dg_sweep.set_output(False, channel=1)
    dg_sweep.set_sync_state(False, channel=1)
    dg_temp.set_output(True, channel=2)
    raise

# %% Cell 8
# ---- 解析 DAQ 结果 ----
# 触发时刻 (t=0) = ramp 周期起点 (V=RAMP_LOW)
# 对称性 20%: 前 20% 上升 (-0.5V→+0.5V), 后 80% 下降 (+0.5V→-0.5V)
# 有效数据窗口: t_norm 在 10%~90% (参考已有代码 start_pos/end_pos)

T_ramp = 1.0 / RAMP_FREQ

recorded_data = {}
aux_key = None

for result in daq_results:
    if "auxin" in result.signal_name.lower():
        aux_key = f"aux_{result.signal_name}"
        recorded_data[aux_key] = result.values
        recorded_data[f"{aux_key}_time"] = result.time
    else:
        recorded_data[result.signal_name] = result.values
        recorded_data[f"{result.signal_name}_time"] = result.time

if len(daq_results) > 0:
    t = daq_results[0].time
    s = RAMP_SYMMETRY / 100.0
    t_norm = t / T_ramp  # t=0 = 周期起点 = SYNC 触发

    if s >= 1.0:
        V_z = RAMP_LOW + (RAMP_HIGH - RAMP_LOW) * t_norm
    elif s <= 0.0:
        V_z = RAMP_HIGH - (RAMP_HIGH - RAMP_LOW) * t_norm
    else:
        rising = t_norm % 1.0 < s
        tn = t_norm % 1.0
        V_z = np.where(rising,
            RAMP_LOW + (RAMP_HIGH - RAMP_LOW) * tn / s,
            RAMP_HIGH - (RAMP_HIGH - RAMP_LOW) * (tn - s) / (1.0 - s))

    recorded_data["Z_voltage"] = V_z
    recorded_data["t_norm"] = t_norm % 1.0
    recorded_data["rising_mask"] = (t_norm % 1.0) < s
    # 有效数据窗口: 前 10%~后 10% 裁剪 (参考已有分析代码)
    recorded_data["effective_mask"] = ((t_norm % 1.0) > 0.1) & ((t_norm % 1.0) < 0.9)

np.savez(raw_dir / "scan_data.npz", **recorded_data)
try:
    import pandas as pd
    pd.DataFrame(recorded_data).to_csv(raw_dir / "scan_log.csv", index=False)
except ImportError:
    pass

snapshot = {
    "experiment_type": EXPERIMENT_TYPE, "purpose": PURPOSE,
    "timestamp": timestamp, "z_v_to_nt": Z_V_TO_NT,
    "ramp_low": RAMP_LOW, "ramp_high": RAMP_HIGH,
    "daq_duration": DAQ_DURATION,
    "hf2_demod_tc": HF2_DEMOD_TC, "hf2_demod_rate": HF2_DEMOD_RATE,
    "pump_mod_freq": PUMP_MOD_FREQ,
    "pump_mod_amplitude": PUMP_MOD_AMPLITUDE,
    "pump_mod_duty": PUMP_MOD_DUTY,
}
with open(run_dir / "params.yaml", "w", encoding="utf-8") as f:
    yaml.dump(snapshot, f, default_flow_style=False)

print(f"数据已保存至: {run_dir}")

# %% Cell 9
# ---- 绘制色散曲线 (下降段, 即 80% 区域) ----
# 对称性 20% → 上升段 20%, 下降段 80%
# 取下降段: 电压从 +0.5V 单调下降到 -0.5V, 无折叠
s = RAMP_SYMMETRY / 100.0
t_n = recorded_data["t_norm"]
# 下降段中间 80% (裁剪首尾各 10%, 避开转折区)
fall_start = s + (1.0 - s) * 0.1
fall_end = s + (1.0 - s) * 0.9
mask = (t_n >= fall_start) & (t_n < fall_end)

V = recorded_data["Z_voltage"][mask]  # 单调下降电压
B = V * Z_V_TO_FT                     # 电压 → 磁场

# fig, axes = plt.subplots(2, 1, figsize=(10, 8))

# for signal, label, color in zip(
#     ["sample.x", "sample.y"],
#     ["X (in-phase)", "Y (quadrature)"],
#     ["blue", "orange"],
# ):
#     if signal in recorded_data:
#         data = recorded_data[signal][mask]
#         axes[0].plot(B, data, "-", color=color, label=label, lw=0.8, alpha=0.8)
# axes[0].set_xlabel("Z magnetic field (fT)")
# axes[0].set_ylabel("Demod signal (V)")
# axes[0].set_title("Dispersion curve - X/Y components (falling edge)")
# axes[0].grid(True, alpha=0.3)
# axes[0].legend()

# ax1 = axes[1]
# if "sample.r" in recorded_data:
#     r_data = recorded_data["sample.r"][mask]
#     ax1.plot(B, r_data, "-", color="green", label="R (magnitude)", lw=1)
# ax1.set_xlabel("Z magnetic field (fT)")
# ax1.set_ylabel("R (V)", color="green")
# ax1.tick_params(axis="y", labelcolor="green")
# ax1.grid(True, alpha=0.3)

# if aux_key and aux_key in recorded_data:
#     ax2 = ax1.twinx()
#     aux_data = recorded_data[aux_key][mask]
#     ax2.plot(B, aux_data, "-", color="red", alpha=0.6, lw=0.8, label="SYNC")
#     ax2.set_ylabel("SYNC signal (V)", color="red")
#     ax2.tick_params(axis="y", labelcolor="red")

# lines1, labels1 = ax1.get_legend_handles_labels()
# if aux_key and aux_key in recorded_data:
#     lines2, labels2 = ax2.get_legend_handles_labels()
#     ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")
# else:
#     ax1.legend(loc="upper right")
# ax1.set_title("Dispersion curve - R magnitude (falling edge)")

# plt.tight_layout()
# fig.savefig(results_dir / "dispersion_curve.png", dpi=150, bbox_inches="tight")
# # plt.show()
# print(f"图表已保存: {results_dir / 'dispersion_curve.png'}")

# %% Cell 10
# ===== 灵敏度测量: 噪声采集 (多次平均提高频谱质量) =====
from scipy import signal as scipy_signal
from tqdm import tqdm

# 使用有效窗口内上升段找零交叉点 (无需排序, V 单调递增)
mask = recorded_data["effective_mask"] & recorded_data["rising_mask"]
V_rising = recorded_data["Z_voltage"][mask]
Y_rising = recorded_data["sample.y"][mask]

sign_changes = np.where(np.diff(np.sign(Y_rising)))[0]
if len(sign_changes) > 0:
    idx = sign_changes[len(sign_changes) // 2]
    v1, v2 = float(V_rising[idx]), float(V_rising[idx + 1])
    y1, y2 = float(Y_rising[idx]), float(Y_rising[idx + 1])
    V0 = v1 - y1 * (v2 - v1) / (y2 - y1)
    print(f"零交叉点: V0={V0:.4f}V, B0={V0*Z_V_TO_FT:.0f}fT")
else:
    V0 = 0.0; print("未找到零交叉点, 使用 V0=0")

# 配置解调器为高速率
noise_demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True,
    rate=HF2_NOISE_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_NOISE_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
actual_rate_noise = demod.configure_demodulator(hfi, noise_demod_cfg)
time.sleep(0.3)

print(f"TC={HF2_NOISE_TC*1e6:.0f}μs, Rate={actual_rate_noise} Sa/s")

# 噪声测量时关闭 Z 磁场 (DC 0V + 输出关闭)
print("关闭 Z 磁场 (DC 0V, 输出 OFF)...")
dg_sweep.setup_dc(0.0, channel=1)
dg_sweep.set_output(False, channel=1)
time.sleep(0.5)

# ===== 多次采集，逐次保存到硬盘，避免内存累积 =====
# 每次采集时短暂关闭温控开关，采集后立即恢复，避免长时间温控中断导致温度漂移
fs_noise = float(actual_rate_noise)
noise_dir = raw_dir / "noise_raw"
noise_dir.mkdir(exist_ok=True)

# 记录初始温度作为基准
initial_temp = tec.get_temperature(channel=1)
print(f"初始温度: {initial_temp:.2f} °C")

for i in tqdm(range(NOISE_N_AVG), desc="采集噪声"):
    # 采集前关闭温控 (消除温控磁场对噪声测量的干扰)
    dg_temp.set_output(False, channel=2)
    noise_cfg = DAQConfig(device=MAPPING["lockin_r"]["device_id"],
        trigger_type=0, duration=NOISE_DURATION,
        grid_cols=int(fs_noise * NOISE_DURATION),
        grid_rows=1, grid_mode=2, signal_paths=["sample.y"])
    noise_results = daq.acquire_data(hfi, config=noise_cfg,
        demod_idx=HF2_DEMOD_IDX, actual_rate=fs_noise,
        timeout=NOISE_DURATION + 5.0)
    # 采集完立即恢复温控，等待温度稳定后再进行下一次
    dg_temp.set_output(True, channel=2)
    time.sleep(2)
    y = noise_results[0].values
    # 保存到硬盘后立即释放内存
    np.save(noise_dir / f"noise_{i:04d}.npy", y)
    del y, noise_results
    # 观察温度波动
    # if i == 0 or i == NOISE_N_AVG - 1:
    #     t_now = tec.get_temperature(channel=1)
    #     print(f"  第 {i+1} 次采集后温度: {t_now:.2f} °C (偏差 {t_now - initial_temp:+.3f} °C)")

# ---- 读出保存的文件并累积 PSD ----
psd_sum = None
freq = None
nperseg = int(fs_noise * NOISE_DURATION)

for i in tqdm(range(NOISE_N_AVG), desc="计算 PSD"):
    y = np.load(noise_dir / f"noise_{i:04d}.npy")
    _, psd = scipy_signal.welch(y - np.mean(y), fs=fs_noise,
                                nperseg=nperseg, scaling="density")
    if psd_sum is None:
        freq = _
        psd_sum = psd.copy()
    else:
        psd_sum += psd
    del y, psd

psd_avg = psd_sum / NOISE_N_AVG

# 保存平均结果
np.savez(raw_dir / "noise_data.npz",
         psd_avg=psd_avg, freq=freq,
         fs=fs_noise, n_avg=NOISE_N_AVG)

print(f"噪声采集完成: {NOISE_N_AVG} 次, 分辨率 {freq[1]-freq[0]:.1f} Hz")
raw_file_size = NOISE_N_AVG * int(fs_noise * NOISE_DURATION) * 8 / 1024 / 1024
print(f"  原始数据已保存: {noise_dir}/ (共 {raw_file_size:.1f} MB)")

# ---- 恢复解调器至采集配置 ----
print("恢复解调器至采集配置...")
restore_demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True,
    rate=HF2_DEMOD_RATE, input_channel=0,
    osc_select=0, harmonic=1,
    time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
    phase=calibrated_phase,
)
demod.configure_demodulator(hfi, restore_demod_cfg)
time.sleep(0.2)
print("解调器已恢复 (rate=1000 Sa/s, TC=1ms)")

# %% Cell 11
# ===== 灵敏度分析: Y 色散拟合 + PSD + 合并绘图 =====
from sensitivity_analysis import (
    fit_dispersive, compute_sensitivity, write_run_record,
)

# ---- 1. Y 色散拟合 (下降段, 与绘图一致) ----
s = RAMP_SYMMETRY / 100.0
t_n = recorded_data["t_norm"]
fall_start = s + (1.0 - s) * 0.1
fall_end = s + (1.0 - s) * 0.9
fall_mask = (t_n >= fall_start) & (t_n < fall_end)

V_fit = recorded_data["Z_voltage"][fall_mask]     # 单调下降电压
Y_raw = recorded_data["sample.y"][fall_mask]

# 质量门控阈值 (可根据实验情况调整)
QUALITY_THRESHOLDS = {
    "r_squared_min": 0.85,
    "relative_gamma_uncertainty_max": 0.5,
    "residual_sign_change_ratio_min": 0.0,  # 实测色散残差有平滑趋势，宽松门控
}

fit_result = fit_dispersive(V_fit, Y_raw, Z_V_TO_NT, Z_V_TO_FT,
                            quality_thresholds=QUALITY_THRESHOLDS)

if fit_result.is_valid:
    print(f"Y 色散拟合 [有效]: V0={fit_result.V0:.4f}V, "
          f"dY/dB={fit_result.slope_V_per_fT:.3e} V/fT, B0={fit_result.B0_fT:.0f}fT")
else:
    print(f"Y 色散拟合 [无效]: {'; '.join(fit_result.rejection_reasons)}")
print(f"  线宽 HWHM: gamma={fit_result.gamma_V:.4f}V = {fit_result.gamma_nT:.2f} nT = {fit_result.gamma_fT:.0f} fT")
print(f"  线宽为: f = {fit_result.f_larmor_Hz:.1f} Hz")
print(f"  R^2={fit_result.r_squared:.4f}, RMSE={fit_result.rmse:.2e}, "
      f"gamma 相对不确定度={fit_result.relative_gamma_uncertainty:.1%}")

# 构造用于绘图的拟合曲线
Vp = np.linspace(V_fit[0], V_fit[-1], 200)
if fit_result.A != 0 or fit_result.gamma_V > 0:
    from sensitivity_analysis.fitting import _dispersive
    Yp = _dispersive(Vp, *fit_result.popt)
else:
    Yp = np.array([])
Bp = Vp * Z_V_TO_FT

# ---- 2. 灵敏度: 加载平均 PSD 计算灵敏度谱 ----
noise_data = np.load(raw_dir / "noise_data.npz")
freq = noise_data["freq"]
psd = noise_data["psd_avg"]                    # 平均 PSD
n_avg = int(noise_data["n_avg"])               # 采集次数
freq_resolution = float(freq[1] - freq[0])

sens_result = compute_sensitivity(
    psd, freq,
    slope_V_per_fT=fit_result.slope_V_per_fT,
    f_larmor_Hz=fit_result.f_larmor_Hz,
)

print(f"加载平均 PSD: {n_avg} 次平均, 分辨率 {freq_resolution:.1f} Hz")
if fit_result.is_valid:
    print(f"平坦段 ({sens_result.flat_fmin:.0f}-{sens_result.flat_fmax:.0f} Hz) 中位数: {sens_result.sens_flat:.0f} fT/√Hz")
else:
    print(f"平坦段 ({sens_result.flat_fmin:.0f}-{sens_result.flat_fmax:.0f} Hz) 中位数: {sens_result.sens_flat:.0f} fT/√Hz [拟合无效, 灵敏度不可靠!]")

# ---- 3. 记录结果 ----
params_dict = {
    "Pump_laser_power": FIXED_PARAMS["Pump_laser_power"],
    "Probe_laser_power": FIXED_PARAMS["Probe_laser_power"],
    "X_magnetic_field": FIXED_PARAMS["X_magnetic_field"],
    "Y_magnetic_field": FIXED_PARAMS["Y_magnetic_field"],
    "main_magnetic_field": FIXED_PARAMS["main_magnetic_field"],
    "temperature": FIXED_PARAMS["temperature"],
    "PUMP_MOD_DUTY": PUMP_MOD_DUTY,
    "PUMP_MOD_AMPLITUDE": PUMP_MOD_AMPLITUDE,
}
summary_dir = project_root / "data" / EXPERIMENT_TYPE
write_run_record(
    run_dir, params_dict, fit_result, sens_result,
    summary_path=summary_dir / "run_summary.csv",
    timestamp=timestamp, run_tag=RUN_TAG,
    n_avg=n_avg, freq_resolution_Hz=freq_resolution,
)

# ---- 4. 合并绘图 (下降段, 电压单调, 按时序画图) ----
V_all = recorded_data["Z_voltage"][fall_mask]
B_all = V_all * Z_V_TO_FT

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 8), height_ratios=[1.2, 1])

ax1.plot(B_all, recorded_data["sample.x"][fall_mask], "-", lw=0.8, label="X")
ax1.plot(B_all, recorded_data["sample.y"][fall_mask], "-", lw=0.8, label="Y")
ax1.plot(B_all, recorded_data["sample.r"][fall_mask], "-", lw=0.8, label="R")
if len(Yp) > 0:
    label_fit = f"DisFit ({fit_result.slope_V_per_fT:.3e} V/fT, R^2={fit_result.r_squared:.3f})"
    ax1.plot(Bp, Yp, "k--", lw=1.5, label=label_fit)
ax1.axvline(fit_result.B0_fT, color="gray", ls=":", alpha=0.5)
ax1.set_xlabel("Z magnetic field (fT)"); ax1.set_ylabel("Signal (V)")
title1 = "Dispersion curve (falling edge)"
if not fit_result.is_valid:
    title1 += " [INVALID]"
ax1.set_title(title1); ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)

ax2.plot(sens_result.freq, sens_result.sens_corrected, "steelblue", lw=0.8,
         alpha=0.6, label="Sensitivity corrected")
# 高亮平坦段
fm = sens_result.flat_mask
ax2.plot(sens_result.freq[fm], sens_result.sens_corrected[fm], "darkgreen",
         lw=1.5, alpha=0.9,
         label=f"Flat: {sens_result.flat_fmin:.0f}-{sens_result.flat_fmax:.0f} Hz")
ax2.plot(sens_result.freq, sens_result.sens_raw, "orange", lw=0.5, alpha=0.4,
         label="Raw (uncorrected)")
if sens_result.sens_flat > 0:
    ax2.axhline(sens_result.sens_flat, color="darkgreen", ls="--", alpha=0.7,
                label=f"Sensitivity = {sens_result.sens_flat:.0f} fT/√Hz")
ax2.axvline(fit_result.f_larmor_Hz, color="red", ls=":", alpha=0.5,
            label=f"HWHM = {fit_result.f_larmor_Hz:.0f} Hz")
ax2.axvline(sens_result.flat_fmax, color="gray", ls=":", alpha=0.4, lw=0.8)
ax2.legend(fontsize=8)
ax2.set_xlabel("Frequency (Hz)"); ax2.set_ylabel("Sensitivity (fT/√Hz)")
title2 = "Sensitivity spectrum (flat region median)"
if not fit_result.is_valid:
    title2 += " [INVALID]"
ax2.set_title(title2); ax2.grid(True, alpha=0.3, which="both")
ax2.set_xlim(0.5, max(fit_result.f_larmor_Hz * 2, 500))
ax2.set_ylim(5e1, 5e6)
ax2.set_yscale("log")

plt.tight_layout()
fig.savefig(results_dir / "full_analysiswithoutpump.png", dpi=150, bbox_inches="tight")
plt.show()
print(f"分析图已保存: {results_dir / 'full_analysiswithoutpump.png'}")

# %% [markdown] Cell 12
# ## 灵敏度分析
#
# $$\delta B(f) = \frac{\sqrt{\text{PSD}_Y(f)}}{|dY/dB|} \quad [\text{fT}/\sqrt{\text{Hz}}]$$
#
# - $dY/dB$: 色散曲线零交叉点斜率 (V/fT)
# - $\text{PSD}_Y$: Y 信号功率谱密度 (V²/Hz)
#
# ### 响应曲线校正
#
# 原子自旋响应为洛伦兹型，等效一阶低通滤波器：
#
# $$\delta B_{\text{corr}}(f) = \delta B(f) \times \sqrt{1 + (f/f_{\text{HWHM}})^2}$$
#
# 在 $\text{HWHM}$ 以下校正因子接近 1，以上则放大灵敏度。
#
# ### 灵敏度报告值
#
# 对校正后灵敏度谱 `sens_corrected` 排序，取最小的 100 个点的中位数。这种方法直接取谱上最优统计值，不受低频 1/f 噪声和高频校正放大区的影响。
#
# ### 流程
#
# 1. **采集噪声**: 切换到 TC=10μs, 关闭 Z 磁场, 采 1s Y 噪声, 重复 `NOISE_N_AVG` 次
# 2. **PSD 平均**: 对各次 Welch 法求 PSD 后等权平均, 降低频谱涨落 ($\propto 1/\sqrt{N}$)
# 3. **拟合**: 对色散曲线 Y 做色散拟合, 得 $dY/dB$
# 4. **灵敏度**: $\delta B = \sqrt{\text{PSD}_{\text{avg}}} / |dY/dB|$
# 5. **响应校正**: 补偿有限线宽的高频滚降
#
# ### 参数
#
# - `NOISE_N_AVG`: 噪声采集次数 (在 Cell 2 中设置, 默认 10)
# - `Z_V_TO_NT`: 磁场校准系数 (在 Cell 2 中设置)

# %% Cell 13
# ========== 安全断开（仅断开 TEC，其余设备保持连接） ==========
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")
