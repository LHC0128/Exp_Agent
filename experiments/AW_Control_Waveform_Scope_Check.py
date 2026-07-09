# %% [markdown] Cell 0
# # AW 控制链路示波器检查脚本
#
# 目的：
# - 配置 dg_am 输出 `control_waveformAW.csv` 对应的 X/Y AM 包络；
# - 配置 dg_comp 输出 90 kHz X/Y AM EXT 载波；
# - 使用固定载波相位，不做锁相放大器相位校准；
# - 输出保持开启，方便把 dg_am 或 dg_comp 接入示波器检查真实控制效果。

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import time
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

from signal_generator import DG4000Instrument

try:
    from sds_acquisition import (
        SDSInstrument, SDSAcquisition, AcquisitionConfig,
        ChannelConfig, TriggerConfig, save_to_csv, save_to_npz,
    )
except Exception:
    SDSInstrument = None
    SDSAcquisition = None
    AcquisitionConfig = None
    ChannelConfig = None
    TriggerConfig = None
    save_to_csv = None
    save_to_npz = None

print("库导入完成")

# %% Cell 2
# ========== 加载配置 ==========
with open(project_root / "params" / "mapping.yaml", encoding="utf-8") as f:
    MAPPING = yaml.safe_load(f)["mapping"]

with open(project_root / "params" / "safety_limits.yaml", encoding="utf-8") as f:
    LIMITS = yaml.safe_load(f)["safety_limits"]


def validate_safety_limit(name, value):
    """检查数值是否在安全范围内；未定义限值时只返回原值。"""
    lim = LIMITS.get(name)
    if lim is None:
        return value
    lo = lim.get("min")
    hi = lim.get("max")
    if lo is not None and value < lo:
        raise ValueError(f"[安全拦截] {name}={value} 低于下限 {lo}")
    if hi is not None and value > hi:
        raise ValueError(f"[安全拦截] {name}={value} 高于上限 {hi}")
    return value


def warn_if_missing_limit(name):
    """提示 safety_limits.yaml 中缺少的限值项。"""
    if name not in LIMITS:
        print(f"[WARN] safety_limits.yaml 未定义 {name}，脚本无法自动拦截该通道越界")


# ========== 顶部可调参数 ==========
EXPERIMENT_TYPE = "AW_Control_Waveform_Scope_Check"
RUN_TAG = "dg_comp_envelope_check"

ARB_WAVEFORM_FILE = "control_waveformAW.csv"
A_ENV_FREQ = 500.0
A_ENV_K = 16319.281020056458
A_ENV_B = 2121.8563579769193
A_ENV_K_X = A_ENV_K
A_ENV_B_X = A_ENV_B
A_ENV_K_Y = A_ENV_K
A_ENV_B_Y = A_ENV_B
AM_VOLTAGE_CONVERSION_MODE = "linear_voltage"
AM_ZERO_V_X = -0.1487540739959558
AM_ZERO_V_Y = -0.12523177770966548
MOD_OFFSET_X = AM_ZERO_V_X  # legacy, not applied in linear_voltage mode
MOD_OFFSET_Y = AM_ZERO_V_Y

XY_CARRIER_FREQ = 90e3
X_CARRIER_AMPLITUDE = 6.0
Y_CARRIER_AMPLITUDE = 6.0
X_AM_DEPTH = 100.0
Y_AM_DEPTH = 100.0
X_CARRIER_PHASE_deg = 0.0
Y_CARRIER_PHASE_deg = 90.0

# 示波器检查建议用连续输出，方便稳定触发和观察包络。
# 若要复现实验中的 burst 外触发，改成 True，并给 dg_am 外触发输入接好触发信号。
USE_BURST_EXT_TRIGGER_FOR_DG_AM = False

# 运行到末尾时暂停，方便观察；按 Enter 后根据 KEEP_OUTPUTS_AFTER_EXIT 决定是否关输出。
WAIT_BEFORE_EXIT = True
KEEP_OUTPUTS_AFTER_EXIT = True

# ========== 预期波形绘图 ==========
GENERATE_EXPECTED_PLOTS = True
EXPECTED_PLOT_DURATION_s = 6e-3
EXPECTED_PLOT_SAMPLE_RATE = 10e6
AM_INPUT_FULL_SCALE_V = 2.5

# ========== AM 输入经验模型 ==========
# transfer_curve: 读取 DG_AM_to_DG_COMP_Transfer_Check 的实测曲线，用插值得到载波包络；
# signed_clipped: fallback 理想模型，AM 输入保留正负号，负值对应载波相位翻转 180 deg。
AM_GAIN_MODEL = "transfer_curve"
AM_GAIN_CLIP_ABS = 1.0
TRANSFER_EXPERIMENT_TYPE = "DG_AM_to_DG_COMP_Transfer_Check"
TRANSFER_DATA_DIR = None  # None 表示读取最新 transfer 实验；也可指定 data/.../MMDD_HHMM_tag
TRANSFER_CURVE_FILENAME = "transfer_analysis_arrays.npz"
SCOPE_ENVELOPE_LPF_CYCLES = 6.0
SIGNED_PHASE_MIN_ABS_GAIN = 0.25

# ========== 示波器自动采集 ==========
# False: 只设置输出并保存理论图；True: 自动配置 SDS 并采集 C2/C3。
ENABLE_SCOPE_ACQUISITION = True
SCOPE_TARGET = "dg_comp"       # "dg_comp" 或 "dg_am"
SCOPE_CHANNEL_X = 2            # 示波器 C2 接 dg_comp CH1 / X
SCOPE_CHANNEL_Y = 3            # 示波器 C3 接 dg_comp CH2 / Y
SCOPE_INPUT_IMPEDANCE = "ONEMeg"  # ONEMeg 或 FIFTy
SCOPE_COUPLING = "DC"
SCOPE_PROBE_ATTENUATION = 1.0  # 直连 BNC 用 1.0；10x 探头改成 10.0
SCOPE_TRIGGER_MODE = "AUTO"
SCOPE_TRIGGER_SOURCE = "C2"
SCOPE_TRIGGER_LEVEL_V = 0.0
SAVE_SCOPE_CSV = True
SCOPE_COMPARE_ALIGN_TO_FIRST_SAMPLE = True

warn_if_missing_limit("X_magnetic_field_AM")
warn_if_missing_limit("Y_magnetic_field_AM")

# %% Cell 3
# ========== 工具函数 ==========
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
    raise ValueError("AM_VOLTAGE_CONVERSION_MODE must be linear_voltage or zero_centered")


def build_aw_params(v_waveform):
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


