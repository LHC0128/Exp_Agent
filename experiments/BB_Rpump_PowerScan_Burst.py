# %% [markdown] Cell 0
# # Bell-Bloom 周期泵浦 R_pump_eff 等效泵浦速率测量
#
# **原理**：利用 Bell-Bloom 磁力仪测量周期泵浦条件下的等效泵浦速率 R_pump_eff。
# 在固定 duty cycle 下，扫描 Pump 光功率 DC 电压，使用 DG4000 脉冲串 (BURSt) 模式输出
# N 个周期的 Bell-Bloom pump 脉冲波形，然后回到 idle (pump off)。
# 在 Z 方向主磁场固定的情况下，原子自旋在 pump on 期间累积进动相干性，
# pump off 后通过暗弛豫衰减；拟合 build-up 与 dark decay 的时间常数即可得到：
#
# - Gamma_BB = 1 / tau_BB     (build-up 速率)
# - Gamma_dark = 1 / tau_dark (暗弛豫速率)
# - R_pump_eff = Gamma_BB - Gamma_dark  (等效周期泵浦速率)
#
# **信号链路**：
# - 主磁场：GS200 恒流 (≈ 9.31 mA，对应 Larmor 频率 ≈ 90 kHz)
# - Pump 光功率 (扫描变量)：DG900 (DG9Q280100002)
#   - CH1 (`Pump_laser_power`): DC 0.02 ~ 0.2 V → PUMP_POWER 列表逐点扫描
# - Pump 调制 (固定 TTL 门控)：DG4000 (DG4E222800868) — TTL high/low 不可作为扫描变量
#   - CH1: 100 MHz CW → RF 开关 IN (AOM 载波)
#   - CH2: 90 kHz 脉冲 (FIXED_DUTY_CYCLE, 5V TTL high / 0V TTL low) Burst N cycles
#          → RF 开关 CTRL → AOM → 周期启亮 pump 光
#          ⚠ high/low 固定为 5V/0V 数字门控，pump 强度由 DG900 CH1 决定
#   - CH2 同步输出 → 示波器触发通道
# - Probe 光 (固定)：DG900 CH2 (`Probe_laser_power`)
# - 信号采集：SDS 示波器
#   - CH1: PD (Bell-Bloom 信号)
#   - CH4: DG4000 CH2 同步输出 (触发，BURSt 窗口)
#
# **采集时序** (单次 shot, 触发点在屏幕中心, 半屏对称):
# 1. PRE_BASELINE_TIME_S (trigger 之前): pump OFF, 等待稳定基线
# 2. burst (trigger 上升沿, t=0): BURST_CYCLES 个 pump 脉冲周期, 累积进动相干
# 3. POST_DARK_TIME_S (trigger 之后): pump OFF, 暗弛豫衰减
#
# **触发**：CH4 = DG4000 CH2 同步输出, RISing 沿 → t=0 = pump 打开
#
# **扫描**：PUMP_POWER 列表中每个 DC 电压 → 一次 shot → npz

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
from datetime import datetime
import matplotlib
matplotlib.use("TkAgg")  # 交互式后端
import matplotlib.pyplot as plt
from tqdm import tqdm

# 设备库
from gs200 import GS200Instrument
from lab_workflows.devices import create_signal_generator
from tec_controller import TECInstrument
from lab_workflows.steps import configure_temperature_control
from sds_acquisition import (
    SDSInstrument, SDSAcquisition,
    AcquisitionConfig, ChannelConfig, TriggerConfig,
)

print("所有库导入成功")

# %% Cell 2
# ========== 加载配置文件 ==========
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
    if lo is None or hi is None:
        return value
    if value < lo or value > hi:
        raise ValueError(f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]")
    return value


# ========== 实验标识 ==========
EXPERIMENT_TYPE = "bb_rpump_power_scan_burst"
RUN_TAG = "rpump_eff"

# ========== 扫描变量: Pump 光功率 DC 电压 ==========
# PUMP_POWER 是 DG900 CH1 (`Pump_laser_power`) 的 DC 输出列表
# 周期泵浦强度由列表逐点写入 DG900 CH1，DG4000 CH2 仅作 TTL 门控
PUMP_POWER = np.linspace(0.02, 0.2, 10).tolist()  # DG900 CH1 DC 电压 (V)

# ========== Bell-Bloom 周期泵浦 burst 参数 ==========
MODULATION_FREQUENCY_HZ = 90000.0        # Pump 调制频率 (Hz) = Ω_L/2π (Larmor 频率)
FIXED_DUTY_CYCLE = 5.0                   # 每个 pump 脉冲周期的固定占空比 (%)
BURST_CYCLES = 1000                      # burst 内 pump 脉冲周期数
PRE_BASELINE_TIME_S = 0.005              # burst 前的稳定基线时间 (s)
POST_DARK_TIME_S = 0.020                 # burst 后的暗弛豫采集时间 (s)
BURST_BURST_PERIOD_S = 0.5               # burst 触发最小间隔 (s)，避免重叠
# [经验] 1000 cycles @ 90 kHz ≈ 11.1 ms burst，与 tau_BB (几 ms) 量级匹配

