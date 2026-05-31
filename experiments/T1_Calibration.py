# %% [markdown] Cell 0
# # Faraday 旋光角与纵向弛豫时间 T₁ 标定
#
# **实验目的**：CSS 制备后关断 Pump，示波器采集 PDB 差分信号波形，
# 以其指数衰减曲线拟合得 $T_1$。
# 支持多探测光功率扫描，线性拟合 $T_1^{-1}$ vs $P_{\text{probe}}$ 分离本征弛豫率与光致退相干系数。
#
# **坐标系**：磁场 $B$ 沿 **X**（与探测光同向），光沿 **X** 传播，RF 线圈沿 **Y**。
# $B \parallel X$ 使得 $\langle J_x \rangle$ 为纵向分量，避免 Larmor 进动。
#
# **信号连接**：
# - PDB 差分输出 → 示波器 **CH1**
# - Time_sequence 同步信号 → 示波器 **CH4**（触发源，标记 Pump 关断 $t_0$）
# - 温控开关 (Temp_Switch) → DG912，采集期间关闭以避免磁场干扰
#
# **设备**：DG4000 #1（X 方向磁场）、DG4000 #2（Pump_modulation CH1 + Time_sequence CH2）、
# DG912 #1（Probe 光 CH2 CW）、DG912 #2（温控开关）、SDS 示波器、TEC103（温控）
#
# **模式**：单功率 T₁ 测量 / 多功率扫描（可选）

# %% Cell 1
# ========== 交互式绘图 ==========
from pathlib import Path
import sys
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
from tqdm import tqdm

from signal_generator import DG4000Instrument, DG900Instrument
from tec_controller import TECInstrument
from sds_acquisition import (
    SDSInstrument, SDSAcquisition,
    AcquisitionConfig, ChannelConfig,
)

print("所有库导入成功")

# %% Cell 2
# 加载映射
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

# 加载安全限值
with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]

# ============================================================
# ========== 实验参数 ==========
# ============================================================
EXPERIMENT_TYPE = "T1_Calibration"
RUN_TAG = "test"

# ---- 多功率扫描开关 ----
DO_POWER_SCAN = True              # True = 多功率扫描；False = 单功率测量

# ---- Pump 控制 ----
# CH2 低频方波: 高电平(5V)→RF开关导通→Pump ON, 低电平(0V)→Pump OFF
# CH4 同步信号下降沿 = Pump 关断 t₀，触发示波器
PUMP_SWITCH_FREQ = 1.0             # Pump 开关方波频率 (Hz)，半周期=0.5s
PUMP_MOD_AMPLITUDE = 0.18          # CH1 100MHz 载波幅度 (V)
PUMP_PREP_TIME = 0.5               # CSS 制备所需 Pump ON 时长 (s)，须 ≤ 半周期

# ---- Probe 光 ----
PROBE_POWER = 0.05                 # 单功率测量时的探测光功率 (V)
PROBE_POWER_LIST = np.linspace(0.01, 0.1, 10)  # 多功率扫描用 (V)

# ---- X 方向磁场（信号发生器提供） ----
X_FIELD_AMPLITUDE = 5.0            # X 方向磁场幅度 (V)，Ω_L/2π ≈ 90 kHz

# ---- 示波器采集参数 ----
# CH1 = PDB 差分输出, CH4 = Time_sequence 同步 (下降沿→触发→t₀)
SCOPE_SAMPLING_RATE = 10000        # 采样率 (Sa/s)
ACQ_DURATION = 0.1                 # 单次采集时长 (s)，须 ≤ 半周期
ACQ_REPEATS = 5                   # 每点重复次数
TRIGGER_SOURCE = "C4"              # 触发源: CH4 同步信号下降沿
TRIGGER_LEVEL = 2.5                # 触发阈值 (V)，TTL 中值
SCOPE_VOLT_SCALE = 0.2             # PDB 通道初始垂直刻度 (V/div)

# ---- 自适应档位参数 ----
VERT_DIVS = 4                      # 有效显示格数 (SDS: 8 div 总, ±4 div)
SCALE_MIN = 0.002                  # 最小 V/div (SDS1204X HD: 500uV/div)
SCALE_MAX = 10.0                   # 最大 V/div
# ---- T₁ 拟合参数 ----
T1_BOUNDS = (0.5e-3, 0.5)         # T₁ 合理范围 (s), 0.5ms ~ 500ms