def load_aw_waveform():
    """加载 Omega_ctrl(t)，转换成 dg_am 的任意波参数。"""
    waveform_path = project_root / "experiments" / ARB_WAVEFORM_FILE
    waveform_data = np.loadtxt(waveform_path, delimiter=",", skiprows=1)
    time_ms = waveform_data[:, 0]
    omega_ctrl = waveform_data[:, 1]

    x_aw = build_aw_params(omega_to_am_voltage(omega_ctrl, "x"))
    y_aw = build_aw_params(omega_to_am_voltage(omega_ctrl, "y"))
    a_waveform = x_aw["waveform_V"]
    a_min = x_aw["min"]
    a_max = x_aw["max"]
    a_center = x_aw["offset"]
    a_normalized = x_aw["normalized"]
    arb_vpp = x_aw["vpp"]
    x_offset = x_aw["offset"]
    y_offset = y_aw["offset"]
    x_min = x_aw["min"]
    x_max = x_aw["max"]
    y_min = y_aw["min"]
    y_max = y_aw["max"]

    for name, value in [
        ("X_magnetic_field_AM", x_offset),
        ("X_magnetic_field_AM", x_min),
        ("X_magnetic_field_AM", x_max),
        ("Y_magnetic_field_AM", y_offset),
        ("Y_magnetic_field_AM", y_min),
        ("Y_magnetic_field_AM", y_max),
    ]:
        validate_safety_limit(name, value)

    print("AW 包络参数:")
    print(f"  CSV: {waveform_path}")
    print(f"  time: {time_ms[0]:.6f} ~ {time_ms[-1]:.6f} ms, points={len(time_ms)}")
    print(f"  Omega_ctrl: [{np.min(omega_ctrl):.2f}, {np.max(omega_ctrl):.2f}] Hz")
    print(f"  AM conversion mode: {AM_VOLTAGE_CONVERSION_MODE}")
    print(f"  CH1 AM voltage: [{x_min:.4f}, {x_max:.4f}] V, offset={x_offset:.4f} V")
    print(f"  CH2 AM voltage: [{y_min:.4f}, {y_max:.4f}] V, offset={y_offset:.4f} V")

    for label, lo, hi in [("CH1", x_min, x_max), ("CH2", y_min, y_max)]:
        if lo < -AM_INPUT_FULL_SCALE_V or hi > AM_INPUT_FULL_SCALE_V:
            print(f"[WARN] {label} 超出 DG4000 AM 输入经验满量程 "
                  f"±{AM_INPUT_FULL_SCALE_V:.2f} V；示波器重点观察饱和/削顶现象")

    return {
        "a_normalized": a_normalized.astype(float),
        "a_waveform": a_waveform.astype(float),
        "x_normalized": x_aw["normalized"].astype(float),
        "y_normalized": y_aw["normalized"].astype(float),
        "x_waveform": x_aw["waveform_V"].astype(float),
        "y_waveform": y_aw["waveform_V"].astype(float),
        "arb_vpp": float(arb_vpp),
        "x_vpp": x_aw["vpp"],
        "y_vpp": y_aw["vpp"],
        "x_offset": float(x_offset),
        "y_offset": float(y_offset),
        "x_range": [float(x_min), float(x_max)],
        "y_range": [float(y_min), float(y_max)],
        "a_center": float(a_center),
        "time_ms": time_ms,
        "omega_ctrl": omega_ctrl,
    }


def periodic_interp(t_query_s, t_src_s, y_src, period_s):
    """按给定周期对单周期波形做周期插值。"""
    t_mod = np.mod(t_query_s, period_s)
    t_ext = np.r_[t_src_s, period_s]
    y_ext = np.r_[y_src, y_src[0]]
    return np.interp(t_mod, t_ext, y_ext)


def latest_transfer_data_dir():
    """选择最新的 dg_am -> dg_comp 传递曲线数据目录。"""
    base = project_root / "data" / TRANSFER_EXPERIMENT_TYPE
    if not base.exists():
        return None
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    for data_dir in dirs:
        if (data_dir / "results" / TRANSFER_CURVE_FILENAME).exists():
            return data_dir
    return None


def load_transfer_curve_model():
    """加载实测 dg_am 电压到 dg_comp 载波峰值包络的传递曲线。"""
    if AM_GAIN_MODEL != "transfer_curve":
        return None

    data_dir = latest_transfer_data_dir() if TRANSFER_DATA_DIR is None else Path(TRANSFER_DATA_DIR)
    if data_dir is None or not data_dir.exists():
        raise FileNotFoundError(
            "未找到 DG_AM_to_DG_COMP_Transfer_Check 数据目录；"
            "请先运行传递曲线实验和对应 plot 脚本，或设置 TRANSFER_DATA_DIR"
        )

    curve_path = data_dir / "results" / TRANSFER_CURVE_FILENAME
    if not curve_path.exists():
        raise FileNotFoundError(f"未找到传递曲线数组: {curve_path}")

    curve = np.load(curve_path)
    required = [
        "am_dc_values_V",
        "ch_x_baseline_subtracted_amp_V",
        "ch_y_baseline_subtracted_amp_V",
    ]
    missing = [name for name in required if name not in curve.files]
    if missing:
        raise KeyError(f"{curve_path} 缺少字段: {missing}; 实际字段: {curve.files}")

    am_v = curve["am_dc_values_V"].astype(float)
    order = np.argsort(am_v)
    model = {
        "data_dir": str(data_dir),
        "curve_path": str(curve_path),
        "am_dc_values_V": am_v[order],
        "ch_x_envelope_V": curve["ch_x_baseline_subtracted_amp_V"].astype(float)[order],
        "ch_y_envelope_V": curve["ch_y_baseline_subtracted_amp_V"].astype(float)[order],
    }
    print("已加载 dg_am -> dg_comp 实测传递曲线:")
    print(f"  {curve_path}")
    print(
        "  AM range: "
        f"{model['am_dc_values_V'][0]:+.3f} ~ {model['am_dc_values_V'][-1]:+.3f} V"
    )
    return model


def transfer_envelope(am_voltage, transfer_model, channel):
    """用实测传递曲线插值得到 dg_comp 载波峰值包络。"""
    am_voltage = np.asarray(am_voltage, dtype=float)
    x = transfer_model["am_dc_values_V"]
    y_key = "ch_x_envelope_V" if channel == "x" else "ch_y_envelope_V"
    y = transfer_model[y_key]

    if np.min(am_voltage) < x[0] or np.max(am_voltage) > x[-1]:
        print(
            f"[WARN] {channel.upper()} AM 电压超出传递曲线范围 "
            f"[{x[0]:+.3f}, {x[-1]:+.3f}] V，超出部分将按边界值外推"
        )

    return np.interp(am_voltage, x, y)


def normalized_shape(y):
    """归一化波形形状，用于相关对齐和无量纲对比。"""
    y = np.asarray(y, dtype=float)
    y = y - np.mean(y)
    scale = np.std(y)
    if scale <= 0:
        return y
    return y / scale


def extract_carrier_envelope(time_s, voltage):
    """用数字锁相方式提取 90 kHz 载波包络幅值。"""
    return np.abs(demodulate_carrier_complex(time_s, voltage))


