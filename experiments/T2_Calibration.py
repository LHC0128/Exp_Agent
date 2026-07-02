# %% [markdown] Cell 0
# # 横向弛豫时间 T₂ 标定 — 光学 FID 时域法
#
# **原理**：RF 开关门控 Pump AOM 产生 Larmor 频率 (≈ 90 kHz) 调制的 Pump 光 Burst，
# 激发横向自旋相干。Burst 结束后 Pump 光关断，自旋自由进动，
# 示波器采集 PD 阻尼振荡信号，拟合得 T₂。
#
# **信号链路**：
# - DG4000 (DG4E222800868) CH1 → 100 MHz 正弦 CW → RF 开关 IN（AOM 载波）
# - DG4000 (DG4E222800868) CH2 → 90 kHz 方波 Burst → RF 开关 CTRL（门控）
# - RF 开关 OUT → AOM → 90 kHz 调制 Pump 光
# - CH2 同步输出 → 示波器 CH4（触发）
# - PDB 输出 → 示波器 CH1（PD 信号采集）
#
# **设备**：GS200 (主磁场) | DG912 Pro (Pump DC + Probe) | DG4000 (AOM载波+门控) | SDS 示波器 | HF2 LIA (监控) | TEC103 (温控)
#
# **参数概览**：
# - AOM 载波 (CH1)：100 MHz CW，0.1 V
# - RF 门控 (CH2)：90 kHz 方波 Burst，5000 周期 (~55.6 ms)，5 Vpp + 2.5V offset
# - 示波器：1 MSa/s，100 ms 窗口，PD=CH1，Trig=CH4（上升沿，1.5V）
# - 重复 5 次取平均
# - 数据分析：直接拟合阻尼振荡 V(t) = A·exp(-(t-t₀)/T₂)·cos(2πf_L(t-t₀)+φ) + V_DC

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
matplotlib.use("TkAgg")  # 交互式后端，确保 plt.show() 弹出图表窗口
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

# 设备库
from gs200 import GS200Instrument
from signal_generator import DG4000Instrument, DG900Instrument
from tec_controller import TECInstrument
from lockin_amplifier import HF2Instrument, demod, SignalInputConfig, DemodulatorConfig
from sds_acquisition import SDSInstrument, SDSAcquisition, AcquisitionConfig, ChannelConfig, TriggerConfig

print("导入完成")

# %% Cell 2
# ========== 加载配置文件 ==========
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ========== 实验参数 ==========
EXPERIMENT_TYPE = "T2_Calibration"
RUN_TAG = "optical_FID"

# ---- Pump 光 ----
PUMP_POWER = 0.1               # Pump 光 DC 功率 (V)
# ---- RF 开关: AOM 载波 (CH1) ----
AOM_CARRIER_FREQ = 100.0e6     # AOM 载波频率 (Hz)，固定 100 MHz
AOM_CARRIER_AMPLITUDE = 0.1    # AOM 载波幅度 (V)，安全限值 ≤ 0.18V
# ---- RF 开关: 门控脉冲 (CH2) ----
RF_GATE_FREQ = 90000           # 门控脉冲频率 (Hz) = Ω_L/2π，等于 Larmor 进动频率
RF_GATE_AMPLITUDE = 5.0        # 门控脉冲幅度 (Vpp)，5V TTL 电平
RF_GATE_OFFSET = 2.5           # 门控脉冲 DC 偏置 (V)，5Vpp + 2.5V offset = 0~5V
RF_GATE_DUTY = 5.0             # 门控脉冲占空比 (%)，窄脉冲驱动 RF 开关
BURST_NCYCLES = 5000           # Burst 周期数，5000/90000 ≈ 55.6 ms
BURST_PERIOD = 0.1             # Burst 触发周期 (s)，两次触发间最小间隔

# ---- Probe 光 ----
DO_POWER_SCAN = True           # True = 多功率扫描；False = 单功率测量
PROBE_POWER = 0.1              # 单功率测量时的 Probe 光功率 (V)
PROBE_POWER_LIST = np.linspace(0.01, 0.1, 10)  # 多功率扫描用 (V)