# ========== 重复采集 ==========
ACQ_REPEATS = 10                            # 每个功率点采集次数, PC 端 mean() 平均
# [经验] N 次独立采样的 SNR 提升 = sqrt(N); ACQ_REPEATS=5 ≈ 2.24× SNR 改善

# ========== Z 方向主磁场 ==========
Z_FIELD_SETPOINT = 9.31                  # 主磁场电流 (mA)，对应 Ω_L/2π ≈ 90 kHz

# ========== RF 开关门控电平 (DG4000 CH2 — 固定 TTL, 不扫描) ==========
# [经验] RF 开关 CTRL 是数字门控，TTL 5V high / 0V low 不可作为 pump 强度变量
# pump 强度由 DG900 CH1 (Pump_laser_power) 决定；DG4000 CH2 只决定 on/off 时序
PUMP_RF_GATE_HIGH_V = 5.0                # TTL 高电平 (V) — 与原 RF 开关方案一致
PUMP_RF_GATE_LOW_V = 0.0                 # TTL 低电平 (V) = burst 后 idle level = pump off
PUMP_AMPLITUDE_TTL = PUMP_RF_GATE_HIGH_V - PUMP_RF_GATE_LOW_V  # 5 Vpp
PUMP_OFFSET_TTL = (PUMP_RF_GATE_HIGH_V + PUMP_RF_GATE_LOW_V) / 2.0  # 2.5 V

# ========== Probe 光 ==========
PROBE_POWER = 0.1                       # Probe 光 DC 功率 (V) — DG900 CH2 (固定)

# ========== AOM 载波 (DG4000 CH1) ==========
AOM_CARRIER_FREQ_HZ = 100.0e6            # AOM 载波频率 (Hz)
AOM_CARRIER_AMPLITUDE_V = 0.18           # AOM 载波幅度 (Vpp)，受 Pump_modulation 安全限值约束
AOM_CARRIER_OFFSET_V = 0.0               # AOM 载波 DC 偏置 (V)

# ========== HF2 / SDS 暂不依赖 ==========
# 本实验主检测链路为 SDS 示波器；HF2 仅作为可选监控（按需开启）
USE_HF2_MONITOR = False                  # True = 启用 HF2 监控 demod 幅度

# ========== SDS 示波器配置 ==========
SDS_SAMPLE_RATE = 1.0e6                  # 采样率 (Sa/s)，>>180 kHz 以还原 90 kHz Larmor 信号
SDS_PD_CHANNEL = 1                       # Bell-Bloom PD 信号输入通道
SDS_TRIG_CHANNEL = 4                     # DG4000 CH2 同步输出 → 触发
SDS_VOLT_SCALE_PD = 0.1                  # PD 通道初始垂直刻度 (V/div)
SDS_VOLT_SCALE_TRIG = 2.0                # 触发通道垂直刻度 (V/div)
SDS_TRIG_LEVEL = 2.5                     # 触发阈值 (V)，TTL 中值
SDS_TRIG_SLOPE = "RISing"                # CH4 同步上升沿 = 周期 pump 打开, t=0
VERT_DIVS = 4                            # 有效显示格数 (SDS 满量程 ±4 div)
SCALE_MIN = 0.005                        # 最小 V/div
SCALE_MAX = 10.0                         # 最大 V/div

# ========== Temp_Switch (DG9000 Pro Ch 5V=ON/0V=OFF, 采集期间必须关闭) ==========
# [经验] 温控通断引入的磁场会干扰 Bell-Bloom 原子信号, 采集前必须 set 0V, 采集后恢复 5V
# [重要] Temp_Switch 的 DG900 型号只在 mapping.yaml 的 model 字段配置
TEMP_SWITCH_CHANNEL = MAPPING.get("Temp_Switch", {}).get("channel", None)
TEMP_SWITCH_OFF_V = 0.0                  # 关闭温控时的 TTL 电平
TEMP_SWITCH_ON_V = 5.0                   # 开启温控时的 TTL 电平
TEMP_SWITCH_OFF_DELAY_S = 0.1            # 关闭温控后等磁场衰减
TEMP_SWITCH_ON_DELAY_S = 2.0             # 恢复温控后等回路稳定

# ========== 温度控制 ==========
TEC_TEMPERATURE = 100.0                  # 气室温度 (°C)

# ---- 计算 burst 时长与采集总时长 ----
BURST_DURATION_S = BURST_CYCLES / MODULATION_FREQUENCY_HZ
# 总采集时间要装下: pre_baseline (trigger 前) + burst + post_dark (trigger 后)
# 由于 trigger 在屏幕中心 (delay=0), 半屏需覆盖两边较大者:
#   pre 半屏 ≈ PRE_BASELINE_TIME_S
#   post 半屏 ≈ BURST_DURATION_S + POST_DARK_TIME_S
TOTAL_ACQ_S = 2.0 * max(PRE_BASELINE_TIME_S,
                        BURST_DURATION_S + POST_DARK_TIME_S)