# ---- 温度控制 ----
TEC_TEMPERATURE = 100.0             # 气室温度 (°C)

# 校验：半周期需覆盖制备时间和采集时间
_half = 0.5 / PUMP_SWITCH_FREQ
assert PUMP_PREP_TIME <= _half, f"PUMP_PREP_TIME > 半周期 ({_half}s)"
assert ACQ_DURATION <= _half, f"ACQ_DURATION > 半周期 ({_half}s)"

print(f"  Pump方波: {PUMP_SWITCH_FREQ}Hz, 载波: {PUMP_MOD_AMPLITUDE}Vpp")
print(f"  X磁场: {X_FIELD_AMPLITUDE}V, Probe: {PROBE_POWER}V")
print(f"  示波器: {SCOPE_SAMPLING_RATE/1e3:.0f}kSa/s, {ACQ_DURATION}s/帧, ×{ACQ_REPEATS}")
print(f"  自适应档位: {VERT_DIVS}div, 最小{SCALE_MIN}V/div")

# %% Cell 3
def validate_safety_limit(name, value):
    """检查数值是否在安全范围内，超出则报错"""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo, hi = lim["min"], lim["max"]
    if lo is not None and value < lo:
        raise ValueError(f"[安全拦截] {name}={value} 低于下限 [{lo}, {hi}]")
    if hi is not None and value > hi:
        raise ValueError(f"[安全拦截] {name}={value} 超出上限 [{lo}, {hi}]")
    return value


def exp_decay(t, A, T1, C):
    """单指数衰减：A·exp(-t/T1) + C

    PDB 差分信号 V_diff ∝ V_x - V_y ∝ θ_F。
    T₁ 拟合只需要衰减时间常数，与信号幅度/偏置无关。
    直接对 V_diff(t) 拟合 A·exp(-t/T₁) + C 即可。
    """
    return A * np.exp(-t / T1) + C


print("✅ 辅助函数定义完成")

# %% Cell 4
# ========== 连接所有设备 ==========
devices = {}

try:
    # ---- DG4000: X 方向磁场 (dg_x) ----
    cfg_x = MAPPING["X_magnetic_field"]
    dg_x = DG4000Instrument(cfg_x["resource"], channel=cfg_x["channel"])
    dg_x.connect()
    print(f"dg_x 已连接: {dg_x.idn()}")
    devices["dg_x"] = dg_x

    # ---- 安全：关闭同设备未用通道 (CH2 = Y_magnetic_field) ----
    cfg_y = MAPPING["Y_magnetic_field"]
    assert cfg_y["resource"] == cfg_x["resource"], "X/Y 磁场不在同一设备"
    dg_x.set_output(False, channel=cfg_y["channel"])
    print(f"  Y 磁场 CH{cfg_y['channel']} 已关闭 (T1 不使用)")

    # ---- 安全：关闭 Z 磁场 (独立设备, 防止串扰) ----
    cfg_z = MAPPING["Z_magnetic_field"]
    dg_z = DG4000Instrument(cfg_z["resource"], channel=cfg_z["channel"])
    dg_z.connect()
    dg_z.set_output(False)
    devices["dg_z"] = dg_z
    print(f"Z 磁场 CH{cfg_z['channel']} 已连接并关闭 (T1 不使用)")

    # ---- DG4000: Pump 调制 (CH1 载波 + CH2 门控方波) ----
    cfg_pump = MAPPING["Pump_modulation"]
    dg_pump = DG4000Instrument(cfg_pump["resource"])
    dg_pump.connect()
    print(f"dg_pump 已连接: {dg_pump.idn()}")
    devices["dg_pump"] = dg_pump

    # ---- DG900: Probe 光功率 (dg_probe) ----
    cfg_probe = MAPPING["Probe_laser_power"]
    dg_probe = DG900Instrument(cfg_probe["resource"], channel=cfg_probe["channel"])
    dg_probe.connect()
    print(f"dg_probe 已连接: {dg_probe.idn()}")
    devices["dg_probe"] = dg_probe

    # ---- DG900: 温控开关 (dg_ts) ----
    cfg_ts = MAPPING["Temp_Switch"]
    dg_ts = DG900Instrument(cfg_ts["resource"], channel=cfg_ts["channel"])
    dg_ts.connect()
    print(f"dg_ts 已连接: {dg_ts.idn()}")
    devices["dg_ts"] = dg_ts

    # ---- SDS 示波器 ----
    cfg_scope = MAPPING["scope_waveform"]
    scope = SDSInstrument(cfg_scope["resource"])
    scope.connect()
    print(f"示波器已连接: {scope.idn()}")
    devices["scope"] = scope

    # ---- TEC103 温控器 ----
    cfg_tec = MAPPING["temperature"]
    tec = TECInstrument(port=cfg_tec["resource"])
    tec.connect()
    print(f"tec 已连接: TEC103 @ {cfg_tec['resource']}")
    devices["tec"] = tec

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