# ---- 主磁场 ----
MAIN_FIELD_mA = 9.30           # 主磁场 (mA)，Ω_L/2π ≈ 90 kHz

# ---- 示波器通道选择 ----
SCOPE_PD_CHANNEL = 1           # PD 信号输入通道 (PDB 输出 → CH1)
SCOPE_TRIG_CHANNEL = 4         # 触发信号输入通道 (DG4000 同步输出 → CH4)
SCOPE_TRIG_SOURCE = f"C{SCOPE_TRIG_CHANNEL}"  # 触发源
SCOPE_TRIG_SLOPE = "FALLing"   # 下降沿触发：同步信号下降沿 = Burst 结束，t=0 即 FID 起点
# ---- 示波器采集 ----
SCOPE_SAMPLE_RATE = 1.0e6      # 采样率 (Sa/s)
SCOPE_DURATION = 0.05          # 采集时长 (s)，Burst 结束后纯 FID 衰减段
ACQ_REPEATS = 5               # 重复次数

# ---- LIA 监控 ----
HF2_DEMOD_IDX = 1              # 解调器索引
HF2_OSC_FREQ = 90000           # 振荡器频率 (Hz)
HF2_SIGNAL_RANGE = 2.0         # 信号输入量程 (V)
HF2_DEMOD_TC = 0.001           # 解调时间常数 (s)
HF2_DEMOD_ORDER = 4            # 滤波器阶数
HF2_DEMOD_RATE = 10000         # 数据速率 (Sa/s)

# ---- 温度 ----
TEC_TEMPERATURE = 100.0        # 气室温度 (°C)

# 计算值
BURST_DURATION = BURST_NCYCLES / RF_GATE_FREQ  # ≈ 0.0556 s
SCOPE_TOTAL_POINTS = int(SCOPE_SAMPLE_RATE * SCOPE_DURATION)

# ========== 安全边界检查 ==========
def validate_safety_limit(name, value):
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if value < lo or value > hi:
        raise ValueError(f"[安全拦截] {name}={value} 超出范围 [{lo}, {hi}]")
    return value

validate_safety_limit("Pump_laser_power", PUMP_POWER)
validate_safety_limit("Probe_laser_power", PROBE_POWER)
validate_safety_limit("main_magnetic_field", MAIN_FIELD_mA)
validate_safety_limit("Pump_modulation", AOM_CARRIER_AMPLITUDE)
validate_safety_limit("Time_sequence", RF_GATE_AMPLITUDE)
validate_safety_limit("temperature", TEC_TEMPERATURE)

print(f"AOM 载波: {AOM_CARRIER_FREQ/1e6:.0f} MHz, {AOM_CARRIER_AMPLITUDE} V")
print(f"RF 门控: {RF_GATE_FREQ} Hz 脉冲, {RF_GATE_DUTY}% 占空比, "
      f"{BURST_NCYCLES} cycles, Burst 时长 {BURST_DURATION*1000:.1f} ms, 周期 {BURST_PERIOD*1000:.0f} ms")
print(f"示波器: PD=CH{SCOPE_PD_CHANNEL}, Trig={SCOPE_TRIG_SOURCE} {SCOPE_TRIG_SLOPE}, "
      f"{SCOPE_SAMPLE_RATE/1e3:.0f} kSa/s, {SCOPE_DURATION*1000:.0f} ms, {SCOPE_TOTAL_POINTS} 点")
print("安全限值检查通过")

# %% Cell 3
# ========== 连接设备 ==========
devices = {}