# 触发点放在屏幕水平中心 (delay = 0), 这样 burst 上升沿位于中央, 两侧都有前/后波形
SDS_TIMEBASE_DELAY_S = 0.0

# ========== 安全校验 ==========
validate_safety_limit("temperature", TEC_TEMPERATURE)
validate_safety_limit("main_magnetic_field", Z_FIELD_SETPOINT)
validate_safety_limit("Pump_modulation", AOM_CARRIER_AMPLITUDE_V)
validate_safety_limit("Probe_laser_power", PROBE_POWER)
for v in PUMP_POWER:
    validate_safety_limit("Pump_laser_power", v)

print(f"实验类型: {EXPERIMENT_TYPE}")
print(f"Burst pump: {MODULATION_FREQUENCY_HZ/1000:.0f} kHz × {BURST_CYCLES} cycles "
      f"= {BURST_DURATION_S*1000:.2f} ms")
print(f"占空比 (FIXED_DUTY_CYCLE): {FIXED_DUTY_CYCLE}%")
print(f"扫描功率电压点数: {len(PUMP_POWER)} ({PUMP_POWER[0]:.3f} "
      f"~ {PUMP_POWER[-1]:.3f} V)")
print(f"采集窗口: pre={PRE_BASELINE_TIME_S*1000:.1f}ms + "
      f"burst={BURST_DURATION_S*1000:.2f}ms + post={POST_DARK_TIME_S*1000:.1f}ms "
      f"= {TOTAL_ACQ_S*1000:.1f}ms")
print(f"示波器: {SDS_SAMPLE_RATE/1e3:.0f} kSa/s, "
      f"timebase_delay={SDS_TIMEBASE_DELAY_S*1000:.2f}ms")
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

    # ---- 安全：关闭其他磁场信号发生器 (本实验仅需要主磁场) ----
    for key in ["X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "rf_coil",
                "X_magnetic_field_AM", "Y_magnetic_field_AM"]:
        cfg = MAPPING.get(key)
        if cfg:
            try:
                dev = create_signal_generator(cfg["resource"], channel=cfg["channel"])
                dev.connect()
                dev.set_output(False)
                devices[f"safe_{key}"] = dev
                print(f"  {key} CH{cfg['channel']} 已连接并关闭 (BB R_pump 不使用)")
            except Exception as e:
                print(f"  ⚠ 跳过 {key}: {e}")

    # ---- DG4000: Pump 调制 (CH1 AOM 载波 + CH2 门控 Burst) ----
    pump_mod_cfg = MAPPING["Pump_modulation"]
    time_seq_cfg = MAPPING["Time_sequence"]
    assert pump_mod_cfg["resource"] == time_seq_cfg["resource"], \
        "Pump_modulation/Time_sequence 不在同一台 DG4000"
    dg_rf = create_signal_generator(pump_mod_cfg["resource"])
    dg_rf.connect()
    print(f"DG4000 RF 开关已连接: {dg_rf.idn()}")
    dg_rf.set_ref_clock_source("EXTernal")
    # [经验] 配置前先重置 burst/mod 状态，避免继承其他实验模式
    for ch in [1, 2]:
        dg_rf.set_burst_state(False, channel=ch)
        dg_rf.set_mod_state(False, channel=ch)
    devices["dg_rf"] = dg_rf

    # ---- DG900: Pump DC + Probe 光 (DG9Q280100002) ----
    pump_cfg = MAPPING["Pump_laser_power"]
    probe_cfg = MAPPING["Probe_laser_power"]
    assert pump_cfg["resource"] == probe_cfg["resource"], "Pump/Probe 不在同一 DG900"
    dg_laser = create_signal_generator(pump_cfg["resource"], channel=pump_cfg["channel"])
    dg_laser.connect()
    print(f"DG900 已连接: {dg_laser.idn()}")
    dg_laser.setup_dc(PUMP_POWER[0], channel=pump_cfg["channel"])
    dg_laser.setup_dc(PROBE_POWER, channel=probe_cfg["channel"])
    print(f"  Pump DC: {PUMP_POWER[0]} V 初始 (CH{pump_cfg['channel']}, "
          f"扫描时由 PUMP_POWER 列表覆盖), "
          f"Probe: {PROBE_POWER} V (CH{probe_cfg['channel']})")
    devices["dg_laser"] = dg_laser

    # ---- TEC103: 温度控制器 ----
    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    try:
        tec.connect()
        print(f"TEC103 已连接 @ {tec_cfg['resource']}")
    except Exception as exc:
        print(f"[警告] TEC103 连接失败：{exc}。实验继续，由外部软件负责温控。")
        tec = None
    devices["tec"] = tec

    # ---- SDS 示波器 ----
    scope_cfg = MAPPING["scope_waveform"]
    sds_inst = SDSInstrument(scope_cfg["resource"])
    sds_inst.connect()
    acquirer = SDSAcquisition(sds_inst)
    print(f"SDS 示波器已连接: {sds_inst.idn()}")
    devices["sds_inst"] = sds_inst
    devices["acquirer"] = acquirer

    # ---- 可选: Temp_Switch（型号由 mapping.yaml 决定）----
    temp_sw_cfg = MAPPING.get("Temp_Switch")
    if temp_sw_cfg:
        # 工厂按 model 选择驱动，不从 resource 字符串推断型号。
        dg_temp_sw = create_signal_generator(temp_sw_cfg["resource"], channel=temp_sw_cfg["channel"])
        dg_temp_sw.connect()
        dg_temp_sw.setup_dc(TEMP_SWITCH_ON_V, channel=temp_sw_cfg["channel"])  # 5V: 温控 ON
        print(f"  Temp_Switch CH{temp_sw_cfg['channel']}: {TEMP_SWITCH_ON_V}V (温控 ON, DG900)")
        devices["dg_temp_sw"] = dg_temp_sw

    # ---- 可选: HF2 LIA (监控) ----
    if USE_HF2_MONITOR:
        from lockin_amplifier import HF2Instrument, demod, SignalInputConfig, DemodulatorConfig
        lia_cfg = MAPPING.get("lockin_r") or MAPPING.get("lockin_xy")
        hfi = HF2Instrument(
            host=lia_cfg["host"], port=lia_cfg["port"],
            api_level=1, device_id=lia_cfg["device_id"],
        )
        hfi.connect()
        hfi.set_extclk(True)
        print(f"HF2 已连接: {lia_cfg['device_id']}")
        devices["hfi"] = hfi

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
# ========== 初始值设置 & 创建运行目录 ==========
tec = devices["tec"]
gs = devices["gs200"]
dg_rf = devices["dg_rf"]
sds_inst = devices["sds_inst"]
acquirer = devices["acquirer"]