def demodulate_carrier_complex(time_s, voltage):
    """用数字锁相方式提取 90 kHz 载波的复包络。"""
    time_s = np.asarray(time_s, dtype=float)
    voltage = np.asarray(voltage, dtype=float)
    dt = float(np.median(np.diff(time_s)))
    sample_rate = 1.0 / dt
    window_points = int(round(SCOPE_ENVELOPE_LPF_CYCLES * sample_rate / XY_CARRIER_FREQ))
    window_points = max(window_points, 3)
    if window_points % 2 == 0:
        window_points += 1

    mixed = (voltage - np.mean(voltage)) * np.exp(-1j * 2.0 * np.pi * XY_CARRIER_FREQ * time_s)
    kernel = np.ones(window_points, dtype=float) / window_points
    baseband = np.convolve(mixed, kernel, mode="same")
    return 2.0 * baseband


def best_periodic_shift(measured, reference):
    """返回 measured 相对 reference 的最佳循环平移点数。"""
    a = normalized_shape(measured)
    b = normalized_shape(reference)
    corr = np.fft.ifft(np.fft.fft(a) * np.conj(np.fft.fft(b))).real
    shift = int(np.argmax(corr))
    coeff = float(corr[shift] / max(len(a), 1))
    return shift, coeff


def build_expected_waveforms(aw, x_phase_deg, y_phase_deg, transfer_model=None):
    """构造用于和示波器对比的预期 dg_am 与 dg_comp 波形。"""
    duration_s = float(EXPECTED_PLOT_DURATION_s)
    sample_rate = float(EXPECTED_PLOT_SAMPLE_RATE)
    n_points = int(round(duration_s * sample_rate))
    if n_points < 2:
        raise ValueError("EXPECTED_PLOT_DURATION_s * EXPECTED_PLOT_SAMPLE_RATE 太小")

    t = np.arange(n_points, dtype=float) / sample_rate
    t_src = aw["time_ms"] * 1e-3
    period_s = 1.0 / A_ENV_FREQ

    dg_am_x = periodic_interp(t, t_src, aw["x_waveform"], period_s)
    dg_am_y = periodic_interp(t, t_src, aw["y_waveform"], period_s)

    carrier_peak_x = X_CARRIER_AMPLITUDE / 2.0
    carrier_peak_y = Y_CARRIER_AMPLITUDE / 2.0

    if AM_GAIN_MODEL == "transfer_curve":
        if transfer_model is None:
            raise ValueError("AM_GAIN_MODEL='transfer_curve' 时必须提供 transfer_model")

        env_x = transfer_envelope(dg_am_x, transfer_model, "x")
        env_y = transfer_envelope(dg_am_y, transfer_model, "y")
        signed_amp_x = np.sign(dg_am_x) * env_x
        signed_amp_y = np.sign(dg_am_y) * env_y
        am_gain_x_raw = signed_amp_x / carrier_peak_x
        am_gain_y_raw = signed_amp_y / carrier_peak_y
        am_gain_x = am_gain_x_raw
        am_gain_y = am_gain_y_raw
        expected_envelope_x = np.abs(signed_amp_x)
        expected_envelope_y = np.abs(signed_amp_y)
        model_name = "transfer_curve"
    elif AM_GAIN_MODEL == "signed_clipped":
        # fallback 理想模型：AM 输入保留正负号；负 AM 等效为载波相位翻转 180 deg。
        # 绝对值超过满量程时按 ±AM_GAIN_CLIP_ABS 饱和。
        am_gain_x_raw = dg_am_x / AM_INPUT_FULL_SCALE_V
        am_gain_y_raw = dg_am_y / AM_INPUT_FULL_SCALE_V
        am_gain_x = np.clip(am_gain_x_raw, -AM_GAIN_CLIP_ABS, AM_GAIN_CLIP_ABS)
        am_gain_y = np.clip(am_gain_y_raw, -AM_GAIN_CLIP_ABS, AM_GAIN_CLIP_ABS)
        signed_amp_x = carrier_peak_x * am_gain_x
        signed_amp_y = carrier_peak_y * am_gain_y
        expected_envelope_x = np.abs(signed_amp_x)
        expected_envelope_y = np.abs(signed_amp_y)
        model_name = "signed_clipped"
    else:
        raise ValueError(f"未知 AM_GAIN_MODEL: {AM_GAIN_MODEL!r}")

    carrier_x = (
        signed_amp_x
        * np.sin(2.0 * np.pi * XY_CARRIER_FREQ * t + np.deg2rad(x_phase_deg))
    )
    carrier_y = (
        signed_amp_y
        * np.sin(2.0 * np.pi * XY_CARRIER_FREQ * t + np.deg2rad(y_phase_deg))
    )

    return {
        "time_s": t,
        "dg_am_ch1_V": dg_am_x,
        "dg_am_ch2_V": dg_am_y,
        "am_gain_ch1_raw": am_gain_x_raw,
        "am_gain_ch2_raw": am_gain_y_raw,
        "am_gain_ch1": am_gain_x,
        "am_gain_ch2": am_gain_y,
        "am_envelope_ch1_abs_V": np.abs(dg_am_x),
        "am_envelope_ch2_abs_V": np.abs(dg_am_y),
        "dg_comp_ch1_signed_amp_V": signed_amp_x,
        "dg_comp_ch2_signed_amp_V": signed_amp_y,
        "dg_comp_ch1_expected_envelope_V": expected_envelope_x,
        "dg_comp_ch2_expected_envelope_V": expected_envelope_y,
        "dg_comp_ch1_est_V": carrier_x,
        "dg_comp_ch2_est_V": carrier_y,
        "expected_model_name": np.array(model_name),
    }