try:
    # ---- GS200: 主磁场 ----
    gs200_cfg = MAPPING["main_magnetic_field"]
    gs200 = GS200Instrument(gs200_cfg["resource"])
    gs200.connect()
    gs200.set_current_limit(0.015)  # 硬件限流 15 mA
    devices["gs200"] = gs200
    print(f"GS200 已连接: {gs200.idn()}")

    # ---- 安全：关闭 X/Y/Z 磁场信号发生器 (T2 只用 GS200 主磁场) ----
    for key in ["X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field"]:
        cfg = MAPPING.get(key)
        if cfg:
            dev = DG4000Instrument(cfg["resource"], channel=cfg["channel"])
            dev.connect()
            dev.set_output(False)
            devices[f"safe_{key}"] = dev
            print(f"  {key} CH{cfg['channel']} 已连接并关闭 (T2 不使用)")

    # ---- DG912 Pro: Pump DC + Probe 光 (同一设备, 不同通道) ----
    pump_cfg = MAPPING["Pump_laser_power"]
    probe_cfg = MAPPING["Probe_laser_power"]
    assert pump_cfg["resource"] == probe_cfg["resource"], "Pump/Probe 不在同一台 DG912"

    PUMP_DC_CH = pump_cfg["channel"]
    PROBE_CH = probe_cfg["channel"]
    # DG912 Pro 必须用 DG900Instrument，setup_dc 走 :APPLy:DC 命令
    dg_laser = DG900Instrument(pump_cfg["resource"], channel=PUMP_DC_CH)
    dg_laser.connect()
    devices["dg_laser"] = dg_laser
    print(f"DG912 Pro 已连接: Pump CH{PUMP_DC_CH}, Probe CH{PROBE_CH}")

    # ---- DG4000 (DG4E222800868): AOM 载波 + RF 开关门控 (同一设备, 双通道) ----
    # CH1: 100 MHz 正弦 CW → RF 开关 IN → AOM 驱动载波
    # CH2: 90 kHz 脉冲 Burst → RF 开关 CTRL → 门控 100 MHz 通断
    #      同步输出 → 示波器 EXT TRIG
    pump_mod_cfg = MAPPING["Pump_modulation"]
    time_seq_cfg = MAPPING["Time_sequence"]
    assert pump_mod_cfg["resource"] == time_seq_cfg["resource"], \
        "Pump_modulation/Time_sequence 不在同一台 DG4000"

    AOM_CARRIER_CH = pump_mod_cfg["channel"]   # CH1: 100 MHz 载波
    RF_GATE_CH = time_seq_cfg["channel"]        # CH2: 90 kHz 门控
    dg_rf_switch = DG4000Instrument(pump_mod_cfg["resource"])
    dg_rf_switch.connect()
    devices["dg_rf_switch"] = dg_rf_switch
    print(f"DG4000 RF 开关已连接: AOM载波 CH{AOM_CARRIER_CH}, 门控 CH{RF_GATE_CH}")
    # ---- Temp_Switch (DG9000 Pro, 必须用 DG900Instrument) ----
    temp_sw_cfg = MAPPING.get("Temp_Switch")
    # [重要] resource 是 DG9Q... 前缀 → DG900 Pro 系列;  DG4000Instrument 不兼容
    dg_temp_sw = DG900Instrument(temp_sw_cfg["resource"], channel=temp_sw_cfg["channel"])
    dg_temp_sw.connect()
    dg_temp_sw.set_output(False)   # 初始关闭, 减少磁场干扰风险
    devices["dg_temp_sw"] = dg_temp_sw
    print(f"Temp_Switch (DG900) 已连接: {temp_sw_cfg['resource']}")
    # ---- SDS 示波器 ----
    scope_cfg = MAPPING["scope_waveform"]
    sds_inst = SDSInstrument(scope_cfg["resource"])
    sds_inst.connect()
    acquirer = SDSAcquisition(sds_inst)
    devices["sds_inst"] = sds_inst
    devices["acquirer"] = acquirer
    print(f"SDS 示波器已连接: {sds_inst.idn()}")

    # ---- HF2 LIA (监控) ----
    lia_cfg = MAPPING["lockin_xy"]
    hfi = HF2Instrument(
        host=lia_cfg["host"], port=lia_cfg["port"],
        api_level=1, device_id=lia_cfg["device_id"],
    )
    hfi.connect()
    devices["hfi"] = hfi
    print(f"HF2 LIA 已连接: {lia_cfg['device_id']}")

    # ---- TEC103 温控器 ----
    tec_cfg = MAPPING["temperature"]
    tec = TECInstrument(port=tec_cfg["resource"])
    tec.connect()
    devices["tec"] = tec
    print(f"TEC103 已连接: {tec_cfg['resource']}")