# ---- 1. 温度控制 (等待稳定 ±1°C) ----
validate_safety_limit("temperature", TEC_TEMPERATURE)
configure_temperature_control(
    tec, TEC_TEMPERATURE, tolerance_c=1.0, stable_reads=1
)

# ---- 2. 主磁场 ----
gs.set_current(Z_FIELD_SETPOINT / 1000.0)  # mA → A
gs.set_output(True)
print(f"主磁场: {Z_FIELD_SETPOINT} mA (对应 Larmor ≈ {MODULATION_FREQUENCY_HZ/1000:.0f} kHz)")

# ---- 3. DG4000 CH1: 100 MHz AOM 载波 (CW 持续输出) ----
validate_safety_limit("Pump_modulation", AOM_CARRIER_AMPLITUDE_V)
dg_rf.apply_wave(
    "SINusoid",
    channel=1,
    freq=AOM_CARRIER_FREQ_HZ,
    amp=AOM_CARRIER_AMPLITUDE_V,
    offset=AOM_CARRIER_OFFSET_V,
    phase=0.0,
)
dg_rf.set_output(True, channel=1)
print(f"CH1: {AOM_CARRIER_FREQ_HZ/1e6:.0f} MHz 正弦, {AOM_CARRIER_AMPLITUDE_V*1000:.0f} mVpp "
      f"→ AOM 载波")

# ---- 4. DG4000 CH2: 周期泵浦 Burst 门控 (固定 TTL high/low，不扫描) ----
# CH2 输出 90 kHz 脉冲 (5V TTL 高/0V TTL 低) → RF 开关 CTRL → AOM → 周期启亮 pump 光
# [经验] RF 开关 CTRL 是数字门控，high/low 都是固定的 (TTL 5V/0V)；
#       真正控制 pump 强度的是 DG900 CH1 (`Pump_laser_power`)，由 PUMP_POWER 列表逐点扫描
# burst N cycles 后输出回到 idle 位置 = low level = pump off
# [经验] 配置前先重置 CH2，避免继承其他实验的 burst/mod 状态
dg_rf.set_burst_state(False, channel=2)
dg_rf.set_mod_state(False, channel=2)
dg_rf.apply_wave(
    "PULSe",
    channel=2,
    freq=MODULATION_FREQUENCY_HZ,
    amp=PUMP_AMPLITUDE_TTL,
    offset=PUMP_OFFSET_TTL,
)
dg_rf.set_pulse_dcycle(FIXED_DUTY_CYCLE, channel=2)
# 固定 TTL 电平: 高 5V, 低 0V (与原 RF 开关方案一致) — 这是数字门控，不是 pump 强度
dg_rf.set_high_level(PUMP_RF_GATE_HIGH_V, channel=2)
dg_rf.set_low_level(PUMP_RF_GATE_LOW_V, channel=2)
# [经验] burst 模式手动触发，burst_period 设为大于总采集时间，避免 burst 重叠
dg_rf.set_burst_mode("TRIGgered", channel=2)
dg_rf.set_burst_ncycles(BURST_CYCLES, channel=2)
dg_rf.set_burst_period(BURST_BURST_PERIOD_S, channel=2)
dg_rf.set_burst_trigger_source("INTernal", channel=2)
dg_rf.set_burst_state(True, channel=2)
# 同步输出 (高电平 = burst 期间) → 用于触发 SDS
dg_rf.set_sync_state(True, channel=2)
dg_rf.set_sync_polarity("POSitive", channel=2)
dg_rf.set_output(True, channel=2)
print(f"CH2: {MODULATION_FREQUENCY_HZ/1000:.0f} kHz 脉冲, "
      f"占空比 {FIXED_DUTY_CYCLE}%, "
      f"high={PUMP_RF_GATE_HIGH_V:.1f}V / low={PUMP_RF_GATE_LOW_V:.1f}V "
      f"(固定 TTL, 非扫描变量)")