def save_expected_waveform_plots(
    aw, x_phase_deg, y_phase_deg, raw_dir, results_dir, transfer_model=None
):
    """保存预期 AW 包络和 AM 后载波波形，供示波器对照。"""
    expected = build_expected_waveforms(aw, x_phase_deg, y_phase_deg, transfer_model)
    t_ms = expected["time_s"] * 1e3
    transfer_curve_path = "" if transfer_model is None else transfer_model["curve_path"]

    np.savez(
        raw_dir / "expected_waveforms.npz",
        **expected,
        x_carrier_phase_deg=np.float64(x_phase_deg),
        y_carrier_phase_deg=np.float64(y_phase_deg),
        am_input_full_scale_V=np.float64(AM_INPUT_FULL_SCALE_V),
        am_gain_clip_abs=np.float64(AM_GAIN_CLIP_ABS),
        transfer_curve_path=np.array(transfer_curve_path),
        expected_model=np.array(
            "transfer_curve uses measured dg_am -> dg_comp envelope; "
            "signed_clipped is a fallback ideal model; negative signed amplitude "
            "means 180 deg carrier phase flip"
        ),
    )

    fig1, ax1 = plt.subplots(figsize=(10, 5.5))
    ax1.plot(t_ms, expected["dg_am_ch1_V"], lw=1.0, label="dg_am CH1")
    ax1.plot(t_ms, expected["dg_am_ch2_V"], lw=1.0, label="dg_am CH2")
    ax1.axhspan(-AM_INPUT_FULL_SCALE_V, AM_INPUT_FULL_SCALE_V, color="C2", alpha=0.12,
                label="Nominal signed AM input range")
    ax1.axhline(0.0, color="black", lw=0.7, alpha=0.6)
    ax1.axhline(AM_INPUT_FULL_SCALE_V, color="black", lw=0.7, ls="--", alpha=0.5)
    ax1.axhline(-AM_INPUT_FULL_SCALE_V, color="black", lw=0.7, ls="--", alpha=0.5)
    ax1.set_xlabel("Time (ms)")
    ax1.set_ylabel("Voltage (V)")
    ax1.set_title("Expected dg_am AW Envelope")
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=9)
    fig1.tight_layout()
    fig1.savefig(results_dir / "expected_dg_am_envelope.png",
                 dpi=150, bbox_inches="tight")
    plt.close(fig1)

    fig2, (ax2a, ax2b) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True)
    ax2a.plot(t_ms, expected["dg_comp_ch1_est_V"], lw=0.55, label="dg_comp CH1 estimate")
    ax2a.plot(t_ms, expected["dg_comp_ch2_est_V"], lw=0.55, label="dg_comp CH2 estimate")
    ax2a.set_ylabel("Voltage (V)")
    ax2a.set_title("Estimated AM-Modulated Carrier (Full Window)")
    ax2a.grid(True, alpha=0.3)
    ax2a.legend(fontsize=9)
    ax2b.plot(t_ms, expected["am_gain_ch1"], lw=1.0, label="CH1 signed AM gain")
    ax2b.plot(t_ms, expected["am_gain_ch2"], lw=1.0, label="CH2 signed AM gain")
    ax2b.axhline(0.0, color="black", lw=0.7, alpha=0.6)
    ax2b.set_xlabel("Time (ms)")
    ax2b.set_ylabel("Signed gain")
    ax2b.grid(True, alpha=0.3)
    ax2b.legend(fontsize=9)
    fig2.tight_layout()
    fig2.savefig(results_dir / "expected_dg_comp_modulated_full.png",
                 dpi=150, bbox_inches="tight")
    plt.close(fig2)

    zoom_mask = expected["time_s"] <= min(2.0e-4, expected["time_s"][-1])
    fig3, ax3 = plt.subplots(figsize=(10, 5.5))
    ax3.plot(t_ms[zoom_mask], expected["dg_comp_ch1_est_V"][zoom_mask],
             lw=1.0, label="dg_comp CH1 estimate")
    ax3.plot(t_ms[zoom_mask], expected["dg_comp_ch2_est_V"][zoom_mask],
             lw=1.0, label="dg_comp CH2 estimate")
    ax3.set_xlabel("Time (ms)")
    ax3.set_ylabel("Voltage (V)")
    ax3.set_title("Estimated AM-Modulated Carrier (Zoom)")
    ax3.grid(True, alpha=0.3)
    ax3.legend(fontsize=9)
    fig3.tight_layout()
    fig3.savefig(results_dir / "expected_dg_comp_modulated_zoom.png",
                 dpi=150, bbox_inches="tight")
    plt.close(fig3)

    print("预期波形已保存:")
    print(f"  {raw_dir / 'expected_waveforms.npz'}")
    print(f"  {results_dir / 'expected_dg_am_envelope.png'}")
    print(f"  {results_dir / 'expected_dg_comp_modulated_full.png'}")
    print(f"  {results_dir / 'expected_dg_comp_modulated_zoom.png'}")
    return expected


def build_scope_acquisition_config(target):
    """根据测量目标生成 SDS 示波器采集配置。"""
    if AcquisitionConfig is None:
        raise RuntimeError("无法导入 sds_acquisition，请确认本地包已安装")

    target = target.lower()
    if target == "dg_am":
        sampling_rate = 2.0e6
        sampling_time = 6.0e-3
        scale = 0.7
        offset = 0.0
        trigger_level = 0.0
    elif target == "dg_comp":
        sampling_rate = 10.0e6
        sampling_time = 6.0e-3
        scale = 1.0
        offset = 0.0
        trigger_level = SCOPE_TRIGGER_LEVEL_V
    else:
        raise ValueError("SCOPE_TARGET 必须是 'dg_comp' 或 'dg_am'")

    ch_x = ChannelConfig(
        number=SCOPE_CHANNEL_X,
        enabled=True,
        scale=scale,
        offset=offset,
        coupling=SCOPE_COUPLING,
        impedance=SCOPE_INPUT_IMPEDANCE,
        probe=SCOPE_PROBE_ATTENUATION,
    )
    ch_y = ChannelConfig(
        number=SCOPE_CHANNEL_Y,
        enabled=True,
        scale=scale,
        offset=offset,
        coupling=SCOPE_COUPLING,
        impedance=SCOPE_INPUT_IMPEDANCE,
        probe=SCOPE_PROBE_ATTENUATION,
    )
    channels = [ch_x, ch_y]
    for ch in (1, 2, 3, 4):
        if ch not in (SCOPE_CHANNEL_X, SCOPE_CHANNEL_Y):
            channels.append(ChannelConfig(number=ch, enabled=False))

    return AcquisitionConfig(
        sampling_rate=sampling_rate,
        sampling_time=sampling_time,
        acquire_type="NORMal",
        acquire_type_param=None,
        acquire_delay=0.5,
        channels=channels,
        trigger=TriggerConfig(
            mode=SCOPE_TRIGGER_MODE,
            source=SCOPE_TRIGGER_SOURCE,
            type="EDGE",
            slope="RISing",
            level=trigger_level,
        ),
    )


def expected_trace_for_scope_channel(expected, target, scope_channel):
    """按示波器通道返回对应的预期波形。"""
    if expected is None:
        return None

    if scope_channel == SCOPE_CHANNEL_X:
        key = "dg_comp_ch1_est_V" if target == "dg_comp" else "dg_am_ch1_V"
        label = "Expected X / CH1"
    elif scope_channel == SCOPE_CHANNEL_Y:
        key = "dg_comp_ch2_est_V" if target == "dg_comp" else "dg_am_ch2_V"
        label = "Expected Y / CH2"
    else:
        return None

    return expected["time_s"], expected[key], label