# %% Cell 5
# ========== 创建运行目录 ==========
timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
run_dir.mkdir(parents=True, exist_ok=True)
(raw_dir := run_dir / "raw").mkdir(exist_ok=True)
(results_dir := run_dir / "results").mkdir(exist_ok=True)
print(f"运行目录: {run_dir}")

# ---- 保存配置 ----
config = {
    "experiment_type": EXPERIMENT_TYPE,
    "timestamp": timestamp,
    "pump_switch_freq_Hz": PUMP_SWITCH_FREQ,
    "pump_mod_amplitude_V": PUMP_MOD_AMPLITUDE,
    "pump_prep_time_s": PUMP_PREP_TIME,
    "probe_power_V": PROBE_POWER,
    "x_field_amplitude_V": X_FIELD_AMPLITUDE,
    "scope": {
        "sampling_rate_Hz": SCOPE_SAMPLING_RATE,
        "acq_duration_s": ACQ_DURATION,
        "acq_repeats": ACQ_REPEATS,
        "trigger_source": TRIGGER_SOURCE,
        "volt_scale_V_per_div": SCOPE_VOLT_SCALE,
    },
    "temperature_C": TEC_TEMPERATURE,
}
with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print("配置已保存")

# ========== 初始设置 ==========

# 1. X 方向磁场
dg_x = devices["dg_x"]
validate_safety_limit("X_magnetic_field", X_FIELD_AMPLITUDE)
dg_x.setup_dc(X_FIELD_AMPLITUDE, channel=cfg_x["channel"])
print(f"X 磁场: {X_FIELD_AMPLITUDE}V (CH{cfg_x['channel']})")

# 2. 温控开关 ON（采集期间关闭）
dg_ts = devices["dg_ts"]
validate_safety_limit("Temp_Switch", 5.0)
dg_ts.setup_dc(5.0, channel=cfg_ts["channel"])
print(f"温控开关: ON (5V)")

# 3. Probe 光（CW DC）
dg_probe_dev = devices["dg_probe"]
validate_safety_limit("Probe_laser_power", PROBE_POWER)
dg_probe_dev.setup_dc(PROBE_POWER, channel=cfg_probe["channel"])
print(f"Probe 光: {PROBE_POWER}V (CH{cfg_probe['channel']})")

# 4. Pump: CH1 100MHz 载波常开 + CH2 低频方波
# [经验] 先重置 CH2，避免继承其他实验（如 T2）的 burst 模式
dg_pump_dev = devices["dg_pump"]
dg_pump_dev.set_burst_state(False, channel=2)     # 确保 burst 关闭
dg_pump_dev.set_mod_state(False, channel=2)       # 确保调制关闭
validate_safety_limit("Pump_modulation", PUMP_MOD_AMPLITUDE)
dg_pump_dev.apply_wave("SINusoid", channel=1, freq=100e6, amp=PUMP_MOD_AMPLITUDE, offset=0.0)
dg_pump_dev.set_output(True, channel=1)
dg_pump_dev.apply_wave("SQUare", channel=2,
                       freq=PUMP_SWITCH_FREQ, amp=5.0, offset=2.5, phase=180.0)
dg_pump_dev.set_square_dcycle(50.0, channel=2)
dg_pump_dev.set_sync_state(True, channel=2)
dg_pump_dev.set_output(True, channel=2)
print(f"Pump CH1: 100MHz {PUMP_MOD_AMPLITUDE}Vpp | CH2: {PUMP_SWITCH_FREQ}Hz 方波 (phase=180°)")