print(f"  Burst: TRIGgered, NCYCles={BURST_CYCLES}, "
      f"period={BURST_BURST_PERIOD_S*1000:.0f}ms")
print(f"  Sync: POSitive (→ 示波器触发通道 CH{SDS_TRIG_CHANNEL})")
print(f"  Pump 强度扫描: 由 DG900 CH1 (`Pump_laser_power`) 提供, 每点 PUMP_POWER[i]")

# ---- 5. 创建运行目录 ----
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"\n运行目录: {run_dir}")

# ---- 6. 示波器通道配置 (一次构建，按需更新 PD 档位) ----
def build_scope_config(pd_scale, sync_scale=SDS_VOLT_SCALE_TRIG):
    """构建 SDS 采集配置：CH1=PD (BB 信号), CH2=DG4000 同步输出。"""
    channels = []
    for ch_num in [1, 2, 3, 4]:
        if ch_num == SDS_PD_CHANNEL:
            channels.append(ChannelConfig(
                number=ch_num, enabled=True, scale=pd_scale,
                offset=0.0, coupling="DC", impedance="ONEMeg", probe=1.0,
            ))
        elif ch_num == SDS_TRIG_CHANNEL:
            channels.append(ChannelConfig(
                number=ch_num, enabled=True, scale=sync_scale,
                offset=0.0, coupling="DC", impedance="ONEMeg", probe=1.0,
            ))
        else:
            channels.append(ChannelConfig(number=ch_num, enabled=False))
    return AcquisitionConfig(
        sampling_rate=SDS_SAMPLE_RATE,
        sampling_time=TOTAL_ACQ_S,
        acquire_type="NORMal",
        acquire_delay=0.1,
        channels=channels,
        trigger=TriggerConfig(
            mode="NORMal",
            source=f"C{SDS_TRIG_CHANNEL}",
            type="EDGE",
            slope=SDS_TRIG_SLOPE,
            level=SDS_TRIG_LEVEL,
        ),
        timebase_delay=SDS_TIMEBASE_DELAY_S,
    )


# ---- 7. 保存实验配置快照 ----
config_snapshot = {
    "experiment_type": EXPERIMENT_TYPE,
    "timestamp": timestamp,
    "run_tag": RUN_TAG,
    "burst_pump": {
        "modulation_freq_Hz": MODULATION_FREQUENCY_HZ,
        "fixed_duty_cycle_pct": FIXED_DUTY_CYCLE,
        "burst_cycles": BURST_CYCLES,
        "burst_duration_s": BURST_DURATION_S,
        "burst_period_s": BURST_BURST_PERIOD_S,
        "pre_baseline_s": PRE_BASELINE_TIME_S,
        "post_dark_s": POST_DARK_TIME_S,
        "rf_gate_high_V": PUMP_RF_GATE_HIGH_V,
        "rf_gate_low_V": PUMP_RF_GATE_LOW_V,
        "burst_trigger_source": "INTernal",
        "burst_mode": "TRIGgered",
    },
    "pump_power_scan": {
        "pump_power_voltage_list_V": PUMP_POWER,
        "control_channel": "DG900 CH1 (`Pump_laser_power` 通道, DC 输出)",
        "rf_gate_role": "DG4000 CH2 (固定 TTL 5V high / 0V low, 不扫描)",
    },
    "main_field": {
        "z_field_setpoint_mA": Z_FIELD_SETPOINT,
        "larmor_freq_Hz": MODULATION_FREQUENCY_HZ,
    },
    "probe": {
        "probe_power_V": PROBE_POWER,
        "control_channel": "DG900 CH2 (`Probe_laser_power` 通道, 固定 DC)",
    },
    "aom_carrier": {
        "freq_Hz": AOM_CARRIER_FREQ_HZ,
        "amplitude_Vpp": AOM_CARRIER_AMPLITUDE_V,
        "offset_V": AOM_CARRIER_OFFSET_V,
    },
    "scope": {
        "sample_rate_Hz": SDS_SAMPLE_RATE,
        "timebase_delay_s": SDS_TIMEBASE_DELAY_S,
        "total_acq_s": TOTAL_ACQ_S,
        "pd_channel": SDS_PD_CHANNEL,
        "trig_channel": SDS_TRIG_CHANNEL,
        "trig_level_V": SDS_TRIG_LEVEL,
        "trig_slope": SDS_TRIG_SLOPE,
        "volt_scale_pd_V_per_div": SDS_VOLT_SCALE_PD,
        "memory_depth": "100K",
        "actual_sample_rate_Hz_populated_after_apply_config": True,
    },
    "temperature_C": TEC_TEMPERATURE,
}
config_path = run_dir / "experiment_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config_snapshot, f, default_flow_style=False, allow_unicode=True)
print(f"实验配置已保存: {config_path}")
print("初始值设置完成")