def plot_scope_results(results, results_dir, target, expected=None):
    """保存示波器采集波形预览图，并在有预期波形时叠加对比。"""
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for r in results:
        t_scope = r.time - r.time[0] if SCOPE_COMPARE_ALIGN_TO_FIRST_SAMPLE else r.time
        ax.plot(t_scope * 1e3, r.voltage, lw=0.8, label=f"Scope C{r.channel}")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Voltage (V)")
    ax.set_title(f"Scope Capture: {target}")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    path = results_dir / f"scope_capture_{target}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"示波器预览图已保存: {path}")
    compare_path = None
    if expected is not None:
        fig_cmp, axes = plt.subplots(len(results), 1, figsize=(10, 3.8 * len(results)),
                                     sharex=True, squeeze=False)
        axes = axes[:, 0]
        for ax_i, r in zip(axes, results):
            t_scope = r.time - r.time[0] if SCOPE_COMPARE_ALIGN_TO_FIRST_SAMPLE else r.time
            ax_i.plot(t_scope * 1e3, r.voltage, lw=0.8, label=f"Scope C{r.channel}")

            exp = expected_trace_for_scope_channel(expected, target, r.channel)
            if exp is not None:
                t_exp, v_exp, label = exp
                ax_i.plot(t_exp * 1e3, v_exp, lw=0.9, ls="--", alpha=0.85, label=label)

            ax_i.set_ylabel("Voltage (V)")
            ax_i.grid(True, alpha=0.3)
            ax_i.legend(fontsize=9)

        axes[-1].set_xlabel("Time (ms)")
        fig_cmp.suptitle(f"Scope vs Expected: {target}")
        fig_cmp.tight_layout()
        compare_path = results_dir / f"scope_vs_expected_{target}.png"
        fig_cmp.savefig(compare_path, dpi=150, bbox_inches="tight")
        plt.close(fig_cmp)
        print(f"示波器与预期对比图已保存: {compare_path}")

    return path, compare_path


def expected_envelope_for_scope_channel(expected, scope_channel):
    """返回与示波器通道对应的 AM 幅值和预期 dg_comp 包络幅值。"""
    if expected is None:
        return None

    if scope_channel == SCOPE_CHANNEL_X:
        return {
            "time_s": expected["time_s"],
            "am_abs_V": expected["am_envelope_ch1_abs_V"],
            "carrier_env_V": expected["dg_comp_ch1_expected_envelope_V"],
            "label": "X / CH1",
        }
    if scope_channel == SCOPE_CHANNEL_Y:
        return {
            "time_s": expected["time_s"],
            "am_abs_V": expected["am_envelope_ch2_abs_V"],
            "carrier_env_V": expected["dg_comp_ch2_expected_envelope_V"],
            "label": "Y / CH2",
        }
    return None


def expected_signed_gain_for_scope_channel(expected, scope_channel):
    """返回与示波器通道对应的带符号 AM 增益。"""
    if expected is None:
        return None

    if scope_channel == SCOPE_CHANNEL_X:
        return {
            "time_s": expected["time_s"],
            "signed_gain": expected["am_gain_ch1"],
            "am_voltage_V": expected["dg_am_ch1_V"],
            "label": "X / CH1",
        }
    if scope_channel == SCOPE_CHANNEL_Y:
        return {
            "time_s": expected["time_s"],
            "signed_gain": expected["am_gain_ch2"],
            "am_voltage_V": expected["dg_am_ch2_V"],
            "label": "Y / CH2",
        }
    return None


def wrap_phase_deg(phase_deg):
    """把角度折叠到 [-180, 180) deg。"""
    return (phase_deg + 180.0) % 360.0 - 180.0


def circular_mean_deg(phase_deg):
    """角度圆均值，单位 deg。"""
    phase_rad = np.deg2rad(phase_deg)
    z = np.mean(np.exp(1j * phase_rad))
    return float(np.rad2deg(np.angle(z)))


def plot_scope_envelope_vs_am(results, results_dir, target, expected):
    """提取 dg_comp 载波包络，并与预期载波包络做形状对比。"""
    if target != "dg_comp" or expected is None:
        return None, []

    fig, axes = plt.subplots(len(results), 2, figsize=(12, 3.6 * len(results)),
                             squeeze=False)
    metrics = []

    for row, r in enumerate(results):
        exp = expected_envelope_for_scope_channel(expected, r.channel)
        if exp is None:
            continue

        t_scope = r.time - r.time[0] if SCOPE_COMPARE_ALIGN_TO_FIRST_SAMPLE else r.time
        measured_env = extract_carrier_envelope(t_scope, r.voltage)
        ref_env = np.interp(t_scope, exp["time_s"], exp["carrier_env_V"])
        shift, corr = best_periodic_shift(measured_env, ref_env)
        measured_env_aligned = np.roll(measured_env, -shift)

        ref_carrier_env = ref_env
        ref_am_abs = np.interp(t_scope, exp["time_s"], exp["am_abs_V"])

        ax0 = axes[row, 0]
        ax0.plot(t_scope * 1e3, measured_env_aligned, lw=1.0,
                 label=f"Scope C{r.channel} carrier envelope")
        ax0.plot(t_scope * 1e3, ref_carrier_env, lw=1.0, ls="--",
                 label="Expected carrier envelope")
        ax0.set_title(f"{exp['label']} Envelope")
        ax0.set_xlabel("Time (ms)")
        ax0.set_ylabel("Voltage (V)")
        ax0.grid(True, alpha=0.3)
        ax0.legend(fontsize=8)

        meas_norm = measured_env_aligned / max(np.percentile(measured_env_aligned, 95), 1e-12)
        am_norm = ref_env / max(np.percentile(ref_env, 95), 1e-12)

        ax1 = axes[row, 1]
        ax1.plot(t_scope * 1e3, meas_norm, lw=1.0,
                 label="Scope envelope norm.")
        ax1.plot(t_scope * 1e3, am_norm, lw=1.0, ls="--",
                 label="Expected envelope norm.")
        ax1.set_title(f"{exp['label']} Shape Corr = {corr:.3f}")
        ax1.set_xlabel("Time (ms)")
        ax1.set_ylabel("Normalized amplitude")
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=8)

        metrics.append({
            "channel": int(r.channel),
            "label": exp["label"],
            "best_shift_samples": int(shift),
            "best_shift_ms": float(shift * np.median(np.diff(t_scope)) * 1e3),
            "shape_correlation": float(corr),
            "scope_envelope_peak_V": float(np.max(measured_env_aligned)),
            "expected_carrier_envelope_peak_V": float(np.max(ref_carrier_env)),
            "am_abs_peak_V": float(np.max(ref_am_abs)),
        })

    fig.suptitle("Scope Carrier Envelope vs Expected Envelope")
    fig.tight_layout()
    path = results_dir / f"scope_envelope_vs_am_{target}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"示波器包络与预期包络对比图已保存: {path}")
    return path, metrics