# 5. 温控稳定
tec_dev = devices["tec"]
validate_safety_limit("temperature", TEC_TEMPERATURE)
tec_dev.set_target_temperature(TEC_TEMPERATURE, channel=1)
tec_dev.set_enable(True, channel=1)
temp_now = tec_dev.get_temperature(channel=1)
print(f"温度设定: {TEC_TEMPERATURE}°C, 当前: {temp_now:.1f}°C")
print("等待温度稳定...")
while True:
    time.sleep(5)
    t = tec_dev.get_temperature(channel=1)
    print(f"  当前温度: {t:.2f} °C")
    if abs(t - TEC_TEMPERATURE) < 1.0:
        print(f"温度已稳定: {t:.2f} °C")
        break

# %% Cell 6
# ========== 示波器配置 ==========

scope = devices["scope"]
acq = SDSAcquisition(scope)

def build_scope_config(scale):
    """构建示波器配置，scale 为 CH1 当前档位 (V/div)。

    CH1 = PDB 差分信号, CH4 = Time_sequence 同步信号 (用于定位 t₀)。
    """
    cfg = AcquisitionConfig()
    cfg.sampling_rate = SCOPE_SAMPLING_RATE
    cfg.sampling_time = ACQ_DURATION
    cfg.acquire_delay = ACQ_DURATION + 0.5
    cfg.channels = [
        ChannelConfig(number=1, enabled=True, scale=scale,
                      coupling="DC", impedance="ONEMeg", probe=1.0),
        ChannelConfig(number=4, enabled=True, scale=2.0,
                      coupling="DC", impedance="ONEMeg", probe=1.0),
    ]
    cfg.trigger.mode = "SINGle"
    cfg.trigger.source = TRIGGER_SOURCE
    cfg.trigger.type = "EDGE"
    cfg.trigger.slope = "FALLing"
    cfg.trigger.level = TRIGGER_LEVEL
    return cfg

print("✅ 示波器配置就绪 (CH1 PDB + CH4 同步)")

# %% Cell 7
# ============================================================
# T₁ 测量 — 自适应档位 + 多次平均
# ============================================================

def auto_scale_signal(waveform, current_scale):
    """根据信号峰值双向自适应调整垂直档位。

    信号 < 40% 满格 → scale/=2 (放大)
    信号 > 90% 满格 → scale*=2 (防削顶)
    """
    abs_max = np.max(np.abs(waveform))
    full_scale = current_scale * VERT_DIVS
    new_scale = current_scale

    if abs_max < 0.4 * full_scale and current_scale > SCALE_MIN * 2:
        new_scale = current_scale / 2
    if abs_max > 0.9 * full_scale and current_scale < SCALE_MAX / 2:
        new_scale = current_scale * 2

    return new_scale, (new_scale != current_scale)


def _acquire_ch1_single(scope_cfg):
    """STOP → 配置 → RUN 的正确 SINGle 采集流程。"""
    inst = acq._inst
    inst.trigger_stop()
    time.sleep(0.05)
    inst.set_sampling_rate(scope_cfg.sampling_rate)
    inst.set_acquire_type(scope_cfg.acquire_type, scope_cfg.acquire_type_param)
    inst.set_timebase_scale(scope_cfg.timebase_scale)
    inst.set_timebase_delay(scope_cfg.timebase_delay)
    for ch in scope_cfg.channels:
        inst.set_channel_state(ch.number, ch.enabled)
        if ch.enabled:
            inst.set_channel_scale(ch.number, ch.scale)
            inst.set_channel_offset(ch.number, ch.offset)
            inst.set_channel_coupling(ch.number, ch.coupling)
            inst.set_channel_impedance(ch.number, ch.impedance)
            inst.set_channel_probe(ch.number, ch.probe)
    trig = scope_cfg.trigger
    inst.set_trigger_mode(trig.mode)
    inst.set_trigger_type(trig.type)
    inst.set_trigger_source(trig.source)
    inst.set_trigger_slope(trig.slope)
    inst.set_trigger_level(trig.level)
    time.sleep(0.05)
    inst.trigger_run()
    acq.start_acquisition(scope_cfg)
    acq.stop_acquisition()
    return acq.acquire_channel(1, scope_cfg.timebase_scale,
                               scope_cfg.horizontal_divisions,
                               trim_points=0)