# %% Cell 5
# ========== 示波器初始配置 ==========
scope_cfg = build_scope_config(pd_scale=SDS_VOLT_SCALE_PD)
acquirer.apply_config(scope_cfg)
# sds_inst.set_memory_depth("100K")
time.sleep(0.1)

# 仅打印审计信息, 不当作硬断言使用
actual_pts = int(sds_inst.get_actual_points())
actual_sample_rate = float(sds_inst.get_sampling_rate())
actual_timebase_scale = float(sds_inst.query(":TIMebase:SCALe?"))
actual_capture_s = actual_pts / actual_sample_rate if actual_sample_rate > 0 else 0.0

print(f"示波器: timebase={actual_timebase_scale*1e3:.2f} ms/div, "
      f"trigger=CH{SDS_TRIG_CHANNEL} {SDS_TRIG_SLOPE} @ {SDS_TRIG_LEVEL}V")
print(f"  期望 rate={SDS_SAMPLE_RATE/1e3:.0f} kSa/s × "
      f"total_time={TOTAL_ACQ_S*1000:.2f} ms ≈ "
      f"{int(SDS_SAMPLE_RATE*TOTAL_ACQ_S)} 点")
print(f"  实测 rate={actual_sample_rate/1e3:.1f} kSa/s × "
      f"capture={actual_capture_s*1000:.2f} ms = {actual_pts} 点")

# 把审计信息写入 yaml 备查
config_snapshot["scope"]["actual_sample_rate_Hz"] = actual_sample_rate
config_snapshot["scope"]["actual_timebase_scale_s_per_div"] = actual_timebase_scale
config_snapshot["scope"]["actual_points"] = actual_pts
config_snapshot["scope"]["actual_capture_duration_s"] = actual_capture_s
config_snapshot["scope"].pop("actual_sample_rate_Hz_populated_after_apply_config", None)
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config_snapshot, f, default_flow_style=False, allow_unicode=True)
print(f"  实验配置 (含实测审计) 已更新: {config_path.name}")


# ========== 自适应档位辅助函数 ==========
def auto_scale_signal(waveform, current_scale):
    """根据信号峰值双向自适应调整垂直档位。
    - 信号 < 40% 满格 → scale/=2 (放大)
    - 信号 > 90% 满格 → scale*=2 (防削顶)
    返回 (new_scale, changed)
    """
    abs_max = float(np.max(np.abs(waveform)))
    full_scale = current_scale * VERT_DIVS
    new_scale = current_scale
    if abs_max < 0.4 * full_scale and current_scale > SCALE_MIN * 2:
        new_scale = current_scale / 2
    elif abs_max > 0.9 * full_scale and current_scale < SCALE_MAX / 2:
        new_scale = current_scale * 2
    return new_scale, (new_scale != current_scale)