def plot_signed_phase_check(results, results_dir, target, expected):
    """用已知 AW 波形做参考，验证负 AM 是否对应 180 deg 载波相位翻转。"""
    if target != "dg_comp" or expected is None:
        return None, []

    fig, axes = plt.subplots(len(results), 3, figsize=(15, 3.6 * len(results)),
                             squeeze=False)
    metrics = []

    for row, r in enumerate(results):
        exp_env = expected_envelope_for_scope_channel(expected, r.channel)
        exp_signed = expected_signed_gain_for_scope_channel(expected, r.channel)
        if exp_env is None or exp_signed is None:
            continue

        t_scope = r.time - r.time[0] if SCOPE_COMPARE_ALIGN_TO_FIRST_SAMPLE else r.time
        z = demodulate_carrier_complex(t_scope, r.voltage)
        measured_env = np.abs(z)
        ref_abs = np.interp(t_scope, exp_env["time_s"], exp_env["carrier_env_V"])
        shift, envelope_corr = best_periodic_shift(measured_env, ref_abs)
        z_aligned = np.roll(z, -shift)

        signed_gain = np.interp(t_scope, exp_signed["time_s"], exp_signed["signed_gain"])
        am_voltage = np.interp(t_scope, exp_signed["time_s"], exp_signed["am_voltage_V"])
        valid = np.abs(signed_gain) >= SIGNED_PHASE_MIN_ABS_GAIN
        pos = valid & (signed_gain > 0)
        neg = valid & (signed_gain < 0)

        if np.count_nonzero(pos) < 10:
            ref_mask = valid
        else:
            ref_mask = pos
        phase_ref = np.angle(np.mean(z_aligned[ref_mask]))
        z_ref = z_aligned * np.exp(-1j * phase_ref)
        signed_amp = np.real(z_ref)

        signed_amp_norm = signed_amp / max(np.percentile(np.abs(signed_amp[valid]), 95), 1e-12)
        gain_norm = signed_gain / max(np.percentile(np.abs(signed_gain[valid]), 95), 1e-12)
        signed_corr = float(np.corrcoef(signed_amp_norm[valid], gain_norm[valid])[0, 1])

        phase_rel = wrap_phase_deg(np.rad2deg(np.angle(z_ref)))
        pos_phase_mean = circular_mean_deg(phase_rel[pos]) if np.count_nonzero(pos) else np.nan
        neg_phase_mean = circular_mean_deg(phase_rel[neg]) if np.count_nonzero(neg) else np.nan
        phase_flip_deg = wrap_phase_deg(neg_phase_mean - pos_phase_mean)

        ax0, ax1, ax2 = axes[row]
        ax0.plot(t_scope * 1e3, gain_norm, lw=1.0, ls="--", label="AW signed gain norm.")
        ax0.plot(t_scope * 1e3, signed_amp_norm, lw=0.9,
                 label="Scope signed demod. norm.")
        ax0.axhline(0.0, color="black", lw=0.7, alpha=0.6)
        ax0.set_title(f"{exp_signed['label']} Signed Amplitude")
        ax0.set_xlabel("Time (ms)")
        ax0.set_ylabel("Normalized signed amp.")
        ax0.grid(True, alpha=0.3)
        ax0.legend(fontsize=8)

        ax1.scatter(am_voltage[valid], signed_amp_norm[valid], s=4, alpha=0.35)
        ax1.axhline(0.0, color="black", lw=0.7, alpha=0.6)
        ax1.axvline(0.0, color="black", lw=0.7, alpha=0.6)
        ax1.set_title(f"Corr = {signed_corr:.3f}")
        ax1.set_xlabel("Expected dg_am (V)")
        ax1.set_ylabel("Scope signed demod. norm.")
        ax1.grid(True, alpha=0.3)

        ax2.scatter(t_scope[pos] * 1e3, phase_rel[pos], s=4, alpha=0.35,
                    label=f"AM > 0 mean {pos_phase_mean:.1f} deg")
        ax2.scatter(t_scope[neg] * 1e3, phase_rel[neg], s=4, alpha=0.35,
                    label=f"AM < 0 mean {neg_phase_mean:.1f} deg")
        ax2.axhline(0.0, color="black", lw=0.7, alpha=0.6)
        ax2.axhline(180.0, color="black", lw=0.7, ls="--", alpha=0.4)
        ax2.axhline(-180.0, color="black", lw=0.7, ls="--", alpha=0.4)
        ax2.set_ylim(-190, 190)
        ax2.set_title(f"Phase Flip = {phase_flip_deg:.1f} deg")
        ax2.set_xlabel("Time (ms)")
        ax2.set_ylabel("Relative phase (deg)")
        ax2.grid(True, alpha=0.3)
        ax2.legend(fontsize=8)

        metrics.append({
            "channel": int(r.channel),
            "label": exp_signed["label"],
            "best_shift_samples": int(shift),
            "best_shift_ms": float(shift * np.median(np.diff(t_scope)) * 1e3),
            "envelope_shape_correlation": float(envelope_corr),
            "signed_amplitude_correlation": signed_corr,
            "positive_phase_mean_deg": float(pos_phase_mean),
            "negative_phase_mean_deg": float(neg_phase_mean),
            "phase_flip_deg": float(phase_flip_deg),
            "valid_points": int(np.count_nonzero(valid)),
            "positive_points": int(np.count_nonzero(pos)),
            "negative_points": int(np.count_nonzero(neg)),
        })

    fig.suptitle("Signed AM Phase Check")
    fig.tight_layout()
    path = results_dir / f"scope_signed_phase_check_{target}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"带符号 AM 相位检查图已保存: {path}")
    return path, metrics


def acquire_scope_waveforms(raw_dir, results_dir, expected=None):
    """配置 SDS 示波器，采集 C2/C3 并保存数据和预览图。"""
    if SDSInstrument is None:
        raise RuntimeError("无法导入 SDSInstrument，请确认 sds_acquisition 可用")

    scope_cfg = MAPPING["scope_waveform"]
    scope = SDSInstrument(scope_cfg["resource"])
    scope.connect()
    print(f"示波器已连接: {scope.idn()}")
    try:
        config = build_scope_acquisition_config(SCOPE_TARGET)
        acq = SDSAcquisition(scope)
        results = acq.acquire_all(config)
        if not results:
            raise RuntimeError("示波器未采集到任何通道数据")

        npz_path = save_to_npz(
            str(raw_dir / f"scope_capture_{SCOPE_TARGET}.npz"),
            results,
            config.to_dict(),
            save_mode="voltage_time",
        )
        csv_paths = []
        if SAVE_SCOPE_CSV:
            for r in results:
                csv_paths.append(save_to_csv(
                    str(raw_dir / f"scope_capture_{SCOPE_TARGET}.csv"),
                    r,
                ))

        preview_path, compare_path = plot_scope_results(
            results=results,
            results_dir=results_dir,
            target=SCOPE_TARGET,
            expected=expected,
        )
        envelope_compare_path, envelope_metrics = plot_scope_envelope_vs_am(
            results=results,
            results_dir=results_dir,
            target=SCOPE_TARGET,
            expected=expected,
        )
        signed_phase_path, signed_phase_metrics = plot_signed_phase_check(
            results=results,
            results_dir=results_dir,
            target=SCOPE_TARGET,
            expected=expected,
        )
        print(f"示波器 NPZ 已保存: {npz_path}")
        for path in csv_paths:
            print(f"示波器 CSV 已保存: {path}")
        return {
            "npz_path": str(npz_path),
            "csv_paths": [str(p) for p in csv_paths],
            "preview_path": str(preview_path),
            "num_channels": len(results),
            "channels": [int(r.channel) for r in results],
            "num_points": [int(len(r.voltage)) for r in results],
            "compare_path": None if compare_path is None else str(compare_path),
            "envelope_compare_path": (
                None if envelope_compare_path is None else str(envelope_compare_path)
            ),
            "envelope_metrics": envelope_metrics,
            "signed_phase_path": None if signed_phase_path is None else str(signed_phase_path),
            "signed_phase_metrics": signed_phase_metrics,
            "config": config.to_dict(),
        }
    finally:
        scope.disconnect()