except Exception as e:
    print(f"设备连接失败: {e}")
    for name, dev in devices.items():
        try:
            if hasattr(dev, "disconnect"):
                dev.disconnect()
        except Exception:
            pass
    raise

print("\n所有设备连接成功")

# %% Cell 4
# ========== 设置初始值 & 创建运行目录 ==========

# 创建运行目录
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# ---- 保存运行配置 ----
config_snapshot = {
    "experiment_type": EXPERIMENT_TYPE,
    "timestamp": timestamp,
    "pump_power_V": PUMP_POWER,
    "aom_carrier_freq_Hz": AOM_CARRIER_FREQ,
    "aom_carrier_amplitude_V": AOM_CARRIER_AMPLITUDE,
    "rf_gate_freq_Hz": RF_GATE_FREQ,
    "rf_gate_amplitude_V": RF_GATE_AMPLITUDE,
    "rf_gate_offset_V": RF_GATE_OFFSET,
    "rf_gate_duty_pct": RF_GATE_DUTY,
    "burst_ncycles": BURST_NCYCLES,
    "burst_period_s": BURST_PERIOD,
    "probe_power_V": PROBE_POWER,
    "main_field_mA": MAIN_FIELD_mA,
    "scope": {
        "pd_channel": SCOPE_PD_CHANNEL,
        "trig_channel": SCOPE_TRIG_CHANNEL,
        "trig_slope": SCOPE_TRIG_SLOPE,
        "sample_rate_Hz": SCOPE_SAMPLE_RATE,
        "duration_s": SCOPE_DURATION,
        "acq_repeats": ACQ_REPEATS,
    },
    "temperature_C": TEC_TEMPERATURE,
}
with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
    yaml.dump(config_snapshot, f, default_flow_style=False, allow_unicode=True)

# ---- 温控 ----
tec_dev = devices["tec"]
validate_safety_limit("temperature", TEC_TEMPERATURE)
tec_dev.set_target_temperature(TEC_TEMPERATURE, channel=1)
tec_dev.set_enable(True, channel=1)
temp_now = tec_dev.get_temperature(channel=1)
print(f"温度设定: {TEC_TEMPERATURE} °C, 当前: {temp_now:.1f} °C")
print("等待温度稳定...")
while True:
    time.sleep(5)
    t = tec_dev.get_temperature(channel=1)
    print(f"  当前温度: {t:.2f} °C")
    if abs(t - TEC_TEMPERATURE) < 1.0:
        print(f"温度已稳定: {t:.2f} °C")
        break

# ---- 主磁场 ----
gs200 = devices["gs200"]
gs200.set_current(MAIN_FIELD_mA / 1000.0)  # mA → A
gs200.set_output(True)
print(f"主磁场: {MAIN_FIELD_mA:.3f} mA")

# ---- Pump DC 光 + Probe 光 ----
dg_laser = devices["dg_laser"]
dg_laser.setup_dc(PUMP_POWER, channel=PUMP_DC_CH)
print(f"Pump DC 光: {PUMP_POWER} V (CH{PUMP_DC_CH})")
probe_init = PROBE_POWER_LIST[0] if DO_POWER_SCAN else PROBE_POWER
dg_laser.setup_dc(probe_init, channel=PROBE_CH)
print(f"Probe 光 (CW): {probe_init} V (CH{PROBE_CH})"
      + (" [多功率扫描，后续动态调整]" if DO_POWER_SCAN else ""))

# ---- RF 开关: CH1 AOM 载波 (100 MHz CW，持续输出) ----
dg_rf_switch = devices["dg_rf_switch"]
dg_rf_switch.setup_sine(AOM_CARRIER_FREQ, AOM_CARRIER_AMPLITUDE, offset=0.0,
                         channel=AOM_CARRIER_CH)
print(f"AOM 载波: {AOM_CARRIER_FREQ/1e6:.0f} MHz CW, {AOM_CARRIER_AMPLITUDE} V (CH{AOM_CARRIER_CH})")