def acquire_single_shot(pump_power_v, current_scale, scope_cfg_ref):
    """执行单次 burst pump shot 采集。

    温控开关策略：
      - 采集开始前: Temp_Switch = 0V, 等 0.3s 让温控磁场衰减, 避免干扰 Bell-Bloom 信号
      - 采集结束后: Temp_Switch = 5V, 等 1.0s 让温控回路重新稳定 (异常退出也会恢复)

    Parameters
    ----------
    pump_power_v : float
        本次 shot 的 Pump 光功率 DC 电压, 写入 DG900 CH1 (Pump_laser_power)。
        DG4000 CH2 (RF 开关门控) 的 TTL 高/低电平在 Cell 4 已固定, 不再修改。

    Returns
    -------
    dict with keys:
        time_s, signal_v, pump_power_v, scale_used, t_burst_start_s, t_burst_end_s
    """
    # 1. 设置 Pump 光功率 DC 电压 (DG900 CH1 `Pump_laser_power` 通道)
    validate_safety_limit("Pump_laser_power", pump_power_v)
    dg_laser.setup_dc(pump_power_v, channel=pump_cfg["channel"])
    time.sleep(0.05)

    # 1b. 关闭温度开关 (温控通断产生的磁场会干扰原子信号)
    dg_temp_sw_dev = devices.get("dg_temp_sw")
    temp_switch_was_off = False
    if dg_temp_sw_dev is not None and TEMP_SWITCH_CHANNEL is not None:
        dg_temp_sw_dev.setup_dc(TEMP_SWITCH_OFF_V, channel=TEMP_SWITCH_CHANNEL)
        time.sleep(TEMP_SWITCH_OFF_DELAY_S)
        temp_switch_was_off = True

    try:
        # 2. 防御性 re-arm: 确保示波器处于 ARMED 状态等触发.
        #    上一次 shot 末尾的 trigger_run() 已经把它设为 ARMED,
        #    但偶尔 SCPI 通信失败会留下 STOP 态, 此处加保险.
        sds_inst.trigger_run()
        time.sleep(0.05)

        # 3. 等待 burst ready 时间 (避免上一次 burst 重叠)
        time.sleep(BURST_BURST_PERIOD_S + 0.05)

        # 4. 等待触发 (SDS NORMal 模式, CH4 RISing → t=0)
        if not sds_inst.wait_for_trigger(timeout=BURST_BURST_PERIOD_S + 5.0):
            print(f"    ⚠ Pump power={pump_power_v:.3f}V 触发超时")

        time.sleep(scope_cfg_ref.acquire_delay)
        # [关键] trigger_stop 冻结当前波形, 防止下一次 burst 触发覆盖
        sds_inst.trigger_stop()
        time.sleep(0.02)

        # 5. 只读 CH1 (PD); CH4 仅作触发源, 不读数据
        pd_result = acquirer.acquire_channel(
            SDS_PD_CHANNEL, scope_cfg_ref.timebase_scale,
            scope_cfg_ref.horizontal_divisions,
            trim_points=actual_pts,
        )

        # 6. 准备下一次触发 (re-arm 给下一轮)
        sds_inst.trigger_run()
        time.sleep(0.05)

        signal = pd_result.voltage
        time_s = pd_result.time

        # 7. 自适应档位 (PD 通道)
        new_scale, changed = auto_scale_signal(signal, current_scale)
        if changed:
            sds_inst.set_channel_scale(SDS_PD_CHANNEL, new_scale)
            scope_cfg_ref.channels[0].scale = new_scale
            current_scale = new_scale

        # 8. burst 起止时刻由配置确定:
        #    - t_burst_start = 0.0: 由 SDS 触发源 C4 RISing 边沿 (= sync 上升沿)
        #    - t_burst_end   = BURST_DURATION_S: DG4000 burst 设置确定
        t_burst_start_s = 0.0
        t_burst_end_s = float(BURST_DURATION_S)

        return {
            "time_s": time_s,
            "signal_v": signal,
            "pump_power_v": pump_power_v,
            "scale_used": current_scale,
            "t_burst_start_s": t_burst_start_s,
            "t_burst_end_s": t_burst_end_s,
        }
    finally:
        # [安全] 无论正常返回或异常, 都恢复温控开关为 ON
        if temp_switch_was_off:
            dg_temp_sw_dev.setup_dc(TEMP_SWITCH_ON_V, channel=TEMP_SWITCH_CHANNEL)
            time.sleep(TEMP_SWITCH_ON_DELAY_S)

# %% Cell 5b
# ============================================================
# 主采集循环：扫描 PUMP_POWER 列表 → DG900 CH1 (Pump_laser_power)
# ============================================================
print("=" * 60)
print(f"周期泵浦 R_pump_eff 功率扫描: {len(PUMP_POWER)} 点, "
      f"每点重复 {ACQ_REPEATS} 次 PC 端平均")
print(f"  频率: {MODULATION_FREQUENCY_HZ/1000:.0f} kHz, "
      f"占空比: {FIXED_DUTY_CYCLE}%, Burst: {BURST_CYCLES} cycles "
      f"({BURST_DURATION_S*1000:.2f} ms)")
print(f"  RF gate (DG4000 CH2, 固定): high={PUMP_RF_GATE_HIGH_V}V / low={PUMP_RF_GATE_LOW_V}V TTL")
print(f"  Pump power (DG900 CH1, 扫描): {PUMP_POWER}")
print("=" * 60)