def configure_dg_comp_am(dg_comp, x_phase_deg, y_phase_deg):
    """配置 dg_comp 为 X/Y 90 kHz AM EXT 载波输出。"""
    for ch in (1, 2):
        dg_comp.set_burst_state(False, channel=ch)
        dg_comp.set_mod_state(False, channel=ch)

    dg_comp.setup_sine(
        freq=XY_CARRIER_FREQ,
        amplitude=X_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=float(x_phase_deg),
        channel=1,
    )
    dg_comp.set_mod_type("AM", channel=1)
    dg_comp.set_mod_am_source("EXT", channel=1)
    dg_comp.set_mod_am_depth(X_AM_DEPTH, channel=1)
    dg_comp.set_mod_state(True, channel=1)

    dg_comp.setup_sine(
        freq=XY_CARRIER_FREQ,
        amplitude=Y_CARRIER_AMPLITUDE,
        offset=0.0,
        phase=float(y_phase_deg),
        channel=2,
    )
    dg_comp.set_mod_type("AM", channel=2)
    dg_comp.set_mod_am_source("EXT", channel=2)
    dg_comp.set_mod_am_depth(Y_AM_DEPTH, channel=2)
    dg_comp.set_mod_state(True, channel=2)

    dg_comp.set_output(True, channel=1)
    dg_comp.set_output(True, channel=2)
    print("dg_comp 已配置:")
    print(f"  CH1 X: {XY_CARRIER_FREQ/1e3:.1f} kHz, {X_CARRIER_AMPLITUDE:.3f} Vpp, "
          f"AM EXT {X_AM_DEPTH:.1f}%, phase={x_phase_deg:.2f} deg")
    print(f"  CH2 Y: {XY_CARRIER_FREQ/1e3:.1f} kHz, {Y_CARRIER_AMPLITUDE:.3f} Vpp, "
          f"AM EXT {Y_AM_DEPTH:.1f}%, phase={y_phase_deg:.2f} deg")


def configure_dg_am_aw(dg_am, aw):
    """配置 dg_am 输出 X/Y AW 包络。"""
    for ch in (1, 2):
        dg_am.set_burst_state(False, channel=ch)
        dg_am.set_mod_state(False, channel=ch)

    dg_am.setup_arbitrary(
        aw["x_normalized"].copy(),
        freq=A_ENV_FREQ,
        amplitude=aw["x_vpp"],
        offset=aw["x_offset"],
        phase=0.0,
        channel=1,
    )
    dg_am.setup_arbitrary(
        aw["y_normalized"].copy(),
        freq=A_ENV_FREQ,
        amplitude=aw["y_vpp"],
        offset=aw["y_offset"],
        phase=0.0,
        channel=2,
    )

    if USE_BURST_EXT_TRIGGER_FOR_DG_AM:
        for ch in (1, 2):
            dg_am.set_burst_state(True, channel=ch)
            dg_am.set_burst_mode("INFinity", channel=ch)
            dg_am.set_burst_trigger_source("EXTernal", channel=ch)
            dg_am.set_burst_phase(0.0, channel=ch)
        print("dg_am 已配置为 AW + Burst INFinity + EXT trigger")
    else:
        for ch in (1, 2):
            dg_am.set_burst_state(False, channel=ch)
        print("dg_am 已配置为连续 AW 输出")

    dg_am.set_output(True, channel=1)
    dg_am.set_output(True, channel=2)
    print(f"  CH1: AW {A_ENV_FREQ:.1f} Hz, Vpp={aw['x_vpp']:.4f} V, "
          f"offset={aw['x_offset']:.4f} V")
    print(f"  CH2: AW {A_ENV_FREQ:.1f} Hz, Vpp={aw['y_vpp']:.4f} V, "
          f"offset={aw['y_offset']:.4f} V")


def safe_outputs_off(devices):
    """关闭本脚本负责的输出。"""
    for name in ("dg_am", "dg_comp"):
        dev = devices.get(name)
        if dev is None:
            continue
        for ch in (1, 2):
            try:
                if hasattr(dev, "set_burst_state"):
                    dev.set_burst_state(False, channel=ch)
                if hasattr(dev, "set_mod_state"):
                    dev.set_mod_state(False, channel=ch)
                if hasattr(dev, "setup_dc"):
                    dev.setup_dc(0.0, channel=ch)
                dev.set_output(False, channel=ch)
            except Exception as exc:
                print(f"[WARN] {name} CH{ch} 关闭失败: {exc}")


# %% Cell 4
# ========== 连接设备 ==========
devices = {}

try:
    dg_comp_cfg = MAPPING["X_magnetic_field"]
    dg_comp = DG4000Instrument(dg_comp_cfg["resource"], channel=1)
    dg_comp.connect()
    dg_comp.set_ref_clock_source("EXTernal")
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)
    devices["dg_comp"] = dg_comp
    print(f"dg_comp 已连接: {dg_comp.idn()}")

    dg_am_cfg = MAPPING["X_magnetic_field_AM"]
    dg_am = DG4000Instrument(dg_am_cfg["resource"], channel=1)
    dg_am.connect()
    dg_am.set_ref_clock_source("EXTernal")
    dg_am.set_output(False, channel=1)
    dg_am.set_output(False, channel=2)
    devices["dg_am"] = dg_am
    print(f"dg_am 已连接: {dg_am.idn()}")

except Exception:
    safe_outputs_off(devices)
    for dev in devices.values():
        try:
            dev.disconnect()
        except Exception:
            pass
    raise

# %% Cell 5
# ========== 配置 AW + AM 载波 ==========
aw = load_aw_waveform()
x_phase = float(X_CARRIER_PHASE_deg)
y_phase = float(Y_CARRIER_PHASE_deg)
configure_dg_comp_am(devices["dg_comp"], x_phase, y_phase)

configure_dg_am_aw(devices["dg_am"], aw)