# ---- LIA 监控设置 ----
hfi = devices["hfi"]
sig_cfg = SignalInputConfig(
    input_index=0, range=HF2_SIGNAL_RANGE,
    ac_coupling=True, diff=False, impedance=50,
)
demod.configure_signal_input(hfi, sig_cfg)

demod_cfg = DemodulatorConfig(
    demod_index=HF2_DEMOD_IDX, enable=True,
    rate=HF2_DEMOD_RATE, input_channel=0, osc_select=0,
    harmonic=1, time_constant=HF2_DEMOD_TC, order=HF2_DEMOD_ORDER,
    phase=0.0,
)
demod.configure_demodulator(hfi, demod_cfg)
hfi.set_double(f"{hfi.osc_path(0)}/freq", HF2_OSC_FREQ)
hfi.sync()
print(f"LIA 监控: demod {HF2_DEMOD_IDX}, osc {HF2_OSC_FREQ} Hz, TC {HF2_DEMOD_TC} s")

print("\n初始值设置完成")

# %% Cell 5
# ========== 配置 RF 门控 Burst + 示波器，执行 FID 采集 ==========

dg_rf_switch = devices["dg_rf_switch"]

# ---- CH2: 90 kHz 脉冲 Burst (RF 开关门控) ----
# CH1 (100 MHz 载波) 已在 Cell 4 中设为 CW 持续输出
# [经验] 先重置 CH2，避免继承其他实验（如 T1）的连续模式
dg_rf_switch.set_burst_state(False, channel=RF_GATE_CH)  # 确保旧 burst 关闭
dg_rf_switch.set_mod_state(False, channel=RF_GATE_CH)    # 确保旧调制关闭
# CH2 输出 90 kHz 窄脉冲 (5% 占空比) → RF 开关 CTRL → 门控 100 MHz 通断
# 下降沿同步输出 → 示波器 CH4 下降沿触发 → t=0 = Burst 结束 → 纯 FID 衰减
# [经验] 门控频率必须精确等于 Ω_L/2π，频率偏差导致 FID 拍频
dg_rf_switch.apply_wave("PULSe", freq=RF_GATE_FREQ, amp=RF_GATE_AMPLITUDE,
                         offset=RF_GATE_OFFSET, channel=RF_GATE_CH)
dg_rf_switch.set_pulse_dcycle(RF_GATE_DUTY, channel=RF_GATE_CH)  # 5% 窄脉冲

# [经验] Burst 模式手动触发，周期 100 ms 防止重叠
dg_rf_switch.set_burst_mode("TRIGgered", channel=RF_GATE_CH)
dg_rf_switch.set_burst_ncycles(BURST_NCYCLES, channel=RF_GATE_CH)
dg_rf_switch.set_burst_period(BURST_PERIOD, channel=RF_GATE_CH)
dg_rf_switch.set_burst_trigger_source("INTernal", channel=RF_GATE_CH)
dg_rf_switch.set_burst_state(True, channel=RF_GATE_CH)
dg_rf_switch.set_sync_state(True, channel=RF_GATE_CH)
dg_rf_switch.set_output(True, channel=RF_GATE_CH)
print(f"RF 门控 Burst: {RF_GATE_FREQ} Hz PULSe {RF_GATE_DUTY}%, {BURST_NCYCLES} cycles, "
      f"周期 {BURST_PERIOD*1000:.0f} ms, 同步输出 → CH{SCOPE_TRIG_CHANNEL}")

# ---- 配置示波器 ----
sds_inst = devices["sds_inst"]
acquirer = devices["acquirer"]

# 动态构建通道列表：PD 通道 + 触发通道启用
scope_channels = []
for ch_num in [1, 2, 3, 4]:
    if ch_num == SCOPE_PD_CHANNEL:
        scope_channels.append(ChannelConfig(
            number=ch_num, enabled=True, scale=0.5, offset=0.0,
            coupling="DC", impedance="ONEMeg", probe=1.0,
        ))
    elif ch_num == SCOPE_TRIG_CHANNEL:
        scope_channels.append(ChannelConfig(
            number=ch_num, enabled=True, scale=2.0, offset=2.5,
            coupling="DC", impedance="ONEMeg", probe=1.0,
        ))
    else:
        scope_channels.append(ChannelConfig(number=ch_num, enabled=False))