all_shots = []
try:
    for idx, pump_power_v in enumerate(tqdm(PUMP_POWER, desc="功率扫描")):
        print(f"\n--- [{idx+1}/{len(PUMP_POWER)}] "
              f"Pump power DC = {pump_power_v:.3f} V ---")

        # Step 1: 自适应档位 (3 次试探, 用最后一次锁定的 scale)
        current_scale = scope_cfg.channels[0].scale
        probe_shot = None
        for retry in range(3):
            probe_shot = acquire_single_shot(
                pump_power_v=pump_power_v,
                current_scale=current_scale,
                scope_cfg_ref=scope_cfg,
            )
            abs_max = np.max(np.abs(probe_shot["signal_v"]))
            new_scale, changed = auto_scale_signal(probe_shot["signal_v"], current_scale)
            if not changed or retry >= 2:
                # 锁定 scale
                sds_inst.set_channel_scale(SDS_PD_CHANNEL, current_scale)
                scope_cfg.channels[0].scale = current_scale
                break
            current_scale = new_scale
            print(f"  缩放 retry{retry+1}: scale→{current_scale:.3f} V/div "
                  f"(peak={abs_max*1e3:.1f} mV)")

        if probe_shot is None:
            print(f"  ⚠ Pump power={pump_power_v:.3f} V 采集失败，跳过")
            continue

        # Step 2: 在锁定 scale 下采集 ACQ_REPEATS 次, PC 端 mean() 平均
        # 第 1 次重用 probe_shot; 后面 N-1 次重新采集
        locked_shots = [probe_shot]
        for rep in range(1, ACQ_REPEATS):
            s = acquire_single_shot(
                pump_power_v=pump_power_v,
                current_scale=current_scale,
                scope_cfg_ref=scope_cfg,
            )
            locked_shots.append(s)

        # 验证时间轴一致性 (probe + 后续帧 time_s 应基本一致)
        ref_time = locked_shots[0]["time_s"]
        max_dt_dev = 0.0
        for s in locked_shots[1:]:
            dts = np.diff(s["time_s"])
            max_dt_dev = max(max_dt_dev, float(np.max(np.abs(dts - np.diff(ref_time)))))
        if max_dt_dev > 1e-6:
            print(f"  ⚠ 时间轴偏差 {max_dt_dev*1e9:.0f} ns > 1µs, 平均按元素直接进行")

        # 元素级平均 (假设时间轴已对齐)
        time_s_avg = ref_time
        signal_avg = np.mean([s["signal_v"] for s in locked_shots], axis=0)
        scale_used = locked_shots[-1]["scale_used"]

        # 单帧 npz (审计用, 供调试查看单次波形)
        for rep, s in enumerate(locked_shots):
            np.savez(
                raw_dir / f"shot_p{idx:02d}_r{rep}.npz",
                time_s=s["time_s"],
                signal=s["signal_v"],
                pump_power_voltage=pump_power_v,
                rep_index=rep,
                scale_used=s["scale_used"],
                t_burst_start_s=s["t_burst_start_s"],
                t_burst_end_s=s["t_burst_end_s"],
                sample_rate_actual_Hz=actual_sample_rate,
                points_actual=actual_pts,
            )

        # 平均 npz (供 plot 拟合用)
        out_file = raw_dir / f"shot_p{idx:02d}_avg.npz"
        np.savez(
            out_file,
            time_s=time_s_avg,                  # ⚠ 权威时间参考
            signal=signal_avg,                 # ACQ_REPEATS 次平均
            pump_power_voltage=pump_power_v,
            duty_cycle=FIXED_DUTY_CYCLE,
            modulation_frequency_hz=MODULATION_FREQUENCY_HZ,
            burst_cycles=BURST_CYCLES,
            burst_duration_s=BURST_DURATION_S,
            pre_baseline_time_s=PRE_BASELINE_TIME_S,
            post_dark_time_s=POST_DARK_TIME_S,
            z_field_setpoint=Z_FIELD_SETPOINT,
            acq_repeats=ACQ_REPEATS,
            sample_rate_actual_Hz=actual_sample_rate,
            sample_rate_requested_Hz=SDS_SAMPLE_RATE,
            timebase_scale_actual_s_per_div=actual_timebase_scale,
            capture_duration_actual_s=actual_capture_s,
            points_actual=actual_pts,
            t_burst_start_s=0.0,                # 由触发配置确定
            t_burst_end_s=float(BURST_DURATION_S),
            scale_used=scale_used,
            rf_gate_high_v=PUMP_RF_GATE_HIGH_V,
            rf_gate_low_v=PUMP_RF_GATE_LOW_V,
        )
        peak_avg = float(np.max(np.abs(signal_avg)))
        rms_avg = float(np.sqrt(np.mean(signal_avg**2)))
        print(f"  共 {len(locked_shots)} 帧平均, "
              f"peak={peak_avg*1e3:.1f} mV, "
              f"RMS={rms_avg*1e3:.1f} mV, "
              f"scale={scale_used:.3f} V/div")
        print(f"  saved: {out_file.name}")
        all_shots.append({
            "pump_power_v": pump_power_v,
            "file": out_file.name,
        })

finally:
    # [安全] 异常时关闭 burst, 并把 DG900 Pump_laser_power 设回安全最低值
    try:
        dg_rf.set_burst_state(False, channel=2)
        dg_rf.set_output(False, channel=2)
        dg_laser.setup_dc(min(PUMP_POWER), channel=pump_cfg["channel"])
        print(f"\n[安全] Burst OFF, Pump_laser_power 设为最小值 "
              f"{min(PUMP_POWER):.3f}V, probe 不变")
    except Exception as e:
        print(f"[安全] 恢复失败: {e}")

# ---- 保存汇总数据 (供 plot 脚本读取) ----
np.savez(
    raw_dir / "power_scan_summary.npz",
    pump_power_voltage=[s["pump_power_v"] for s in all_shots],
    files=[s["file"] for s in all_shots],
    n_shots=len(all_shots),
)
print(f"\n汇总已保存: {raw_dir / 'power_scan_summary.npz'} ({len(all_shots)} shots)")

print("\n✅ 周期泵浦 R_pump_eff 功率扫描完成")

# %% Cell 6
# ========== 安全断开（仅断开 TEC，其余设备保持连接） ==========
print("正在断开 TEC...")
tec = devices.get("tec")
if tec and hasattr(tec, "disconnect"):
    try:
        tec.disconnect()
        print("  TEC 已断开")
    except Exception as e:
        print(f"  TEC 断开失败: {e}")
print("其他设备保持连接，Burst OFF, Pump_laser_power 已降到最小值")