timestamp = datetime.now().strftime("%m%d_%H%M")
run_dir = project_root / "data" / EXPERIMENT_TYPE / f"{timestamp}_{RUN_TAG}"
raw_dir = run_dir / "raw"
results_dir = run_dir / "results"
raw_dir.mkdir(parents=True, exist_ok=True)
results_dir.mkdir(parents=True, exist_ok=True)

expected = None
transfer_model = (
    load_transfer_curve_model()
    if (GENERATE_EXPECTED_PLOTS or ENABLE_SCOPE_ACQUISITION)
    else None
)
if GENERATE_EXPECTED_PLOTS:
    expected = save_expected_waveform_plots(
        aw=aw,
        x_phase_deg=x_phase,
        y_phase_deg=y_phase,
        raw_dir=raw_dir,
        results_dir=results_dir,
        transfer_model=transfer_model,
    )

scope_capture = None
if ENABLE_SCOPE_ACQUISITION:
    scope_capture = acquire_scope_waveforms(raw_dir, results_dir, expected=expected)

config_path = run_dir / "scope_check_config.yaml"
scope_config = {
    "experiment_type": EXPERIMENT_TYPE,
    "run_tag": RUN_TAG,
    "timestamp": timestamp,
    "phase_source": "fixed_scope_check",
    "x_carrier_phase_deg": float(x_phase),
    "y_carrier_phase_deg": float(y_phase),
    "xy_phase_diff_deg": float((y_phase - x_phase) % 360.0),
    "use_burst_ext_trigger_for_dg_am": bool(USE_BURST_EXT_TRIGGER_FOR_DG_AM),
    "envelope": {
        "ARB_WAVEFORM_FILE": ARB_WAVEFORM_FILE,
        "A_ENV_FREQ_Hz": A_ENV_FREQ,
        "AM_VOLTAGE_CONVERSION_MODE": AM_VOLTAGE_CONVERSION_MODE,
        "A_ENV_K_Hz_per_V": A_ENV_K,
        "A_ENV_B_Hz": A_ENV_B,
        "A_ENV_K_X_Hz_per_V": A_ENV_K_X,
        "A_ENV_B_X_Hz": A_ENV_B_X,
        "A_ENV_K_Y_Hz_per_V": A_ENV_K_Y,
        "A_ENV_B_Y_Hz": A_ENV_B_Y,
        "AM_ZERO_V_X": AM_ZERO_V_X,
        "AM_ZERO_V_Y": AM_ZERO_V_Y,
        "MOD_OFFSET_X_V_legacy_not_applied": MOD_OFFSET_X,
        "MOD_OFFSET_Y_V_legacy_not_applied": MOD_OFFSET_Y,
        "x_vpp_V": aw["x_vpp"],
        "y_vpp_V": aw["y_vpp"],
        "x_offset_V": aw["x_offset"],
        "y_offset_V": aw["y_offset"],
        "x_range_V": aw["x_range"],
        "y_range_V": aw["y_range"],
    },
    "xy_carrier": {
        "XY_CARRIER_FREQ_Hz": XY_CARRIER_FREQ,
        "X_CARRIER_AMPLITUDE_Vpp": X_CARRIER_AMPLITUDE,
        "Y_CARRIER_AMPLITUDE_Vpp": Y_CARRIER_AMPLITUDE,
        "X_AM_DEPTH_pct": X_AM_DEPTH,
        "Y_AM_DEPTH_pct": Y_AM_DEPTH,
    },
    "expected_plots": {
        "enabled": bool(GENERATE_EXPECTED_PLOTS),
        "duration_s": float(EXPECTED_PLOT_DURATION_s),
        "sample_rate_Sa_s": float(EXPECTED_PLOT_SAMPLE_RATE),
        "am_input_full_scale_V": float(AM_INPUT_FULL_SCALE_V),
        "am_gain_model": AM_GAIN_MODEL,
        "am_gain_clip_abs": float(AM_GAIN_CLIP_ABS),
        "transfer_experiment_type": TRANSFER_EXPERIMENT_TYPE,
        "transfer_data_dir": None if transfer_model is None else transfer_model["data_dir"],
        "transfer_curve_path": None if transfer_model is None else transfer_model["curve_path"],
        "model_note": (
            "transfer_curve uses measured dg_am -> dg_comp envelope; negative "
            "signed amplitude means 180 deg carrier phase flip"
        ),
        "files": [] if expected is None else [
            str(raw_dir / "expected_waveforms.npz"),
            str(results_dir / "expected_dg_am_envelope.png"),
            str(results_dir / "expected_dg_comp_modulated_full.png"),
            str(results_dir / "expected_dg_comp_modulated_zoom.png"),
        ],
    },
    "scope_acquisition": {
        "enabled": bool(ENABLE_SCOPE_ACQUISITION),
        "target": SCOPE_TARGET,
        "channel_x": int(SCOPE_CHANNEL_X),
        "channel_y": int(SCOPE_CHANNEL_Y),
        "input_impedance": SCOPE_INPUT_IMPEDANCE,
        "coupling": SCOPE_COUPLING,
        "probe_attenuation": float(SCOPE_PROBE_ATTENUATION),
        "trigger_mode": SCOPE_TRIGGER_MODE,
        "trigger_source": SCOPE_TRIGGER_SOURCE,
        "trigger_level_V": float(SCOPE_TRIGGER_LEVEL_V),
        "save_scope_csv": bool(SAVE_SCOPE_CSV),
        "compare_align_to_first_sample": bool(SCOPE_COMPARE_ALIGN_TO_FIRST_SAMPLE),
        "signed_phase_min_abs_gain": float(SIGNED_PHASE_MIN_ABS_GAIN),
        "capture": scope_capture,
    },
}
with open(config_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(scope_config, f, allow_unicode=True, sort_keys=False)
print(f"检查配置已保存: {config_path}")

print("\n示波器连接建议:")
print("  1) 当前默认采集 dg_comp 输出: 示波器 C2 接 dg_comp CH1/X。")
print("  2) 示波器 C3 接 dg_comp CH2/Y。")
print("  3) 预期图默认使用实测 dg_am -> dg_comp transfer curve。")
print("  4) scope_envelope_vs_am_dg_comp.png 用于比较实测包络和预期包络。")
print("  5) scope_signed_phase_check_dg_comp.png 用于验证负 AM 的 180 deg 相位翻转。")
print("\n当前输出已经开启。")

# %% Cell 6
# ========== 保持输出，手动结束 ==========
try:
    if WAIT_BEFORE_EXIT:
        input("示波器观察完成后按 Enter 继续...")
finally:
    if KEEP_OUTPUTS_AFTER_EXIT:
        print("KEEP_OUTPUTS_AFTER_EXIT=True，保持 dg_am/dg_comp 输出开启。")
    else:
        print("正在关闭 dg_am/dg_comp 输出...")
        safe_outputs_off(devices)

print("脚本结束")