def _fit_T1(t_decay, v_decay):
    """对衰减数据做带边界约束的单指数拟合。

    返回 (T1, T1_err, popt, pcov)。拟合失败或结果不合理则返回 NaN。
    """
    fit_mask = t_decay >= 0
    t_fit = t_decay[fit_mask]
    v_fit = v_decay[fit_mask]

    if len(t_fit) < 10:
        print(f"  ⚠ t≥0 仅 {len(t_fit)} 点，无法拟合")
        return np.nan, np.nan, [np.nan]*3, np.zeros((3, 3))

    A_guess = v_fit[0] - v_fit[-1]
    T1_guess = np.clip(0.03, T1_BOUNDS[0] * 2, T1_BOUNDS[1] / 2)
    C_guess = v_fit[-1]

    bounds = (
        [-np.inf, T1_BOUNDS[0], -np.inf],  # lower: T1 ≥ 0.5ms
        [np.inf, T1_BOUNDS[1], np.inf],     # upper: T1 ≤ 500ms
    )

    try:
        popt, pcov = curve_fit(
            exp_decay, t_fit, v_fit,
            p0=[A_guess, T1_guess, C_guess],
            bounds=bounds, maxfev=10000,
        )
        T1 = abs(popt[1])
        T1_err = np.sqrt(pcov[1, 1]) if pcov[1, 1] > 0 else np.inf

        # 质量检查：T1_err 不应超过 T1 本身
        if T1_err > T1 * 3:
            print(f"  ⚠ T1_err ({T1_err*1e3:.1f}ms) >> T1 ({T1*1e3:.1f}ms)，拟合不可靠")
            return np.nan, np.nan, popt, pcov

        print(f"  T₁ = {T1*1e3:.2f} ± {T1_err*1e3:.2f} ms")
        return T1, T1_err, popt, pcov

    except Exception as e:
        print(f"  拟合失败: {e}")
        return np.nan, np.nan, [np.nan]*3, np.zeros((3, 3))


def measure_T1_single(probe_power):
    """执行单次 T₁ 测量。

    CH2 方波 + CH4 sync → scope SINGle 触发 @ Pump 关断。
    时间轴零点 = 触发点 = t₀，t ≥ 0 为衰减段。
    """
    half_period = 0.5 / PUMP_SWITCH_FREQ

    dg_p = devices["dg_probe"]
    validate_safety_limit("Probe_laser_power", probe_power)
    dg_p.setup_dc(probe_power, channel=cfg_probe["channel"])
    print(f"Probe 功率: {probe_power:.3f} V")

    try:
        # ---- 采集 + 自适应档位（每帧都检查，通过即保存） ----
        scale = SCOPE_VOLT_SCALE
        scope_cfg_ref = build_scope_config(scale)
        all_vdiff = []
        t_axis = None

        for rep in range(ACQ_REPEATS):
            dg_ts_dev = devices["dg_ts"]
            dg_ts_dev.setup_dc(0.0, channel=cfg_ts["channel"])
            time.sleep(PUMP_PREP_TIME + half_period)

            # 自适应缩放: 最多重试 2 次
            for retry in range(3):
                ch1_result = _acquire_ch1_single(scope_cfg_ref)
                waveform = ch1_result.voltage

                new_scale, need_retry = auto_scale_signal(waveform, scale)
                if need_retry and retry < 2:
                    scale = new_scale
                    abs_max = np.max(np.abs(waveform))
                    tqdm.write(f"  [{rep+1}/{ACQ_REPEATS}] 缩放 retry{retry+1}: "
                               f"scale→{scale:.3f}V/div "
                               f"(peak={abs_max*1e3:.1f}mV, "
                               f"{abs_max/(scale*VERT_DIVS)*100:.0f}% 满格)")
                    scope.set_channel_scale(1, scale)
                    scope_cfg_ref.channels[0].scale = scale
                    time.sleep(half_period + PUMP_PREP_TIME + 0.05)
                    continue
                break  # 通过或已达最大重试

            dg_ts_dev.setup_dc(5.0, channel=cfg_ts["channel"])
            time.sleep(2)

            if t_axis is None:
                t_axis = ch1_result.time

            all_vdiff.append(waveform)
            abs_max = np.max(np.abs(waveform))
            print(f"  采集 {rep+1}/{ACQ_REPEATS}: "
                  f"peak={abs_max*1e3:.1f}mV, scale={scale:.3f}V/div "
                  f"({abs_max/(scale*VERT_DIVS)*100:.0f}% 满格)")

        vdiff_avg = np.mean(all_vdiff, axis=0)
        print(f"  成功采集 {len(all_vdiff)}/{ACQ_REPEATS} 帧，已平均, 最终档位={scale:.3f}V/div")

    finally:
        dg_ts_dev.setup_dc(5.0, channel=cfg_ts["channel"])
        print("  温控开关: ON (5V)")

    # 拟合（带边界约束）
    T1, T1_err, popt, pcov = _fit_T1(t_axis, vdiff_avg)

    return {
        "probe_power": probe_power, "T1": T1, "T1_err": T1_err,
        "vdiff_avg": vdiff_avg, "t_axis": t_axis, "t0": 0.0,
        "popt": popt, "pcov": pcov,
        "n_acquired": len(all_vdiff), "scale_used": scale,
    }