scope_cfg = AcquisitionConfig(
    sampling_rate=SCOPE_SAMPLE_RATE,
    sampling_time=SCOPE_DURATION,
    acquire_type="NORMal",
    acquire_delay=0.5,
    channels=scope_channels,
    trigger=TriggerConfig(
        mode="NORMal",
        source=SCOPE_TRIG_SOURCE,
        type="EDGE",
        slope=SCOPE_TRIG_SLOPE,
        level=1.5,
    ),
)
print(f"示波器: {SCOPE_SAMPLE_RATE/1e3:.0f} kSa/s, {SCOPE_DURATION*1000:.0f} ms, "
      f"PD=CH{SCOPE_PD_CHANNEL}, Trig={SCOPE_TRIG_SOURCE} {SCOPE_TRIG_SLOPE}")


# ---- 初始配置示波器 ----
acquirer.apply_config(scope_cfg)
# [经验] apply_config 不设存储深度，示波器默认 10K 点。手动设 ≥ SCOPE_TOTAL_POINTS
sds_inst.set_memory_depth("100K")  # 100K ≥ 50000
time.sleep(0.1)
actual_pts = int(sds_inst.get_actual_points())
print(f"示波器存储深度: 100K, 实际采集 {actual_pts} 点 (期望 ≥ {SCOPE_TOTAL_POINTS})")
time.sleep(0.2)

# ---- 自适应档位参数 ----
VERT_DIVS = 4
SCALE_MIN = 0.01
SCALE_MAX = 10.0


def acquire_T2_fid(probe_power):
    """执行单次 T₂ FID 采集，返回 (t_axis, waveforms_fid, avg_wf, scale_used)。

    内部自适应档位、多次重复取平均。
    """
    dg_laser_dev = devices["dg_laser"]
    validate_safety_limit("Probe_laser_power", probe_power)
    dg_laser_dev.setup_dc(probe_power, channel=PROBE_CH)
    print(f"  Probe 功率: {probe_power:.3f} V")

    all_waveforms = []
    scale_list = []
    current_scale = scope_cfg.channels[0].scale
    time_axis = None

    for i in range(ACQ_REPEATS):
        for retry in range(3):
            dg_temp_sw.set_output(False)
            time.sleep(0.5)
            if not sds_inst.wait_for_trigger(timeout=10.0):
                print(f"    第 {i+1} 次触发超时，跳过")
                break
            time.sleep(scope_cfg.acquire_delay)
            sds_inst.trigger_stop()

            result = acquirer.acquire_channel(
                SCOPE_PD_CHANNEL, scope_cfg.timebase_scale,
                scope_cfg.horizontal_divisions, trim_points=SCOPE_TOTAL_POINTS,
            )
            dg_temp_sw.set_output(True)
            time.sleep(0.1)

            abs_max = np.max(np.abs(result.voltage))
            full_scale = current_scale * VERT_DIVS
            need_retry = False

            if abs_max < 0.4 * full_scale and current_scale > SCALE_MIN * 2:
                current_scale /= 2
                scope_cfg.channels[0].scale = current_scale
                sds_inst.set_channel_scale(SCOPE_PD_CHANNEL, current_scale)
                need_retry = True
            if abs_max > 0.9 * full_scale and current_scale < SCALE_MAX / 2:
                current_scale *= 2
                scope_cfg.channels[0].scale = current_scale
                sds_inst.set_channel_scale(SCOPE_PD_CHANNEL, current_scale)
                need_retry = True

            if need_retry and retry < 2:
                print(f"    第 {i+1} 次: 缩放 retry{retry+1} ({current_scale:.2f}V/div)")
                sds_inst.trigger_run()
                time.sleep(0.02)
                continue
            break

        if result is None:
            sds_inst.trigger_run(); time.sleep(0.02)
            continue

        all_waveforms.append(result.voltage)
        scale_list.append(current_scale)
        if i == 0:
            time_axis = result.time

        vpp = result.voltage.max() - result.voltage.min()
        print(f"    采集 {i+1}/{ACQ_REPEATS}: {len(result.voltage)} 点, "
              f"Vpp={vpp:.3f}V, scale={current_scale:.2f}V/div")
        sds_inst.trigger_run()
        time.sleep(0.02)

    waveforms_array = np.array(all_waveforms)
    mask = time_axis >= 0
    wf_fid = waveforms_array[:, mask]
    avg_wf = np.mean(wf_fid, axis=0)
    return time_axis[mask], wf_fid, avg_wf, scale_list