print("✅ T₁ 测量函数定义完成")

# %% Cell 8
# ========== 执行测量 ==========

if DO_POWER_SCAN:
    print("=" * 60)
    print("多功率 T₁ 扫描模式")
    print(f"功率列表: {PROBE_POWER_LIST}")
    print("=" * 60)

    all_results = []
    for i, pwr in enumerate(tqdm(PROBE_POWER_LIST, desc="功率扫描")):
        print(f"\n--- 功率点 {i+1}/{len(PROBE_POWER_LIST)}: {pwr:.3f} V ---")
        try:
            result = measure_T1_single(pwr)
            all_results.append(result)
        except Exception as e:
            print(f"  功率 {pwr:.3f} V 测量失败: {e}")
            continue

    np.savez(raw_dir / "power_scan_results.npz",
             probe_power=[r["probe_power"] for r in all_results],
             T1=[r["T1"] for r in all_results],
             T1_err=[r["T1_err"] for r in all_results],
             scale_used=[r["scale_used"] for r in all_results],
             n_acquired=[r["n_acquired"] for r in all_results])
    # 额外保存每点原始衰减波形 + 拟合参数（供 plot 程序画多功率叠加图）
    t_ref = all_results[0]["t_axis"]  # 所有点时间轴相同
    v_stack = np.array([r["vdiff_avg"] for r in all_results])
    popt_stack = np.array([r["popt"] if r["popt"] is not None else [np.nan]*3
                           for r in all_results])
    np.savez(raw_dir / "power_scan_waveforms.npz",
             t_axis=t_ref, vdiff_stack=v_stack, popt_stack=popt_stack,
             probe_power=[r["probe_power"] for r in all_results])
    print(f"波形数据已保存: {raw_dir / 'power_scan_waveforms.npz'}")
    print(f"\n多功率扫描结果已保存: {raw_dir / 'power_scan_results.npz'}")

else:
    print("=" * 60)
    print(f"单功率 T₁ 测量: Probe = {PROBE_POWER} V")
    print("=" * 60)

    result = measure_T1_single(PROBE_POWER)

    # 仅保存 t ≥ 0 的衰减段，丢弃触发前数据
    t_full = result["t_axis"]
    v_full = result["vdiff_avg"]
    mask = t_full >= 0
    t_decay = t_full[mask]
    v_decay = v_full[mask]
    print(f"  触发前丢弃: {np.sum(~mask)} 点, 衰减段: {np.sum(mask)} 点")

    np.savez(raw_dir / "t1_measurement.npz",
             probe_power=result["probe_power"],
             T1=result["T1"],
             T1_err=result["T1_err"],
             t_axis=t_decay,
             t0=0.0,
             vdiff_avg=v_decay,
             popt=result["popt"],
             pcov=result["pcov"],
             scale_used=result["scale_used"])
    print(f"\n原始数据已保存: {raw_dir / 't1_measurement.npz'}")


# %% Cell 9
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