# ---- 执行测量 ----
if DO_POWER_SCAN:
    print("=" * 60)
    print("多功率 T₂ 扫描模式")
    print(f"功率列表: {PROBE_POWER_LIST}")
    print("=" * 60)

    all_T2_results = []
    for pi, pwr in enumerate(PROBE_POWER_LIST):
        print(f"\n--- 功率点 {pi+1}/{len(PROBE_POWER_LIST)}: {pwr:.3f} V ---")
        t_fid, wf_fid, avg_wf, scl = acquire_T2_fid(pwr)

        # 保存每点原始数据
        np.savez(raw_dir / f"fid_p{pi:02d}.npz",
                 time=t_fid, avg_waveform=avg_wf, waveforms=wf_fid,
                 probe_power=pwr, scale_used=scl,
                 sample_rate=SCOPE_SAMPLE_RATE, burst_duration=BURST_DURATION,
                 rf_gate_freq=RF_GATE_FREQ, rf_gate_duty=RF_GATE_DUTY,
                 trigger_slope=SCOPE_TRIG_SLOPE)
        all_T2_results.append({"t_fid": t_fid, "avg_wf": avg_wf, "wf_fid": wf_fid,
                               "probe_power": pwr, "scale_used": scl})
        print(f"  已保存: fid_p{pi:02d}.npz ({len(avg_wf)} 点)")

    # 保存汇总
    np.savez(raw_dir / "power_scan_results.npz",
             probe_power=[r["probe_power"] for r in all_T2_results],
             scale_used=[r["scale_used"][-1] for r in all_T2_results])
    # 保存波形堆栈（供 plot 程序用），假设各点时间轴相同
    t_ref = all_T2_results[0]["t_fid"]
    np.savez(raw_dir / "power_scan_waveforms.npz",
             t_axis=t_ref,
             avg_stack=np.array([r["avg_wf"] for r in all_T2_results]),
             probe_power=[r["probe_power"] for r in all_T2_results])
    print(f"\n多功率扫描完成，共 {len(all_T2_results)} 点")

else:
    print("=" * 60)
    print(f"单功率 T₂ 测量: Probe = {PROBE_POWER} V")
    print("=" * 60)
    time_fid, waveforms_fid, avg_waveform, scale_used = acquire_T2_fid(PROBE_POWER)

    print(f"FID 段: {len(time_fid)} 点 "
          f"({time_fid[0]*1000:.2f} ~ {time_fid[-1]*1000:.1f} ms)")

    np.savez(
        raw_dir / "fid_waveforms.npz",
        waveforms=waveforms_fid, avg_waveform=avg_waveform, time=time_fid,
        sample_rate=SCOPE_SAMPLE_RATE, burst_duration=BURST_DURATION,
        rf_gate_freq=RF_GATE_FREQ, rf_gate_duty=RF_GATE_DUTY,
        trigger_slope=SCOPE_TRIG_SLOPE, acq_repeats=ACQ_REPEATS,
        scale_used=np.array(scale_used),
        pd_channel=SCOPE_PD_CHANNEL, trig_channel=SCOPE_TRIG_CHANNEL,
    )
    print(f"原始数据已保存: {raw_dir / 'fid_waveforms.npz'}")


# ---- 关断 Burst 模式 ----
dg_rf_switch.set_burst_state(False, channel=RF_GATE_CH)
print("RF 门控 Burst 已关断")


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

print("其他设备保持连接，输出状态不变")


# %%